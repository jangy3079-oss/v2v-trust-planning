"""몬테카를로 시뮬레이션 평가 시나리오 모듈.

다차선 고속 주행 환경에서 4종의 위험 시나리오를 무작위성(Monte Carlo Randomness)과 함께 생성한다:
1. Scenario 1 (Reckless Cut-in): 급가속 및 급조향으로 자차 전방을 파고드는 난폭운전 (급성 이상)
2. Scenario 2 (Drowsy Lane Drifting): 미세 흔들림 후 자차 차선으로 서서히 침범하는 졸음운전 (점진적/만성 이상)
3. Scenario 3 (Novice Jittering): 잦은 조향 수정과 불규칙한 속도 변동을 보이는 미숙운전 (Open-set 이상)
4. Scenario 4 (Foggy Adverse): 안개(가시거리 30m) 저시야 환경에서 이상 차량 접근 (센서-V2V 융합 검증)
"""

from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple
import numpy as np


@dataclass
class ScenarioConfig:
    name: str
    warmup_s: float = 3.0  # 30스텝 정상 주행 워밍업 (History 버퍼 100% 충진용)
    duration_s: float = 4.0  # 40스텝 본선 이상 행동 시나리오
    dt: float = 0.1
    ego_initial_speed: float = 20.0  # m/s (~72 km/h)
    target_velocity: float = 20.0
    visibility_m: float = 300.0  # 맑은 날 기본값
    packet_loss_rate: float = 0.0  # 패킷 손실률
    is_occluded_until: float = 0.0  # 센서 사각지대 가림 해제 시점 [s] (워밍업 이후 기준)

    @property
    def total_steps(self) -> int:
        return int((self.warmup_s + self.duration_s) / self.dt)


@dataclass
class ScenarioStep:
    t: float
    neighbor_id: str
    neighbor_position: np.ndarray  # [X, Y]
    neighbor_u: np.ndarray  # [accel, yaw_rate]
    neighbor_speed: float
    is_anomaly: bool  # 지면 실측 라벨 (Ground Truth)
    sensor_estimate: Optional[np.ndarray] = None  # 센서 관측치 (시야 밖이면 None)
    v2v_packet_received: bool = True  # V2V 패킷 수신 성공 여부 (패킷 손실 시뮬레이션)


