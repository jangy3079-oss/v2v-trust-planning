"""7절: Algorithm 1 — 단일홉 신뢰가중 이상운전 대응 협력 회피 (자차 관점 오케스트레이션).

의사코드(알고리즘_수식_정식화.md 7절) 1~17행에 대응하며, Kinematic Bicycle Model NMPC와 연동된다.
"""

from typing import Dict, List, Optional

import numpy as np

from .anomaly import AnomalyDetector
from .attention import trust_weighted_attention
from .config import Config
from .fusion import local_fusion, sensor_confidence
from .messaging import BSMMessage, SingleHopChannel
from .planning import (
    ObstacleTrajectory,
    PlanningContext,
    SafeMPCPlanner,
    dynamic_safety_margin,
    steer_to_yaw_rate,
)


class EgoVehicleAgent:
    """자차(ego) 하나에 대응하는 상태 보유 객체. 매 스텝 step()을 호출한다."""

    def __init__(
        self,
        vehicle_id: str,
        config: Config,
        channel: SingleHopChannel,
        anomaly_detector: AnomalyDetector,
        planner: SafeMPCPlanner,
        initial_speed: float = 15.0,
        initial_heading: float = 0.0,
    ):
        self.vehicle_id = vehicle_id
        self.config = config
        self.channel = channel
        self.anomaly = anomaly_detector
        self.planner = planner

        # 자차 기구학 상태 추적 [v, theta]
        self.speed = initial_speed
        self.heading = initial_heading

    def step(
        self,
        t: float,
        own_position: np.ndarray,
        own_u: np.ndarray,
        visibility_m: float,
        sensor_estimates: Dict[str, Optional[np.ndarray]],
        u_nominal: np.ndarray,
        opinions_to_share: Optional[Dict[str, float]] = None,
        own_speed: Optional[float] = None,
        own_heading: Optional[float] = None,
        target_velocity: Optional[float] = None,
    ) -> np.ndarray:
        """한 스텝 실행. Algorithm 1의 1~17행에 대응.

        Args:
            t: 현재 시각 [s].
            own_position: 자차 위치, shape (2,).
            own_u: 자차 자신의 압축 운동정보 [accel, heading_rate] (브로드캐스트용).
            visibility_m: 현재 시야(가시거리) [m] — 안개 시뮬레이션 시 낮게 설정.
            sensor_estimates: {neighbor_id: 자기 센서로 추정한 u_hat 또는 None(미탐지)}.
            u_nominal: 이번 스텝의 목표(명목) 제어입력.
            opinions_to_share: (선택) 2절 메시지에 실을 자신의 이웃 신뢰도 판단.
            own_speed: (선택) 자차 현재 속도 [m/s]. None이면 내부 상태 사용.
            own_heading: (선택) 자차 현재 헤딩 [rad]. None이면 내부 상태 사용.
            target_velocity: (선택) 목표 주행 속도 [m/s].

        Returns:
            u_i(t): 최종 제어 입력 [accel, steer_angle], shape (2,).
        """
        if own_speed is not None:
            self.speed = own_speed
        if own_heading is not None:
            self.heading = own_heading

        v_target = target_velocity if target_velocity is not None else max(10.0, self.speed)

        # 1: 원본 정보만 브로드캐스트 (재전달 없음, 단일홉)
        self.channel.broadcast(BSMMessage(self.vehicle_id, t, own_position, own_u, opinions_to_share))

        # 2: 단일홉 수신
        received = self.channel.receive(self.vehicle_id, own_position)

        c_i = sensor_confidence(visibility_m)
        features: Dict[str, np.ndarray] = {}
        trust: Dict[str, float] = {}
        rel_positions: Dict[str, np.ndarray] = {}
        rel_speed_proxy: Dict[str, float] = {}
        obstacles: List[ObstacleTrajectory] = []

        # 3~10: 이웃별 로컬 융합 + 이상탐지(예측오차+CUSUM) + 신뢰도
        for nid, msg in received.items():
            x_j = local_fusion(msg.u, sensor_estimates.get(nid), c_i)  # 3절
            tau_j = self.anomaly.step(nid, x_j)  # 4절
            features[nid] = x_j
            trust[nid] = tau_j
            rel_positions[nid] = msg.position - own_position
            # 융합된 가속도 크기를 상대속도의 대리값으로 사용 (추후 센서/GPS 상대속도 교체 가능)
            rel_speed = float(abs(x_j[0])) + 1.0
            rel_speed_proxy[nid] = rel_speed

            # 13~15: 동적 안전거리 (절대 마진 beta_base + 상대속도 비례 마진 beta_speed)
            d_safe_j = dynamic_safety_margin(
                tau_j, rel_speed, self.config.d0_m, self.config.beta_base, self.config.beta_speed
            )

            # N스텝 장애물 미래 궤적 전개 (장애물의 현재 위치 및 운동 정보 기반)
            obs_traj = np.zeros((2, self.config.mpc_horizon))
            obs_x0, obs_y0 = msg.position[0], msg.position[1]
            for k in range(self.config.mpc_horizon):
                obs_traj[0, k] = obs_x0 + self.speed * np.cos(self.heading) * k * self.config.dt
                obs_traj[1, k] = obs_y0 + self.speed * np.sin(self.heading) * k * self.config.dt

            obstacles.append(ObstacleTrajectory(nid, obs_traj, d_safe_j, tau_j))

        # 11~12: 신뢰가중 어텐션 융합
        alpha, z_i = trust_weighted_attention(features, trust)

        safety_margins = {obs.neighbor_id: obs.d_safe for obs in obstacles}

        # 16: 강건 계획 (Kinematic Bicycle Model NMPC)
        ego_state = np.array([own_position[0], own_position[1], self.heading, self.speed])
        ctx = PlanningContext(
            z_i=z_i if z_i is not None else own_u,
            safety_margins=safety_margins,
            relative_positions=rel_positions,
            u_nominal=u_nominal,
            ego_state=ego_state,
            obstacles=obstacles,
            target_velocity=v_target,
        )
        u_i = self.planner.solve(ctx, trust)

        # 자차 상태 업데이트
        accel_cmd, steer_cmd = float(u_i[0]), float(u_i[1])
        self.speed = max(0.0, self.speed + accel_cmd * self.config.dt)
        self.heading += (self.speed * np.tan(steer_cmd) / self.config.wheelbase_m) * self.config.dt

        # 17: 반환
        return u_i
