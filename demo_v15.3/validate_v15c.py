"""demo_v15c 검증 (README 2절, 판정은 v15b와 같음): 직접 조작 M1·M2(주 판정)·성분 분해, 회복 시간, 시나리오 1~7, 횡방향 회귀(v14b 데모), 이전 v15b 같은 시드 비교:
v15와 v14b 데모는 모듈 이름(sim, detector)이 겹치므로 서로 다른 프로세스 풀에서 돌린다.
v14b 비교(시나리오 6)는 v14b 데모 탐지기(τ14b, 경보14b)를 같은 백엔드에 넣은 반사실 실행이다(v14b 데모 자체는 자차 제어 off 고정).
사용: /opt/anaconda3/bin/python -I -B validate_v15c.py [--workers 8] → results/validate_v15c.{json,md}"""
import argparse
import json
import sys
import time
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

sys.dont_write_bytecode = True
V15 = Path(__file__).resolve().parent
ROOT = V15.parent

import numpy as np  # noqa: E402

DT, N_RUN, K0 = 0.1, 20, 200            # 주입 t = 20 s
WIN = 400                               # 주입 후 40 s
LC_HOLD, LC_T = 3.0, 5.5               # v15b sim(조향 램프 0.05)에서 README 1절 규칙으로 측정
LONG = {"1": ("accel", 40.0, "앞에서 급가속"), "2": ("brake", -30.0, "뒤에서 급제동"), "3a": ("brake", 40.0, "앞에서 급제동"),
        "3b": ("accel", -15.0, "뒤에서 급가속"), "4a": ("accel", 0.0, "나란히 급가속"), "4b": ("brake", 0.0, "나란히 급제동")}
LAT = {"drift": "드리프트 0.2 m/s", "stuck": "조향 고정 0.01 rad/s", "weave": "위빙 0.8 m"}
NORMAL = {"6a": "신호 켠 차선변경", "6b": "신호 끈 차선변경", "6c": "정상 직진 60 s", "6d": "W/S 정상 가감속"}
MANUAL = {"M1": "직접 조작 M1 (20 s마다 W 2 s, 15 s마다 A/D 0.5 s)", "M2": "직접 조작 M2 (20 s마다 W 2 s → S 1 s)"}
NM = 1200
INJ_KEY = {"accel": "5", "brake": "4", "drift": "1", "stuck": "2", "weave": "3"}


# ---------------------------------------------------------------- 작업 (워커에서 실행)
def _v15(folder=None):
    sys.path.insert(0, str(folder or V15))
    import torch
    torch.set_num_threads(1)
    import app
    return app


N7 = 900                                # 시나리오 7: 주입 후 70 s


def _v14b():
    sys.path.insert(0, str(ROOT / "scratch_v14b"))
    import torch
    torch.set_num_threads(1)
    import app_v14b
    import ego_controller as EC          # demo_v13 (app_v14b가 경로 등록)
    return app_v14b, EC


def plan_normal(sc, seed):
    """시나리오 6 입력 계획 (v15·v14b 같은 키). 반환 (n, taps {k: key}, holds {k: set})."""
    rng = np.random.RandomState(1000 + seed)
    taps, holds = {}, {}
    n = 600
    if sc in ("6a", "6b"):
        k0 = int(round(rng.uniform(20, 25) / DT))
        hold = rng.uniform(LC_HOLD - 0.1, LC_HOLD + 0.1)
        for k in range(k0, k0 + int(round(hold / DT))):
            holds[k] = {"D"}
        if sc == "6a":
            taps[k0 - int(round(rng.uniform(1, 3) / DT))] = "E"
            taps[k0 + int(round(LC_T / DT))] = "E"
    elif sc == "6d":
        for k in range(200, 220):
            holds[k] = {"W"}
        for k in range(300, 310):
            holds[k] = {"S"}
    return n, taps, holds


