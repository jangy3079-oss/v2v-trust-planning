"""4절: 이상탐지 및 신뢰도 산출 (신뢰도의 이중 역할 ①).

설계 근거 (2026-09-03 논의):
- 오토인코더 재구성(reconstruction) 방식은 이상치가 학습된 주성분공간 근처에 우연히
  놓이면 낮은 오차로 "정상"처럼 오판될 수 있다는 신뢰성 문제가 보고됨
  (Autoencoders for Anomaly Detection are Unreliable, arXiv:2501.13864).
  -> 다음스텝 예측(prediction) 방식을 채택. 미래 값을 학습 시 아예 보지 못하므로 더 엄격한 검증.
- 예측 단독도 한계가 있어(AER, arXiv:2212.13558) 보조 재구성항을 가중합으로 추가할 수 있게 함.
- 순간 오차를 그대로 임계값 판정하면 "속임수적(deceptive) OOD" 문제 발생 — 매 순간은
  정상처럼 보이나 위험이 서서히 누적되는 상황(졸음운전의 저주파 미세진동)을 놓친다
  (Dynamic Aware, arXiv:2509.13577). -> 순간 오차 대신 CUSUM 누적통계를 사용.
"""

from collections import defaultdict, deque
from typing import Dict, Optional, Tuple

import numpy as np
import torch
import torch.nn as nn


class NextStepPredictor(nn.Module):
    """f_phi: 과거 K스텝 윈도우 -> 다음스텝 u 예측 (+ 선택적 재구성 보조헤드, AER 방식).

    정상 주행 데이터만으로 자기지도 학습한다 (scripts/train_predictor.py 참고).
    이상치 라벨이 학습 단계에서 전혀 필요하지 않다.
    """

    def __init__(self, input_dim: int = 2, hidden_dim: int = 32, use_recon_head: bool = True):
        super().__init__()
        self.gru = nn.GRU(input_size=input_dim, hidden_size=hidden_dim, batch_first=True)
        self.predict_head = nn.Linear(hidden_dim, input_dim)
        self.use_recon_head = use_recon_head
        if use_recon_head:
            self.recon_head = nn.Linear(hidden_dim, input_dim)

    def forward(self, window: torch.Tensor) -> Tuple[torch.Tensor, Optional[torch.Tensor]]:
        """window: (B, K, input_dim).

        Returns:
            u_hat_next: (B, input_dim) — 다음 스텝 예측.
            u_recon_last: (B, input_dim) 또는 None — 마지막 입력 스텝의 재구성(use_recon_head=True일 때만).
        """
        out, _ = self.gru(window)
        last_hidden = out[:, -1, :]
        u_hat_next = self.predict_head(last_hidden)
        u_recon_last = self.recon_head(last_hidden) if self.use_recon_head else None
        return u_hat_next, u_recon_last

    @torch.no_grad()
    def predict(self, window: np.ndarray) -> Tuple[np.ndarray, Optional[np.ndarray]]:
        """agent.py 등 numpy 기반 온라인 루프에서 쓰는 추론 전용 편의 메서드.

        Args:
            window: shape (K, input_dim).
        """
        self.eval()
        w = torch.from_numpy(window).float().unsqueeze(0)
        u_hat, u_recon = self(w)
        u_hat_np = u_hat.squeeze(0).numpy()
        u_recon_np = u_recon.squeeze(0).numpy() if u_recon is not None else None
        return u_hat_np, u_recon_np


class HistoryBuffer:
    """이웃 차량별 최근 K스텝 특징 이력 X_j^(i)(t) 관리.

    버퍼가 K개로 가득 찰 때까지는 예측에 사용할 윈도우를 내주지 않는다
    (get_window가 None을 반환) — 즉 관측 시작 후 K스텝 동안은 신뢰도가 갱신되지 않고
    tau=1.0(초기값)로 유지된다.
    """

    def __init__(self, k: int):
        self.k = k
        self._buffers: Dict[str, deque] = defaultdict(lambda: deque(maxlen=k))

    def push(self, neighbor_id: str, x_t: np.ndarray) -> None:
        self._buffers[neighbor_id].append(x_t)

    def get_window(self, neighbor_id: str) -> Optional[np.ndarray]:
        buf = self._buffers[neighbor_id]
        if len(buf) < self.k:
            return None
        return np.stack(buf, axis=0)  # (K, feature_dim)


