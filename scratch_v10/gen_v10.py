"""PROTOCOL_v10 3절 생성기: 직선 3차로, 실데이터 적합 a·r, 차선 횡위치 d, d 기반 이상 주입 (정의만, import 시 파일을 쓰지 않음).
반환 특징은 features_v10.make_features 규약 [a_long, r_rel, d] (κ_road = 0)."""
import json
from dataclasses import dataclass, replace
from functools import lru_cache
from pathlib import Path

import numpy as np
from scipy.signal import butter, lfilter, sosfilt, sosfilt_zi, sosfiltfilt

from features_v10 import W_LANE, make_features

V10 = Path(__file__).resolve().parent
DT = 0.1
N_LANES = 3
V_LIM = (1.0, 50.0)        # 변경 이력 2: 40 → 50 (accel_ramp 4/100건 상한 고착)
K_V = 0.05                 # 속도 조절 이득 (gen_v6)
LC_RATE = 22.8 / 3600      # 차선변경 [1/s] (v8 comma 실측)
LC_T = (4.0, 6.0)          # 차선변경 시간 [s] (v8 실측 중앙값 5.0 s)
SIG_N_YAW = 0.00107        # 요레이트 노이즈 바닥 [rad/s] (v8)
SIG_V_MEAS = 0.01          # 차속 측정 백색 노이즈 [m/s] (가정)
F_D_LP = 0.2               # d 차선유지 성분 평활 [Hz] (생성 전용, 변경 이력 1: 0.5 → 0.2)
ACT = 100
A2_SOS = butter(2, 1.0, btype="low", fs=1.0 / DT, output="sos")
D_SOS = butter(2, F_D_LP, btype="low", fs=1.0 / DT, output="sos")

TRAIN_SEEDS = range(0, 300)
VAL_SEEDS = range(100000, 100100)
TEST_SEEDS = range(200000, 200100)
EP_BASE = 300000
N_EP = 100
TRAIN_N, LONG_N = 6000, 18000
POST = 600
KINDS = ["brake_step", "brake_ramp", "accel_ramp", "weave_L1", "weave_L2", "weave_L3", "depart_fast", "depart_slow",
         "drift_005", "drift_010", "drift_020", "offset_03", "offset_06", "offset_09", "speed_unstable"]
WEAVE = {"weave_L1": (0.3, (6.0, 10.0)), "weave_L2": (0.5, (4.0, 6.0)), "weave_L3": (0.8, (3.0, 4.0))}
DEPART_T = {"depart_fast": (1.0, 1.5), "depart_slow": (2.0, 3.0)}
DRIFT_S = {"drift_005": 0.05, "drift_010": 0.10, "drift_020": 0.20}
OFFSET_C = {"offset_03": 0.3, "offset_06": 0.6, "offset_09": 0.9}


@dataclass(frozen=True)
class Cfg:
    sigma_d: float
    tau_d: float
    sigma_meas: float
    sig_a: float
    tau_a: float
    sig_rx: float
    tau_rx: float
    rx_scale: float = 1.0
    ev_rate: float = 0.0
    ev_b: float = 0.0
    ev_d: tuple = (2.0, 6.0)


def load_cfg(sigma_d=None, sigma_meas=0.1, rx_scale=1.0):
    """stats_comma_v10.json(comma 적합)과 명목 σ_d·τ_d(UAH 요약 수치)로 설정을 만든다."""
    st = json.load(open(V10 / "stats_comma_v10.json"))
    return Cfg(sigma_d=st["sigma_d_nom"] if sigma_d is None else sigma_d, tau_d=st["tau_d_nom"], sigma_meas=sigma_meas,
               sig_a=st["sig_a"], tau_a=st["tau_a"], sig_rx=st["sig_rx"], tau_rx=st["tau_rx"], rx_scale=rx_scale,
               ev_rate=st["ev_rate"], ev_b=st["ev_b"], ev_d=tuple(st["ev_d"]))


def ou_unit(rng, n, tau):
    """정상 분포 단위 OU (분산 1), 10 Hz."""
    rho = np.exp(-DT / tau)
    e = rng.normal(0.0, np.sqrt(1 - rho ** 2), n)
    x0 = rng.normal()
    return lfilter([1.0], [1.0, -rho], e, zi=[rho * x0])[0]


@lru_cache(maxsize=None)
def d_scale(tau):
    """단위 OU를 F_D_LP 영위상 평활했을 때의 표준편차 (긴 고정 시드 표본)."""
    return float(sosfiltfilt(D_SOS, ou_unit(np.random.RandomState(12345), 400000, tau)).std())


