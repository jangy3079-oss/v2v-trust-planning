"""3절: 수신 및 로컬 융합.

자차는 수신한 브로드캐스트 u_j(t)를 자신의 센서 기반 상대 관측치와 결합한다.
안개 등으로 센서 신뢰도가 떨어질수록 V2V 정보의 비중이 자동으로 커진다.
"""

from typing import Optional

import numpy as np


def sensor_confidence(visibility_m: float, reference_visibility_m: float = 200.0) -> float:
    """센서(카메라/라이다) 신뢰도 c_i(t) in [0,1].

    가시거리가 reference_visibility_m 이상이면 1.0(정상), 짧을수록 0에 수렴한다.
    안개 시뮬레이션에서는 visibility_m을 낮춰서 넘기면 된다.
    """
    return float(np.clip(visibility_m / reference_visibility_m, 0.0, 1.0))


def local_fusion(
    u_broadcast: np.ndarray,
    u_sensor_estimate: Optional[np.ndarray],
    c_i: float,
) -> np.ndarray:
    """x_j^(i)(t) = c_i * u_hat_sensor + (1 - c_i) * u_broadcast.

    Args:
        u_broadcast: 이웃 j가 보낸 u_j(t), shape (2,).
        u_sensor_estimate: 자기 센서로 추정한 j의 상대운동, shape (2,).
            센서로 아예 탐지하지 못했다면 None을 전달 — 이 경우 브로드캐스트 값만 사용한다.
        c_i: sensor_confidence()의 반환값.

    Returns:
        융합된 특징 x_j^(i)(t), shape (2,). (거리 등 상대기하 피처를 추가하려면
        이 함수의 반환값에 concatenate하여 feature_dim을 확장할 것.)
    """
    if u_sensor_estimate is None:
        return u_broadcast.copy()
    return c_i * u_sensor_estimate + (1.0 - c_i) * u_broadcast