class CUSUMTracker:
    """4.2절: 순간 오차 e(t) -> 누적 통계 S(t) -> 신뢰도 tau(t).

    급성 이상(난폭운전의 급조작)은 e(t) 급등으로 즉시 S가 임계값을 넘고,
    만성/점진적 이상(졸음)은 작은 초과분이 누적되며 S가 서서히 커져 임계값을 넘는다.
    """

    def __init__(self, nu: float, trust_lambda: float):
        self.nu = nu
        self.trust_lambda = trust_lambda
        self._S: Dict[str, float] = defaultdict(float)

    def update(self, neighbor_id: str, e_t: float) -> float:
        """S_j(t) = max(0, S_j(t-1) + e(t) - nu). 반환: tau_j(t)."""
        s_new = max(0.0, self._S[neighbor_id] + e_t - self.nu)
        self._S[neighbor_id] = s_new
        return self.trust(neighbor_id)

    def trust(self, neighbor_id: str) -> float:
        return float(np.exp(-self._S[neighbor_id] / self.trust_lambda))

    def reset(self, neighbor_id: str) -> None:
        self._S[neighbor_id] = 0.0


def calibrate_nu(
    predictor: NextStepPredictor,
    normal_sequences,
    k: int,
    recon_weight: float = 0.3,
    percentile: float = 95.0,
) -> float:
    """4.2절의 nu(정상 허용 미세오차)를 정상 시퀀스들에서 실측 산정한다.

    주의: config.py의 기본값(cusum_nu)은 임의값이므로 반드시 이 함수로 재산정해서 덮어쓸 것.
    실측 없이 임의 임계값을 쓰면 CUSUM이 전혀 누적되지 않거나(임계값이 너무 큼) 정상 주행도
    계속 이상으로 잡히는(너무 작음) 문제가 생긴다 — percentile 기반 산정을 스켈레톤 기본값으로 한다.

    Args:
        predictor: 학습된(정상데이터로 자기지도 학습된) NextStepPredictor.
        normal_sequences: 정상 주행 시퀀스 목록, 각 원소 shape (T, feature_dim).
            학습에 쓰지 않은 held-out 정상 세션을 쓰는 것을 권장.
        k: history_len.
        percentile: 이 백분위수를 nu로 사용 (기본 95 — 정상 오차의 상위 5%까지는 허용).
    """
    detector = AnomalyDetector(predictor, k, predictor.predict_head.out_features, nu=0.0, trust_lambda=1.0, recon_weight=recon_weight)
    errors = []
    for seq_idx, seq in enumerate(normal_sequences):
        vid = f"_calib_{seq_idx}"
        for x_t in seq:
            s_before = detector.cusum._S[vid]
            detector.step(vid, x_t)
            errors.append(detector.cusum._S[vid] - s_before)
    errors = np.array(errors[len(normal_sequences) * k :]) if errors else np.array([0.0])
    # 위 슬라이싱은 근사치(세션마다 초기 k스텝은 e_t=0이라 어차피 낮게 잡힘) — 정확히 하려면
    # 세션 경계마다 초반 k개를 걸러내는 로직으로 교체할 것.
    return float(np.percentile(errors, percentile))


class AnomalyDetector:
    """4절 전체(예측기 + 이력버퍼 + CUSUM)를 캡슐화한 온라인 이상탐지기.

    step()을 매 스텝 이웃별로 호출하면 신뢰도 tau_j^(i)(t)가 반환된다.
    내부적으로 "직전 스텝에 예측해 둔 값 vs 이번에 도착한 실측값"을 비교해 오차를 내고,
    (온라인 예측-검증 루프이므로 K스텝이 쌓이기 전까지는 pending prediction이 없어 e_t=0으로 처리된다.)
    """

    def __init__(
        self,
        predictor: NextStepPredictor,
        k: int,
        feature_dim: int,
        nu: float,
        trust_lambda: float,
        recon_weight: float = 0.3,
    ):
        self.predictor = predictor
        self.feature_dim = feature_dim
        self.history = HistoryBuffer(k)
        self.cusum = CUSUMTracker(nu, trust_lambda)
        self.recon_weight = recon_weight
        self._pending_pred: Dict[str, np.ndarray] = {}

    def step(self, neighbor_id: str, x_t: np.ndarray) -> float:
        """새 관측 x_t를 받아 신뢰도 tau_j^(i)(t)를 반환. Algorithm 1의 4~10행에 대응."""
        e_t = 0.0
        if neighbor_id in self._pending_pred:
            e_t = float(np.sum((x_t - self._pending_pred[neighbor_id]) ** 2))

        self.history.push(neighbor_id, x_t)
        window = self.history.get_window(neighbor_id)
        if window is not None:
            u_hat_next, u_recon = self.predictor.predict(window)
            self._pending_pred[neighbor_id] = u_hat_next
            if u_recon is not None:
                e_t += self.recon_weight * float(np.sum((x_t - u_recon) ** 2))
        else:
            self._pending_pred.pop(neighbor_id, None)

        return self.cusum.update(neighbor_id, e_t)
