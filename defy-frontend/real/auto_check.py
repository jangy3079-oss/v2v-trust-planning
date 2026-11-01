"""서버 단독 확인 (프론트·WS 없이 bridge.Bridge 를 직접 돌린다).

  수동 회귀:  python -X utf8 real/auto_check.py manual --real <real 폴더> --solver rule --out a.jsonl
              python -X utf8 real/auto_check.py compare a.jsonl b.jsonl
  자동 모드:  python -X utf8 real/auto_check.py auto --seed 7 --level normal --minutes 5 [--out sched.jsonl]
              python -X utf8 real/auto_check.py auto-suite --minutes 5
--real 로 다른 폴더(예: baseline worktree 의 defy-frontend/real)의 bridge.py 를 불러와 같은 입력을 넣을 수 있다."""
import argparse
import hashlib
import importlib.util
import json
import os
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
DT = 0.1


def load_bridge(real_dir):
    real_dir = Path(real_dir).resolve()
    os.environ.setdefault("DEFY_V15C", str(real_dir.parent.parent / "demo_v15c"))
    spec = importlib.util.spec_from_file_location("bridge_under_test", real_dir / "bridge.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["bridge_under_test"] = mod
    spec.loader.exec_module(mod)
    b = mod.Bridge()
    b.hello_cal = b.hello()["calibration"]
    return b


# ---- 수동 세션 고정 입력: (초, held, actions)
def manual_script():
    plan = [(10.0, [], [])]                       # 준비 (탐지기 100 표본)
    plan += [(3.0, ["W"], []), (2.0, ["A"], []), (2.0, ["S"], []), (2.0, [], [])]
    for kind in ("drift", "stuck", "weave", "brake", "accel"):
        plan.append((0.1, [], [dict(action="scenario.start", payload=dict(kind=kind))]))
        plan.append((5.9, [], []))
        if kind in ("drift", "stuck", "weave"):
            plan.append((0.1, [], [dict(action="scenario.stop", payload={})]))
            plan.append((3.0, ["D"] if kind != "weave" else [], []))
        else:
            plan.append((2.0, [], []))
    plan.append((0.1, [], [dict(action="signal.toggle", payload=dict(side="left"))]))
    plan.append((1.0, [], [dict(action="signal.toggle", payload=dict(side="left"))]))
    plan.append(("reset", None, None))
    plan.append((5.0, ["W"], []))
    ticks = []
    for dur, held, acts in plan:
        if dur == "reset":
            ticks.append("reset")
            continue
        n = int(round(dur / DT))
        for i in range(n):
            ticks.append((held, acts if i == 0 else []))
    return ticks


def strip_wall(P):
    """비교에서 벽시계 값(solve_ms)만 뺀다."""
    P = json.loads(json.dumps(P))
    nm = P.get("ego_control", {}).get("nmpc")
    if nm:
        nm.pop("solve_ms", None)
    return P


def run_manual(args):
    b = load_bridge(args.real)
    from_bridge = sys.modules["bridge_under_test"]
    b.new(args.solver)
    out = open(args.out, "w", encoding="utf-8")
    tick = -1
    t0 = time.perf_counter()
    for item in manual_script():
        if item == "reset":
            P = b.reset()
            tick = -1
            out.write(json.dumps(dict(reset=True)) + "\n")
            continue
        held, acts = item
        P = from_bridge.clean(b.step(held, acts))
        tick += 1
        ec, m = P["ego_control"], P["model"]
        rec = dict(tick=tick, nb_y=P["vehicles"]["neighbor"]["y_m"], ego=[P["vehicles"]["ego"][k] for k in ("x_m", "y_m", "psi_rad", "v_mps")],
                   alarm_det=m["alarms"]["alarm_det"], alarm_eff=m["alarms"]["alarm_eff"], state=ec["state"],
                   scenario=P["scenario"]["kind"] if P["scenario"] else None,
                   solve_ms=ec["nmpc"]["solve_ms"] if ec["nmpc"] else None,
                   full=hashlib.sha256(json.dumps(strip_wall(P), sort_keys=True).encode()).hexdigest()[:16])
        out.write(json.dumps(rec) + "\n")
    out.close()
    print(f"[manual] {args.real} solver={args.solver} → {args.out} ({time.perf_counter() - t0:.1f}s)")


def compare(args):
    A = [json.loads(l) for l in open(args.a, encoding="utf-8")]
    B = [json.loads(l) for l in open(args.b, encoding="utf-8")]
    if len(A) != len(B):
        print(f"줄 수 다름: {len(A)} vs {len(B)}")
    fields = ("nb_y", "ego", "alarm_det", "alarm_eff", "state", "scenario", "full")
    first = {}
    for i, (a, c) in enumerate(zip(A, B)):
        for f in fields:
            if a.get(f) != c.get(f) and f not in first:
                first[f] = (i, a.get("tick"), a.get(f), c.get(f))
    for name, rows in (("A", A), ("B", B)):
        ms = [r["solve_ms"] for r in rows if r.get("solve_ms") is not None]
        if ms:
            print(f"solve_ms {name}: 평균 {sum(ms) / len(ms):.2f} · 최대 {max(ms):.2f} · 100ms 초과 {sum(x > 100 for x in ms)} / {len(ms)}")
    if not first:
        print(f"차이 없음 ({len(A)}줄, 필드 {', '.join(fields)})")
        return 0
    for f, (i, tk, va, vb) in sorted(first.items(), key=lambda x: x[1][0]):
        print(f"첫 차이 {f}: 줄 {i} tick {tk}: {va} vs {vb}")
    return 1


# ---- 자동 모드
def run_auto(seed, level, minutes, solver="rule", real=HERE, quiet=False, sid="check"):
    """실제 서버와 같은 기록 파일(real/logs/auto_<세션>_<seed>.jsonl)을 남기며 운전자만 돌린다."""
    b = load_bridge(real)
    log = Path(real) / "logs" / f"auto_{sid}_{seed}.jsonl"
    log.unlink(missing_ok=True)
    if True:
        b.new(solver, dict(seed=seed, level=level, session_id=sid))
        drv = b.driver
        LAT = ("drift", "stuck", "weave")
        n = int(minutes * 60 / DT)
        bad_ad, min_margin, levels, states, departures = [], 9.9, set(), {}, 0
        for _ in range(n):
            P = b.step([], [])
            sc = P["scenario"]
            if sc and sc["kind"] in LAT and set(P["auto_held"]) & {"A", "D"}:
                bad_ad.append(P["auto"]["log"][-1:] if P["auto"]["log"] else sc["kind"])
            y = P["vehicles"]["neighbor"]["y_m"]
            min_margin = min(min_margin, 5.25 - (abs(y) + 0.9))
            departures += 5.25 - (abs(y) + 0.9) < 0  # 차체가 도로 끝을 넘은 틱
            levels.add(P["auto"]["level"])
            states[P["auto"]["state"]] = states.get(P["auto"]["state"], 0) + 1
        recs = [json.loads(l) for l in open(drv.log_path, encoding="utf-8")]
    starts = [r for r in recs if r["type"] == "note" and r["text"].startswith("이벤트:")]
    kinds = {}
    for r in starts:
        kinds[r["event"]] = kinds.get(r["event"], 0) + 1
    res = dict(seed=seed, level=level, minutes=minutes, events=len(starts), kinds=kinds, bad_ad=len(bad_ad), min_margin=round(min_margin, 3), departures=departures,
               log=str(drv.log_path), switches=sum(1 for r in recs if r.get("text", "").startswith("위험도")),
               levels=sorted(levels), states=states, coll=P["ego_control"]["collision_count"],
               skipped=sum(1 for r in recs if "건너뜀" in r.get("text", "")), timeouts=sum(1 for r in recs if "초과" in r.get("text", "")))
    if not quiet:
        print(json.dumps(res, ensure_ascii=False))
    return res, recs


def sched_key(recs):
    return [{k: v for k, v in r.items() if k not in ("session_id",)} for r in recs]


def event_seq(recs):
    return [(r["tick"], r["t_s"], r["text"]) for r in recs if r["type"] == "note"]


def auto_suite(args):
    m = args.minutes
    r1, s1 = run_auto(7, "normal", m, quiet=True, sid="checkA")
    r2, s2 = run_auto(7, "normal", m, quiet=True, sid="checkB")
    e1, e2 = event_seq(s1), event_seq(s2)
    print(f"같은 시드 두 번 (기록 파일 {Path(r1['log']).name} / {Path(r2['log']).name}): 이벤트 순서·시각 {'일치' if e1 == e2 else '불일치'} ({len(e1)}건), "
          f"입력까지 전체 {'일치' if sched_key(s1) == sched_key(s2) else '불일치'} ({len(s1)}줄)")
    r3, s3 = run_auto(8, "normal", m, quiet=True, sid="checkC")
    print(f"다른 시드(8): {'다름' if sched_key(s3) != sched_key(s1) else '같음(!)'}")
    out = {}
    for lv in ("low", "normal", "high", "random"):
        r, _ = run_auto(7, lv, m, quiet=True, sid=f"check{lv}") if lv != "normal" else (r1, s1)
        out[lv] = r
        print(json.dumps(r, ensure_ascii=False))
    ok = out["low"]["events"] < out["normal"]["events"] < out["high"]["events"]
    print(f"이벤트 횟수 low {out['low']['events']} < normal {out['normal']['events']} < high {out['high']['events']}: {'통과' if ok else '실패'}")
    print(f"random 단계 전환: {out['random']['levels']}")


def handback_check(args):
    """사람 개입 정리: 운전자가 건 횡 시나리오는 0 으로 해제, 운전자가 켠 깜빡이는 끔, 그 스텝부터 사람 키만."""
    b = load_bridge(HERE)
    out = {}
    for want in ("lateral", "signal"):
        found = None
        for seed in range(1, 40):
            b.new("rule", dict(seed=seed, level="high", session_id="handback", log=False))
            for _ in range(int(10 * 60 / DT)):
                P = b.step([], [])
                d, sc = b.driver, P["scenario"]
                if want == "lateral" and sc and sc["kind"] in ("drift", "stuck", "weave") and d.phase == "run":
                    found = seed; break
                if want == "signal" and d.my_signal and P["vehicles"]["neighbor"]["signal_side"] != "off":
                    found = seed; break
            if found:
                break
        P = b.step(["W"], [], "human_input")
        out[want] = dict(seed=found, scenario_after=P["scenario"]["kind"] if P["scenario"] else None, auto_stop=P["auto_stop"],
                         signal_after=P["vehicles"]["neighbor"]["signal_side"], held_used=P["auto_held"], state=P["auto"]["state"],
                         reason=P["auto"]["stopped_reason"])
        print(want, json.dumps(out[want], ensure_ascii=False))
    ok = (out["lateral"]["scenario_after"] is None and out["lateral"]["auto_stop"] and out["signal"]["signal_after"] == "off"
          and all(o["held_used"] == ["W"] and o["state"] == "manual" for o in out.values()))
    print("수동 복귀 정리:", "통과" if ok else "실패")


def main():
    ap = argparse.ArgumentParser()
    sp = ap.add_subparsers(dest="mode", required=True)
    a = sp.add_parser("manual"); a.add_argument("--real", default=str(HERE)); a.add_argument("--solver", default="rule"); a.add_argument("--out", required=True)
    c = sp.add_parser("compare"); c.add_argument("a"); c.add_argument("b")
    u = sp.add_parser("auto"); u.add_argument("--seed", type=int, default=7); u.add_argument("--level", default="normal")
    u.add_argument("--minutes", type=float, default=5); u.add_argument("--solver", default="rule"); u.add_argument("--out")
    s = sp.add_parser("auto-suite"); s.add_argument("--minutes", type=float, default=5)
    sp.add_parser("handback")
    args = ap.parse_args()
    if args.mode == "manual":
        run_manual(args)
    elif args.mode == "compare":
        sys.exit(compare(args))
    elif args.mode == "auto":
        _, recs = run_auto(args.seed, args.level, args.minutes, args.solver)
        if args.out:
            with open(args.out, "w", encoding="utf-8") as f:
                for r in recs:
                    f.write(json.dumps(r, ensure_ascii=False) + "\n")
    elif args.mode == "handback":
        handback_check(args)
    else:
        auto_suite(args)


if __name__ == "__main__":
    main()