def generate_scenario_data(
    scenario_type: str,
    seed: int = 42,
    config: Optional[ScenarioConfig] = None,
) -> Tuple[ScenarioConfig, List[ScenarioStep]]:
    """몬테카를로 7.0초(워밍업 3초 + 본선 4초) 극한 가혹 조건 시나리오 시계열 데이터 생성."""
    rng = np.random.RandomState(seed)
    cfg = config or ScenarioConfig(name=scenario_type)

    total_steps = cfg.total_steps
    warmup_steps = int(cfg.warmup_s / cfg.dt)
    result: List[ScenarioStep] = []

    # 가혹 조건별 환경 설정 (물리적 제동거리 한계 시험)
    if scenario_type == "fog":
        # 1. 초극단 안개: 가시거리 10m, 앞차 이상 급감속(a = -5.5 m/s^2), 패킷 손실 25%
        cfg.visibility_m = 10.0
        cfg.packet_loss_rate = 0.25
        obs_init_dist = 18.0 + rng.uniform(-1.0, 1.5)
        obs_init_speed = 20.0 + rng.uniform(-0.5, 0.5)
        cut_in_time = cfg.warmup_s + 0.5 + rng.uniform(-0.1, 0.1)
    elif scenario_type == "novice":
        # 2. 사각지대 기습 침범: 워밍업 후 1.5초간 센서 사각지대 + 기습 끼어들기 및 급감속
        cfg.visibility_m = 300.0
        cfg.is_occluded_until = cfg.warmup_s + 1.5
        cfg.packet_loss_rate = 0.10
        obs_init_dist = 17.0 + rng.uniform(-1.0, 1.0)
        obs_init_speed = 20.0 + rng.uniform(-0.5, 0.5)
        cut_in_time = cfg.warmup_s + 0.6 + rng.uniform(-0.1, 0.1)
    elif scenario_type == "drowsy":
        # 3. 점진적 졸음운전: 서서히 차선 침범 + 가속도 불안정
        cfg.visibility_m = 150.0
        cfg.packet_loss_rate = 0.05
        obs_init_dist = 18.0 + rng.uniform(-1.0, 1.0)
        obs_init_speed = 20.0 + rng.uniform(-0.5, 0.5)
        cut_in_time = cfg.warmup_s + 0.6 + rng.uniform(-0.1, 0.1)
    elif scenario_type == "reckless":
        # 4. 공격적 급감속 기습 칼치기 (Cut-in with Hard Deceleration a = -5.0 m/s^2)
        cfg.visibility_m = 300.0
        cfg.packet_loss_rate = 0.0
        obs_init_dist = 16.0 + rng.uniform(-1.0, 1.0)
        obs_init_speed = 20.0 + rng.uniform(-0.5, 0.5)
        cut_in_time = cfg.warmup_s + 0.5 + rng.uniform(-0.1, 0.1)
    else:
        raise ValueError(f"Unknown scenario: {scenario_type}")

    cut_in_step = int(cut_in_time / cfg.dt)
    curr_x = obs_init_dist
    curr_y = 3.5  # 인접 차선 (폭 3.5m)
    curr_v = obs_init_speed

    for step in range(total_steps):
        t = step * cfg.dt

        # 워밍업 구간 (0 ~ 3.0s): 완벽한 정상 주행 순항
        if step < warmup_steps:
            is_anomaly = False
            accel = rng.normal(0, 0.02)
            yaw_rate = rng.normal(0, 0.005)
            curr_y = 3.5 + rng.normal(0, 0.02)
        else:
            # 본선 시나리오 구간 (3.0s ~ 7.0s)
            is_anomaly = (step >= cut_in_step)

            if scenario_type == "reckless":
                if is_anomaly:
                    accel = -5.0 + rng.normal(0, 0.2)
                    yaw_rate = -0.60 + rng.normal(0, 0.05)
                    curr_y = max(0.0, curr_y - 0.70)
                else:
                    accel = rng.normal(0, 0.05)
                    yaw_rate = rng.normal(0, 0.01)

            elif scenario_type == "drowsy":
                if is_anomaly:
                    t_anom = t - cut_in_time
                    accel = -3.0 + 0.5 * np.sin(2.0 * t_anom) + rng.normal(0, 0.1)
                    yaw_rate = -0.15 + rng.normal(0, 0.02)
                    curr_y = max(0.0, curr_y - 0.35)
                else:
                    accel = rng.normal(0, 0.05)
                    yaw_rate = rng.normal(0, 0.01)

            elif scenario_type == "novice":
                if is_anomaly:
                    t_anom = t - cut_in_time
                    accel = -3.5 + rng.normal(0, 0.2)
                    yaw_rate = 0.25 * np.sin(4.0 * t_anom) + rng.normal(0, 0.04)
                    curr_y = max(0.0, curr_y - 0.50)
                else:
                    accel = rng.normal(0, 0.05)
                    yaw_rate = rng.normal(0, 0.01)

            elif scenario_type == "fog":
                if is_anomaly:
                    accel = -5.5 + rng.normal(0, 0.2)
                    yaw_rate = -0.30 + rng.normal(0, 0.03)
                    curr_y = max(0.0, curr_y - 0.60)
                else:
                    accel = rng.normal(0, 0.05)
                    yaw_rate = rng.normal(0, 0.01)

        curr_v = max(1.0, curr_v + accel * cfg.dt)
        curr_x += curr_v * cfg.dt
        u_true = np.array([accel, yaw_rate])

        # V2V 패킷 손실 시뮬레이션
        v2v_received = bool(rng.uniform(0, 1) >= cfg.packet_loss_rate)

        # 센서 관측 모델
        dist_from_ego_origin = float(np.hypot(curr_x, curr_y))
        is_occluded = (t < cfg.is_occluded_until)
        is_out_of_sight = (dist_from_ego_origin > cfg.visibility_m)

        if is_occluded or is_out_of_sight:
            sensor_est = None
        else:
            sensor_noise = rng.normal(0, 0.05, size=2)
            sensor_est = u_true + sensor_noise

        result.append(
            ScenarioStep(
                t=t,
                neighbor_id="neighbor1",
                neighbor_position=np.array([curr_x, curr_y]),
                neighbor_u=u_true,
                neighbor_speed=curr_v,
                is_anomaly=is_anomaly,
                sensor_estimate=sensor_est,
                v2v_packet_received=v2v_received,
            )
        )

    return cfg, result

