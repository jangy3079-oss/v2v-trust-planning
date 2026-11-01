"""PROTOCOL_v11h 3절: gen_v10에 drift 0.03·0.15와 stuck_steer 5단계를 추가한다 (정의만, import 시 파일을 쓰지 않음).
v10 15유형은 gen_v10.episode를 그대로 부르고, 새 유형은 KINDS 뒤에 이어 붙여 기존 시드를 바꾸지 않는다."""
import sys
from pathlib import Path

V11H = Path(__file__).resolve().parent
sys.path[:0] = [str(V11H.parent / "scratch_v10")]

import numpy as np  # noqa: E402

import gen_v10 as G  # noqa: E402
from features_v10 import W_LANE  # noqa: E402

DRIFT_NEW = {"drift_003": 0.03, "drift_015": 0.15}
STUCK = {"stuck_0020": 0.002, "stuck_0030": 0.003, "stuck_0050": 0.005, "stuck_0075": 0.0075, "stuck_0100": 0.01}
KINDS = G.KINDS + list(DRIFT_NEW) + list(STUCK)
DRIFT_ALL = {**G.DRIFT_S, **DRIFT_NEW}
CROSS_KINDS = set(DRIFT_ALL) | set(STUCK) | {"depart_slow"}


def crossing(y, on):
    """참 y가 onset 시 차선 중심에서 W/2 이상 벗어나는 첫 인덱스 (없으면 None)."""
    lane_c = W_LANE * np.round(y[on] / W_LANE)
    out = np.abs(y - lane_c) >= W_LANE / 2
    out[:on] = False
    return int(np.argmax(out)) if out.any() else None


def episode(kind, s, cfg, inject=True):
    """이상 에피소드. v10 유형은 G.episode 그대로. 새 유형 시드 = 300000 + KINDS.index·1000 + s, 이상 난수 = seed·10 + 5."""
    if kind in G.KINDS:
        o = G.episode(kind, s, cfg, inject)
    else:
        seed = G.EP_BASE + KINDS.index(kind) * 1000 + s
        prng = np.random.RandomState(seed * 10 + 5)
        on = int(round(prng.uniform(30.0, 35.0) / G.DT))
        n = on + G.POST
        b = G.base(seed, n, cfg, lc_until=on - 100)
        tt = (np.arange(n) - on) * G.DT
        pos = tt >= 0
        meta = dict(kind=kind, seed=seed, onset=on)
        if kind in DRIFT_NEW:
            sl, R, sg = DRIFT_NEW[kind], prng.uniform(1, 3), prng.choice([-1, 1])
            d_y = np.cumsum(sg * sl * G.smoothstep(tt / R) * pos) * G.DT
            meta.update(slope=sl, R=R)
        else:
            r0, sg = STUCK[kind], prng.choice([-1, 1])
            _, v = G.speed_loop(b["a_cmd"], b["v0"])
            v = np.clip(v, *G.V_LIM)
            d_y = np.cumsum(v * sg * r0 * tt * pos) * G.DT      # Δy' = v·Δψ, Δψ = ±r₀·t'
            meta.update(r0=r0)
        o = G.observe(seed, b, cfg, None, d_y if inject else None)
        o.update(onset=on, meta=meta)
    if kind in CROSS_KINDS and inject:
        o["meta"]["cross"] = crossing(o["y"], o["onset"])
    return o
