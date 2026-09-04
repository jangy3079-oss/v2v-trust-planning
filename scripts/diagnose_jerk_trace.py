"""문제 B 정밀 진단 스크립트 (요청사항 3, 4, 5 반영).

반영 사항:
3. "불가피한 안전제동" 트레이드오프 검증: 슬루레이트 제한 적용 시 피크 저크뿐만 아니라 min_dist 및 collision_rate(%)를 30 seed로 측정.
4. 문제 A와 원인 공유 여부 확인: 문제 A의 램프 시나리오(0.5s, 1.0s)를 reckless에 적용해 저크/min_dist 변화 측정.
5. Fallback 판정: planning.py 소스코드(line 231의 fallback_a = -3.0 및 RuntimeError)를 바탕으로,
   임의 감속값 매칭이 아닌 opti.solve()의 RuntimeError 예외 발생 여부를 직접 계측.
6. 30 seed 몬테카를로 정량 결과(평균 ± 표준편차) 객관적 전달.

실행:
    python scripts/diagnose_jerk_trace.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from typing import Dict, List, Optional, Tuple
import matplotlib.pyplot as plt
import numpy as np
import torch

from v2v_trust_planning.anomaly import AnomalyDetector, NextStepPredictor
from v2v_trust_planning.baselines import ZhangClassifierBaseline, ThresholdExclusionBaseline
from v2v_trust_planning.config import Config
from v2v_trust_planning.planning import ObstacleTrajectory, TrustAwareNMPC, dynamic_safety_margin
from v2v_trust_planning.scenarios import ScenarioConfig, ScenarioStep, generate_scenario_data


class DiagnosticNMPC(TrustAwareNMPC):
    """planning.py의 실제 구현을 바탕으로 RuntimeError 예외를 직접 포착하는 진단 솔버."""

    def __init__(self, config: Config, max_obstacles: int = 1):
        super().__init__(config, max_obstacles=max_obstacles)
        self.fallback_count: int = 0

    def solve_diagnostic(
        self,
        ego_state: np.ndarray,
        obstacles: Optional[List[ObstacleTrajectory]] = None,
        target_velocity: float = 20.0,
    ) -> Tuple[np.ndarray, np.ndarray, bool]:
        ref_traj = np.zeros((4, self.N + 1))
        curr_x, curr_y, curr_theta, _ = ego_state
        for k in range(self.N + 1):
            ref_traj[0, k] = curr_x + target_velocity * np.cos(curr_theta) * k * self.dt
            ref_traj[1, k] = curr_y + target_velocity * np.sin(curr_theta) * k * self.dt
            ref_traj[2, k] = curr_theta
            ref_traj[3, k] = target_velocity

        self.opti.set_value(self.x0, ego_state.reshape(4, 1))
        self.opti.set_value(self.u_prev, self.last_u.reshape(2, 1))
        self.opti.set_value(self.ref_traj, ref_traj)

        obs_list = obstacles or []
        for j in range(self.max_obs):
            if j < len(obs_list):
                obs = obs_list[j]
                self.opti.set_value(self.obs_traj[j], obs.trajectory)
                self.opti.set_value(self.obs_dsafe[j], float(obs.d_safe))
                self.opti.set_value(self.obs_active[j], 1.0)
            else:
                self.opti.set_value(self.obs_traj[j], np.ones((2, self.N)) * 1000.0)
                self.opti.set_value(self.obs_dsafe[j], 0.0)
                self.opti.set_value(self.obs_active[j], 0.0)

        try:
            sol = self.opti.solve()
            a_val = float(np.clip(sol.value(self.U[0, 0]), self.cfg.min_accel_mps2, self.cfg.max_accel_mps2))
            steer_val = float(np.clip(sol.value(self.U[1, 0]), self.cfg.min_steer_rad, self.cfg.max_steer_rad))
            u_opt = np.array([a_val, steer_val], dtype=float)
            x_pred = sol.value(self.X)
            self.last_u = u_opt.copy()
            return u_opt, x_pred, False

        except RuntimeError:
            self.fallback_count += 1
            # planning.py 라인 231-232의 실제 코드:
            fallback_a = float(np.clip(-3.0, self.cfg.min_accel_mps2, self.cfg.max_accel_mps2))
            fallback_u = np.array([fallback_a, 0.0])
            self.last_u = fallback_u.copy()
            fallback_x = np.repeat(ego_state.reshape(4, 1), self.N + 1, axis=1)
            return fallback_u, fallback_x, True


def generate_reckless_ramp_scenario(
    ramp_duration_s: float,
    seed: int = 42,
    warmup_s: float = 3.0,
    duration_s: float = 4.0,
    dt: float = 0.1,
) -> Tuple[ScenarioConfig, List[ScenarioStep]]:
    """문제 4번 확인용: Reckless 시나리오의 급감속/급조향에 램프 적용."""
    rng = np.random.RandomState(seed)
    cfg = ScenarioConfig(
        name=f"reckless_ramp_{ramp_duration_s:.1f}s",
        warmup_s=warmup_s,
        duration_s=duration_s,
        dt=dt,
        visibility_m=300.0,
        packet_loss_rate=0.0,
    )

    total_steps = cfg.total_steps
    warmup_steps = int(cfg.warmup_s / cfg.dt)
    cut_in_time = cfg.warmup_s + 0.5
    cut_in_step = int(cut_in_time / cfg.dt)

    obs_init_dist = 16.0 + rng.uniform(-0.5, 0.5)
    obs_init_speed = 20.0
    curr_x = obs_init_dist
    curr_y = 3.5
    curr_v = obs_init_speed

    steps: List[ScenarioStep] = []

    for step in range(total_steps):
        t = step * cfg.dt

        if step < warmup_steps:
            is_anomaly = False
            accel = rng.normal(0, 0.02)
            yaw_rate = rng.normal(0, 0.005)
            curr_y = 3.5 + rng.normal(0, 0.02)
        else:
            is_anomaly = (step >= cut_in_step)
            if not is_anomaly:
                accel = rng.normal(0, 0.05)
                yaw_rate = rng.normal(0, 0.01)
            else:
                t_anom = t - cut_in_time
                if ramp_duration_s <= 1e-4:
                    ramp_scale = 1.0
                else:
                    ratio = min(1.0, t_anom / ramp_duration_s)
                    ramp_scale = 3.0 * (ratio ** 2) - 2.0 * (ratio ** 3)

                accel = ramp_scale * (-5.0) + rng.normal(0, 0.1)
                yaw_rate = ramp_scale * (-0.60) + rng.normal(0, 0.03)
                curr_y = max(0.0, curr_y - 0.70 * ramp_scale)

        curr_v = max(1.0, curr_v + accel * cfg.dt)
        curr_x += curr_v * cfg.dt
        u_true = np.array([accel, yaw_rate])
        sensor_est = u_true + rng.normal(0, 0.05, size=2)

        steps.append(
            ScenarioStep(
                t=t,
                neighbor_id="neighbor1",
                neighbor_position=np.array([curr_x, curr_y]),
                neighbor_u=u_true,
                neighbor_speed=curr_v,
                is_anomaly=is_anomaly,
                sensor_estimate=sensor_est,
                v2v_packet_received=True,
            )
        )

    return cfg, steps


def simulate_single_seed(
    scenario_type: str,
    model_name: str,
    seed: int,
    cfg: Config,
    predictor: NextStepPredictor,
    d_safe_slew_rate: Optional[float] = None,
    zero_sensor_noise: bool = False,
    ramp_duration_s: Optional[float] = None,
) -> Dict:
    if ramp_duration_s is not None and scenario_type == "reckless":
        sc_cfg, steps = generate_reckless_ramp_scenario(ramp_duration_s=ramp_duration_s, seed=seed)
    else:
        sc_cfg, steps = generate_scenario_data(scenario_type, seed=seed)

    nmpc = DiagnosticNMPC(cfg, max_obstacles=1)
    det = AnomalyDetector(predictor, cfg.history_len, cfg.feature_dim, cfg.cusum_nu, cfg.trust_lambda)
    carmen = ThresholdExclusionBaseline(error_threshold=0.02)
    zhang = ZhangClassifierBaseline()

    ego_state = np.array([0.0, 0.0, 0.0, 20.0])
    feature_hist = []
    last_known_pos: Optional[np.ndarray] = None
    last_known_speed: float = 20.0
    last_known_u: np.ndarray = np.zeros(2)
    comm_alive: bool = False
    prev_d_safe: float = cfg.d0_m
    prev_accel: float = 0.0

    accels, jerks, dists, fallbacks = [], [], [], []

    for s in steps:
        has_sensor_sight = (s.sensor_estimate is not None)
        v2v_available = s.v2v_packet_received
        is_excluded = (model_name == "CARMEN+ (2025)" and carmen.excluded.get(s.neighbor_id, False))

        if zero_sensor_noise and s.sensor_estimate is not None:
            current_sensor_est = s.neighbor_u.copy()
        else:
            current_sensor_est = s.sensor_estimate

        if model_name == "Sensor-Only MPC":
            can_perceive = has_sensor_sight
            if has_sensor_sight:
                current_obs_pos = s.neighbor_position.copy()
                current_obs_speed = s.neighbor_speed
                current_obs_u = current_sensor_est
            else:
                current_obs_pos, current_obs_speed, current_obs_u = None, 20.0, np.zeros(2)
        else:
            if has_sensor_sight:
                current_obs_pos = s.neighbor_position.copy()
                current_obs_speed = s.neighbor_speed
                current_obs_u = current_sensor_est
                last_known_pos = current_obs_pos.copy()
                last_known_speed = current_obs_speed
                last_known_u = current_obs_u.copy()
                can_perceive = True
            elif v2v_available and not is_excluded:
                current_obs_pos = s.neighbor_position.copy()
                current_obs_speed = s.neighbor_speed
                current_obs_u = s.neighbor_u
                last_known_pos = current_obs_pos.copy()
                last_known_speed = current_obs_speed
                last_known_u = current_obs_u.copy()
                comm_alive = True
                can_perceive = True
            elif comm_alive and not is_excluded and last_known_pos is not None:
                last_known_pos[0] += last_known_speed * cfg.dt
                current_obs_pos = last_known_pos.copy()
                current_obs_speed = last_known_speed
                current_obs_u = last_known_u.copy()
                can_perceive = True
            else:
                current_obs_pos, current_obs_speed, current_obs_u = None, 20.0, np.zeros(2)
                can_perceive = False

        if current_obs_pos is not None:
            feature_hist.append(current_obs_u)
            if len(feature_hist) > cfg.history_len:
                feature_hist.pop(0)

            if model_name == "Zhang et al. (2019)":
                tau = zhang.predict_trust(np.array(feature_hist))
            elif model_name == "Fixed-Margin MPC (V2V)":
                tau = 1.0
            else:
                tau = det.step(s.neighbor_id, current_obs_u)
        else:
            tau = 1.0

        rel_speed = max(0.0, ego_state[3] - current_obs_speed)
        if model_name in ["Sensor-Only MPC", "Fixed-Margin MPC (V2V)"]:
            raw_d_safe = cfg.d0_m
        elif model_name == "Zhang et al. (2019)":
            raw_d_safe = cfg.d0_m + ((cfg.beta_base + cfg.beta_speed * rel_speed) if tau == 0.0 else 0.0)
        else:
            raw_d_safe = dynamic_safety_margin(tau, rel_speed, cfg.d0_m, cfg.beta_base, cfg.beta_speed)

        if d_safe_slew_rate is not None:
            max_delta = d_safe_slew_rate * cfg.dt
            filtered_d_safe = float(np.clip(raw_d_safe, prev_d_safe - max_delta, prev_d_safe + max_delta))
            prev_d_safe = filtered_d_safe
        else:
            filtered_d_safe = raw_d_safe
            prev_d_safe = raw_d_safe

        if not can_perceive or current_obs_pos is None:
            obstacles = []
        else:
            obs_traj = np.zeros((2, cfg.mpc_horizon))
            for k in range(cfg.mpc_horizon):
                obs_traj[0, k] = current_obs_pos[0] + current_obs_speed * k * cfg.dt
                obs_traj[1, k] = current_obs_pos[1]
            obstacles = [ObstacleTrajectory(s.neighbor_id, obs_traj, filtered_d_safe, tau)]

        u_opt, _, is_fb = nmpc.solve_diagnostic(ego_state, obstacles=obstacles, target_velocity=20.0)

        accel = float(u_opt[0])
        steer = float(u_opt[1])
        jerk = abs(accel - prev_accel) / cfg.dt
        prev_accel = accel

        dist = float(np.hypot(ego_state[0] - s.neighbor_position[0], ego_state[1] - s.neighbor_position[1]))

        accels.append(accel)
        jerks.append(jerk)
        dists.append(dist)
        fallbacks.append(is_fb)

        ego_state[0] += ego_state[3] * np.cos(ego_state[2]) * cfg.dt
        ego_state[1] += ego_state[3] * np.sin(ego_state[2]) * cfg.dt
        ego_state[2] += (ego_state[3] * np.tan(steer) / cfg.wheelbase_m) * cfg.dt
        ego_state[3] = max(0.0, ego_state[3] + accel * cfg.dt)

    peak_jerk = max(jerks[1:]) if len(jerks) > 1 else 0.0
    min_dist = min(dists)
    collision = 1 if min_dist < 2.5 else 0
    fb_count = sum(1 for f in fallbacks if f)

    return {
        "peak_jerk": peak_jerk,
        "min_dist": min_dist,
        "collision": collision,
        "fallback_count": fb_count,
        "total_steps": len(steps),
    }


def run_30_seed_evaluation(
    exp_name: str,
    scenario_type: str,
    model_name: str,
    cfg: Config,
    predictor: NextStepPredictor,
    num_seeds: int = 30,
    d_safe_slew_rate: Optional[float] = None,
    zero_sensor_noise: bool = False,
    ramp_duration_s: Optional[float] = None,
) -> Dict:
    results = {"peak_jerk": [], "min_dist": [], "collision": [], "fb_count": []}

    for idx in range(num_seeds):
        seed = 1000 + idx
        res = simulate_single_seed(
            scenario_type=scenario_type,
            model_name=model_name,
            seed=seed,
            cfg=cfg,
            predictor=predictor,
            d_safe_slew_rate=d_safe_slew_rate,
            zero_sensor_noise=zero_sensor_noise,
            ramp_duration_s=ramp_duration_s,
        )
        results["peak_jerk"].append(res["peak_jerk"])
        results["min_dist"].append(res["min_dist"])
        results["collision"].append(res["collision"])
        results["fb_count"].append(res["fallback_count"])

    j_mean, j_std = float(np.mean(results["peak_jerk"])), float(np.std(results["peak_jerk"]))
    d_mean, d_std = float(np.mean(results["min_dist"])), float(np.std(results["min_dist"]))
    coll_pct = (sum(results["collision"]) / num_seeds) * 100.0
    fb_total = sum(results["fb_count"])
    fb_pct = (fb_total / (num_seeds * 70)) * 100.0

    print(
        f"{exp_name:<46} | "
        f"{j_mean:5.2f} ± {j_std:4.2f} m/s³ | "
        f"{d_mean:5.2f} ± {d_std:4.2f} m | "
        f"{coll_pct:5.1f}% | "
        f"{fb_total:2d}회 ({fb_pct:4.2f}%)"
    )

    return {
        "exp_name": exp_name,
        "jerk_mean": j_mean,
        "jerk_std": j_std,
        "dist_mean": d_mean,
        "dist_std": d_std,
        "collision_rate": coll_pct,
        "fallback_total": fb_total,
        "fallback_pct": fb_pct,
    }


def main():
    cfg = Config()
    predictor = NextStepPredictor(cfg.feature_dim, cfg.predictor_hidden_dim)
    weights_path = Path(__file__).resolve().parent / "predictor_normal.pt"
    if weights_path.exists():
        predictor.load_state_dict(torch.load(weights_path, weights_only=True))
    predictor.eval()

    print("=" * 115)
    print("🔬 [문제 B 진단] 30-Seed 몬테카를로 가설 검증 결과 (평균 ± 표준편차)")
    print("=" * 115)
    header = f"{'Experiment':<46} | {'Peak Jerk':<18} | {'Min Distance':<16} | {'Collision':<10} | {'Fallback Rate'}"
    print(header)
    print("-" * 115)

    all_summaries = []

    # 1. Reckless Baseline & Proposed (Default)
    all_summaries.append(run_30_seed_evaluation("Reckless: Proposed (Default)", "reckless", "Proposed (Ours)", cfg, predictor))
    # 2. Reckless + Slew-Rate Limiter (2.0 m/s) -> 3번 트레이드오프 검증
    all_summaries.append(run_30_seed_evaluation("Reckless: Proposed + Slew-Rate (2.0 m/s)", "reckless", "Proposed (Ours)", cfg, predictor, d_safe_slew_rate=2.0))
    # 3. Reckless + Ramp 0.5s & 1.0s -> 4번 원인 공유 여부 검증
    all_summaries.append(run_30_seed_evaluation("Reckless: Proposed + Ramp 0.5s", "reckless", "Proposed (Ours)", cfg, predictor, ramp_duration_s=0.5))
    all_summaries.append(run_30_seed_evaluation("Reckless: Proposed + Ramp 1.0s", "reckless", "Proposed (Ours)", cfg, predictor, ramp_duration_s=1.0))
    # 4. Reckless: Fixed-Margin MPC
    all_summaries.append(run_30_seed_evaluation("Reckless: Fixed-Margin MPC (대조군)", "reckless", "Fixed-Margin MPC (V2V)", cfg, predictor))

    print("-" * 115)

    # 5. Fog Baseline & Proposed (Default)
    all_summaries.append(run_30_seed_evaluation("Fog: Proposed (Default)", "fog", "Proposed (Ours)", cfg, predictor))
    # 6. Fog + Zero Sensor Noise
    all_summaries.append(run_30_seed_evaluation("Fog: Proposed (Zero Sensor Noise)", "fog", "Proposed (Ours)", cfg, predictor, zero_sensor_noise=True))
    # 7. Fog + Slew-Rate Limiter (2.0 m/s)
    all_summaries.append(run_30_seed_evaluation("Fog: Proposed + Slew-Rate (2.0 m/s)", "fog", "Proposed (Ours)", cfg, predictor, d_safe_slew_rate=2.0))
    # 8. Fog: Fixed-Margin MPC
    all_summaries.append(run_30_seed_evaluation("Fog: Fixed-Margin MPC (대조군)", "fog", "Fixed-Margin MPC (V2V)", cfg, predictor))
    # 9. Fog: Sensor-Only MPC
    all_summaries.append(run_30_seed_evaluation("Fog: Sensor-Only MPC (대조군)", "fog", "Sensor-Only MPC", cfg, predictor))

    print("=" * 115)


if __name__ == "__main__":
    main()