def speed_loop(a_cmd, v0):
    n = len(a_cmd)
    a, v = np.zeros(n), np.zeros(n)
    vc = v0
    for i in range(n):
        a[i] = a_cmd[i] - K_V * (vc - v0)
        vc = vc + a[i] * DT
        v[i] = vc
    return a, v


def a2_observe(v_meas):
    """A2 진폭 응답을 갖는 인과 필터: 1 Hz 2차 버터워스를 두 번(인과) → 차분."""
    zi = sosfilt_zi(A2_SOS) * v_meas[0]
    x = sosfilt(A2_SOS, sosfilt(A2_SOS, v_meas, zi=zi)[0], zi=zi)[0]
    return np.r_[0.0, np.diff(x) / DT]


def lc_disp(T, w=W_LANE):
    """사인 횡가속 차선변경의 횡변위 y(t) = W·(s − sin(2πs)/(2π)), s = t/T."""
    s = np.arange(int(round(T / DT)) + 1) * DT / T
    return w * (s - np.sin(2 * np.pi * s) / (2 * np.pi))


def smoothstep(x):
    x = np.clip(x, 0.0, 1.0)
    return x * x * (3 - 2 * x)


def base(seed, n, cfg, lc_until=None):
    """궤적 난수 = RandomState(seed). 순서 고정: v0, a, d, r_extra, 시작 차로, 차선변경, 가속도 사건."""
    rng = np.random.RandomState(seed)
    v0 = rng.uniform(15, 30)
    a_u = ou_unit(rng, n, cfg.tau_a)
    d_u = sosfiltfilt(D_SOS, ou_unit(rng, n, cfg.tau_d)) / d_scale(cfg.tau_d)
    rx_u = ou_unit(rng, n, cfg.tau_rx)
    lane = rng.randint(N_LANES)
    y = (lane - 1) * W_LANE + cfg.sigma_d * d_u
    lim = n if lc_until is None else lc_until
    t, lcs = rng.exponential(1 / LC_RATE), []
    while True:
        T = rng.uniform(*LC_T)
        go = rng.choice([-1, 1])
        i0 = int(t / DT)
        prof = lc_disp(T)
        if i0 + len(prof) > lim:
            break
        if not 0 <= lane + go < N_LANES:
            go = -go
        lane += go
        y[i0:i0 + len(prof)] += go * prof
        y[i0 + len(prof):] += go * W_LANE
        lcs.append((i0, T, int(go)))
        t += T + rng.exponential(1 / LC_RATE)
    a_cmd = cfg.sig_a * a_u + accel_events(rng, n, lim, cfg.ev_rate, cfg.ev_b, cfg.ev_d)
    return dict(v0=v0, a_cmd=a_cmd, y=y, r_extra=cfg.rx_scale * cfg.sig_rx * rx_u, lcs=lcs)


def accel_events(rng, n, lim, rate, b, ev_d=(2.0, 6.0), dv_max=3.0, dv_cap=8.0):
    """v5식 sin² 가속도 사건: Poisson(rate), 길이 U(ev_d), 진폭 Exp(b) (변경 이력 1).
    부호: 사건들이 누적한 속도 변화 |Δv| > dv_max면 Δv를 줄이는 쪽, 아니면 무작위. 진폭은 사건 후 |Δv| ≤ dv_cap이 되게 자른다
    (변경 이력 2: 속도 표류·한계 고착 방지)."""
    out = np.zeros(n)
    if rate <= 0:
        return out
    t, dv = rng.exponential(1 / rate), 0.0
    while True:
        D, sg, A = rng.uniform(*ev_d), rng.choice([-1, 1]), rng.exponential(b)
        if abs(dv) > dv_max:
            sg = -np.sign(dv)
        A = min(A, max(0.0, dv_cap - sg * dv) * 2 / D)
        i0, m = int(t / DT), int(D / DT)
        if i0 + m > lim:
            break
        out[i0:i0 + m] += sg * A * np.sin(np.pi * np.arange(m) / m) ** 2
        dv += sg * A * D / 2
        t += D + rng.exponential(1 / rate)
    return out


