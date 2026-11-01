"""PROTOCOL_v11 3·4절 점수와 경보 (정의만, import 시 파일을 쓰지 않음).
S0 = e의 CUSUM(학습 모델 점수 위), S1~S3 = d 기반 규칙 점수(학습 없음, 차선 이탈 경고 성격)."""
import sys
from pathlib import Path

sys.dont_write_bytecode = True
V11 = Path(__file__).resolve().parent
sys.path[:0] = [str(V11), str(V11.parent / "scratch_v10")]

import numpy as np  # noqa: E402

from features_v10 import W_LANE, unwrap_lane  # noqa: E402

DT = 0.1
ACT = 100
REARM = 300
HALF = W_LANE / 2


def rearm_count(a):
    """경보 표본 중 직전 계수 사건에서 30 s 이상 지난 것만 센다(연속 구간이면 v10 rearm_events와 같음)."""
    d = np.diff(np.r_[0, a.astype(np.int8), 0])
    st, en = np.flatnonzero(d == 1), np.flatnonzero(d == -1)
    n, last = 0, -10 ** 9
    for s, e in zip(st, en):
        if s - last >= REARM:
            n += 1
            last = s
        k = (e - 1 - last) // REARM
        if k > 0:
            n += int(k)
            last += k * REARM
    return n


def event_starts(a):
    """계수된 사건의 시작 표본(차선변경 구간 귀속용)."""
    idx, last = [], -10 ** 9
    for i in np.flatnonzero(a):
        if i - last >= REARM:
            idx.append(i)
            last = i
    return np.array(idx, int)


def lane_y(d):
    y, off = unwrap_lane(d)
    return y, off


def roll_mean(y, w):
    c = np.r_[0.0, np.cumsum(y)]
    out = np.full(len(y), np.nan)
    out[w - 1:] = (c[w:] - c[:-w]) / w
    return out


def d_smooth(d, ws):
    """차선 기준 인과 이동평균: 펼친 y의 [t − W_s, t] 평균 − off(t)."""
    y, off = lane_y(d)
    return roll_mean(y, int(round(ws / DT))) - off


def tlc(d, L):
    """최근 L초 직선 맞춤(현재 차선 기준)의 경계 도달 시간 [s]. 경계 방향 기울기만, 경계 밖이면 0, 아니면 inf."""
    y, off = lane_y(d)
    n, w = len(y), int(round(L / DT))
    i = np.arange(n, dtype=float)
    cy, ciy = np.r_[0.0, np.cumsum(y)], np.r_[0.0, np.cumsum(i * y)]
    out = np.full(n, np.inf)
    t = np.arange(w - 1, n)
    sy, siy = cy[t + 1] - cy[t + 1 - w], ciy[t + 1] - ciy[t + 1 - w]
    si = w * (t - (w - 1) / 2.0)
    var_i = w * (w * w - 1) / 12.0
    b = (siy - si * sy / w) / var_i                     # [m/표본]
    dh = sy / w + b * ((w - 1) / 2.0) - off[t]          # t에서의 맞춤값, 현재 차선 기준
    bs = b / DT                                         # [m/s]
    with np.errstate(divide="ignore", invalid="ignore"):
        T = np.where(bs > 0, (HALF - dh) / bs, np.where(bs < 0, (-HALF - dh) / bs, np.inf))
    T = np.where(np.abs(dh) >= HALF, 0.0, T)
    out[t] = T
    return out


def start_of(n, w_samples):
    a = np.zeros(n, bool)
    a[max(ACT, w_samples - 1):] = True
    return a


def s1_alarm(d, ws, c):
    ds = d_smooth(d, ws)
    return (np.nan_to_num(np.abs(ds)) > c) & start_of(len(d), int(round(ws / DT)))


def s2_alarm(d, L, Tc):
    T = tlc(d, L)
    return (T <= Tc) & start_of(len(d), int(round(L / DT)))


def cusum_reset(x, h, active):
    """S = max(0, S + x), S ≥ h면 경보 후 0 리셋 (repo CUSUMTracker.update/reset 식). x = e − ν 또는 (|d_s| − ν_d)·Δt."""
    a = np.zeros(len(x), bool)
    s = 0.0
    for i in np.flatnonzero(active):
        s = max(0.0, s + x[i])
        if s >= h:
            a[i] = True
            s = 0.0
    return a


def s0_alarm(e, nu, h):
    return cusum_reset(e - nu, h, np.arange(len(e)) >= ACT)


def s3_input(d, ws, nu_d):
    ds = d_smooth(d, ws)
    act = start_of(len(d), int(round(ws / DT)))
    return np.where(act, (np.abs(np.nan_to_num(ds)) - nu_d) * DT, 0.0), act


def s3_alarm(d, ws, nu_d, h):
    x, act = s3_input(d, ws, nu_d)
    return cusum_reset(x, h, act)


def cusum_counts_grid(X, ACTV, hs):
    """검증 보정: 여러 run(X: 목록)과 h 격자에 대해 리셋 CUSUM 경보의 재무장 사건 수를 한 번에 센다."""
    hs = np.asarray(hs, float)
    tot = np.zeros(len(hs), int)
    for x, act in zip(X, ACTV):
        s = np.zeros(len(hs))
        last = np.full(len(hs), -10 ** 9)
        for i in np.flatnonzero(act):
            s = np.maximum(0.0, s + x[i])
            hit = s >= hs
            if hit.any():
                cnt = hit & (i - last >= REARM)
                tot += cnt
                last = np.where(cnt, i, last)
                s[hit] = 0.0
    return tot