def run_prev(job):
    """이전 demo_v15b (v14 B-W 누적기, 완화된 키 입력) — 같은 시드 비교용. 모듈 이름이 겹치므로 별도 풀에서만 부른다."""
    return run_v15(job, ROOT / "demo_v15b")


def manual_keys(sc, k):
    """README 3.1 직접 조작 패턴 (15 s부터)."""
    if k < 150:
        return set()
    h = set()
    if sc == "M1":
        if (k % 200) < 20:
            h.add("W")
        if (k % 150) < 5:
            h.add("A")
        elif (k % 150) < 10:
            h.add("D")
    else:
        if (k % 200) < 20:
            h.add("W")
        elif 40 <= (k % 200) < 50:
            h.add("S")
    return h


def run_v15(job, folder=None):
    app = _v15(folder)
    sc, backend, seed = job["sc"], job["backend"], job["seed"]
    kd = job.get("k_drain", app.AC.K_DRAIN_DEFAULT)
    gap = LONG[sc][1] if sc in LONG else 40.0
    ses = app.Session(seed=seed, gap=gap, lane=2, backend=backend, k_drain=kd)
    if sc in LAT:                                                      # 강도: v15 기본(드리프트 0.2, 조향 0.01, 위빙 0.8)
        ses.sim.drift_i, ses.sim.stuck_i, ses.sim.weave_i = 2, 4, 1
    if sc in NORMAL:
        n, taps, holds = plan_normal(sc, seed)
    elif sc == "7":
        n, taps, holds = N7, {K0: "4"}, {}
    elif sc in MANUAL:
        n, taps, holds = NM, {}, {k: manual_keys(sc, k) for k in range(NM)}
    else:
        n, taps, holds = K0 + WIN, {K0: INJ_KEY[LONG[sc][0] if sc in LONG else sc]}, {}
    COMP = ("tau_state", "tau_r1", "tau_r2", "alarm_path", "alarm_r1", "alarm_r2")
    rec = {k: [] for k in ("tau_det", "tau_eff", "alarm_det", "alarm_eff", "clipped", "reacted", "g", "zone", "v_ego") + COMP}
    for k in range(n):
        if k in taps:
            ses.key(taps[k])
        o, out = ses.step(holds.get(k, set()))
        for key in ("tau_det", "tau_eff", "alarm_det", "alarm_eff", "clipped", "g", "zone") + COMP:
            rec[key].append(out[key])
        rec["reacted"].append(ses.ego_rec["reacted"])
        rec["v_ego"].append(float(o["ego"][3]))
    return dict(job, **{k: np.array(v) for k, v in rec.items()}, n_coll=ses.ego_rec["n_coll"], gap_min=ses.ego_rec["gap_min"])


def run_v14(job):
    """v14 데모(scratch_v14/app_v14.Session14, v13 sim) τ14 — 횡방향 회귀(README 3.3). 별도 풀."""
    sys.path.insert(0, str(ROOT / "scratch_v14"))
    import torch
    torch.set_num_threads(1)
    import app_v14
    ses = app_v14.Session14(seed=job["seed"], gap=40.0, lane=2)
    ses.sim.drift_i, ses.sim.stuck_i, ses.sim.weave_i = 3, 4, 2                # v13 목록에서 0.2 / 0.01 / 0.8 m
    tau, al = [], []
    for k in range(K0 + WIN):
        if k == K0:
            ses.key(INJ_KEY[job["sc"]])
        o, out = ses.step(set())
        tau.append(out["tau14"])
        al.append(out["alarm14"])
    return dict(job, tau14=np.array(tau), alarm14=np.array(al))


