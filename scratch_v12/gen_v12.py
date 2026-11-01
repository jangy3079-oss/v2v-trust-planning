"""PROTOCOL_v12 1·3절: 차선변경 의도 신호(가정)와 weave_L4. v10 생성기·v11h 유형은 import만 한다.
import 시 파일을 쓰지 않는다. 직접 실행하면 신호·weave_L4·경계 통과 점검 → gen_v12_check.json."""
import json
import sys
from pathlib import Path

sys.dont_write_bytecode = True
V12 = Path(__file__).resolve().parent
ROOT = V12.parent
sys.path[:0] = [str(V12), str(ROOT / "scratch_v11h"), str(ROOT / "scratch_v10")]

import numpy as np  # noqa: E402

import gen_v11h as GH  # noqa: E402
from gen_v11h import G  # noqa: E402

KINDS = GH.KINDS + ["weave_L4"]
LATERAL = {k for k in KINDS if k.startswith(("drift", "stuck", "depart", "offset", "weave"))}
WEAVE_L4 = (1.2, (3.0, 5.0))
VAL_EP_BASE = 400000
INTENT_TAIL = 30          # 신호 꺼진 뒤 3 s
P_FALSE = 0.02            # 오신호 [/h]


def v_meas_of(seed, v):
    """observe와 같은 차속 측정값 (난수 = seed·10 + 1)."""
    return v + G.SIG_V_MEAS * np.random.RandomState(seed * 10 + 1).normal(size=len(v))


def signal(seed, n, lcs, p_sig, p_false=P_FALSE):
    """방향지시등(가정). 반환 (sig bool[n], 차선변경별 신호 여부 list)."""
    sig = np.zeros(n, bool)
    rng = np.random.RandomState(seed * 10 + 7)
    on = []
    for i0, T, _ in lcs:
        u, lead = rng.uniform(), rng.uniform(1.0, 3.0)
        if u < p_sig:
            sig[max(0, i0 - int(round(lead / G.DT))):min(n, i0 + int(round(T / G.DT)) + 1)] = True
        on.append(bool(u < p_sig))
    if p_false > 0:
        r2 = np.random.RandomState(seed * 10 + 8)
        t = r2.exponential(3600.0 / p_false)
        while t < n * G.DT:
            D = r2.uniform(2.0, 6.0)
            sig[int(t / G.DT):min(n, int((t + D) / G.DT))] = True
            t += D + r2.exponential(3600.0 / p_false)
    return sig, on


def intent(sig):
    """의도 구간: 신호 켜짐 + 꺼진 뒤 3 s (인과)."""
    i = np.arange(len(sig))
    last = np.maximum.accumulate(np.where(sig, i, -10 ** 9))
    return sig | (i - last <= INTENT_TAIL)


def normal(seed, n, cfg):
    o = G.normal_run(seed, n, cfg)
    return dict(F=o["F"], v_meas=v_meas_of(seed, o["v"]), lcs=G.base(seed, n, cfg)["lcs"], seed=seed)


def episode(kind, s, cfg, inject=True, base=None):
    """이상 에피소드 + v_meas, 생성기 차선변경 목록(onset − 10 s 전까지), 경계 통과. base = 시드 기저(검증 400000)."""
    old = G.EP_BASE
    if base is not None:
        G.EP_BASE = base
    try:
        if kind != "weave_L4":
            o = GH.episode(kind, s, cfg, inject)
            seed, on = o["meta"]["seed"], o["onset"]
        else:
            seed = G.EP_BASE + KINDS.index(kind) * 1000 + s
            prng = np.random.RandomState(seed * 10 + 5)
            on = int(round(prng.uniform(30.0, 35.0) / G.DT))
            n = on + G.POST
            b = G.base(seed, n, cfg, lc_until=on - 100)
            tt = (np.arange(n) - on) * G.DT
            A, Pr = WEAVE_L4
            P, R = prng.uniform(*Pr), prng.uniform(5, 10)
            d_y = G.smoothstep(tt / R) * (tt >= 0) * A * np.sin(2 * np.pi * tt / P)
            o = G.observe(seed, b, cfg, None, d_y if inject else None)
            o.update(onset=on, meta=dict(kind=kind, seed=seed, onset=on, A=A, P=P, R=R))
        n = len(o["v"])
        lcs = G.base(seed, n, cfg, lc_until=on - 100)["lcs"]
    finally:
        G.EP_BASE = old
    cross = GH.crossing(o["y"], on) if (inject and kind in LATERAL) else None
    return dict(kind=kind, s=s, seed=seed, onset=on, cross=cross, F=o["F"], v_meas=v_meas_of(seed, o["v"]), lcs=lcs)


if __name__ == "__main__":
    cfg = G.load_cfg()
    out = {}
    # 신호: 테스트 정상 100대
    tot = dict(lc=0, sig_lc=0, false_on=0)
    for p in (1.0, 0.8, 0.5, 0.0):
        n_lc = n_sig = fs = 0
        for s in G.TEST_SEEDS:
            r = normal(s, G.LONG_N, cfg)
            sig, on = signal(s, G.LONG_N, r["lcs"], p)
            sig0, _ = signal(s, G.LONG_N, r["lcs"], p, p_false=0.0)
            n_lc += len(on)
            n_sig += sum(on)
            fs += int(np.sum(np.diff(np.r_[0, (sig & ~sig0).astype(np.int8)]) == 1))
        out[f"p{p}"] = dict(lcs=n_lc, signaled=n_sig, frac=n_sig / n_lc, false_signal_onsets=fs)
        print("p_sig", p, out[f"p{p}"], flush=True)
    # weave_L4 점검 (σ_meas 0)
    c0 = G.variant(cfg, sigma_meas=0.0)
    pre, cr = [], []
    for s in range(G.N_EP):
        e1, e0 = episode("weave_L4", s, c0), episode("weave_L4", s, c0, inject=False)
        pre.append(bool(np.allclose(e1["F"][:e1["onset"]], e0["F"][:e0["onset"]])))
        cr.append(None if e1["cross"] is None else (e1["cross"] - e1["onset"]) * G.DT)
    c = [x for x in cr if x is not None]
    out["weave_L4"] = dict(pre_equal_all=all(pre), cross_n=len(c), cross_med=float(np.median(c)) if c else None)
    # 경계 통과 (명목)
    out["cross_nom"] = {}
    for k in ("depart_fast", "depart_slow", "weave_L3", "weave_L4", "offset_09", "drift_010", "drift_020", "stuck_0075", "stuck_0100"):
        eps = [episode(k, s, cfg) for s in range(G.N_EP)]
        c = [(e["cross"] - e["onset"]) * G.DT for e in eps if e["cross"] is not None]
        out["cross_nom"][k] = dict(n=len(c), med=float(np.median(c)) if c else None)
    # 시드 겹침 (검증 이상 6유형 대 나머지)
    vk = ["drift_010", "drift_015", "drift_020", "stuck_0050", "stuck_0075", "stuck_0100"]
    vs = {VAL_EP_BASE + KINDS.index(k) * 1000 + s for k in vk for s in range(G.N_EP)}
    ts = {G.EP_BASE + KINDS.index(k) * 1000 + s for k in KINDS for s in range(G.N_EP)}
    out["seed_overlap"] = len(vs & ts) + len(vs & (set(G.VAL_SEEDS) | set(G.TEST_SEEDS) | set(G.TRAIN_SEEDS)))
    print({k: v for k, v in out.items() if not k.startswith("p")}, flush=True)
    json.dump(out, open(V12 / "gen_v12_check.json", "w"), ensure_ascii=False, indent=1)
