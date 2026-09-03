"""전역 하이퍼파라미터. 알고리즘_수식_정식화.md 각 절의 기호에 그대로 대응한다."""

from dataclasses import dataclass


@dataclass(frozen=True)
class Config:
    # 1절: 시간/통신
    dt: float = 0.1  # BSM 주기 (10Hz)
    comm_range_clear_m: float = 1000.0  # 맑은 날 단일홉 범위
    comm_range_fog_m: float = 300.0  # 안개 시 단일홉 범위

    # 3절: 로컬 융합
    feature_dim: int = 2  # x_j = [accel, heading_rate]

    # 4절: 이상탐지 (예측 기반 + CUSUM)
    history_len: int = 30  # K, 3초 윈도우 @ 10Hz
    predictor_hidden_dim: int = 32
    recon_loss_weight: float = 0.3  # gamma, AER식 보조 재구성항 가중치
    # nu, 정상 허용 미세오차. 아래 값은 synthetic_normal_sequence(스켈레톤 기본 predictor 기준)의
    # 오차 분포 95th percentile로 실측 산정한 값(scripts/calibrate_nu.py). 데이터셋/predictor를
    # 바꾸면 반드시 anomaly.calibrate_nu()로 재산정할 것 — recalibrate 없이 재사용 금지.
    cusum_nu: float = 0.011
    trust_lambda: float = 1.0  # lambda, S -> tau 변환 스케일

    # 6절: 강건 계획 (Kinematic Bicycle Model NMPC)
    d0_m: float = 5.0  # 기본 안전거리
    beta_base: float = 3.0  # 신뢰도 저하 시 상대속도 무관 절대 공간 마진 [m]
    beta_speed: float = 1.0  # 상대속도 비례 동적 확장 계수
    beta: float = 1.0  # (하위 호환용)
    mpc_horizon: int = 10  # N
    wheelbase_m: float = 2.7  # L (Wheelbase [m])
    max_accel_mps2: float = 3.0  # 최대 가속도 [m/s^2]
    min_accel_mps2: float = -5.0  # 최대 감속도 [m/s^2]
    max_steer_rad: float = 0.5  # 최대 조향각 [rad] (~28.6도)
    min_steer_rad: float = -0.5  # 최소 조향각 [rad]
    weight_track_pos: float = 0.8  # 기준 경로 추종 가중치 (2.0 -> 0.8: 차선 내 횡방향 회피 허용)
    weight_track_vel: float = 1.0  # 기준 속도 추종 가중치
    weight_control_accel: float = 0.1  # 가속도 페널티
    weight_control_steer: float = 0.2  # 조향각 페널티
    weight_delta_control: float = 2.5  # 제어 입력 변화율 (저크) 억제 가중치 (0.5 -> 2.5: 승차감 극대화)
    weight_slack: float = 10000.0  # 충돌 회피 슬랙 변수 페널티 (안전 절대 보장 유지)
    slack_reg: float = 1e-4  # 거리 계산 수치 안정화 상수

    # 이전 스켈레톤 호환용
    lambda1_uncertainty: float = 1.0
    lambda2_comfort: float = 0.1