def run_v14b(job):
    """v14b 데모(scratch_v14b/app_v14b.Session14b, 자차 off 고정) + 같은 백엔드를 τ14b로 몬 반사실 자차 (참고·시나리오 5·6용)."""
    app_v14b, EC = _v14b()
    sc, backend, seed = job["sc"], job["backend"], job["seed"]
    ses = app_v14b.Session14b(seed=seed, gap=40.0, lane=2)
    if sc in LAT:                                                      # v13 sim 목록에서 같은 강도 (0.2 / 0.01 / 0.8 m)
        ses.sim.drift_i, ses.sim.stuck_i, ses.sim.weave_i = 3, 4, 2
    if sc in NORMAL:
        n, taps, holds = plan_normal(sc, seed)
    else:
        n, taps, holds = K0 + WIN, {K0: INJ_KEY[sc]}, {}
    ctl = EC.EgoController(backend, v_set=25.0)
    ego = np.array([0.0, 0.0, 0.0, 25.0])
    ctl.reset(ego)
    rec = dict(tau14b=[], alarm14b=[], reacted=[])
    for k in range(n):
        if k in taps:
            ses.key(taps[k])
        o, out = ses.step(holds.get(k, set()))
        nb = dict(x=o["x"], y=o["y_obs"], psi=o["psi"], v=o["v_meas"], r=o["r_obs"])
        a, d = ctl.step(ego, nb, out["tau14b"], out["alarm14b"])
        yr = ctl.yaw_rate(d, ego[3])
        X, Y, th, v = ego
        ego = np.array([X + v * np.cos(th) * DT, Y + v * np.sin(th) * DT, th + yr * DT, max(0.0, v + a * DT)])
        st = ctl.info.get("state", "정상")
        engaged = bool(getattr(ctl.impl, "engaged", False))
        rec["reacted"].append((backend == "rule" and engaged) or (backend == "nmpc" and (st != "정상" or a <= -1.0)))
        rec["tau14b"].append(out["tau14b"])
        rec["alarm14b"].append(out["alarm14b"])
    return dict(job, **{k: np.array(v) for k, v in rec.items()})


# ---------------------------------------------------------------- 집계
def events(a):
    sys.path.insert(0, str(ROOT / "scratch_v11"))
    import scores_v11 as S
    return S.rearm_count(np.asarray(a, bool))


def fmt1(x):
    return "—" if x is None else f"{x:.1f}"


