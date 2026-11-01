"""15c 브리지: demo_v15c 의 Session 을 JSON 줄 단위로 감싼다 (Node 서버 real/server.ts 가 자식 프로세스로 띄운다).
계약(src/contracts.ts)의 Snapshot 중 '시뮬레이션이 정하는 부분'만 만들고, 세션 상태(phase·튜토리얼·입력 커서·이벤트)는 Node 쪽이 맡는다.
합성 · 직선 · 시뮬레이션. 15c 코드는 읽기만 하고 고치지 않는다."""
import json
import math
import os
import sys
import traceback
from pathlib import Path

HERE = Path(__file__).resolve().parent
V15C = Path(os.environ.get("DEFY_V15C", HERE.parent.parent / "demo_v15c")).resolve()
sys.path.insert(0, str(V15C))
if (V15C.parent / "src").is_dir():  # 저장소 배치: v2v_trust_planning 패키지는 <저장소>/src 에 있다
    sys.path.insert(1, str(V15C.parent / "src"))
sys.dont_write_bytecode = True

import numpy as np  # noqa: E402

DT = 0.1
ZONE = {"앞": "front", "뒤": "rear", "나란히": "alongside"}
KEY_OF_KIND = {"drift": "1", "stuck": "2", "weave": "3", "brake": "4", "accel": "5"}
STATES = ("NORMAL", "AVOIDING", "RETURNING")