def observe(seed, b, cfg, d_acc=None, d_y=None):
    """종방향(가속도 증분)·횡방향(y 증분) 이상을 더하고 관측을 만든다. 관측 노이즈 난수 = seed·10 + 1/2/3."""
    a, v = speed_loop(b["a_cmd"], b["v0"])
    if d_acc is not None:
        a = a + d_acc
        v = v + np.cumsum(d_acc) * DT
    clip = float(np.mean((v <= V_LIM[0]) | (v >= V_LIM[1])))
    v = np.clip(v, *V_LIM)
    y = b["y"] + (0.0 if d_y is None else d_y)
    yd = np.r_[0.0, np.diff(y)] / DT                       # 후진 차분(인과, 변경 이력 2)
    psi = yd / v
    r = np.r_[0.0, np.diff(psi)] / DT + b["r_extra"]
    n = len(v)
    v_meas = v + SIG_V_MEAS * np.random.RandomState(seed * 10 + 1).normal(size=n)
    a_obs = a2_observe(v_meas)
    r_obs = r + SIG_N_YAW * np.random.RandomState(seed * 10 + 2).normal(size=n)
    y_obs = y + cfg.sigma_meas * np.random.RandomState(seed * 10 + 3).normal(size=n)
    d_obs = y_obs - W_LANE * np.round(y_obs / W_LANE)
    F = make_features(a_obs, r_obs, v_meas, 0.0, d_obs)
    return dict(F=F, v=v, a=a, r=r, y=y, clip=clip, v_raw_out=float(np.mean((v < 8.0) | (v > 38.0))))


def normal_run(seed, n, cfg):
    return observe(seed, base(seed, n, cfg), cfg)


def episode(kind, s, cfg, inject=True):
    """이상 에피소드. 시드 = 300000 + 유형·1000 + s. 이상 파라미터 난수 = seed·10 + 5 (궤적과 분리)."""
    seed = EP_BASE + KINDS.index(kind) * 1000 + s
    prng = np.random.RandomState(seed * 10 + 5)
    on = int(round(prng.uniform(30.0, 35.0) / DT))
    n = on + POST
    b = base(seed, n, cfg, lc_until=on - 100)
    tt = (np.arange(n) - on) * DT
    pos = tt >= 0
    d_acc = d_y = None
    meta = dict(kind=kind, seed=seed, onset=on)
    if kind in ("brake_step", "brake_ramp"):
        D = prng.uniform(4, 6)
        p = np.zeros(n)
        if kind == "brake_step":
            p[on:on + 20] = -D
        else:
            R = prng.uniform(1, 2)
            r = int(round(R / DT))
            p[on:on + r] = -D * np.arange(1, r + 1) / r
            p[on + r:on + r + 10] = -D
            meta["R"] = R
        d_acc = p
        meta["D"] = D
    elif kind == "accel_ramp":
        A, R = prng.uniform(2.5, 3.5), prng.uniform(1, 2)
        r = int(round(R / DT))
        p = np.zeros(n)
        p[on:on + r] = A * np.arange(1, r + 1) / r
        p[on + r:on + r + 20] = A
        d_acc = p
        meta.update(A=A, R=R)
    elif kind == "speed_unstable":
        R, f, A = prng.uniform(5, 10), prng.uniform(0.05, 0.2), prng.uniform(0.3, 0.8)
        d_acc = smoothstep(tt / R) * pos * A * np.sin(2 * np.pi * f * tt)
        meta.update(R=R, f=f, A=A)
    elif kind in WEAVE:
        A, Pr = WEAVE[kind]
        P, R = prng.uniform(*Pr), prng.uniform(5, 10)
        d_y = smoothstep(tt / R) * pos * A * np.sin(2 * np.pi * tt / P)
        meta.update(A=A, P=P, R=R)
    elif kind in DEPART_T:
        D, T, sg = prng.uniform(1.5, 2.5), prng.uniform(*DEPART_T[kind]), prng.choice([-1, 1])
        d_y = sg * D * smoothstep(tt / T) * pos
        meta.update(D=D, T=T)
    elif kind in DRIFT_S:
        sl, R, sg = DRIFT_S[kind], prng.uniform(1, 3), prng.choice([-1, 1])
        d_y = np.cumsum(sg * sl * smoothstep(tt / R) * pos) * DT
        meta.update(slope=sl, R=R)
    elif kind in OFFSET_C:
        c, R, sg = OFFSET_C[kind], prng.uniform(10, 20), prng.choice([-1, 1])
        d_y = sg * c * smoothstep(tt / R) * pos
        meta.update(c=c, R=R)
    if not inject:
        d_acc = d_y = None
    o = observe(seed, b, cfg, d_acc, d_y)
    if kind in DRIFT_S and inject:
        lane_c = W_LANE * np.round(b["y"][on] / W_LANE)
        out = np.abs(o["y"] - lane_c) >= W_LANE / 2
        out[:on] = False
        meta["cross"] = int(np.argmax(out)) if out.any() else None
    o.update(onset=on, meta=meta)
    return o


def variant(cfg, **kw):
    return replace(cfg, **kw)
