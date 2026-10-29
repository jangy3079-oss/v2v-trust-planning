"""demo_v15c 단위 테스트 (README 2절 3.4; demo_v15a/test_v15.py 복사 + e′ 생성 버그 수정). 합성 환경, 직선 도로, 시뮬레이션.
1) e 분해: Detector 스텝의 e_long + e_lat = 배치 e_score (생성기 정상 run 1개·이상 에피소드 4개)
2) k_drain 1.0 누적기(Online, batch) = scratch_v14b accum_v14b.trace (v14 캐시 e·의도, 명목·σ_meas 0.3, 51개 시계열)
3) S_eff = min(S′, S) ≤ S, 그리고 잘림 없이 S′ > S가 된 표본 비율 (임의 e′ ≤ e, k_drain 1.0·1.3·2.0)
사용: /opt/anaconda3/bin/python -I -B test_v15c.py → results/test_v15c.json"""
import json
import sys
from pathlib import Path

sys.dont_write_bytecode = True
V15 = Path(__file__).resolve().parent
ROOT = V15.parent
sys.path[:0] = [str(V15), str(ROOT / "scratch_v14b"), str(ROOT / "scratch_v14")]

import numpy as np  # noqa: E402
import torch  # noqa: E402

import accum as AC  # noqa: E402
import accum_v14b as AB  # noqa: E402
import common_v14 as CM  # noqa: E402
import detector as DE  # noqa: E402
from detector import GV, e_score  # noqa: E402
from gen_v12 import G  # noqa: E402

torch.set_num_threads(2)
out = {}

# 1) e 분해
det = DE.Detector("nom")
cfg = G.load_cfg()
runs = [GV.normal(200000, 1500, cfg)["F"]] + [GV.episode(k, 0, cfg)["F"] for k in ("brake_step", "accel_ramp", "weave_L2", "drift_010")]
worst = worst_b = 0.0
th = det.cfg["theta"]
for F in runs:
    det.reset()
    rows = [det.step(float(F[i, 0]), float(F[i, 1]), float(F[i, 2]), 0, 25.0) for i in range(len(F))]
    # 기존 e = demo_v13 Detector와 같은 계산: 최근 K+1 표본 창의 e_score 마지막 값
    Ff = F.astype(np.float32)
    ref = np.array([0.0 if i < DE.K else float(e_score(det.model, Ff[i - DE.K:i + 1], det.info["C"], det.info["mu"], det.info["sd"],
                                                          det.info["sigma_c"])[-1]) for i in range(len(F))])
    bat = e_score(det.model, F, det.info["C"], det.info["mu"], det.info["sd"], det.info["sigma_c"])
    s = np.array([r["e_long"] + r["e_lat"] for r in rows])
    m = ref > 0
    worst = max(worst, float(np.max(np.abs(s[m] - ref[m]) / ref[m])))
    worst_b = max(worst_b, float(np.max(np.abs(s - bat)) / th))
out["e_split_max_rel_err_vs_window_e_score"] = worst
out["e_split_max_abs_err_vs_batch_over_theta"] = worst_b
print("1) e_long + e_lat = 기존 창 e_score: 최대 상대 오차", worst, "| 전체 배치 e_score와 최대 차 / θ", worst_b)
assert worst < 1e-9

# 2) k_drain 1.0 = accum_v14b
v = AB.grid()[AC.B_IDX]
assert {k: v[k] for k in AC.VARIANT} == AC.VARIANT
res2 = {}
for st in ("nom", "sm0.30"):
    C = CM.load(st)
    p = AC.load_params(st)
    for ps, (nu, h) in ((1.0, p["on"]), (0.0, p["off"])):
        P = AB.params([v], p["theta"], {v["q"]: nu})
        ok = n = 0
        for k, idx in (("test", range(5)), ("teps", range(0, 2300, 50))):
            E, I = C["E"][k][list(idx)], CM.pad([C["I"][ps][k][i] for i in idx])
            ref = AB.trace(E, P, h, I)
            bt = AC.batch(E.astype(np.float64), I, nu, h, p["theta"], 1.0)
            for j, i in enumerate(idx):
                L = C["meta"][k]["L"][i]
                on = AC.Online(p["theta"], nu, h, k_drain=1.0)
                S = np.array([on.step(float(E[j, t]), float(E[j, t]), bool(I[j, t]))["S"] for t in range(L)])
                n += 1
                ok += bool(np.allclose(S, ref[j, :L], rtol=1e-9, atol=1e-6) and np.allclose(bt[j, :L], ref[j, :L], rtol=1e-9, atol=1e-6))
        res2[f"{st} p{ps}"] = f"{ok}/{n}"
        print(f"2) {st} p_sig {ps}: k_drain 1.0 = accum_v14b.trace  {ok}/{n}")
        assert ok == n
out["kdrain1_equals_v14b"] = res2

# 3) S_eff ≤ S, 잘림 비율
C = CM.load("nom")
p = AC.load_params("nom")
rng = np.random.RandomState(0)
res3 = {}
for kd in (1.0, 1.3, 2.0):
    clip = tot = viol = 0
    for i in range(0, 2300, 23):
        L = C["meta"]["teps"]["L"][i]
        e = C["E"]["teps"][i][:L].astype(np.float64)
        mask = rng.uniform(size=L) < 0.5                                       # 절반 표본만 줄인다 (v15 버그 수정, v15b README 6절)
        ep = np.where(mask, e * rng.uniform(0, 1, L), e)
        assert (ep <= e).all()
        it = C["I"][1.0]["teps"][i]
        on = AC.Online(p["theta"], *p["on"], k_drain=kd)
        for t in range(L):
            r = on.step(float(e[t]), float(ep[t]), bool(it[t]))
            tot += 1
            clip += r["clipped"]
            viol += r["S_eff"] > r["S"] + 1e-12
    res3[str(kd)] = dict(samples=tot, raw_S_prime_gt_S=clip, frac=clip / tot, S_eff_gt_S=viol)
    print(f"3) k_drain {kd}: S′ > S(잘림 전) {clip}/{tot} = {clip / tot:.4%},  S_eff > S {viol}")
    assert viol == 0
out["seff_le_s"] = res3
json.dump(out, open(V15 / "results" / "test_v15c.json", "w"), ensure_ascii=False, indent=1, default=float)
print("OK")
