"""파이프라인 배선이 깨지지 않았는지 확인하는 최소 스모크 테스트.

실행: pytest tests/
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import numpy as np

from v2v_trust_planning.agent import EgoVehicleAgent
from v2v_trust_planning.anomaly import AnomalyDetector, NextStepPredictor
from v2v_trust_planning.config import Config
from v2v_trust_planning.messaging import BSMMessage, SingleHopChannel
from v2v_trust_planning.planning import SafeMPCPlanner


def _build_agent(config: Config, channel: SingleHopChannel) -> EgoVehicleAgent:
    predictor = NextStepPredictor(config.feature_dim, config.predictor_hidden_dim)
    detector = AnomalyDetector(predictor, config.history_len, config.feature_dim, config.cusum_nu, config.trust_lambda)
    planner = SafeMPCPlanner(config.lambda1_uncertainty, config.lambda2_comfort)
    return EgoVehicleAgent("ego", config, channel, detector, planner)


def test_agent_step_runs_and_returns_correct_shape():
    config = Config()
    channel = SingleHopChannel(comm_range_m=config.comm_range_fog_m)
    ego = _build_agent(config, channel)

    channel.broadcast(BSMMessage("n1", 0.0, np.array([10.0, 0.0]), np.array([0.1, 0.0])))

    u = ego.step(
        t=0.0,
        own_position=np.zeros(2),
        own_u=np.zeros(2),
        visibility_m=300.0,
        sensor_estimates={"n1": None},
        u_nominal=np.zeros(2),
    )
    assert u.shape == (2,)


def test_channel_respects_single_hop_range():
    channel = SingleHopChannel(comm_range_m=50.0)
    channel.broadcast(BSMMessage("far", 0.0, np.array([1000.0, 0.0]), np.array([0.0, 0.0])))
    channel.broadcast(BSMMessage("near", 0.0, np.array([10.0, 0.0]), np.array([0.0, 0.0])))

    received = channel.receive("ego", np.zeros(2))
    assert "near" in received
    assert "far" not in received


def test_trust_stays_at_full_confidence_before_history_fills():
    """K스텝이 쌓이기 전에는 예측이 아직 없으므로 e_t=0 -> tau=1.0 이어야 한다."""
    config = Config()
    predictor = NextStepPredictor(config.feature_dim, config.predictor_hidden_dim)
    detector = AnomalyDetector(predictor, config.history_len, config.feature_dim, config.cusum_nu, config.trust_lambda)

    tau = detector.step("n1", np.array([0.0, 0.0]))
    assert tau == 1.0


def test_multiple_neighbors_are_fused_with_attention():
    config = Config()
    channel = SingleHopChannel(comm_range_m=config.comm_range_fog_m)
    ego = _build_agent(config, channel)

    channel.broadcast(BSMMessage("front", 0.0, np.array([15.0, 0.0]), np.array([0.05, 0.0])))
    channel.broadcast(BSMMessage("rear", 0.0, np.array([-10.0, 0.0]), np.array([-0.05, 0.0])))

    u = ego.step(
        t=0.0,
        own_position=np.zeros(2),
        own_u=np.zeros(2),
        visibility_m=300.0,
        sensor_estimates={"front": None, "rear": None},
        u_nominal=np.zeros(2),
    )
    assert u.shape == (2,)


def test_trust_aware_nmpc_produces_stronger_deceleration_for_low_trust():
    """신뢰도 tau가 낮을수록 d_safe가 확장되어 더 강한 선제적 감속이 발생하는지 검증 (축3 차별점)."""
    config = Config(mpc_horizon=10, d0_m=5.0, beta=3.0)
    from v2v_trust_planning.planning import ObstacleTrajectory, TrustAwareNMPC, dynamic_safety_margin

    nmpc = TrustAwareNMPC(config, max_obstacles=2)

    ego_state = np.array([0.0, 0.0, 0.0, 15.0])  # [X=0, Y=0, theta=0, v=15m/s]
    obs_x = 20.0  # 전방 20m에 위치한 장애물

    # 1) 정상 신뢰도 (tau = 1.0)
    d_safe_high = dynamic_safety_margin(1.0, 15.0, config.d0_m, config.beta_base, config.beta_speed)
    obs_traj_high = np.zeros((2, config.mpc_horizon))
    for k in range(config.mpc_horizon):
        obs_traj_high[0, k] = obs_x + 15.0 * k * config.dt
    obs_high = [ObstacleTrajectory("obs", obs_traj_high, d_safe_high, 1.0)]

    u_high, _ = nmpc.solve(ego_state, obstacles=obs_high, target_velocity=15.0)

    # 2) 비정상/불확실 신뢰도 (tau = 0.1)
    d_safe_low = dynamic_safety_margin(0.1, 15.0, config.d0_m, config.beta_base, config.beta_speed)
    obs_traj_low = obs_traj_high.copy()
    obs_low = [ObstacleTrajectory("obs", obs_traj_low, d_safe_low, 0.1)]

    u_low, _ = nmpc.solve(ego_state, obstacles=obs_low, target_velocity=15.0)

    # tau가 낮을 때 안전거리가 훨씬 커지므로 가속도가 더 음수(강한 감속)여야 한다
    assert d_safe_low > d_safe_high
    assert u_low[0] < u_high[0]
    assert config.min_accel_mps2 <= u_low[0] <= config.max_accel_mps2
    assert config.min_steer_rad <= u_low[1] <= config.max_steer_rad
