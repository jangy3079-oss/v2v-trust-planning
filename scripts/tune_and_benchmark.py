"""V2V 이상운전 탐지 SOTA 벤치마크 및 분리 튜닝 (Decoupled Tuning) & 몬테카를로 평가 스크립트.

구성:
Step 1: 탐지단 분리 튜닝 ([nu, lambda] 스윕) — FPR < 2% 제약 하에 탐지 조기성 최대화
Step 2: 제어단 분리 튜닝 (beta 스윕) — 최소 거리, TTC, Jerk 최적 밸런스 도출
Step 3: 몬테카를로 벤치마크 (30회 반복) — 4개 모델 x 4개 시나리오 정량 평가 (평균 +- 표준편차)

실행:
    python scripts/tune_and_benchmark.py
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import csv
import numpy as np
import torch

from v2v_trust_planning.anomaly import AnomalyDetector, NextStepPredictor
from v2v_trust_planning.baselines import ZhangClassifierBaseline, ThresholdExclusionBaseline
from v2v_trust_planning.config import Config
from v2v_trust_planning.datasets import SyntheticConfig, synthetic_drowsy_sequence, synthetic_normal_sequence, synthetic_novice_sequence, synthetic_reckless_sequence
from v2v_trust_planning.planning import ObstacleTrajectory, TrustAwareNMPC, dynamic_safety_margin
from v2v_trust_planning.scenarios import generate_scenario_data


def load_trained_predictor(cfg: Config) -> NextStepPredictor:
    predictor = NextStepPredictor(cfg.feature_dim, cfg.predictor_hidden_dim)
    weights_path = Path(__file__).resolve().parent / "predictor_normal.pt"
    if weights_path.exists():
        predictor.load_state_dict(torch.load(weights_path))
    return predictor


# =========================================================================
# Step 1: 탐지단 분리 튜닝 (Decoupled Detection Tuning)
# =========================================================================
def tune_detection_parameters(predictor: NextStepPredictor, cfg: Config):
    print("=" * 80)
    print("🚀 [Step 1] 탐지단 분리 튜닝 (Decoupled Detection Tuning)")
    print("목표: 정상 데이터 FPR < 2% 제약 하에, 졸음/미숙운전 탐지 조기성(Lead Time) 최대화")
    print("=" * 80)

    nu_candidates = [0.007, 0.010, 0.013, 0.016]
    lambda_candidates = [0.5, 1.0, 1.5, 2.0]

    scfg = SyntheticConfig(length=200)
    normal_seqs = [synthetic_normal_sequence(scfg, seed=s) for s in range(15)]
    drowsy_seqs = [synthetic_drowsy_sequence(scfg, seed=s) for s in range(15)]
    novice_seqs = [synthetic_novice_sequence(scfg, seed=s) for s in range(15)]

    best_score = -1e9
    best_params = (cfg.cusum_nu, cfg.trust_lambda)

    print(f"{'nu':>8} | {'lambda':>8} | {'Normal FPR (%)':>15} | {'Drowsy Lead(s)':>15} | {'Novice Lead(s)':>15} | {'Status':>10}")
    print("-" * 80)

    for nu in nu_candidates:
        for lam in lambda_candidates:
            # 1) 정상 데이터 FPR 평가 (정상인데 tau < 0.8로 떨어진 비율)
            fp_count = 0
            total_normal_steps = 0
            for seq in normal_seqs:
                det = AnomalyDetector(predictor, cfg.history_len, cfg.feature_dim, nu, lam)
                for t in range(len(seq)):
                    tau = det.step("norm", seq[t])
                    if t >= cfg.history_len:
                        total_normal_steps += 1
                        if tau < 0.8:
                            fp_count += 1
            fpr = (fp_count / max(1, total_normal_steps)) * 100.0

            # 2) 졸음운전 및 미숙운전에서 tau <= 0.6 도달 시점 (빠를수록 우수)
            drowsy_times = []
            for seq in drowsy_seqs:
                det = AnomalyDetector(predictor, cfg.history_len, cfg.feature_dim, nu, lam)
                det_time = 20.0
                for t in range(len(seq)):
                    tau = det.step("drowsy", seq[t])
                    if t >= cfg.history_len and tau <= 0.6:
                        det_time = t * cfg.dt
                        break
                drowsy_times.append(det_time)
            avg_drowsy_time = float(np.mean(drowsy_times))

            novice_times = []
            for seq in novice_seqs:
                det = AnomalyDetector(predictor, cfg.history_len, cfg.feature_dim, nu, lam)
                det_time = 20.0
                for t in range(len(seq)):
                    tau = det.step("novice", seq[t])
                    if t >= cfg.history_len and tau <= 0.6:
                        det_time = t * cfg.dt
                        break
                novice_times.append(det_time)
            avg_novice_time = float(np.mean(novice_times))

            # 제약 조건: FPR < 2.0%
            is_valid = (fpr <= 2.0)
            status = "FEASIBLE" if is_valid else "VIOLATED"

            # 점수: 20초에서 감지 시간을 뺀 여유 리드타임
            lead_score = (20.0 - avg_drowsy_time) + (20.0 - avg_novice_time)
            if is_valid and lead_score > best_score:
                best_score = lead_score
                best_params = (nu, lam)

            print(f"{nu:8.3f} | {lam:8.1f} | {fpr:14.2f}% | {avg_drowsy_time:14.2f}s | {avg_novice_time:14.2f}s | {status:>10}")

    print("-" * 80)
    print(f"✨ 최적 탐지 파라미터 확정: nu* = {best_params[0]:.3f}, lambda* = {best_params[1]:.1f}\n")
    return best_params[0], best_params[1]


# =========================================================================
# Step 2: 제어단 분리 튜닝 (Decoupled Control Tuning)
# =========================================================================
def tune_control_parameters(nu_star: float, lambda_star: float, predictor: NextStepPredictor):
    print("=" * 80)
    print("🚀 [Step 2] 제어단 분리 튜닝 (Decoupled Control Tuning)")
    print("목표: 확정된 [nu*, lambda*] 고정 하에, 절대 마진 beta_base* 및 동적 마진 beta_speed* 도출")
    print("=" * 80)

    beta_base_candidates = [2.0, 3.0, 4.0, 5.0]
    beta_speed_fixed = 1.0
    print(f"{'beta_base':>10} | {'Min Dist (m)':>15} | {'Min TTC (s)':>15} | {'Peak Jerk (m/s^3)':>20} | {'Status':>10}")
    print("-" * 80)

    best_beta_base = 3.0
    best_cost = 1e9

    for beta_base in beta_base_candidates:
        cfg = Config(
            mpc_horizon=10,
            dt=0.1,
            cusum_nu=nu_star,
            trust_lambda=lambda_star,
            beta_base=beta_base,
            beta_speed=beta_speed_fixed,
            weight_delta_control=2.5,
            weight_track_pos=0.8,
        )
        nmpc = TrustAwareNMPC(cfg, max_obstacles=1)
        det = AnomalyDetector(predictor, cfg.history_len, cfg.feature_dim, nu_star, lambda_star)

        _, steps = generate_scenario_data("drowsy", seed=100)
        ego_state = np.array([0.0, 0.0, 0.0, 20.0])

        distances = []
        ttcs = []
        accels = []

        for s in steps:
            tau = det.step(s.neighbor_id, s.neighbor_u)
            rel_speed = max(0.0, ego_state[3] - s.neighbor_speed)
            d_safe = dynamic_safety_margin(tau, rel_speed, cfg.d0_m, beta_base, beta_speed_fixed)

            obs_traj = np.zeros((2, cfg.mpc_horizon))
            for k in range(cfg.mpc_horizon):
                obs_traj[0, k] = s.neighbor_position[0] + s.neighbor_speed * k * cfg.dt
                obs_traj[1, k] = s.neighbor_position[1]

            obstacles = [ObstacleTrajectory(s.neighbor_id, obs_traj, d_safe, tau)]
            u_opt, _ = nmpc.solve(ego_state, obstacles=obstacles, target_velocity=20.0)

            accel, steer = float(u_opt[0]), float(u_opt[1])
            dist = float(np.hypot(ego_state[0] - s.neighbor_position[0], ego_state[1] - s.neighbor_position[1]))
            ttc = dist / max(0.1, rel_speed)

            distances.append(dist)
            ttcs.append(ttc)
            accels.append(accel)

            ego_state[0] += ego_state[3] * np.cos(ego_state[2]) * cfg.dt
            ego_state[1] += ego_state[3] * np.sin(ego_state[2]) * cfg.dt
            ego_state[2] += (ego_state[3] * np.tan(steer) / cfg.wheelbase_m) * cfg.dt
            ego_state[3] = max(0.0, ego_state[3] + accel * cfg.dt)

        min_dist = min(distances)
        min_ttc = min(ttcs)
        jerks = [abs(accels[i] - accels[i - 1]) / cfg.dt for i in range(1, len(accels))]
        peak_jerk = max(jerks) if jerks else 0.0

        # 패널티 함수: 안전거리 확보를 우대하고, 저크는 5.0 이하로 제어
        cost = -min_dist * 2.0 - min_ttc * 5.0 + peak_jerk * 0.2
        if min_ttc < 1.5:
            cost += 50.0

        status = "SELECTED" if cost < best_cost else "CANDIDATE"
        if cost < best_cost:
            best_cost = cost
            best_beta_base = beta_base

        print(f"{beta_base:10.1f} | {min_dist:14.2f}m | {min_ttc:14.2f}s | {peak_jerk:19.2f} | {status:>10}")

    print("-" * 80)
    print(f"✨ 최적 제어 파라미터 확정: beta_base* = {best_beta_base:.1f}, beta_speed* = {beta_speed_fixed:.1f}\n")
    return best_beta_base, beta_speed_fixed


# =========================================================================
# Step 3: 몬테카를로 벤치마크 (Monte Carlo Benchmark, 30회 반복)
# =========================================================================
def run_monte_carlo_benchmark(
    nu_star: float,
    lambda_star: float,
    beta_base_star: float,
    beta_speed_star: float,
    predictor: NextStepPredictor,
    num_runs: int = 30,
):
    print("=" * 80)
    print(f"🚀 [Step 3] 몬테카를로 벤치마크 시뮬레이션 ({num_runs}회 반복, 5대 대조군 정밀 평가)")
    print("비교 모델:")
    print("  1. Sensor-Only MPC           : V2V 통신 없음 (온보드 센서 가시거리만 의존)")
    print("  2. Fixed-Margin MPC (V2V)    : V2V 위치 100% 공유, 고정 마진 (tau=1.0 고정)")
    print("  3. CARMEN+ (US DOT 2025)     : 순간오차 초과 시 V2V 통신 영구 배제 (Exclusion)")
    print("  4. Zhang et al. (TVT 2019)   : Closed-set 지도학습 분류기 (Reckless 전용)")
    print("  5. Proposed (Ours)           : V2V 신뢰가중 NMPC (최적 nu*, lambda*, beta* 탑재)")
    print("=" * 80)

    cfg = Config(
        mpc_horizon=10,
        dt=0.1,
        cusum_nu=nu_star,
        trust_lambda=lambda_star,
        beta_base=beta_base_star,
        beta_speed=beta_speed_star,
        weight_delta_control=2.5,
        weight_track_pos=0.8,
    )
    nmpc = TrustAwareNMPC(cfg, max_obstacles=1)

    scenarios = ["reckless", "drowsy", "novice", "fog"]
    models = [
        "Sensor-Only MPC",
        "Fixed-Margin MPC (V2V)",
        "CARMEN+ (2025)",
        "Zhang et al. (2019)",
        "Proposed (Ours)",
    ]

    metrics_summary = {sc: {m: {"min_dist": [], "min_ttc": [], "ttc_viol": [], "peak_jerk": [], "collision": []} for m in models} for sc in scenarios}

    for sc in scenarios:
        print(f"\n  ▶ 시나리오 진행 중: [{sc}] (Monte Carlo {num_runs}회 실행, 7.0초 워밍업 포함)...")
        for run_idx in range(num_runs):
            if (run_idx + 1) % 10 == 0 or run_idx == 0:
                print(f"     - Run {run_idx + 1}/{num_runs} 완료...", flush=True)
            seed = 1000 + run_idx
            sc_cfg, steps = generate_scenario_data(sc, seed=seed)

            for model_name in models:
                ego_state = np.array([0.0, 0.0, 0.0, 20.0])
                feature_hist = []
                zhang = ZhangClassifierBaseline()
                carmen = ThresholdExclusionBaseline(error_threshold=0.02)
                det = AnomalyDetector(predictor, cfg.history_len, cfg.feature_dim, nu_star, lambda_star)

                last_known_pos: Optional[np.ndarray] = None
                last_known_speed: float = 20.0
                last_known_u: np.ndarray = np.zeros(2)
                comm_alive: bool = False

                distances, ttcs, accels = [], [], []

                for s in steps:
                    has_sensor_sight = (s.sensor_estimate is not None)
                    v2v_available = s.v2v_packet_received

                    # CARMEN+ 통신 배제 여부
                    is_excluded = (model_name == "CARMEN+ (2025)" and carmen.excluded.get(s.neighbor_id, False))

                    # 1. 인지 및 추적 (모든 V2V 모델에 동일한 Dead Reckoning 공정 적용)
                    if model_name == "Sensor-Only MPC":
                        can_perceive_obstacle = has_sensor_sight
                        if has_sensor_sight:
                            current_obs_pos = s.neighbor_position.copy()
                            current_obs_speed = s.neighbor_speed
                            current_obs_u = s.sensor_estimate
                        else:
                            current_obs_pos = None
                            current_obs_speed = 20.0
                            current_obs_u = np.zeros(2)
                    else:
                        # V2V 탑재 모델들
                        if has_sensor_sight:
                            current_obs_pos = s.neighbor_position.copy()
                            current_obs_speed = s.neighbor_speed
                            current_obs_u = s.sensor_estimate
                            last_known_pos = current_obs_pos.copy()
                            last_known_speed = current_obs_speed
                            last_known_u = current_obs_u.copy()
                            can_perceive_obstacle = True
                        elif v2v_available and not is_excluded:
                            current_obs_pos = s.neighbor_position.copy()
                            current_obs_speed = s.neighbor_speed
                            current_obs_u = s.neighbor_u
                            last_known_pos = current_obs_pos.copy()
                            last_known_speed = current_obs_speed
                            last_known_u = current_obs_u.copy()
                            comm_alive = True
                            can_perceive_obstacle = True
                        elif comm_alive and not is_excluded and last_known_pos is not None:
                            # 패킷 유실 시 공통 Dead Reckoning 외삽
                            last_known_pos[0] += last_known_speed * cfg.dt
                            current_obs_pos = last_known_pos.copy()
                            current_obs_speed = last_known_speed
                            current_obs_u = last_known_u.copy()
                            can_perceive_obstacle = True
                        else:
                            current_obs_pos = None
                            current_obs_speed = 20.0
                            current_obs_u = np.zeros(2)
                            can_perceive_obstacle = False

                    # 2. 특징 입력 및 신뢰도/이상 계산
                    if current_obs_pos is not None:
                        u_obs = current_obs_u
                        feature_hist.append(u_obs)
                        if len(feature_hist) > cfg.history_len:
                            feature_hist.pop(0)

                        if model_name == "Zhang et al. (2019)":
                            tau = zhang.predict_trust(np.array(feature_hist))
                        elif model_name == "CARMEN+ (2025)":
                            if len(feature_hist) >= cfg.history_len:
                                u_hat, _ = predictor.predict(np.array(feature_hist))
                                err = float(np.sum((u_obs - u_hat) ** 2))
                            else:
                                err = 0.0
                            tau = carmen.step(s.neighbor_id, err)
                        elif model_name in ["Sensor-Only MPC", "Fixed-Margin MPC (V2V)"]:
                            tau = 1.0  # 고정 마진 대조군
                        else:  # Proposed (Ours)
                            tau = det.step(s.neighbor_id, u_obs)
                    else:
                        tau = 1.0

                    # 3. 안전거리 산출 및 NMPC 제어
                    rel_speed = max(0.0, ego_state[3] - current_obs_speed)

                    if not can_perceive_obstacle or current_obs_pos is None:
                        obstacles = []
                    else:
                        if model_name in ["Sensor-Only MPC", "Fixed-Margin MPC (V2V)", "CARMEN+ (2025)"]:
                            d_safe = cfg.d0_m
                        elif model_name == "Zhang et al. (2019)":
                            d_safe = cfg.d0_m + ((cfg.beta_base + cfg.beta_speed * rel_speed) if tau == 0.0 else 0.0)
                        else:  # Proposed (Ours)
                            d_safe = dynamic_safety_margin(tau, rel_speed, cfg.d0_m, beta_base_star, beta_speed_star)

                        obs_traj = np.zeros((2, cfg.mpc_horizon))
                        for k in range(cfg.mpc_horizon):
                            obs_traj[0, k] = current_obs_pos[0] + current_obs_speed * k * cfg.dt
                            obs_traj[1, k] = current_obs_pos[1]
                        obstacles = [ObstacleTrajectory(s.neighbor_id, obs_traj, d_safe, tau)]

                    u_opt, _ = nmpc.solve(ego_state, obstacles=obstacles, target_velocity=20.0)

                    accel, steer = float(u_opt[0]), float(u_opt[1])
                    dist = float(np.hypot(ego_state[0] - s.neighbor_position[0], ego_state[1] - s.neighbor_position[1]))
                    ttc = dist / max(0.1, rel_speed)

                    distances.append(dist)
                    ttcs.append(ttc)
                    accels.append(accel)

                    ego_state[0] += ego_state[3] * np.cos(ego_state[2]) * cfg.dt
                    ego_state[1] += ego_state[3] * np.sin(ego_state[2]) * cfg.dt
                    ego_state[2] += (ego_state[3] * np.tan(steer) / cfg.wheelbase_m) * cfg.dt
                    ego_state[3] = max(0.0, ego_state[3] + accel * cfg.dt)

                min_dist = min(distances)
                min_ttc = min(ttcs)
                ttc_violations = sum(1 for t in ttcs if t < 1.5)
                jerks = [abs(accels[i] - accels[i - 1]) / cfg.dt for i in range(1, len(accels))]
                peak_jerk = max(jerks) if jerks else 0.0
                collision = 1 if min_dist < 2.5 else 0  # 차량 중심 간 거리 2.5m 미만: 물리적 충돌

                m_dict = metrics_summary[sc][model_name]
                m_dict["min_dist"].append(min_dist)
                m_dict["min_ttc"].append(min_ttc)
                m_dict["ttc_viol"].append(ttc_violations)
                m_dict["peak_jerk"].append(peak_jerk)
                m_dict["collision"].append(collision)

    # =========================================================================
    # 최종 결과 집계 및 출력 (Table 1: 논문 포맷)
    # =========================================================================
    print("=" * 110)
    print("📋 [Table 1] 몬테카를로 벤치마크 최종 정량 평가 결과 (평균 ± 표준편차, 30회 반복)")
    print("=" * 110)
    header = f"{'Scenario':<12} | {'Model':<22} | {'Min Dist (m)':<17} | {'Min TTC (s)':<15} | {'TTC Viol (<1.5s)':<18} | {'Peak Jerk (m/s^3)':<18} | {'Collision (%)':<12}"
    print(header)
    print("-" * 110)

    csv_rows = [["Scenario", "Model", "Min_Dist_Mean", "Min_Dist_Std", "Min_TTC_Mean", "Min_TTC_Std", "TTC_Viol_Mean", "Peak_Jerk_Mean", "Collision_Rate_Pct"]]

    for sc in scenarios:
        for model_name in models:
            m = metrics_summary[sc][model_name]
            dist_mean, dist_std = np.mean(m["min_dist"]), np.std(m["min_dist"])
            ttc_mean, ttc_std = np.mean(m["min_ttc"]), np.std(m["min_ttc"])
            viol_mean = np.mean(m["ttc_viol"])
            jerk_mean, jerk_std = np.mean(m["peak_jerk"]), np.std(m["peak_jerk"])
            coll_pct = (sum(m["collision"]) / len(m["collision"])) * 100.0

            dist_str = f"{dist_mean:.2f} ± {dist_std:.2f}"
            ttc_str = f"{ttc_mean:.2f} ± {ttc_std:.2f}"
            viol_str = f"{viol_mean:.1f} steps"
            jerk_str = f"{jerk_mean:.2f} ± {jerk_std:.2f}"
            coll_str = f"{coll_pct:.1f}%"

            print(f"{sc:<12} | {model_name:<22} | {dist_str:<17} | {ttc_str:<15} | {viol_str:<18} | {jerk_str:<18} | {coll_str:<12}")

            csv_rows.append([sc, model_name, dist_mean, dist_std, ttc_mean, ttc_std, viol_mean, jerk_mean, coll_pct])
        print("-" * 110)

    csv_path = Path(__file__).resolve().parent / "benchmark_results.csv"
    with open(csv_path, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerows(csv_rows)
    print(f"\n📁 결과 파일 저장 완료: {csv_path}")


def main():
    cfg = Config()
    predictor = load_trained_predictor(cfg)

    # 1. 탐지단 분리 튜닝
    nu_star, lambda_star = tune_detection_parameters(predictor, cfg)

    # 2. 제어단 분리 튜닝
    beta_base_star, beta_speed_star = tune_control_parameters(nu_star, lambda_star, predictor)

    # 3. 몬테카를로 벤치마크 실행 (30회 반복)
    run_monte_carlo_benchmark(nu_star, lambda_star, beta_base_star, beta_speed_star, predictor, num_runs=30)


if __name__ == "__main__":
    main()
