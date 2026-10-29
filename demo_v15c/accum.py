"""v15 누적기 (README 2·4절). v14b B14b + k_drain(홀드 뒤 감소 스텝만 배율) + 자차 반응용 S_eff = min(S′, S).
정의만, import 시 파일을 쓰지 않는다. 합성 환경, 직선 도로, 시뮬레이션."""
import json
from pathlib import Path

import numpy as np

V15 = Path(__file__).resolve().parent
ROOT = V15.parent
DT, ACT = 0.1, 100
TAU_ALARM = 0.7
K_DRAIN_DEFAULT, K_DRAIN_RANGE = 1.3, (1.0, 2.0)
B_IDX = 10                         # v14b 선택안 인덱스 (ν p90 · 홀드 3 s · T_r 20 s · cap 2θ · 동결)
VARIANT = dict(q=90.0, hold=3.0, Tr=20.0, cap=2.0, gate=True)
D_ZONE, D_HYST = 8.0, 2.0


def load_params(setting):
    """v14b 결과 파일의 ν·h (신호 가정 ON = p_sig 1.0, OFF = p_sig 0.0). 재보정 없음."""
    R = json.load(open(ROOT / "scratch_v14b" / "results" / "eval_v14b.json"))
    assert R["selection"]["W"]["idx"] == B_IDX
    S = R["settings"][setting]
    th = S["theta"]
    bv = S["b_variants"]
    return dict(theta=th, on=(bv["B14b p1.0"]["nu_theta"] * th, bv["B14b p1.0"]["h"]),
                off=(bv["B14b p0.0"]["nu_theta"] * th, bv["B14b p0.0"]["h"]))


def drain_step(S, tinc, i, x, nu, rho, hold, k):
    """한 표본 갱신. x = min(e, cap). 반환 (S, tinc). k = 1이면 v14b(accum_v14b._step)와 같다."""
    cand = max(0.0, rho * S + x - nu)
    if cand > S:
        return cand, i
    if i - tinc <= hold:
        return S, tinc
    return max(0.0, S + x - k * (nu + (1.0 - rho) * S)), tinc


class Online:
    """한 이웃의 탐지 S와 반응 S′ (같은 파라미터). step → dict."""

    def __init__(self, theta, nu, h, k_drain=K_DRAIN_DEFAULT):
        self.theta, self.cap = theta, VARIANT["cap"] * theta
        self.rho = float(np.exp(-DT / VARIANT["Tr"]))
        self.hold = int(round(VARIANT["hold"] / DT))
        self.k = k_drain
        self.set(nu, h)
        self.reset()

    def set(self, nu, h):
        self.nu, self.h = nu, h

    def reset(self):
        self.i = -1
        self.S, self.tS = 0.0, -10 ** 9
        self.Sp, self.tSp = 0.0, -10 ** 9

    def step(self, e, e_prime, intent):
        self.i += 1
        if self.i >= ACT and not (VARIANT["gate"] and intent):
            self.S, self.tS = drain_step(self.S, self.tS, self.i, min(e, self.cap), self.nu, self.rho, self.hold, self.k)
            self.Sp, self.tSp = drain_step(self.Sp, self.tSp, self.i, min(e_prime, self.cap), self.nu, self.rho, self.hold, self.k)
        act = self.i >= ACT
        S_eff = min(self.Sp, self.S)
        return dict(S=self.S, S_prime=self.Sp, S_eff=S_eff, clipped=self.Sp > self.S, hold_on=self.i - self.tS <= self.hold,
                    alarm_path=bool(act and self.S > self.h), alarm_eff_path=bool(act and S_eff > self.h),
                    tau_state=TAU_ALARM ** (self.S / self.h) if act else 1.0, tau_state_eff=TAU_ALARM ** (S_eff / self.h) if act else 1.0)


class Zone:
    """dx 구역 (앞/뒤/나란히), D = 8 m, ±2 m 히스테리시스 (README 4절)."""

    def __init__(self):
        self.z = None

    def update(self, dx):
        hi, lo = D_ZONE + D_HYST, D_ZONE - D_HYST
        if self.z is None:
            self.z = "앞" if dx > hi else "뒤" if dx < -hi else "나란히"
        elif self.z == "앞" and dx < lo:
            self.z = "뒤" if dx < -hi else "나란히"
        elif self.z == "뒤" and dx > -lo:
            self.z = "앞" if dx > hi else "나란히"
        elif self.z == "나란히":
            self.z = "앞" if dx > hi else "뒤" if dx < -hi else "나란히"
        return self.z

    @staticmethod
    def g(zone, r_a):
        """e_long 포함 여부. 앞: 감속(r_a < 0)만, 뒤: 가속(r_a > 0)만, 나란히: 항상."""
        if zone == "앞":
            return r_a < 0
        if zone == "뒤":
            return r_a > 0
        return True


def batch(E, I, nu, h, theta, k):
    """같은 길이로 채운 run 묶음 (N, T)의 탐지 S (N, T). k_drain 영향 표용 (kdrain_v15.py)."""
    N, T = E.shape
    cap, rho, hold = VARIANT["cap"] * theta, float(np.exp(-DT / VARIANT["Tr"])), int(round(VARIANT["hold"] / DT))
    S, tinc = np.zeros(N), np.full(N, -10 ** 9)
    out = np.zeros((N, T))
    for i in range(ACT, T):
        x = np.minimum(E[:, i], cap)
        cand = np.maximum(0.0, rho * S + x - nu)
        up = cand > S
        held = (i - tinc) <= hold
        drain = np.maximum(0.0, S + x - k * (nu + (1.0 - rho) * S))
        Sn = np.where(up, cand, np.where(held, S, drain))
        tn = np.where(up, i, tinc)
        frz = I[:, i] if I is not None else np.zeros(N, bool)
        S, tinc = np.where(frz, S, Sn), np.where(frz, tinc, tn)
        out[:, i] = S
    return out