def first(a, k0=K0):
    w = np.flatnonzero(np.asarray(a)[k0:])
    return None if len(w) == 0 else w[0] * DT


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--workers", type=int, default=8)
    a = ap.parse_args()
    t0 = time.time()
    jobs15 = [dict(sc=sc, backend=b, seed=s) for sc in LONG for b in ("rule", "nmpc") for s in range(N_RUN)]
    jobs15 += [dict(sc=sc, backend="off", seed=s, k_drain=1.0) for sc in LAT for s in range(N_RUN)]
    jobs15 += [dict(sc=sc, backend="rule", seed=s) for sc in MANUAL for s in range(N_RUN)]
    jobs15 += [dict(sc=sc, backend=b, seed=s) for sc in NORMAL for b in ("rule", "nmpc") for s in range(N_RUN)]
    jobs15 += [dict(sc="7", backend=b, seed=s) for b in ("rule", "nmpc") for s in range(N_RUN)]
    jobsP = [dict(sc=sc, backend="rule", seed=s) for sc in ("7", "3a", "1", "M1", "M2") for s in range(N_RUN)]
    jobs14w = [dict(sc=sc, backend="off", seed=s) for sc in LAT for s in range(N_RUN)]
    jobs14 = [dict(sc=sc, backend=b, seed=s) for sc in NORMAL for b in ("rule", "nmpc") for s in range(N_RUN)]
    # nmpc 작업을 앞에 두어 전체 시간을 줄인다
    jobs15.sort(key=lambda j: j["backend"] != "nmpc")
    jobs14.sort(key=lambda j: j["backend"] != "nmpc")
    with ProcessPoolExecutor(a.workers) as ex:
        R15 = list(ex.map(run_v15, jobs15, chunksize=1))
    print("v15c", len(R15), "runs", round(time.time() - t0), "s", flush=True)
    with ProcessPoolExecutor(a.workers) as ex:
        RP = list(ex.map(run_prev, jobsP, chunksize=1))
    print("v15b(이전)", len(RP), "runs", round(time.time() - t0), "s", flush=True)
    with ProcessPoolExecutor(a.workers) as ex:
        RW = list(ex.map(run_v14b, jobs14w, chunksize=1))
    print("v14b 데모 (횡방향 회귀)", len(RW), "runs", round(time.time() - t0), "s", flush=True)
    with ProcessPoolExecutor(a.workers) as ex:
        R14 = list(ex.map(run_v14b, jobs14, chunksize=1))
    print("v14b", len(R14), "runs", round(time.time() - t0), "s", flush=True)

    out = dict(note="합성 환경 · 직선 도로 · 시뮬레이션, 신호 = 가정. 시드 0~19, 시나리오당 20회. README 6절 판정.", rows=[])
    md = ["# demo_v15c 검증 (헤드리스, 시드 0~19, 시나리오당 20회)", "",
          "> 합성 환경 · 직선 도로 · 시뮬레이션, 신호 = 가정. 누적기 v14b B14b(되돌림), k_drain 1.3(기본), 키 입력 완화(v15b), 신호 가정 ON. README 2절 판정(코드 전 고정, v15b와 같음). 실패도 그대로 적는다.", ""]
    # 3.1 직접 조작 (주 판정)
    md += ["## 직접 조작에서 신뢰도 회복 (3.1, 주 판정)", "",
           "| 패턴 | 버전 | 탐지 경보 시간 비율 중앙 (p90) | τ 탐지 < 0.8 시간 비율 중앙 (p90) | τ 탐지 평균 중앙 | 자차 반응 run | 판정 (≤ 5% 그리고 ≤ 10%) |",
           "|---|---|---|---|---|---|---|"]
    for sc in MANUAL:
        for ver, RR in (("v15c", R15), ("v15b (이전)", RP)):
            rs = [r for r in RR if r["sc"] == sc]
            fa = np.array([r["alarm_det"][150:].mean() for r in rs])
            fl = np.array([(r["tau_det"][150:] < 0.8).mean() for r in rs])
            tm = np.array([r["tau_det"][150:].mean() for r in rs])
            ok = np.median(fa) <= 0.05 and np.median(fl) <= 0.10
            row = dict(sc=sc, version=ver, alarm_frac_med=float(np.median(fa)), alarm_frac_p90=float(np.percentile(fa, 90)),
                       low_frac_med=float(np.median(fl)), low_frac_p90=float(np.percentile(fl, 90)), tau_mean_med=float(np.median(tm)),
                       reacted=sum(bool(r["reacted"][150:].any()) for r in rs), verdict=("충족" if ok else "미충족") if ver == "v15c" else "(참고)")
            out["rows"].append(row)
            md.append(f"| {MANUAL[sc]} | {ver} | {row['alarm_frac_med']:.1%} ({row['alarm_frac_p90']:.1%}) | {row['low_frac_med']:.1%} ({row['low_frac_p90']:.1%}) | "
                      f"{row['tau_mean_med']:.2f} | {row['reacted']}/20 | **{row['verdict']}** |")
    # 3.1 성분 분해 (README 2절 새 보고)
    md += ["", "### 직접 조작 성분 분해 (v15c)", "", "| 패턴 | τ < 0.8 표본: 최소 성분 누적기 / R1 / R2 | 경보 표본: 누적기 경로 / R1 / R2 (중복 가능) |", "|---|---|---|"]
    for sc in MANUAL:
        rs = [r for r in R15 if r["sc"] == sc]
        low = st = r1 = r2 = al = ap = a1 = a2 = 0
        for r in rs:
            sl = slice(150, None)
            m = r["tau_det"][sl] < 0.8
            ts, t1, t2 = r["tau_state"][sl][m], r["tau_r1"][sl][m], r["tau_r2"][sl][m]
            mn = np.minimum(ts, np.minimum(t1, t2))
            low += int(m.sum())
            st += int((ts == mn).sum())
            r1 += int(((t1 == mn) & (ts != mn)).sum())
            r2 += int(((t2 == mn) & (ts != mn) & (t1 != mn)).sum())
            am = r["alarm_det"][sl]
            al += int(am.sum())
            ap += int(r["alarm_path"][sl][am].sum())
            a1 += int(r["alarm_r1"][sl][am].sum())
            a2 += int(r["alarm_r2"][sl][am].sum())
        pc = lambda x, n: f"{x / n:.0%}" if n else "—"  # noqa: E731
        out["rows"].append(dict(sc=f"comp-{sc}", low=low, low_state=st, low_r1=r1, low_r2=r2, alarm=al, alarm_path=ap, alarm_r1=a1, alarm_r2=a2))
        md.append(f"| {sc} | {pc(st, low)} / {pc(r1, low)} / {pc(r2, low)} (표본 {low}) | {pc(ap, al)} / {pc(a1, al)} / {pc(a2, al)} (표본 {al}) |")
    # 3.2 회복 시간
    def rec(tau, k_end):
        ok = np.asarray(tau[k_end:]) >= 0.8
        run_ = np.convolve(ok.astype(int), np.ones(10, int), "valid") == 10 if len(ok) >= 10 else np.zeros(0, bool)
        return float(np.argmax(run_) * DT) if run_.any() else float(len(ok) * DT)
    md += ["", "## 이상 뒤 τ 탐지 회복 시간 (3.2)", "", "| 이상 | 버전 | 회복 중앙 s (p90) | 판정 (급제동 ≤ 10 s) |", "|---|---|---|---|"]
    for sc, k_end, name in (("7", K0 + 20, "급제동 (40 m 앞)"), ("1", K0 + 35, "급가속 (40 m 앞)")):
        for ver, RR in (("v15c", R15), ("v15b (이전)", RP)):
            rs = [r for r in RR if r["sc"] == sc and r["backend"] == "rule"]
            v = [rec(r["tau_det"], k_end) for r in rs]
            verdict = (("충족" if np.median(v) <= 10 else "미충족") if sc == "7" else "(보고)") if ver == "v15c" else "(참고)"
            out["rows"].append(dict(sc=f"rec-{sc}", version=ver, rec_med=float(np.median(v)), rec_p90=float(np.percentile(v, 90)), verdict=verdict))
            md.append(f"| {name} | {ver} | {np.median(v):.1f} ({np.percentile(v, 90):.1f}) | **{verdict}** |")
    md.append("")
    # 시나리오 1~4
    md += ["## 종방향 (1~4)", "",
           "| # | 시나리오 | 백엔드 | 탐지 경보 run | 반응 경보 run | 자차 반응 run | 최소 τ 중앙 | 최소 τ_eff 중앙 | 반응 경보 지연 중앙 s | 판정 |",
           "|---|---|---|---|---|---|---|---|---|---|"]
    viol_total = clip_total = samp_total = 0
    for sc in LONG:
        for b in ("rule", "nmpc"):
            rs = [r for r in R15 if r["sc"] == sc and r["backend"] == b]
            det = [bool(r["alarm_det"][K0:].any()) for r in rs]
            eff = [bool(r["alarm_eff"][K0:].any()) for r in rs]
            rea = [bool(r["reacted"][K0:].any()) for r in rs]
            if sc in ("1", "2"):
                ok = [d and not e and not x for d, e, x in zip(det, eff, rea)]
            else:
                ok = [e and x for e, x in zip(eff, rea)]
            lat = [first(r["alarm_eff"]) for r in rs if first(r["alarm_eff"]) is not None]
            row = dict(sc=sc, name=LONG[sc][2], backend=b, det=sum(det), eff=sum(eff), reacted=sum(rea), ok=sum(ok),
                       verdict="충족" if sum(ok) == N_RUN else "미충족",
                       tau_min=float(np.median([r["tau_det"][K0:].min() for r in rs])),
                       tau_eff_min=float(np.median([r["tau_eff"][K0:].min() for r in rs])),
                       eff_delay_med=float(np.median(lat)) if lat else None, coll=sum(r["n_coll"] for r in rs))
            out["rows"].append(row)
            md.append(f"| {sc} | {row['name']} | {b} | {row['det']}/20 | {row['eff']}/20 | {row['reacted']}/20 | {row['tau_min']:.2f} | "
                      f"{row['tau_eff_min']:.2f} | {fmt1(row['eff_delay_med'])} | "
                      f"**{row['verdict']}** ({row['ok']}/20) |")
    for r in R15:
        viol_total += int((r["tau_eff"] < r["tau_det"] - 1e-12).sum())
        clip_total += int(r["clipped"].sum())
        samp_total += len(r["clipped"])
    # 시나리오 5
    md += ["", "## 횡방향 (5) — 회귀 (v14b 데모 τ14b와 비교, README 2절)", "",
           "| 이상 | (a) k_drain 1.0 대 v14b 데모 τ 최대 차 | 경보열 일치 run | (b) 탐지 경보 길이 중앙 s (같은 값) | 탐지 경보 지연 중앙 s | 반응 경보 지연 중앙 s | 반응 경보 run | 판정 (a) |",
           "|---|---|---|---|---|---|---|---|"]
    for sc in LAT:
        r10 = sorted([r for r in R15 if r["sc"] == sc and r.get("k_drain") == 1.0], key=lambda r: r["seed"])
        r13 = r10
        r14 = sorted([r for r in RW if r["sc"] == sc], key=lambda r: r["seed"])
        dmax = max(float(np.max(np.abs(a["tau_det"] - b["tau14b"]))) for a, b in zip(r10, r14))
        same = sum(bool(np.array_equal(a["alarm_det"], b["alarm14b"])) for a, b in zip(r10, r14))
        dur = lambda rs: float(np.median([r["alarm_det"][K0:].sum() * DT for r in rs]))  # noqa: E731
        fd = [first(r["alarm_det"]) for r in r13 if first(r["alarm_det"]) is not None]
        fe = [first(r["alarm_eff"]) for r in r13 if first(r["alarm_eff"]) is not None]
        row = dict(sc=sc, name=LAT[sc], max_abs_diff=dmax, alarm_equal=same, verdict="충족" if (dmax < 1e-9 and same == N_RUN) else "미충족",
                   dur13=dur(r13), dur10=dur(r10), det_delay=float(np.median(fd)) if fd else None, eff_delay=float(np.median(fe)) if fe else None,
                   eff_runs=sum(bool(r["alarm_eff"][K0:].any()) for r in r13))
        out["rows"].append(row)
        f1 = lambda x: "—" if x is None else f"{x:.1f}"  # noqa: E731
        md.append(f"| {row['name']} | {dmax:.2e} | {same}/20 | {row['dur13']:.1f} ({row['dur10']:.1f}) | {f1(row['det_delay'])} | {f1(row['eff_delay'])} | "
                  f"{row['eff_runs']}/20 | **{row['verdict']}** |")
    # 시나리오 6
    md += ["", "## 정상 주행 (6) — v15 대 v14b 데모 탐지기(같은 백엔드 반사실)", "",
           "| # | 시나리오 | 백엔드 | v15 탐지 경보 사건 | v15 반응 경보 사건 | v15 자차 반응 run | v14b 경보 사건 | v14b 자차 반응 run | 판정 (v15 ≤ v14b) |",
           "|---|---|---|---|---|---|---|---|---|"]
    for sc in NORMAL:
        for b in ("rule", "nmpc"):
            a15 = [r for r in R15 if r["sc"] == sc and r["backend"] == b]
            a14 = [r for r in R14 if r["sc"] == sc and r["backend"] == b]
            d15 = sum(events(r["alarm_det"]) for r in a15)
            e15 = sum(events(r["alarm_eff"]) for r in a15)
            x15 = sum(bool(r["reacted"].any()) for r in a15)
            d14 = sum(events(r["alarm14b"]) for r in a14)
            x14 = sum(bool(r["reacted"].any()) for r in a14)
            ok = d15 <= d14 and e15 <= d14 and x15 <= x14
            row = dict(sc=sc, name=NORMAL[sc], backend=b, det15=d15, eff15=e15, react15=x15, det14=d14, react14=x14, verdict="충족" if ok else "미충족")
            out["rows"].append(row)
            md.append(f"| {sc} | {row['name']} | {b} | {d15} | {e15} | {x15}/20 | {d14} | {x14}/20 | **{row['verdict']}** |")
    # 시나리오 7 (정지 확인) + 3a 안전 비교
    md += ["", "## 정지 확인 (7) — 40 m 앞 이웃 급제동, 주입 후 70 s (README 2절)", "",
           "| 버전 | 백엔드 | v < 0.5 m/s run | 최저 속도 중앙 m/s | 0.5 m/s 미만 시간 중앙 s (정지 run) | 충돌 run | 최소 간격 중앙 m | 판정 |",
           "|---|---|---|---|---|---|---|---|"]
    for ver, RR, bes in (("v15c", R15, ("rule", "nmpc")), ("v15b (이전)", RP, ("rule",))):
        for b in bes:
            rs = [r for r in RR if r["sc"] == "7" and r["backend"] == b]
            stop = [r for r in rs if (r["v_ego"] < 0.5).any()]
            row = dict(sc="7", version=ver, backend=b, stop_runs=len(stop), vmin_med=float(np.median([r["v_ego"].min() for r in rs])),
                       stop_dur_med=float(np.median([(r["v_ego"] < 0.5).sum() * DT for r in stop])) if stop else 0.0,
                       coll_runs=sum(r["n_coll"] > 0 for r in rs), gap_min_med=float(np.median([r["gap_min"] for r in rs])))
            row["verdict"] = ("충족" if len(stop) == 0 else "미충족") if (ver == "v15c" and b == "rule") else "(참고)"
            out["rows"].append(row)
            md.append(f"| {ver} | {b} | {row['stop_runs']}/20 | {row['vmin_med']:.1f} | {row['stop_dur_med']:.1f} | {row['coll_runs']}/20 | "
                      f"{row['gap_min_med']:.1f} | **{row['verdict']}** |")
    md += ["", "### 3a 앞차 급제동 안전 비교 (rule, 보고만)", "", "| 버전 | 충돌 run | 최소 간격 중앙 m | 최저 속도 중앙 m/s | 자차 반응 run |", "|---|---|---|---|---|"]
    for ver, RR in (("v15c", R15), ("v15b (이전)", RP)):
        rs = [r for r in RR if r["sc"] == "3a" and r["backend"] == "rule"]
        row = dict(sc="3a-safety", version=ver, coll_runs=sum(r["n_coll"] > 0 for r in rs), gap_min_med=float(np.median([r["gap_min"] for r in rs])),
                   vmin_med=float(np.median([r["v_ego"].min() for r in rs])), reacted=sum(bool(r["reacted"][K0:].any()) for r in rs))
        out["rows"].append(row)
        md.append(f"| {ver} | {row['coll_runs']}/20 | {row['gap_min_med']:.1f} | {row['vmin_med']:.1f} | {row['reacted']}/20 |")
    out.update(tau_eff_lt_tau_samples=viol_total, clipped_samples=clip_total, samples=samp_total)
    md += ["", f"- 모든 v15 run 표본에서 τ_eff < τ인 표본: **{viol_total}** / {samp_total}.",
           f"- S′ > S로 잘린 표본(S_eff = min 적용): {clip_total} / {samp_total} ({clip_total / samp_total:.2%}).",
           f"- 실행 시간 {time.time() - t0:.0f} s, 워커 {a.workers}."]
    json.dump(out, open(V15 / "results" / "validate_v15c.json", "w"), ensure_ascii=False, indent=1, default=float)
    open(V15 / "results" / "validate_v15c.md", "w").write("\n".join(md) + "\n")
    print("\n".join(md))


if __name__ == "__main__":
    main()
