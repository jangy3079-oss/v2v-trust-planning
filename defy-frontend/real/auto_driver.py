"""자동 모드(가상 운전자, 명세 1.3 · v1.5). 시연자와 같은 입구(WASD 눌림 집합, Q/E, 1~5/0)만 쓴다. 이웃 상태를 직접 바꾸지 않는다.
흐름: cruise → wait → event(접근 → 이벤트) → recover → wait … · 사람이 개입하면 manual (R 로 자동 복귀).
결정은 틱 단위이고 난수는 auto_seed 로 시드한 PRNG 만 쓴다 → 같은 시드·같은 solver·같은 단계면 같은 행동 스케줄."""
import json
import math
import random
from collections import deque
from pathlib import Path

# ---- 조정값 (명세 1.3: 한 곳에 둔다. 초기값, 실행하며 조정)
AUTO = dict(
    interval_s=dict(low=(15.0, 30.0), normal=(8.0, 20.0), high=(4.0, 10.0)),  # 회복 완료 뒤 다음 이벤트까지 (균등)
    random_switch_s=(30.0, 60.0),        # random: 이 간격(균등)마다 low/normal/high 중 다른 단계로
    cut_in_signal_p=dict(low=1.0, normal=0.7, high=0.3),  # cut_in 에서만 방향지시등을 켤 확률 (위험할수록 덜 켠다)
    events=("drift", "stuck", "weave", "brake", "accel", "cut_in"),  # 균등
    cruise_s=10.0,                       # 첫 대기 전 보통 주행 (탐지기 준비 100 표본과 같은 길이)
    scenario_s=dict(drift=8.0, stuck=4.0, weave=12.0),  # 횡방향 시나리오는 이 시간 뒤 0 (brake/accel 은 원본이 끝낸다)
    road_edge_m=5.25,                    # 바깥 차로 경계 |y| (3차로 × 3.5 m 의 절반, 명세 1절)
    half_width_m=0.9,                    # 차폭 1.8 m 의 절반
    # 가장자리 여유 하한. 명세 초기값 1.0 m 는 바깥 차로 중심(|y|=3.5, 여유 0.85 m)보다 커서 기본 위치에서부터 걸린다.
    # 바깥 차로 중심 +0.35 m(|y|=3.85)까지 허용하도록 0.5 m 로 둔다.
    edge_min_m=0.5,
    lane_keep_m=(0.5, 0.15),             # 차로 유지: 중심에서 이만큼 벗어나면 조향 시작, 이 안이면 멈춤
    steer_deadband_m=0.15,               # 횡 목표 추종: 예측 정지 위치 오차 데드밴드
    steer_predict_s=(3.0, 2.0),          # 예측 정지 위치 y + v·(Tψ·ψ + Tr·r_cmd) 의 (Tψ, Tr) [s]. 키 반전 램프·차량 지연까지 덮도록 크게 (seed 7~11 × normal/high 에서 최소 여유가 가장 큼)
    gap_cruise_m=40.0, gap_hold_m=8.0,   # 평소 앞뒤 간격 목표(자차 기준 dx)와 그대로 두는 폭
    gap_approach_m=25.0, gap_approach_tol_m=5.0, approach_timeout_s=10.0,  # 이벤트 전 접근
    speed_gain=0.15, speed_rel_max=2.5, speed_deadband=0.5,  # 간격 → 원하는 상대 속도 [1/s, m/s, m/s]
    align_timeout_s=8.0,                 # 횡방향 시나리오 전 시작 위치 맞추기, 넘으면 이번 이벤트 건너뜀
    # 위빙은 자차 앞(접근 거리), 자차 차로 중심에서 시작한다. 표의 진폭은 목표값이고 실제 흔들림은 한쪽 최대 약 2.6 m(1.2 m 단계,
    # 명세 2.3)라 바깥 차로에서는 도로를 벗어나지만, 가운데 차로는 양쪽 여유가 5.25 − 0.9 = 4.35 m 라 단계와 관계없이 끝까지 흔들 수 있다.
    # 바깥 차로에서 자차 차로까지 3.5 m 를 옮기는 제한 시간
    weave_align_timeout_s=20.0,
    # 안전망: 지금 풀었을 때 방향각 정렬(원본 Tψ)로 멈출 위치가 도로 밖이면 흔들림 도중이라도 해제한다.
    # 키보드 예측 계수(steer_predict_s)는 위빙 방향각에서 크게 넘쳐 잡혀 위빙에는 쓰지 않는다
    signal_lead_s=1.0,                   # cut_in: 깜빡이 켠 뒤 조향까지
    cut_in_steer_timeout_s=8.0, cut_in_hold_s=3.0,
    recover_tol_m=0.5,                   # 회복 완료: 자기 차로 중심 ±0.5 m (명세 1.3)
    recover_timeout_s=60.0,              # 회복이 이보다 오래 걸리면 기록하고 대기로 넘어간다
    log_ui=20,
)
LATERAL = ("drift", "stuck", "weave")
KEY_OF = {"drift": "1", "stuck": "2", "weave": "3", "brake": "4", "accel": "5"}
NAME = {"drift": "드리프트", "stuck": "조향 고정", "weave": "위빙", "brake": "급제동", "accel": "급가속", "cut_in": "차로 진입"}
LEVEL_NAME = {"low": "낮음", "normal": "보통", "high": "높음"}
LEVELS = ("low", "normal", "high")


