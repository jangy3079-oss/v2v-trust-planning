"""PROTOCOL_v12 2절: R1(S3 + 의도 게이팅), R2(2상태 칼만 + 2차 외삽 경계 도달 시간). 정의만, import 시 파일을 쓰지 않음."""
import sys
from pathlib import Path

sys.dont_write_bytecode = True
V12 = Path(__file__).resolve().parent
ROOT = V12.parent
sys.path[:0] = [str(V12), str(ROOT / "scratch_v11"), str(ROOT / "scratch_v10")]

import numpy as np  # noqa: E402

import scores_v11 as S  # noqa: E402
from features_v10 import W_LANE, unwrap_lane  # noqa: E402

DT = 0.1
HALF = W_LANE / 2
SIG_N, SIG_B = 0.00107, 0.0050      # 요레이트 잡음 바닥(v8), 직진 행동 편차(v10 PROTOCOL 0절)
SIG_D_NOM = 0.23                    # 설계 상수 (모든 설정 공통)
TAU_GRID = np.round(np.arange(0.0, 2.0001, 0.1), 1)
TAU_F, T_C = (3.0, 5.0, 10.0), (0.5, 1.0, 2.0)


def r1_alarm(d, ws, nu_d, h, intent):
    """S3 리셋 CUSUM. 의도 구간에서는 g 동결·경보 없음, 구간이 끝나는 첫 표본에서 g = 0 (2.1절)."""
    x, act = S.s3_input(d, ws, nu_d)
    a = np.zeros(len(d), bool)
    g, held = 0.0, False
    for i in np.flatnonzero(act):
        if intent[i]:
            held = True
            continue
        if held:
            g, held = 0.0, False
        g = max(0.0, g + x[i])
        if g >= h:
            a[i] = True
            g = 0.0
    return a


def kf_tstar(D, Rr, V, sigma_meas, tau_f):
    """행 = run(같은 길이로 채움). D 관측 d, Rr 관측 r_rel, V 측정 차속. 반환 T* [s] (2 s 안에 안 닿으면 inf), d̂, ψ̂."""
    D, Rr, V = (np.asarray(x, np.float64) for x in (D, Rr, V))
    N, T = D.shape
    Y = np.empty_like(D)
    OFF = np.empty_like(D)
    for i in range(N):
        Y[i], OFF[i] = unwrap_lane(D[i])
    q_y = SIG_D_NOM ** 2 * 2 * DT / tau_f
    q_p = (SIG_N ** 2 + SIG_B ** 2) * DT ** 2
    R = sigma_meas ** 2
    y, p = Y[:, 0].copy(), np.zeros(N)
    P00, P01, P11 = np.full(N, R), np.zeros(N), np.full(N, 0.01 ** 2)
    YH, PH = np.empty_like(D), np.empty_like(D)
    for k in range(T):
        if k > 0:
            v, r = V[:, k], Rr[:, k]
            a = v * DT
            y = y + a * p + 0.5 * v * r * DT ** 2
            p = p + r * DT
            n00 = P00 + 2 * a * P01 + a * a * P11 + q_y
            n01 = P01 + a * P11
            n11 = P11 + q_p
            P00, P01, P11 = n00, n01, n11
        s = P00 + R
        k0, k1 = P00 / s, P01 / s
        inn = Y[:, k] - y
        y, p = y + k0 * inn, p + k1 * inn
        P00, P01, P11 = (1 - k0) * P00, (1 - k0) * P01, P11 - k1 * P01
        YH[:, k], PH[:, k] = y, p
    DH = YH - OFF
    TS = np.full(D.shape, np.inf)
    tau = TAU_GRID[None, :]
    for i in range(N):
        pred = DH[i][:, None] + (V[i] * PH[i])[:, None] * tau + 0.5 * (V[i] * Rr[i])[:, None] * tau ** 2
        hit = np.abs(pred) >= HALF
        anyh = hit.any(1)
        TS[i, anyh] = TAU_GRID[np.argmax(hit[anyh], 1)]
    return TS, DH, PH


def r2_alarm(tstar, Tc, intent):
    a = (tstar <= Tc) & ~intent
    a[:S.ACT] = False
    return a
