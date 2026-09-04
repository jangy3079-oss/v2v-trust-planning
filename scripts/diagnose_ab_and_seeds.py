"""3대 추가 요청사항 정밀 측정 스크립트:

(1) 실험 A-2: 순간임계값 미달(Sub-threshold) 장기 미세 이상에서 τ 민감도 (τ in [0.5, 0.6, 0.7, 0.8, 0.9]) 및 CUSUM 통계량 S_t 실측 (30 Seeds)
(2) 슬랙 가중치 리밸런싱 정식 A/B 30-Seed 검증 (Reckless, Fog, Drowsy, Novice 4대 시나리오 전체)
    - Default (w_slack=10000, w_delta=2.5) vs Balanced (w_slack=1000, w_delta=10)
    - Peak Jerk, Min Distance, Collision Rate, Fallback Rate
(3) 저크 고분산(High Variance) 원인의 Seed별 전수 분해 (Seed 1000~1029: 30개 시드별 정량 데이터)
    - Peak Jerk, a_min, dist_min, max |ego_y| (차선변경 여부), 장애물 초기 거리

실행:
    python scripts/diagnose_ab_and_seeds.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from typing import Dict, List, Optional, Tuple
import numpy as np
import torch

from v2v_trust_planning.anomaly import AnomalyDetector, NextStepPredictor
from v2v_trust_planning.baselines import ThresholdExclusionBaseline
from v2v_trust_planning.config import Config
from v2v_trust_planning.planning import ObstacleTrajectory, TrustAwareNMPC, dynamic_safety_margin
from v2v_trust_planning.scenarios import generate_scenario_data


class DiagnosticNMPC(TrustAwareNMPC):
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
            fallback_a = float(np.clip(-3.0, self.cfg.min_accel_mps2, self.cfg.max_accel_mps2))
            fallback_u = np.array([fallback_a, 0.0])
            self.last_u = fallback_u.copy()
            fallback_x = np.repeat(ego_state.reshape(4, 1), self.N + 1, axis=1)
            return fallback_u, fallback_x, True


# =========================================================================
# (1) 실험 A-2 민감도 재확인
# =========================================================================
def run_task1_subthreshold_sensitivity(predictor: NextStepPredictor, cfg: Config, num_seeds: int = 30):
    print("=" * 115)
    print("🔬 [(1) 실험 A-2 재확인] 순간임계값 미달(Sub-threshold) 장기 미세 이상 τ 민감도 (30 Seeds)")
    print("   설계: e_t in [0.012, 0.018] (임계값 0.020 미만 100% 준수, nu=0.010 초과), 지속시간 3.5초")
    print("=" * 115)

    tau_thresholds = [0.5, 0.6, 0.7, 0.8, 0.9]
    inst_detected = 0
    cusum_detected = {th: 0 for th in tau_thresholds}
    max_errors = []
    min_taus = []
    final_S_vals = []

    for seed in range(num_seeds):
        det = AnomalyDetector(predictor, cfg.history_len, cfg.feature_dim, cfg.cusum_nu, cfg.trust_lambda)
        carmen = ThresholdExclusionBaseline(error_threshold=0.020)
        rng = np.random.default_rng(3000 + seed)

        # 30스텝 정상 워밍업
        for _ in range(30):
            det.step("n1", np.array([rng.normal(0, 0.02), rng.normal(0, 0.005)]))

        seed_errs = []
        seed_taus = []
        inst_trip = False

        for k in range(35):
            pred_u = det._pending_pred.get("n1", np.zeros(2))
            # e_t가 0.013~0.017에 머물도록 설계
            target_err = float(np.clip(0.0145 + rng.normal(0, 0.001), 0.012, 0.0175))
            angle = 0.25 * k + rng.normal(0, 0.05)
            scale = np.sqrt(target_err / (1.0 + cfg.recon_loss_weight))
            delta = np.array([np.cos(angle), np.sin(angle)]) * scale
            u = pred_u + delta

            actual_err = float(np.sum((u - pred_u) ** 2))
            tau = det.step("n1", u)
            c_tau = carmen.step("n1", actual_err)

            if actual_err > 0.020:
                inst_trip = True

            seed_errs.append(actual_err)
            seed_taus.append(tau)

        max_errors.append(max(seed_errs))
        min_taus.append(min(seed_taus))
        final_S_vals.append(det.cusum._S["n1"])

        if inst_trip:
            inst_detected += 1
        for th in tau_thresholds:
            if min(seed_taus) <= th:
                cusum_detected[th] += 1

    print(f"  • 순간오차 e_t 실측치 (평균 최대값): {np.mean(max_errors):.5f} ± {np.std(max_errors):.5f} (전체 시드 max <= 0.020 준수)")
    print(f"  • CUSUM 통계량 S_t 최종치 (평균):  {np.mean(final_S_vals):.4f} ± {np.std(final_S_vals):.4f}")
    print(f"  • 최저 신뢰도 min τ 실측치 (평균):  {np.mean(min_taus):.4f} ± {np.std(min_taus):.4f}")
    print(f"  • 순간임계값 탐지율 (e_t > 0.020): {inst_detected / num_seeds * 100.0:.1f}% ({inst_detected}/{num_seeds})")
    for th in tau_thresholds:
        print(f"  • CUSUM 탐지율 (τ <= {th:.1f}):         {cusum_detected[th] / num_seeds * 100.0:.1f}% ({cusum_detected[th]}/{num_seeds})")
    print("-" * 115)


# =========================================================================
# (2) 슬랙 가중치 리밸런싱 정식 A/B 30-Seed 검증
# =========================================================================
def simulate_single_seed_ab(
    scenario_type: str,
    seed: int,
    w_slack: float,
    w_delta: float,
    predictor: NextStepPredictor,
) -> Dict:
    cfg = Config(
        mpc_horizon=10,
        dt=0.1,
        cusum_nu=0.010,
        trust_lambda=1.0,
        beta_base=3.0,
        beta_speed=1.0,
        weight_delta_control=w_delta,
        weight_slack=w_slack,
        weight_track_pos=0.8,
    )

    nmpc = DiagnosticNMPC(cfg, max_obstacles=1)
    det = AnomalyDetector(predictor, cfg.history_len, cfg.feature_dim, cfg.cusum_nu, cfg.trust_lambda)

    sc_cfg, steps = generate_scenario_data(scenario_type, seed=seed)
    ego_state = np.array([0.0, 0.0, 0.0, 20.0])
    feature_hist = []

    last_known_pos: Optional[np.ndarray] = None
    last_known_speed: float = 20.0
    last_known_u: np.ndarray = np.zeros(2)
    comm_alive: bool = False
    prev_accel: float = 0.0

    accels, jerks, dists, fallbacks = [], [], [], []
    y_positions = []

    for s in steps:
        has_sensor_sight = (s.sensor_estimate is not None)
        v2v_available = s.v2v_packet_received

        if has_sensor_sight:
            current_obs_pos = s.neighbor_position.copy()
            current_obs_speed = s.neighbor_speed
            current_obs_u = s.sensor_estimate
            last_known_pos = current_obs_pos.copy()
            last_known_speed = current_obs_speed
            last_known_u = current_obs_u.copy()
            can_perceive = True
        elif v2v_available:
            current_obs_pos = s.neighbor_position.copy()
            current_obs_speed = s.neighbor_speed
            current_obs_u = s.neighbor_u
            last_known_pos = current_obs_pos.copy()
            last_known_speed = current_obs_speed
            last_known_u = current_obs_u.copy()
            comm_alive = True
            can_perceive = True
        elif comm_alive and last_known_pos is not None:
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
            tau = det.step(s.neighbor_id, current_obs_u)
        else:
            tau = 1.0

        rel_speed = max(0.0, ego_state[3] - current_obs_speed)
        d_safe = dynamic_safety_margin(tau, rel_speed, cfg.d0_m, cfg.beta_base, cfg.beta_speed)

        if not can_perceive or current_obs_pos is None:
            obstacles = []
        else:
            obs_traj = np.zeros((2, cfg.mpc_horizon))
            for k in range(cfg.mpc_horizon):
                obs_traj[0, k] = current_obs_pos[0] + current_obs_speed * k * cfg.dt
                obs_traj[1, k] = current_obs_pos[1]
            obstacles = [ObstacleTrajectory(s.neighbor_id, obs_traj, d_safe, tau)]

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
        y_positions.append(ego_state[1])

        ego_state[0] += ego_state[3] * np.cos(ego_state[2]) * cfg.dt
        ego_state[1] += ego_state[3] * np.sin(ego_state[2]) * cfg.dt
        ego_state[2] += (ego_state[3] * np.tan(steer) / cfg.wheelbase_m) * cfg.dt
        ego_state[3] = max(0.0, ego_state[3] + accel * cfg.dt)

    peak_jerk = max(jerks[1:]) if len(jerks) > 1 else 0.0
    min_dist = min(dists)
    collision = 1 if min_dist < 2.5 else 0
    fb_count = sum(1 for f in fallbacks if f)
    min_accel = min(accels)
    max_abs_y = max(abs(y) for y in y_positions)

    return {
        "seed": seed,
        "peak_jerk": peak_jerk,
        "min_dist": min_dist,
        "collision": collision,
        "fb_count": fb_count,
        "min_accel": min_accel,
        "max_abs_y": max_abs_y,
    }


def run_task2_ab_comparison(predictor: NextStepPredictor, num_seeds: int = 30):
    print("\n" + "=" * 115)
    print("🔬 [(2) 슬랙 가중치 리밸런싱 정식 A/B 30-Seed 검증] Default vs Balanced (4대 시나리오 전체)")
    print("   Default : w_slack = 10,000.0, w_delta = 2.5  (기존 4,000 : 1)")
    print("   Balanced: w_slack =  1,000.0, w_delta = 10.0 (조정   100 : 1)")
    print("=" * 115)
    header = f"{'Scenario & Setting':<36} | {'Peak Jerk (m/s³)':<20} | {'Min Distance (m)':<18} | {'Collision':<10} | {'Fallback'}"
    print(header)
    print("-" * 115)

    scenarios = ["reckless", "fog", "drowsy", "novice"]
    ab_results = {}

    for sc in scenarios:
        for mode, w_s, w_d in [("Default", 10000.0, 2.5), ("Balanced", 1000.0, 10.0)]:
            label = f"{sc.capitalize()}: {mode}"
            runs = [simulate_single_seed_ab(sc, 1000 + i, w_s, w_d, predictor) for i in range(num_seeds)]

            j_vals = [r["peak_jerk"] for r in runs]
            d_vals = [r["min_dist"] for r in runs]
            coll_rate = sum(r["collision"] for r in runs) / num_seeds * 100.0
            fb_total = sum(r["fb_count"] for r in runs)
            fb_pct = fb_total / (num_seeds * 70) * 100.0

            j_str = f"{np.mean(j_vals):5.2f} ± {np.std(j_vals):4.2f}"
            d_str = f"{np.mean(d_vals):5.2f} ± {np.std(d_vals):4.2f}"
            c_str = f"{coll_rate:5.1f}%"
            fb_str = f"{fb_total}회 ({fb_pct:.2f}%)"

            print(f"{label:<36} | {j_str:<20} | {d_str:<18} | {c_str:<10} | {fb_str}")
            ab_results[label] = runs

        print("-" * 115)

    return ab_results


# =========================================================================
# (3) 저크 고분산 원인의 Seed별 전수 분해
# =========================================================================
def run_task3_seed_decomposition(ab_results: Dict):
    print("\n" + "=" * 115)
    print("🔬 [(3) 저크 고분산 원인의 Seed별 전수 분해] Reckless 및 Fog 시나리오 30개 시드별 정량 실측치")
    print("=" * 115)

    header = f"{'Seed':<6} | {'Reckless Default (Jerk / a_min / Max|Y| / MinDist)':<50} | {'Fog Default (Jerk / a_min / Max|Y| / MinDist)'}"
    print(header)
    print("-" * 115)

    reckless_runs = ab_results["Reckless: Default"]
    fog_runs = ab_results["Fog: Default"]

    for i in range(len(reckless_runs)):
        r = reckless_runs[i]
        f = fog_runs[i]
        seed_id = r["seed"]

        r_str = f"J={r['peak_jerk']:5.2f} | a={r['min_accel']:5.2f} | Y={r['max_abs_y']:4.2f}m | D={r['min_dist']:5.2f}m"
        f_str = f"J={f['peak_jerk']:5.2f} | a={f['min_accel']:5.2f} | Y={f['max_abs_y']:4.2f}m | D={f['min_dist']:5.2f}m"

        print(f"{seed_id:<6} | {r_str:<50} | {f_str}")

    print("=" * 115)


def main():
    cfg = Config()
    predictor = NextStepPredictor(cfg.feature_dim, cfg.predictor_hidden_dim)
    weights_path = Path(__file__).resolve().parent / "predictor_normal.pt"
    if weights_path.exists():
        predictor.load_state_dict(torch.load(weights_path, weights_only=True))
    predictor.eval()

    # 1. 실험 A-2 민감도 재확인
    run_task1_subthreshold_sensitivity(predictor, cfg, num_seeds=30)

    # 2. 슬랙 가중치 리밸런싱 A/B 30-Seed 검증
    ab_results = run_task2_ab_comparison(predictor, num_seeds=30)

    # 3. 저크 고분산 Seed별 분해
    run_task3_seed_decomposition(ab_results)


if __name__ == "__main__":
    main()