def clean(x):
    """JSON 에 넣을 수 있게: numpy → 파이썬, NaN/inf → None."""
    if isinstance(x, dict):
        return {k: clean(v) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [clean(v) for v in x]
    if isinstance(x, np.ndarray):
        return clean(x.tolist())
    if isinstance(x, (np.bool_, bool)):
        return bool(x)
    if isinstance(x, (np.integer, int)):
        return int(x)
    if isinstance(x, (np.floating, float)):
        v = float(x)
        return v if math.isfinite(v) else None
    return x


def fin(v):
    v = float(v)
    return v if math.isfinite(v) else None


class Bridge:
    def __init__(self):
        import app as A  # noqa: E402  (demo_v15c/app.py, torch 를 불러온다)
        import sim as SM
        import torch
        torch.set_num_threads(1)  # app.py 실행 때와 같게 (멀티스레드 추론의 비트 차이 방지)
        self.A, self.SM = A, SM
        self.ses = None
        self.prev_u = (0.0, 0.0)
        self.driver = None

    # ---- 구성
    def hello(self):
        try:
            import casadi  # noqa: F401
            solvers = ["rule", "nmpc"]
        except Exception:
            solvers = ["rule"]
        s = self.A.Session()
        c = s.det.cfg
        return dict(available_solvers=solvers, calibration=dict(
            setting="nom", model_name="B1_sd0.23_s0", theta=s.acc.theta, nu=s.acc.nu, h=s.acc.h, h_d=c["h_d"], T_c_s=c["T_c"],
            r2_calib_ok=bool(c["r2_calib_ok"]), hold_s=3, decay_time_s=20, cap_theta_multiple=2,
            source_ids=["demo_v15c/scratch_v14b/results/eval_v14b.json"]))

    def new(self, solver, auto=None):
        self.ses = self.A.Session(solver=solver)
        self.prev_u = (0.0, 0.0)
        self.driver = None
        if auto:  # 자동 모드 (명세 1.3). 수동 세션은 driver 없이 기존 경로 그대로
            if str(HERE) not in sys.path:
                sys.path.append(str(HERE))  # -I 로 실행되어 스크립트 폴더가 경로에 없다. 15c 모듈보다 뒤에 둔다
            from auto_driver import AutoDriver
            log = HERE / "logs" / f"auto_{auto.get('session_id', 'local')}_{auto['seed']}.jsonl" if auto.get("log", True) else None
            self.driver = AutoDriver(auto["seed"], auto.get("level", "normal"), log_path=log,
                                     meta=dict(session_id=auto.get("session_id"), solver=solver), SM=self.SM)
        return self.parts(False)

    def reset(self):
        self.ses.reset()
        self.prev_u = (0.0, 0.0)
        if self.driver:
            self.driver.reset()
        return self.parts(False)

    def auto_halt(self, reason):
        if self.driver:
            self.driver.halt(self.ses, reason)
        return None

    # ---- 한 스텝
    def step(self, held, actions, takeover=None):
        ses = self.ses
        if self.driver:
            return self.auto_step(held, actions, takeover)
        for a in actions:
            act, pl = a["action"], a.get("payload", {})
            if act == "signal.toggle":
                ses.key("Q" if pl["side"] == "left" else "E")
            elif act == "scenario.start":
                ses.key(KEY_OF_KIND[pl["kind"]])
            elif act == "scenario.stop":
                ses.key("0")
        self.prev_u = ses.u if ses.u is not None else (0.0, 0.0)
        ses.step(set(held))
        return self.parts(True)

    def apply(self, actions):
        ses = self.ses
        for a in actions:
            act, pl = a["action"], a.get("payload", {})
            if act == "signal.toggle":
                ses.key("Q" if pl["side"] == "left" else "E")
            elif act == "scenario.start":
                ses.key(KEY_OF_KIND[pl["kind"]])
            elif act == "scenario.stop":
                ses.key("0")

    def auto_step(self, held, actions, takeover):
        """자동 세션 한 스텝. 운전자 조종 중이면 운전자 키만, 개입한 스텝부터는 사람 입력만 쓴다 (섞지 않는다)."""
        ses, drv = self.ses, self.driver
        stop = False
        if takeover and drv.state != "manual":
            for k in drv.handback(ses, takeover):  # 운전자가 건 것만 정리 (0 / 깜빡이 끔)
                stop |= k == "0"
                ses.key(k)
        if drv.state == "manual":
            self.apply(actions)
            used = set(held)
        else:
            keys, used = drv.decide(ses)
            for k in keys:
                stop |= k == "0"
                ses.key(k)
        self.prev_u = ses.u if ses.u is not None else (0.0, 0.0)
        ses.step(used)
        P = self.parts(True)
        P["auto_held"] = sorted(used)
        P["auto_stop"] = stop
        return P

    # ---- Snapshot 중 시뮬레이션이 정하는 부분
    def veh(self, v, sig):
        x, y, th, sp = (float(t) for t in v.state)
        return dict(x_m=x, y_m=y, psi_rad=th, v_mps=sp, a_mps2=float(v.a), steer_rad=float(v.delta), yaw_rate_radps=float(v.r),
                    length_m=4.5, width_m=1.8, signal_side=sig)

    def plant(self, v, sampled):
        last = v.last
        return dict(last=None if not sampled or not last else {k: (dict(last[k]) if k == "clips" else float(last[k])) for k in
                    ("a", "delta", "r", "a_lat", "jerk_max", "a_lat_max", "r_max", "delta_rate_max", "rate_violation", "slip_max_deg", "clips")},
                    counts=dict(v.counts))

    def scenario(self):
        sim, SM = self.ses.sim, self.SM
        j = sim.inj
        if j is None:
            return None
        kind, lv = j["kind"], j["level"]
        p = {"drift": dict(kind="drift", lateral_speed_mps=float(lv) if kind == "drift" else 0, ramp_s=2),
             "stuck": dict(kind="stuck", r0_radps=float(lv) if kind == "stuck" else 0),
             "weave": dict(kind="weave", amplitude_m=float(lv[0]) if kind == "weave" else 0, period_s=float(lv[1]) if kind == "weave" else 0, ramp_s=7.5),
             "brake": dict(kind="brake", added_accel_mps2=-6, duration_s=2),
             "accel": dict(kind="accel", added_accel_mps2=float(SM.ACCEL_A), ramp_s=float(SM.ACCEL_R), hold_s=float(SM.ACCEL_HOLD * DT))}[kind]
        return dict(kind=kind, parameters=p, start_tick=int(j["k0"] - 1), start_sample_time_s=(j["k0"] - 1) * DT,
                    elapsed_s=max(0.0, (sim.k - j["k0"] + 1) * DT), direction_sign=int(j["sign"]),
                    lane_center_at_start_m=float(j["lane_c"]))

    def report(self):
        """가장 최근 주입의 지연 (시나리오가 끝나면 Node 가 last_scenario_report 로 쓴다)."""
        ses = self.ses
        if not ses.injections:
            return None
        j = ses.injections[-1]
        r = ses.inj_report(j)
        sc = self.scenario_params_of(j)
        d = r["delay"]
        return dict(kind=j["kind"], parameters=sc, start_sample_time_s=(j["k0"] - 1) * DT, boundary_cross_delay_s=r["cross"],
                    detection_delay_s=d.get("탐지"), reaction_delay_s=d.get("반응"), a2_delay_s=d.get("A2"))

    def scenario_params_of(self, j):
        SM = self.SM
        kind, lv = j["kind"], j["level"]
        return {"drift": dict(kind="drift", lateral_speed_mps=float(lv) if kind == "drift" else 0, ramp_s=2),
                "stuck": dict(kind="stuck", r0_radps=float(lv) if kind == "stuck" else 0),
                "weave": dict(kind="weave", amplitude_m=float(lv[0]) if kind == "weave" else 0, period_s=float(lv[1]) if kind == "weave" else 0, ramp_s=7.5),
                "brake": dict(kind="brake", added_accel_mps2=-6, duration_s=2),
                "accel": dict(kind="accel", added_accel_mps2=float(SM.ACCEL_A), ramp_s=float(SM.ACCEL_R), hold_s=float(SM.ACCEL_HOLD * DT))}[kind]

    def parts(self, sampled):
        ses, SM = self.ses, self.SM
        sim, o, out = ses.sim, ses.obs, ses.out
        sig = "left" if sim.sig_l else "right" if sim.sig_r else "off"
        cal = self.hello_cal
        P = dict(
            vehicles=dict(ego=self.veh(sim.ego_vehicle, "off"), neighbor=self.veh(sim.neighbor_vehicle, sig)),
            observation=None, model=None,
            plant=dict(ego=self.plant(sim.ego_vehicle, sampled), neighbor=self.plant(sim.neighbor_vehicle, sampled),
                       neighbor_command=None if not sampled else dict(a_mps2=float(o["nb_command"][0]), steer_rad=float(o["nb_command"][1]))),
            scenario=self.scenario(), report=self.report(),
            next_weave=dict(amplitude_m=SM.WEAVE_LEVELS[sim.weave_i % len(SM.WEAVE_LEVELS)][0], period_s=SM.WEAVE_LEVELS[sim.weave_i % len(SM.WEAVE_LEVELS)][1]),
            a_user_mps2=float(sim.a_user), r_cmd_radps=float(sim.r_cmd),
        )
        if sampled:
            P["observation"] = dict(a_obs_mps2=o["a_obs"], r_obs_radps=o["r_obs"], d_obs_m=o["d_obs"], v_meas_mps=o["v_meas"], y_obs_m=o["y_obs"],
                                    d_true_m=o["d_true"], sent_signal=1 if ses.sent_signal() else 0)
            ts = out["R2_ttb"]
            P["model"] = dict(
                trust={k: out[k] for k in ("tau_det", "tau_eff", "tau_a2", "tau_rule", "tau_gru", "tau_r1", "tau_r2", "tau_state", "tau_state_eff")},
                alarms={k: bool(out[k]) for k in ("alarm_det", "alarm_eff", "alarm_a2", "alarm_gru", "alarm_rule", "alarm_r1", "alarm_r2",
                                                  "alarm_r2_shadow", "alarm_path", "alarm_eff_path")},
                scores=dict(e=out["e"], e_long=out["e_long"], e_lat=out["e_lat"], r_a_mps2=out["r_a"], R1_S=out["R1_S"],
                            R2_ttb_s=fin(ts), R2_ttb_reason="finite" if math.isfinite(ts) else "no_crossing_within_horizon",
                            d_hat_m=out["d_hat"], psi_hat_rad=out["psi_hat"]),
                accumulator=dict(S=out["S"], S_prime=out["S_prime"], S_eff=out["S_eff"], clipped=bool(out["clipped"]), hold_on=bool(out["hold_on"])),
                context=dict(active=bool(out["active"]), intent_window=bool(out["intent_window"]), zone=ZONE.get(out["zone"], "alongside"),
                             dx_m=out["dx"], g=bool(out["g"]), r2_enabled=bool(ses.det.r2_enabled), r2_calib_ok=bool(ses.det.cfg["r2_calib_ok"])),
                calibration=cal)
        P["ego_control"] = self.ego_control(sampled)
        if self.driver:
            P["auto"] = self.driver.status(ses)
        return P

    def ego_control(self, sampled):
        ses = self.ses
        ctl, info, rec = ses.ctl, ses.ctl.info, ses.ego_rec
        st = str(info.get("state", "NORMAL")).upper()
        st = st if st in STATES else "NORMAL"
        u = ses.u if ses.u is not None else (0.0, 0.0)
        ttc = fin(rec.get("ttc", float("inf"))) if sampled else None
        pttc = fin(info.get("ttc", float("inf"))) if sampled else None
        pol = ctl.policy
        nm = None
        if ctl.solver == "nmpc":
            xp = ctl.x_pred
            traj = [] if xp is None else [dict(offset_s=i * DT, x_m=float(xp[0, i]), y_m=float(xp[1, i]), psi_rad=float(xp[2, i]), v_mps=float(xp[3, i]))
                                          for i in range(xp.shape[1])]
            nm = dict(state=st, solve_ms=float(info.get("solve_ms", 0.0)), fallback_count=int(info.get("fallback", 0)),
                      fallback_now=bool(info.get("fallback_now", False)), d_safe_m=fin(info["d_safe"]) if sampled and "d_safe" in info else None,
                      target_y_m=float(pol.target_y) if sampled else None, predicted_trajectory=traj)
        gap_min = rec.get("gap_min", float("inf"))
        return dict(
            control_mode="combined", solver=ctl.solver, state=st,
            command_applied=dict(a_mps2=float(self.prev_u[0]), steer_rad=float(self.prev_u[1])),
            command_next=dict(a_mps2=float(u[0]), steer_rad=float(u[1])),
            reacted=bool(rec.get("reacted")), engaged=bool(rec.get("engaged")), hit=bool(rec.get("hit")), collision_count=int(rec.get("n_coll", 0)),
            dx_m=float(rec.get("dx", 0.0)), dy_m=float(rec.get("dy", 0.0)), gap_long_m=float(rec.get("g_lon", 0.0)), gap_lat_m=float(rec.get("g_lat", 0.0)),
            gap_m=float(rec.get("gap", 0.0)) if math.isfinite(rec.get("gap", float("inf"))) else 0.0, gap_min_m=fin(gap_min) if sampled else None,
            ttc_s=ttc, ttc_reason=("not_sampled" if not sampled else "finite" if ttc is not None else "not_ahead_or_not_closing"),
            policy=dict(stage=int(info.get("stage", 0)), closing_mps=float(info.get("closing", 0.0)), danger=bool(info.get("danger", False)),
                        rear_only=bool(info.get("rear_only", False)), emergency=bool(info.get("emergency", False)),
                        target_y_m=float(info.get("target_y", 0.0)), policy_target_y_m=float(pol.target_y), nmpc_override=pol.ov is not None,
                        v_ref_mps=float(info.get("v_ref", ses.ctl.v_set)), brake_mps2=float(info.get("brake", 0.0) or 0.0),
                        ttc_s=pttc, ttc_reason=("not_sampled" if not sampled else "finite" if pttc is not None else "no_predicted_contact"),
                        raw_a_mps2=fin(info["raw_a"]) if sampled and "raw_a" in info else None,
                        raw_steer_rad=fin(info["raw_delta"]) if sampled and "raw_delta" in info else None,
                        command_clips=int(info.get("command_clips", 0))),
            nmpc=nm)


def main():
    b = Bridge()
    b.hello_cal = b.hello()["calibration"]
    out = sys.stdout
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        req = json.loads(line)
        try:
            cmd = req["cmd"]
            if cmd == "hello":
                data = b.hello()
            elif cmd == "new":
                data = b.new(req.get("solver", "rule"), req.get("auto"))
            elif cmd == "reset":
                data = b.reset()
            elif cmd == "step":
                data = b.step(req.get("held", []), req.get("actions", []), req.get("takeover"))
            elif cmd == "auto_halt":
                data = b.auto_halt(req.get("reason", ""))
            else:
                raise ValueError(f"unknown cmd {cmd}")
            resp = dict(id=req["id"], ok=True, data=clean(data))
        except Exception:
            resp = dict(id=req["id"], ok=False, error=traceback.format_exc())
        out.write(json.dumps(resp, ensure_ascii=False) + "\n")
        out.flush()


if __name__ == "__main__":
    main()
