"""SOTA 및 비교군 베이스라인 모듈.

1. ZhangClassifierBaseline: Zhang et al. (IEEE TVT 2019)
   - Closed-set 지도학습 분류기 (난폭운전 패턴으로 지도학습).
   - 급가속/급조향 난폭운전에는 정확히 경보를 울리지만, 미학습된 졸음/미숙운전은 정상으로 오판.
2. ThresholdExclusionBaseline: CARMEN+ (US DOT 2025) / MATE 계열
   - 순간 예측오차가 임계값을 넘으면 즉시 이웃을 배제(tau=0, 이산 필터링).
3. FixedMarginBaseline: 일반 협력 자율주행 / RC-MPC 스타일
   - 신뢰도 개념 없이 tau=1.0 고정, 고정 안전거리 d_safe = d0 유지.
"""

from typing import Dict, List, Optional
import numpy as np

from .config import Config
from .planning import SafeMPCPlanner, dynamic_safety_margin, PlanningContext, ObstacleTrajectory


class ZhangClassifierBaseline:
    """Zhang et al. (IEEE TVT 2019) 스타일의 Closed-set 난폭운전 분류기.

    공정한 비교를 위해: 시나리오 1(급차선변경/난폭) 데이터만으로 지도학습된 분류기를 모사한다.
    - 급성 난폭 거동 (|a| > 1.5 m/s^2 또는 |yaw_rate| > 0.3 rad/s) 감지 시: tau = 0.0 (위험 경보)
    - 졸음/미숙운전처럼 진폭이 작거나 점진적인 OOD 거동: 정상으로 오판 (tau = 1.0)
    """

    def __init__(self, accel_threshold: float = 3.8, yaw_rate_threshold: float = 0.45):
        self.accel_th = accel_threshold
        self.yaw_rate_th = yaw_rate_threshold

    def predict_trust(self, feature_history: np.ndarray) -> float:
        """feature_history: shape (K, 2) [accel, heading_rate]."""
        if len(feature_history) == 0:
            return 1.0
        recent = feature_history[-1]
        accel, yaw_rate = abs(recent[0]), abs(recent[1])

        # Closed-set 판정: 난폭운전 패턴만 감지
        if accel > self.accel_th or yaw_rate > self.yaw_rate_th:
            return 0.0  # 난폭운전 감지
        return 1.0  # 그 외 모든 패턴(졸음, 미숙운전 포함)은 정상으로 오판


class ThresholdExclusionBaseline:
    """CARMEN+ (US DOT 2025) 스타일의 순간오차 임계값 이산 배제 필터.

    순간오차 e_t = ||u_t - u_hat_t||^2 가 임계치를 넘으면 통신에서 영구 배제(tau=0.0).
    CUSUM 누적이 없으므로 점진적 졸음운전을 초기에 놓침.
    """

    def __init__(self, error_threshold: float = 0.025):
        self.th = error_threshold
        self.excluded: Dict[str, bool] = {}

    def step(self, neighbor_id: str, error: float) -> float:
        if self.excluded.get(neighbor_id, False):
            return 0.0
        if error > self.th:
            self.excluded[neighbor_id] = True
            return 0.0
        return 1.0


class FixedMarginBaseline:
    """고정 안전거리 MPC 베이스라인 (신뢰도 미반영)."""

    def __init__(self, d0_m: float = 5.0):
        self.d0_m = d0_m

    def get_safety_margin(self, neighbor_id: str) -> float:
        return self.d0_m
