"""4.2절 CUSUM의 nu(정상 허용 미세오차)를 정상 시퀀스에서 실측 산정한다.

config.py의 cusum_nu 기본값은 이 스크립트로 산정한 값이다. predictor를 다시 학습하거나
정상 데이터를 UAH-DriveSet 등 실데이터로 교체하면 반드시 재실행해서 값을 갱신할 것 —
임의 임계값을 쓰면 CUSUM이 전혀 누적되지 않거나(너무 큼) 정상 주행도 계속 이상으로 잡히는
(너무 작음) 문제가 생긴다.

실행:
    python scripts/train_predictor.py   # predictor_normal.pt 생성
    python scripts/calibrate_nu.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import torch

from v2v_trust_planning.anomaly import NextStepPredictor, calibrate_nu
from v2v_trust_planning.config import Config
from v2v_trust_planning.datasets import SyntheticConfig, synthetic_normal_sequence


def main() -> None:
    cfg = Config()
    predictor = NextStepPredictor(cfg.feature_dim, cfg.predictor_hidden_dim)
    weights_path = Path(__file__).resolve().parent / "predictor_normal.pt"
    predictor.load_state_dict(torch.load(weights_path))

    # TODO: 학습에 쓴 시퀀스(seed 0~19)와 겹치지 않는 held-out 정상 세션으로 교체할 것.
    # 실제로는 UAH-DriveSet의 NORMAL 세션 중 학습에 쓰지 않은 나머지를 쓴다.
    held_out_normal = [synthetic_normal_sequence(SyntheticConfig(length=200), seed=100 + s) for s in range(10)]

    nu_95 = calibrate_nu(predictor, held_out_normal, cfg.history_len, cfg.recon_loss_weight, percentile=95.0)
    nu_99 = calibrate_nu(predictor, held_out_normal, cfg.history_len, cfg.recon_loss_weight, percentile=99.0)

    print(f"nu (95th percentile, 권장 기본값) = {nu_95:.5f}")
    print(f"nu (99th percentile, 더 보수적)   = {nu_99:.5f}")
    print("\nconfig.py의 cusum_nu 값을 위 결과로 갱신할 것.")


if __name__ == "__main__":
    main()
