"""문제 A 진단 스크립트 (요청사항 1, 2 반영).

반영 사항:
1. ramp_duration별 30 seed 몬테카를로 반복 실행 및 평균 ± 표준편차 측정.
2. tau 트립 기준 민감도 분석: tau in [0.5, 0.6, 0.7]에 대한 리드타임 동시 측정.
3. 결과 수치 객관적 전달.

실행:
    python scripts/diagnose_cusum_onset.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from typing import Dict, List, Optional, Tuple
import matplotlib.pyplot as plt
import numpy as np
import torch

from v2v_trust_planning.anomaly import AnomalyDetector, NextStepPredictor
from v2v_trust_planning.baselines import ThresholdExclusionBaseline
from v2v_trust_planning.config import Config
from v2v_trust_planning.scenarios import ScenarioConfig, ScenarioStep


def load_or_verify_predictor(cfg: Config) -> NextStepPredictor:
    predictor = NextStepPredictor(cfg.feature_dim, cfg.predictor_hidden_dim)
    weights_path = Path(__file__).resolve().parent / "predictor_normal.pt"
    if weights_path.exists():
        predictor.load_state_dict(torch.load(weights_path, weights_only=True))
    predictor.eval()
    return predictor


def generate_ramp_drowsy_scenario(
    ramp_duration_s: float,
    seed: int = 42,
    warmup_s: float = 3.0,
    duration_s: float = 4.0,
    dt: float = 0.1,
) -> Tuple[ScenarioConfig, List[ScenarioStep]]:
    rng = np.random.RandomState(seed)
    cfg = ScenarioConfig(
        name=f"drowsy_ramp_{ramp_duration_s:.1f}s",
        warmup_s=warmup_s,
        duration_s=duration_s,
        dt=dt,
        visibility_m=150.0,
        packet_loss_rate=0.0,
    )

    total_steps = cfg.total_steps
    warmup_steps = int(cfg.warmup_s / cfg.dt)
    cut_in_time = cfg.warmup_s + 0.6
    cut_in_step = int(cut_in_time / cfg.dt)

    obs_init_dist = 18.0 + rng.uniform(-0.5, 0.5)
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
                    # 부드러운 전이를 위한 3차 스무스스텝 램프
                    ratio = min(1.0, t_anom / ramp_duration_s)
                    ramp_scale = 3.0 * (ratio ** 2) - 2.0 * (ratio ** 3)

                target_accel = -3.0 + 0.5 * np.sin(2.0 * t_anom)
                target_yaw_rate = -0.15

                accel = ramp_scale * target_accel + rng.normal(0, 0.03)
                yaw_rate = ramp_scale * target_yaw_rate + rng.normal(0, 0.01)
                curr_y = max(0.0, curr_y - 0.35 * ramp_scale)

        curr_v = max(1.0, curr_v + accel * cfg.dt)
        curr_x += curr_v * cfg.dt
        u_true = np.array([accel, yaw_rate])
        sensor_noise = rng.normal(0, 0.03, size=2)
        sensor_est = u_true + sensor_noise

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


def generate_subthreshold_drowsy_scenario(
    seed: int = 42,
    warmup_s: float = 3.0,
    duration_s: float = 9.5,
    dt: float = 0.1,
) -> Tuple[ScenarioConfig, List[ScenarioStep]]:
    rng = np.random.RandomState(seed)
    cfg = ScenarioConfig(
        name="drowsy_subthreshold",
        warmup_s=warmup_s,
        duration_s=duration_s,
        dt=dt,
        visibility_m=150.0,
        packet_loss_rate=0.0,
    )

    total_steps = cfg.total_steps
    warmup_steps = int(cfg.warmup_s / cfg.dt)
    cut_in_time = cfg.warmup_s + 0.6
    cut_in_step = int(cut_in_time / cfg.dt)

    obs_init_dist = 18.0
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
                accel = rng.normal(0, 0.02)
                yaw_rate = rng.normal(0, 0.005)
                curr_y = 3.5 + rng.normal(0, 0.02)
            else:
                t_anom = t - cut_in_time
                # 목표 오차 e_t in [0.017, 0.019] (nu=0.011 초과, threshold=0.020 미만)
                # 주기 0.6s(6스텝) 미세 조향 수정(micro-weaving) 진동
                phase = ((step - cut_in_step) % 6) / 6.0
                wave = 1.0 if phase < 0.5 else -1.0
                yaw_rate = 0.124 * wave + rng.normal(0, 0.001)
                accel = rng.normal(0, 0.005)
                curr_y = max(0.5, curr_y - 0.05 * (t_anom / 9.5))

        curr_v = max(1.0, curr_v + accel * cfg.dt)
        curr_x += curr_v * cfg.dt
        u_true = np.array([accel, yaw_rate])
        sensor_noise = rng.normal(0, 0.02, size=2)
        sensor_est = u_true + sensor_noise

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


def run_experiment_a1_multiseed_sensitivity(
    predictor: NextStepPredictor,
    cfg: Config,
    ramp_list: List[float],
    tau_thresholds: List[float] = [0.5, 0.6, 0.7],
    num_seeds: int = 30,
    inst_threshold: float = 0.020,
) -> Dict:
    cut_in_reference = 3.6
    max_delay = 3.4  # 시나리오 지속 시간 한계

    print("=" * 115)
    print(f"🔬 [실험 A-1] Ramp 길이별 30-Seed 몬테카를로 평가 및 τ 트립 민감도 분석 (τ ∈ {tau_thresholds})")
    print(f"   (순간임계값 기준: e_t > {inst_threshold})")
    print("=" * 115)

    header = f"{'Ramp':<8} | {'Threshold t_trip (s)':<22} | " + " | ".join([f"τ<={tau} Lead Δt (s)" for tau in tau_thresholds])
    print(header)
    print("-" * 115)

    results_by_tau = {tau: {"lead_mean": [], "lead_std": [], "cusum_mean": [], "cusum_std": []} for tau in tau_thresholds}
    results_inst = {"mean": [], "std": []}

    for ramp_s in ramp_list:
        inst_times = []
        cusum_times_dict = {tau: [] for tau in tau_thresholds}
        lead_times_dict = {tau: [] for tau in tau_thresholds}

        for seed_idx in range(num_seeds):
            seed = 2000 + seed_idx
            _, steps = generate_ramp_drowsy_scenario(ramp_duration_s=ramp_s, seed=seed)

            det = AnomalyDetector(predictor, cfg.history_len, cfg.feature_dim, cfg.cusum_nu, cfg.trust_lambda)
            carmen = ThresholdExclusionBaseline(error_threshold=inst_threshold)

            t_inst: Optional[float] = None
            t_cusum: Dict[float, Optional[float]] = {tau: None for tau in tau_thresholds}
            feature_hist = []

            for s in steps:
                t = s.t
                u_obs = s.neighbor_u
                feature_hist.append(u_obs)
                if len(feature_hist) > cfg.history_len:
                    feature_hist.pop(0)

                tau_val = det.step(s.neighbor_id, u_obs)

                if s.is_anomaly:
                    for tau_th in tau_thresholds:
                        if t_cusum[tau_th] is None and tau_val <= tau_th:
                            t_cusum[tau_th] = t - cut_in_reference

                if len(feature_hist) >= cfg.history_len:
                    u_hat, _ = predictor.predict(np.array(feature_hist))
                    err = float(np.sum((u_obs - u_hat) ** 2))
                else:
                    err = 0.0

                c_tau = carmen.step(s.neighbor_id, err)
                if s.is_anomaly and t_inst is None and c_tau == 0.0:
                    t_inst = t - cut_in_reference

            t_i = t_inst if t_inst is not None else max_delay
            inst_times.append(t_i)

            for tau_th in tau_thresholds:
                t_c = t_cusum[tau_th] if t_cusum[tau_th] is not None else max_delay
                cusum_times_dict[tau_th].append(t_c)
                lead_times_dict[tau_th].append(t_i - t_c)

        i_m, i_s = float(np.mean(inst_times)), float(np.std(inst_times))
        results_inst["mean"].append(i_m)
        results_inst["std"].append(i_s)

        row_str = f"{ramp_s:5.1f}s   | {i_m:6.2f} ± {i_s:4.2f} s        | "
        lead_strs = []
        for tau_th in tau_thresholds:
            c_m, c_s = float(np.mean(cusum_times_dict[tau_th])), float(np.std(cusum_times_dict[tau_th]))
            l_m, l_s = float(np.mean(lead_times_dict[tau_th])), float(np.std(lead_times_dict[tau_th]))
            results_by_tau[tau_th]["cusum_mean"].append(c_m)
            results_by_tau[tau_th]["cusum_std"].append(c_s)
            results_by_tau[tau_th]["lead_mean"].append(l_m)
            results_by_tau[tau_th]["lead_std"].append(l_s)
            lead_strs.append(f"{l_m:6.2f} ± {l_s:4.2f} s")

        row_str += " | ".join(lead_strs)
        print(row_str)

    print("-" * 115)
    return {"ramps": ramp_list, "inst": results_inst, "by_tau": results_by_tau}


def run_experiment_a2_multiseed_subthreshold(
    predictor: NextStepPredictor,
    cfg: Config,
    num_seeds: int = 30,
    inst_threshold: float = 0.020,
    tau_thresholds: List[float] = [0.5, 0.6, 0.7],
) -> Dict:
    print("\n" + "=" * 115)
    print(f"🔬 [실험 A-2] 순간임계값 미달(Sub-threshold) 장기 미세 이상 30-Seed 몬테카를로 결과")
    print("=" * 115)

    inst_detect_count = 0
    cusum_detect_counts = {tau: 0 for tau in tau_thresholds}
    max_errors = []
    min_taus = []

    for seed_idx in range(num_seeds):
        seed = 3000 + seed_idx
        _, steps = generate_subthreshold_drowsy_scenario(seed=seed)

        det = AnomalyDetector(predictor, cfg.history_len, cfg.feature_dim, cfg.cusum_nu, cfg.trust_lambda)
        carmen = ThresholdExclusionBaseline(error_threshold=inst_threshold)

        feature_hist = []
        seed_errors = []
        seed_taus = []
        c_tripped = {tau: False for tau in tau_thresholds}
        i_tripped = False

        for s in steps:
            u_obs = s.neighbor_u
            feature_hist.append(u_obs)
            if len(feature_hist) > cfg.history_len:
                feature_hist.pop(0)

            tau_val = det.step(s.neighbor_id, u_obs)
            seed_taus.append(tau_val)

            if len(feature_hist) >= cfg.history_len:
                u_hat, _ = predictor.predict(np.array(feature_hist))
                err = float(np.sum((u_obs - u_hat) ** 2))
            else:
                err = 0.0
            seed_errors.append(err)

            c_tau = carmen.step(s.neighbor_id, err)

            if s.is_anomaly:
                if c_tau == 0.0:
                    i_tripped = True
                for tau_th in tau_thresholds:
                    if tau_val <= tau_th:
                        c_tripped[tau_th] = True

        if i_tripped:
            inst_detect_count += 1
        for tau_th in tau_thresholds:
            if c_tripped[tau_th]:
                cusum_detect_counts[tau_th] += 1

        max_errors.append(max(seed_errors[36:]))
        min_taus.append(min(seed_taus[36:]))
        print(f"  [Seed {seed}] max(e_t) = {max_errors[-1]:.5f} | min(tau) = {min_taus[-1]:.3f} | Inst Trip: {i_tripped} | CUSUM Trip (tau<=0.7): {c_tripped[0.7]}")

    inst_rate = (inst_detect_count / num_seeds) * 100.0
    cusum_rates = {tau: (cusum_detect_counts[tau] / num_seeds) * 100.0 for tau in tau_thresholds}

    print(f"  • 이상 구간 평균 최대 순간오차 (max e_t) : {np.mean(max_errors):.5f} ± {np.std(max_errors):.5f} (임계값 {inst_threshold} 미만)")
    print(f"  • 이상 구간 최저 신뢰도 (min tau)        : {np.mean(min_taus):.3f} ± {np.std(min_taus):.3f}")
    print(f"  • 순간임계값 탐지율 (Threshold Detection) : {inst_rate:5.1f}% ({inst_detect_count}/{num_seeds})")
    for tau_th in tau_thresholds:
        print(f"  • CUSUM 탐지율 (τ <= {tau_th:3.1f})         : {cusum_rates[tau_th]:5.1f}% ({cusum_detect_counts[tau_th]}/{num_seeds})")
    print("=" * 115)

    return {
        "max_err_mean": float(np.mean(max_errors)),
        "max_err_std": float(np.std(max_errors)),
        "min_tau_mean": float(np.mean(min_taus)),
        "min_tau_std": float(np.std(min_taus)),
        "inst_rate": inst_rate,
        "cusum_rates": cusum_rates,
    }


def plot_results(a1_data: Dict, save_path: Path):
    ramps = a1_data["ramps"]
    by_tau = a1_data["by_tau"]

    fig, ax = plt.subplots(figsize=(8, 5))
    colors = {0.5: "#2ca02c", 0.6: "#1f77b4", 0.7: "#ff7f0e"}

    for tau_th, d in by_tau.items():
        ax.errorbar(
            ramps, d["lead_mean"], yerr=d["lead_std"],
            fmt="-o", capsize=4, lw=1.8, color=colors[tau_th],
            label=f"Lead-Time Δt (τ <= {tau_th})"
        )

    ax.axhline(0.0, color="gray", linestyle="--", alpha=0.6)
    ax.set_title("Problem A: Lead-Time Sensitivity across Ramp Durations (30 Seeds)", fontsize=12, fontweight="bold")
    ax.set_xlabel("Ramp Duration (s)", fontsize=11)
    ax.set_ylabel("Lead-Time Δt = t_inst - t_cusum (s)", fontsize=11)
    ax.grid(True, linestyle=":", alpha=0.6)
    ax.legend(fontsize=10)

    plt.tight_layout()
    plt.savefig(save_path, dpi=200)
    plt.close()


def main():
    cfg = Config()
    predictor = load_or_verify_predictor(cfg)

    ramps = [0.0, 0.5, 1.0, 1.5, 2.0, 2.5, 3.0]
    tau_thresholds = [0.5, 0.6, 0.7]

    a1_data = run_experiment_a1_multiseed_sensitivity(predictor, cfg, ramps, tau_thresholds=tau_thresholds, num_seeds=30)
    a2_data = run_experiment_a2_multiseed_subthreshold(predictor, cfg, num_seeds=30, tau_thresholds=[0.5, 0.6, 0.7, 0.8, 0.9])

    plot_path = Path(__file__).resolve().parent / "diagnose_cusum_onset.png"
    plot_results(a1_data, plot_path)


if __name__ == "__main__":
    main()
