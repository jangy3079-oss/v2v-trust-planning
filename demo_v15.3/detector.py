"""v15 탐지기 래퍼 (demo_v13/detector.py 복사 후 수정: e를 채널별로 내보냄 — e_long(a), e_lat(r_rel + d), 종잔차 r_a):
 이웃 차량 1대, 10 Hz 스텝 호출. 합성 환경, 직선 도로, 시뮬레이션.

재사용 (수정 없이 import):
  - GRU: scratch_v10 `train_v10.load_model`, `features_v10.e_score`(최근 K+1 표본 버퍼), θ = v10 `results/eval_nom.json`
  - R1: scratch_v11 `scores_v11.d_smooth`(3 s 인과 평균), 파라미터 = v11 `results/v11_sd0.23_sm*.json`의 S3 선택값
  - R2: scratch_v12 `rules_v12` 상수(SIG_N, SIG_B, SIG_D_NOM, TAU_GRID, HALF), 조합 = v12 `results/v12_*.json`
  - 의도 구간: scratch_v12 `gen_v12.intent`(최근 31 표본 버퍼)
`rules_v12.r1_alarm`·`kf_tstar`는 배치 전용이라 그 루프만 스텝 함수로 옮겼다(R1Online, KF2Online).
같은 입력에서 배치 함수와 경보열·T*가 같은지는 test_equiv.py가 확인한다.

τ는 표시용 매핑이다(v10~v12에는 연속 τ가 없음). 기존 임계에 고정: τ = 0.7^(점수/임계) → τ ≤ 0.7 ⇔ 기존 경보.
"""
import json
import sys
from collections import deque
from pathlib import Path

sys.dont_write_bytecode = True
V15 = Path(__file__).resolve().parent
ROOT = V15.parent
sys.path[:0] = [str(ROOT / "scratch_v12"), str(ROOT / "scratch_v11"), str(ROOT / "scratch_v11h"), str(ROOT / "scratch_v10"),
                str(ROOT / "v2v_trust_planning" / "src")]

import numpy as np  # noqa: E402
import torch  # noqa: E402

import gen_v12 as GV  # noqa: E402
import rules_v12 as RU  # noqa: E402
import scores_v11 as S  # noqa: E402
from features_v10 import K, W_LANE, e_score, residuals, unwrap_lane  # noqa: E402,F401
from train_v10 import load_model  # noqa: E402

DT = RU.DT
ACT = S.ACT                 # 10 s 이전에는 경보 없음 (v10~v12 공통)
TAU_ALARM = 0.7             # 저장소 config.trust_threshold_avoid

# 설정 = (GRU 모델, v10 결과 라벨, σ_meas, v11 결과 파일, v12 결과 파일)
SETTINGS = {"nom": ("B1_sd0.23_s0", "B1_s0", 0.1, "v11_sd0.23_sm0.1.json", "v12_nom.json"),
            "sm0.30": ("B2_sd0.23_s0", "B2_s0", 0.3, "v11_sd0.23_sm0.3.json", "v12_sm0.30.json")}


def load_setting(setting):
    """보정값을 원 결과 파일에서 읽는다(재보정 없음). R2는 v12 p_sig 1.0 변형의 선택 조합, 보정 실패면 ref 조합(표시 전용)."""
    mname, label, sm, f11, f12 = SETTINGS[setting]
    v10 = json.load(open(ROOT / "scratch_v10" / "results" / "eval_nom.json"))
    theta = next(r for r in v10["results"] if r["model"] == label)["theta"]
    v11 = json.load(open(ROOT / "scratch_v11" / "results" / f11))
    r1 = next(r for r in v11["results"] if r["score"] == "S3")["chosen"]
    v12 = json.load(open(ROOT / "scratch_v12" / "results" / f12))
    var = next(v for v in v12["variants"] if v["p_sig"] == 1.0)
    c = var["chosen"] or var["ref_if_failed"]
    return dict(setting=setting, model=mname, theta=theta, sigma_meas=sm, ws=r1["ws"], nu_d=r1["nu_d"], h_d=r1["h"],
                tau_f=c["tau_f"], T_c=c["Tc"], r2_calib_ok=var["calib_ok"],
                sources=dict(theta=f"scratch_v10/results/eval_nom.json [{label}]", r1=f"scratch_v11/results/{f11} [S3 chosen]",
                             r2=f"scratch_v12/results/{f12} [p_sig 1.0 {'chosen' if var['calib_ok'] else 'ref_if_failed'}]"))


class R1Online:
    """rules_v12.r1_alarm의 스텝 판. x = (|d_s| − ν_d)·Δt, 의도 구간 동결, 구간 종료 첫 표본에 g = 0, g ≥ h면 경보 후 0."""

    def __init__(self, ws, nu_d, h):
        self.w, self.nu_d, self.h = int(round(ws / DT)), nu_d, h
        self.buf = deque(maxlen=self.w)
        self.g, self.held, self.i = 0.0, False, -1

    def step(self, d, intent):
        self.i += 1
        self.buf.append(d)
        if self.i < max(ACT, self.w - 1):          # scores_v11.start_of
            return False, self.g
        ds = S.d_smooth(np.array(self.buf), self.buf.maxlen * DT)[-1]
        x = (abs(ds) - self.nu_d) * DT
        if intent:
            self.held = True
            return False, self.g
        if self.held:
            self.g, self.held = 0.0, False
        self.g = max(0.0, self.g + x)
        if self.g >= self.h:
            g_hit, self.g = self.g, 0.0
            return True, g_hit
        return False, self.g


