"""baselines.py 및 scenarios.py 동작 확인 단위 테스트."""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import numpy as np
from v2v_trust_planning.baselines import (
    ZhangClassifierBaseline,
    ThresholdExclusionBaseline,
    FixedMarginBaseline,
)
from v2v_trust_planning.scenarios import generate_scenario_data


def test_zhang_baseline_detects_reckless_but_misses_drowsy():
    zhang = ZhangClassifierBaseline(accel_threshold=1.4, yaw_rate_threshold=0.28)

    # 1) 난폭운전 패턴 (급가속 및 급조향) -> 위험 감지 (tau = 0.0)
    reckless_features = np.array([[2.0, -0.35]])
    assert zhang.predict_trust(reckless_features) == 0.0

    # 2) 점진적 졸음 패턴 (작은 진폭) -> 오판 (tau = 1.0)
    drowsy_features = np.array([[-0.4, -0.09]])
    assert zhang.predict_trust(drowsy_features) == 1.0


def test_threshold_exclusion_baseline():
    carmen = ThresholdExclusionBaseline(error_threshold=0.02)
    # 작은 오차는 통과
    assert carmen.step("n1", 0.005) == 1.0
    # 큰 오차 발생 시 영구 배제
    assert carmen.step("n1", 0.035) == 0.0
    # 이후 다시 작은 오차가 와도 영구 배제 상태 유지
    assert carmen.step("n1", 0.001) == 0.0


def test_scenarios_generate_valid_trajectories():
    for sc_type in ["reckless", "drowsy", "novice", "fog"]:
        cfg, steps = generate_scenario_data(sc_type, seed=123)
        assert len(steps) == cfg.total_steps
        assert steps[0].neighbor_position.shape == (2,)
        assert steps[0].neighbor_u.shape == (2,)
        if sc_type == "fog":
            assert cfg.visibility_m == 10.0
            assert steps[0].sensor_estimate is None