class AutoDriver:
    def __init__(self, seed, level_setting="normal", log_path=None, meta=None, SM=None):
        self.seed, self.level_setting = int(seed), level_setting
        self.SM = SM
        self.W, self.DT = SM.W, SM.DT
        self.T_PSI, self.R_REL = SM.OLD.T_PSI, SM.OLD.R_REL
        self.log_path = Path(log_path) if log_path else None
        self.meta = dict(meta or {})
        self.generation = 0
        self.reset()

    # ---- 상태 초기화 (R: 같은 설정·같은 시드)
    def reset(self):
        self.rng = random.Random(self.seed)
        self.rng_level = random.Random(self.seed * 2654435761 % 2**32 + 1)  # 단계 전환 전용 흐름 (이벤트 순서와 분리)
        self.state, self.stopped_reason = "cruise", None
        self.level = self.level_setting if self.level_setting in LEVELS else self.rng_level.choice(LEVELS)
        self.level_until = None  # random: 첫 running 스텝에서 정한다
        self.event, self.phase, self.phase_k = None, None, 0
        self.wait_until = None
        self.home_c = None                   # 이벤트 전 자기 차로 중심 (회복 목표)
        self.target_y = None
        self.my_inj_k0 = None                # 운전자가 건 주입의 k0
        self.my_signal = None                # 운전자가 켠 깜빡이 'Q' | 'E'
        self.lane_keeping = False
        self.prev_held = ()
        self.halted = None
        self.ui_log = deque(maxlen=AUTO["log_ui"])
        self.generation += 1
        self.record(dict(type="start", seed=self.seed, level_setting=self.level_setting, level=self.level, generation=self.generation, **self.meta), t=0.0)

    # ---- 기록
    def say(self, t, text):
        self.ui_log.append(dict(t_s=round(t, 4), text=text))

    def record(self, rec, t):
        if not self.log_path:
            return
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        with self.log_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(dict(t_s=round(t, 4), **rec), ensure_ascii=False) + "\n")

    def note(self, t, k, text, **extra):
        # extra 의 kind: select(이벤트 고름) / start(시작) / skip(건너뜀) / end(끝, reason) — 집계용 (auto_check)
        self.say(t, text)
        self.record(dict(type="note", tick=k, state=self.state, event=self.event, level=self.level, text=text, **extra), t)

    # ---- 읽는 값 (참 상태)
    def view(self, ses):
        sim = ses.sim
        self.sim_weave_i = int(sim.weave_i)  # 다음 위빙 강도 (누를 때마다 순환)
        x, y, psi, v = (float(t) for t in sim.neighbor_vehicle.state)
        ex, ey, _, ev = (float(t) for t in sim.ego)
        W = self.W
        r_cmd = float(sim.keyboard.r_cmd)
        tp, tr = AUTO["steer_predict_s"]
        y_stop = y + v * (tp * psi + tr * r_cmd)  # 지금 키를 떼면 멈출 y (횡 운동량 포함)
        edge = lambda yy: AUTO["road_edge_m"] - (abs(yy) + AUTO["half_width_m"])
        return dict(x=x, y=y, psi=psi, v=v, dx=x - ex, ego_v=ev, ego_c=W * round(ey / W), lane_c=W * round(y / W),
                    margin=edge(y), y_stop=y_stop, margin_pred=min(edge(y), edge(y_stop)), r_cmd=r_cmd,
                    a_user=float(sim.a_user), inj=sim.inj, sig="Q" if sim.sig_l else "E" if sim.sig_r else None,
                    ego_state=str(ses.ctl.info.get("state", "NORMAL")).upper())

    def edge_margin(self, ses):
        return AUTO["road_edge_m"] - (abs(float(ses.sim.neighbor_vehicle.state[1])) + AUTO["half_width_m"])

    # ---- 키 생성 보조
    def steer_to(self, s, y_tgt):
        """예측 정지 위치(키를 지금 떼면 멈출 y)로 A/D 결정. A=+y(왼쪽), D=−y."""
        err = y_tgt - s["y_stop"]
        db = AUTO["steer_deadband_m"]
        return "A" if err > db else "D" if err < -db else None

    def lane_keep(self, s, c):
        off = abs(s["y"] - c)
        start, stop = AUTO["lane_keep_m"]
        if off > start:
            self.lane_keeping = True
        elif off < stop:
            self.lane_keeping = False
        return self.steer_to(s, c) if self.lane_keeping else None

    def speed_to(self, s, dx_tgt, hold):
        want = 0.0 if abs(s["dx"] - dx_tgt) < hold else max(-AUTO["speed_rel_max"], min(AUTO["speed_rel_max"], AUTO["speed_gain"] * (dx_tgt - s["dx"])))
        rel = s["v"] + s["a_user"] * 1.0 - s["ego_v"]  # 키를 떼면 2초 램프로 줄어드는 만큼(면적 a·1 s)까지 예측
        db = AUTO["speed_deadband"]
        return "W" if rel < want - db else "S" if rel > want + db else None

    def guard(self, s, steer):
        """도로 이탈 방지: 여유(지금 또는 예측 정지 위치)가 하한보다 작으면 바깥쪽 키 금지·안쪽으로."""
        if s["margin_pred"] >= AUTO["edge_min_m"]:
            return steer
        return "D" if s["y"] > 0 else "A"

    # ---- 단계 (random)
    def update_level(self, t, k):
        if self.level_setting != "random":
            return
        if self.level_until is None:
            self.level_until = t + self.rng_level.uniform(*AUTO["random_switch_s"])
            return
        if t >= self.level_until:
            old = self.level
            self.level = self.rng_level.choice([x for x in LEVELS if x != old])
            self.level_until = t + self.rng_level.uniform(*AUTO["random_switch_s"])
            self.note(t, k, f"위험도 {LEVEL_NAME[old]} → {LEVEL_NAME[self.level]}", level_from=old)

    def start_wait(self, t, k):
        self.state, self.event, self.phase = "wait", None, None
        d = self.rng.uniform(*AUTO["interval_s"][self.level])
        self.wait_until = t + d
        self.note(t, k, f"대기 {d:.1f}초 ({LEVEL_NAME[self.level]})", wait_s=round(d, 4))

    def set_phase(self, p, k):
        self.phase, self.phase_k = p, k

    # ---- 한 스텝: (이산 키, 눌림 집합)
    def decide(self, ses):
        sim = ses.sim
        k = sim.k + 1
        t = k * self.DT
        if self.halted is not None:
            self.note(t, k, "재개", reason=self.halted)
            self.halted = None
        if self.state == "manual":
            return [], set()
        s = self.view(ses)
        keys, steer, speed = [], None, None
        self.update_level(t, k)
        el = (k - self.phase_k) * self.DT  # 현재 phase 경과

        if self.home_c is None:
            self.home_c = s["lane_c"]
        if self.state == "cruise":
            steer, speed = self.lane_keep(s, self.home_c), self.speed_to(s, AUTO["gap_cruise_m"], AUTO["gap_hold_m"])
            if t >= AUTO["cruise_s"]:
                self.start_wait(t, k)
        elif self.state == "wait":
            steer, speed = self.lane_keep(s, self.home_c), self.speed_to(s, AUTO["gap_cruise_m"], AUTO["gap_hold_m"])
            if t >= self.wait_until - 1e-9:
                self.event = self.rng.choice(AUTO["events"])
                self.state, self.home_c = "event", s["lane_c"]
                self.set_phase("approach", k)
                self.note(t, k, f"이벤트: {NAME[self.event]} (접근)", kind="select")
        elif self.state == "event":
            keys, steer, speed = self.event_step(s, t, k, el)
        elif self.state == "recover":
            steer = self.steer_to(s, self.home_c) if s["inj"] is None or s["inj"]["kind"] not in LATERAL else None
            speed = self.speed_to(s, AUTO["gap_cruise_m"], AUTO["gap_hold_m"]) if s["inj"] is None else None
            near = max(abs(s["y"] - self.home_c), abs(s["y_stop"] - self.home_c)) <= AUTO["recover_tol_m"]  # 지나쳐 가는 중이면 아직
            ok = s["inj"] is None and s["sig"] is None and near and s["ego_state"] == "NORMAL"
            if ok:
                self.note(t, k, "회복 완료")
                self.start_wait(t, k)
                steer = None
            elif el > AUTO["recover_timeout_s"]:
                self.note(t, k, "회복 시간 초과 · 대기로", y=round(s["y"], 3), ego_state=s["ego_state"])
                self.start_wait(t, k)

        # 횡방향 시나리오 중에는 A/D 를 넣지 않는다 (원본도 무시하지만 운전자 입력으로도 넣지 않는다)
        inj_now = sim.inj if "0" not in keys else None
        for kk in keys:
            if kk in KEY_OF.values():
                inj_now = {"kind": [n for n, v in KEY_OF.items() if v == kk][0]}
        lateral_now = inj_now is not None and inj_now["kind"] in LATERAL
        if lateral_now:
            steer = None
        else:
            steer = self.guard(s, steer) if (steer is not None or s["margin_pred"] < AUTO["edge_min_m"]) else None
        held = {x for x in (steer, speed) if x}
        if tuple(sorted(held)) != self.prev_held or keys:
            self.record(dict(type="input", tick=k, held=sorted(held), keys=keys, state=self.state, event=self.event, level=self.level), t)
            self.prev_held = tuple(sorted(held))
        return keys, held

    def event_step(self, s, t, k, el):
        ev, keys, steer, speed = self.event, [], None, None
        inj = s["inj"]
        if self.phase == "approach":
            steer = self.lane_keep(s, self.home_c)
            speed = self.speed_to(s, AUTO["gap_approach_m"], AUTO["gap_approach_tol_m"])
            if abs(s["dx"] - AUTO["gap_approach_m"]) < AUTO["gap_approach_tol_m"] or el >= AUTO["approach_timeout_s"]:
                speed = None
                if ev in LATERAL:
                    self.target_y = None
                    self.set_phase("align", k)
                elif ev == "cut_in":
                    tgt = s["ego_c"] if s["ego_c"] != self.home_c else (self.home_c - math.copysign(self.W, self.home_c) if self.home_c else self.W)
                    self.target_y = tgt
                    if self.rng.random() < AUTO["cut_in_signal_p"][self.level]:
                        key = "Q" if tgt > s["y"] else "E"
                        if s["sig"] != key:
                            keys.append(key)
                        self.my_signal = key
                        self.note(t, k, f"깜빡이 {'왼쪽' if key == 'Q' else '오른쪽'}", key=key, kind="start")
                        self.set_phase("signal", k)
                    else:
                        self.note(t, k, "깜빡이 없이 진입", kind="start")
                        self.set_phase("steer", k)
                else:
                    keys.append(KEY_OF[ev])
                    self.set_phase("run", k)
                    self.my_inj_k0 = k
                    self.note(t, k, f"{NAME[ev]} 시작", key=KEY_OF[ev], kind="start")
        elif self.phase == "align":
            # 횡방향 시나리오는 이웃이 도로 중앙 쪽일 때만 시작. drift/stuck 은 자기 차로 중심(원본 방향이 도로 가운데 쪽),
            # weave 는 양쪽으로 흔들리므로 바깥으로 흔들려도 여유가 남는 위치까지 중앙 쪽으로 옮긴 뒤
            if self.target_y is None:
                self.target_y = self.start_y(s, ev)
            tgt = self.target_y
            ready = abs(s["y"] - tgt) <= 0.2 and abs(s["y_stop"] - tgt) <= 0.3 and s["margin_pred"] >= AUTO["edge_min_m"]
            if ready:
                keys.append(KEY_OF[ev])
                self.set_phase("run", k)
                self.my_inj_k0 = k
                amp = self.SM.WEAVE_LEVELS[self.weave_i_of(s)][0] if ev == "weave" else None
                self.note(t, k, f"{NAME[ev]} 시작" + (f" ({amp} m)" if amp else ""), key=KEY_OF[ev], kind="start", y0=round(s["y"], 3), amp=amp)
            elif el >= (AUTO["weave_align_timeout_s"] if ev == "weave" else AUTO["align_timeout_s"]):
                why = "시작 위치 도달 못함" if ev == "weave" else "차로 중심 아님"
                self.note(t, k, f"{NAME[ev]} 건너뜀 ({why})", kind="skip")
                self.state = "recover"
                self.set_phase("recover", k)
            else:
                steer = self.steer_to(s, tgt)
        elif self.phase == "signal":
            steer = self.lane_keep(s, self.home_c)
            if el >= AUTO["signal_lead_s"]:
                self.set_phase("steer", k)
        elif self.phase == "steer":
            steer = self.steer_to(s, self.target_y)
            if (abs(s["y"] - self.target_y) < 0.3 and abs(s["psi"]) < 0.02) or el >= AUTO["cut_in_steer_timeout_s"]:
                self.set_phase("hold", k)
                self.note(t, k, "진입 완료", y=round(s["y"], 3))
        elif self.phase == "hold":
            steer = self.steer_to(s, self.target_y)
            if el >= AUTO["cut_in_hold_s"]:
                if self.my_signal and s["sig"] == self.my_signal:
                    keys.append(self.my_signal)
                self.my_signal = None
                self.note(t, k, f"{NAME[ev]} 끝", kind="end", reason="duration")
                self.begin_recover(t, k)
        elif self.phase == "run":
            mine = inj is not None and inj["k0"] == self.my_inj_k0
            if ev in LATERAL:
                if not mine:
                    self.note(t, k, f"{NAME[ev]} 끝 (다른 원인)", kind="end", reason="other")
                    self.begin_recover(t, k)
                elif ev == "weave" and self.weave_off_road(s):
                    keys.append("0")
                    self.note(t, k, f"{NAME[ev]} 해제 (해제 뒤 도로 밖 예측, 가장자리 여유 {s['margin']:.2f} m)", key="0", kind="end",
                              reason="guard", dur=round(el, 1))
                    self.my_inj_k0 = None
                    self.begin_recover(t, k)
                elif ev != "weave" and s["margin_pred"] < AUTO["edge_min_m"]:
                    keys.append("0")
                    self.note(t, k, f"{NAME[ev]} 해제 (가장자리 여유 {s['margin']:.2f} m, 예측 {s['margin_pred']:.2f} m)", key="0",
                              kind="end", reason="early_pred", dur=round(el, 1))
                    self.my_inj_k0 = None
                    self.begin_recover(t, k)
                elif el >= AUTO["scenario_s"][ev] - 1e-9:
                    keys.append("0")
                    self.note(t, k, f"{NAME[ev]} 해제 ({AUTO['scenario_s'][ev]:.0f}초)", key="0", kind="end", reason="duration", dur=round(el, 1))
                    self.my_inj_k0 = None
                    self.begin_recover(t, k)
            elif not mine and el > 0:
                self.note(t, k, f"{NAME[ev]} 종료", kind="end", reason="auto_end", dur=round(el, 1))
                self.my_inj_k0 = None
                self.begin_recover(t, k)
        return keys, steer, speed

    def start_y(self, s, ev):
        """횡방향 시나리오 시작 위치. drift/stuck 은 자기 차로 중심(원본 방향이 도로 가운데 쪽), weave 는 자차 차로 중심."""
        return s["ego_c"] if ev == "weave" else s["lane_c"]

    def weave_off_road(self, s):
        """지금 풀면 방향각 정렬(Tψ)로 멈출 때까지 가는 위치가 도로 밖이다."""
        y_rel = s["y"] + s["v"] * self.T_PSI * s["psi"]
        return AUTO["road_edge_m"] - (abs(y_rel) + AUTO["half_width_m"]) < 0

    def weave_i_of(self, s):
        return self.sim_weave_i % len(self.SM.WEAVE_LEVELS)

    def begin_recover(self, t, k):
        self.state = "recover"
        self.set_phase("recover", k)
        self.note(t, k, "회복 중")

    # ---- 사람 개입 → manual
    def handback(self, ses, reason):
        """운전자가 건 횡방향 시나리오는 0 으로 해제, brake/accel 은 원본이 끝내게 두고, 운전자가 켠 깜빡이는 끈다."""
        if self.state == "manual":
            return []
        sim = ses.sim
        k = sim.k + 1
        t = k * self.DT
        keys = []
        inj = sim.inj
        if inj is not None and inj["kind"] in LATERAL and inj["k0"] == self.my_inj_k0:
            keys.append("0")
        sig = "Q" if sim.sig_l else "E" if sim.sig_r else None
        if self.my_signal and sig == self.my_signal:
            keys.append(self.my_signal)
        self.state, self.stopped_reason, self.event, self.phase = "manual", reason, None, None
        self.my_inj_k0 = self.my_signal = None
        self.note(t, k, f"수동 복귀 ({'사람 입력' if reason == 'human_input' else '사람 명령'})", reason=reason, cleanup=keys)
        return keys

    def halt(self, ses, reason):
        if self.state == "manual":
            return
        self.halted = reason  # 재개 전에 다시 멈춰도(예: 정지 직후 끊김) 사유마다 기록
        t = (ses.sim.k + 1) * self.DT
        self.note(t, ses.sim.k + 1, f"정지 ({reason})", reason=reason)

    def status(self, ses):
        k = ses.sim.k + 1
        t = k * self.DT
        return dict(
            enabled=True, seed=self.seed, level_setting=self.level_setting, level=self.level, state=self.state,
            stopped_reason=self.stopped_reason if self.state == "manual" else None,
            event_kind=self.event if self.state in ("event", "recover") else None,
            next_event_in_s=round(max(0.0, self.wait_until - t), 4) if self.state == "wait" else None,
            edge_margin_m=round(self.edge_margin(ses), 4), log=list(self.ui_log))