class KF2Online:
    """rules_v12.kf_tstar의 스텝 판 (2상태 [y, ψ], 펼친 y 위에서 추정, 경계는 현재 차선 기준 d̂ = ŷ − off)."""

    def __init__(self, sigma_meas, tau_f):
        self.q_y = RU.SIG_D_NOM ** 2 * 2 * DT / tau_f
        self.q_p = (RU.SIG_N ** 2 + RU.SIG_B ** 2) * DT ** 2
        self.R = sigma_meas ** 2
        self.k = -1
        self.d_prev = None
        self.off = 0.0

    def step(self, d, r, v):
        self.k += 1
        if self.d_prev is not None:                 # features_v10.unwrap_lane의 점프 규칙을 한 표본씩
            _, o = unwrap_lane([self.d_prev, d])
            self.off += o[1]
        self.d_prev = d
        Y = d + self.off
        if self.k == 0:
            self.y, self.p = Y, 0.0
            self.P00, self.P01, self.P11 = self.R, 0.0, 0.01 ** 2
        else:
            a = v * DT
            self.y = self.y + a * self.p + 0.5 * v * r * DT ** 2
            self.p = self.p + r * DT
            n00 = self.P00 + 2 * a * self.P01 + a * a * self.P11 + self.q_y
            n01 = self.P01 + a * self.P11
            n11 = self.P11 + self.q_p
            self.P00, self.P01, self.P11 = n00, n01, n11
        s = self.P00 + self.R
        k0, k1 = self.P00 / s, self.P01 / s
        inn = Y - self.y
        self.y, self.p = self.y + k0 * inn, self.p + k1 * inn
        self.P00, self.P01, self.P11 = (1 - k0) * self.P00, (1 - k0) * self.P01, self.P11 - k1 * self.P01
        dh = self.y - self.off
        pred = dh + v * self.p * RU.TAU_GRID + 0.5 * v * r * RU.TAU_GRID ** 2
        hit = np.abs(pred) >= RU.HALF
        ts = float(RU.TAU_GRID[np.argmax(hit)]) if hit.any() else np.inf
        return ts, dh, self.p


class Detector:
    """step(a_long, r_rel, d, turn_signal, v) → dict. turn_signal은 의도 구간에만 쓰고 GRU 입력에는 넣지 않는다.

    r2_enabled: v12 최종 규칙에서 R2를 쓰는지. 신호 가정 off이거나 v12 보정 실패 설정이면 False(R1 단독),
    이때도 T*는 계산해서 내보낸다(경보·τ_rule 미반영). r2_override = (τ_f, T_c)는 검증용(기본 None).
    """

    def __init__(self, setting="nom", r2_enabled=True, r2_override=None):
        self.cfg = load_setting(setting)
        if r2_override is not None:
            self.cfg.update(tau_f=r2_override[0], T_c=r2_override[1])
        self.model, self.info = load_model(self.cfg["model"])
        self.r2_enabled = bool(r2_enabled and self.cfg["r2_calib_ok"])
        self.reset()

    def reset(self):
        self.i = -1
        self.F = deque(maxlen=K + 1)
        self.sig = deque(maxlen=GV.INTENT_TAIL + 1)
        self.r1 = R1Online(self.cfg["ws"], self.cfg["nu_d"], self.cfg["h_d"])
        self.kf = KF2Online(self.cfg["sigma_meas"], self.cfg["tau_f"])

    @torch.no_grad()
    def step(self, a_long, r_rel, d, turn_signal, v):
        self.i += 1
        c = self.cfg
        self.F.append(np.array([a_long, r_rel, d], np.float32))       # features_v10.make_features 열 순서
        self.sig.append(bool(turn_signal))
        intent = bool(GV.intent(np.array(self.sig))[-1])
        e = e_long = e_lat = r_a = 0.0
        if len(self.F) == K + 1:
            # e_score와 같은 계산(features_v10.residuals 마지막 표본)을 채널별로: e = Σ_c (잔차_c/σ_c)²
            res = residuals(self.model, np.stack(self.F), self.info["C"], self.info["mu"], self.info["sd"])[-1]
            terms = (res / np.asarray(self.info["sigma_c"])) ** 2
            e_long, e_lat, r_a = float(terms[0]), float(terms[1] + terms[2]), float(res[0])
            e = float(terms.sum())
        active = self.i >= ACT
        alarm_gru = bool(active and e > c["theta"])
        alarm_r1, S1 = self.r1.step(d, intent)
        ts, dh, psi = self.kf.step(d, r_rel, v)
        r2_raw = bool(active and ts <= c["T_c"] and not intent)       # rules_v12.r2_alarm
        alarm_r2 = r2_raw and self.r2_enabled
        alarm_rule = bool(alarm_r1 or alarm_r2)
        tau_gru = TAU_ALARM ** (e / c["theta"]) if active else 1.0
        tau_r1 = TAU_ALARM ** (S1 / c["h_d"]) if (active and not intent) else 1.0
        tau_r2 = (TAU_ALARM ** (c["T_c"] / ts) if ts > 0 else 0.0) if (active and not intent and self.r2_enabled) else 1.0
        tau_rule = min(tau_r1, tau_r2)
        return dict(e_long=e_long, e_lat=e_lat, r_a=r_a, i=self.i, t=self.i * DT, tau=min(tau_gru, tau_rule), tau_gru=tau_gru, tau_rule=tau_rule, tau_r1=tau_r1,
                    tau_r2=tau_r2, e=e, R1_S=S1, R2_ttb=ts, alarm_gru=alarm_gru, alarm_r1=bool(alarm_r1), alarm_r2=alarm_r2,
                    alarm_r2_shadow=r2_raw, alarm_rule=alarm_rule, alarm=bool(alarm_gru or alarm_rule), intent_window=intent,
                    active=active, d_hat=dh, psi_hat=psi)
