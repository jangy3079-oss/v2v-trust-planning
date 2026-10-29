"""demo_v15c 실시간 시연: v14b B14b 누적기 탐지(되돌림) + 키 입력 완화(v15b) + v15a rule(접근 중일 때만 감속) + k_drain 손잡이 + 자차 반응용 τ_eff(종방향은 접근 방향만).
합성 환경 · 직선 도로 · 시뮬레이션. 방향지시등 = 가정. 출발점: demo_v15b(→ v15a → v15 → scratch_v14b/app_v14b.py → demo_v13). README 참조.

사용:
  /opt/anaconda3/bin/python -I -B app.py [--backend off|rule|nmpc] [--k-drain 1.3]
  /opt/anaconda3/bin/python -I -B app.py --headless --script demo_v15.json [--backend rule] [--trace out.csv]
"""
import argparse
import json
import sys
from collections import deque
from pathlib import Path

sys.dont_write_bytecode = True
V15 = Path(__file__).resolve().parent
sys.path.insert(0, str(V15))

import numpy as np  # noqa: E402
import torch  # noqa: E402

import accum as AC  # noqa: E402
import ego_controller as EC  # noqa: E402
import sim as SM  # noqa: E402
from detector import TAU_ALARM, Detector  # noqa: E402

DT = SM.DT
BANNER = "합성 환경 · 직선 도로 · 시뮬레이션  |  방향지시등 = 가정"
SM_SETTING = {0.1: "nom", 0.3: "sm0.30"}
INJ_KEYS = {"1": "drift", "2": "stuck", "3": "weave", "4": "brake", "5": "accel", "0": None}
INJ_NAME = {"drift": "드리프트", "stuck": "조향 고정", "weave": "위빙", "brake": "급제동", "accel": "급가속"}


class Session:
    """시뮬 + 탐지기 + B14b 누적기(S, S′) + 자차 제어. UI·헤드리스 공용."""

    def __init__(self, seed=0, sigma_meas=0.1, signal_assumption=True, bg=True, gap=40.0, lane=2, v0=25.0, backend="off",
                 v_ego=25.0, k_drain=AC.K_DRAIN_DEFAULT):
        self.init = dict(seed=seed, sigma_meas=sigma_meas, bg=bg, gap=gap, lane=lane, v0=v0, v_ego=v_ego)
        self.signal_assumption = signal_assumption
        self.k_drain = k_drain
        self.dets, self.params = {}, {}
        self.ctl = EC.EgoController(backend, v_set=v_ego)
        self.reset()

    # ---- 구성
    def detector(self, sm):
        st = SM_SETTING[sm]
        if st not in self.dets:
            self.dets[st] = Detector(st)
            self.params[st] = AC.load_params(st)
        d = self.dets[st]
        d.reset()
        d.r2_enabled = self.signal_assumption and d.cfg["r2_calib_ok"]
        p = self.params[st]
        self.acc = AC.Online(p["theta"], *(p["on"] if self.signal_assumption else p["off"]), k_drain=self.k_drain)
        self.zone = AC.Zone()
        self.prev = dict(det=False, eff=False)
        return d

    def reset(self):
        self.sim = SM.Sim(**self.init)
        self.det = self.detector(self.sim.sigma_meas)
        self.log = deque(maxlen=200)
        self.injections = []
        self.obs = self.out = None
        self.ctl.reset(self.sim.ego)
        self.u = None
        self.ego_rec = dict(a=0.0, steer=0.0, n_coll=0, hit=False, gap_min=float("inf"), engaged=False, reacted=False)
        self.note(f"리셋 (자차 제어 {self.ctl.backend}, k_drain {self.k_drain:.1f})")

    def note(self, msg, t=None):
        self.log.append(((self.sim.k + 1) * DT if t is None else t, msg))

    @property
    def r2_state(self):
        c = self.det.cfg
        if not c["r2_calib_ok"]:
            return "보정 실패(v12) · 경보 미반영"
        return "사용" if self.det.r2_enabled else "꺼짐(신호 가정 off, v12) · 경보 미반영"

    @property
    def p_label(self):
        th = self.acc.theta
        return f"ν {self.acc.nu / th:.3f}θ · h {self.acc.h / th:.2f}θ ({'p_sig 1.0' if self.signal_assumption else 'p_sig 0.0'} 검증 보정, v14b)"

    def sent_signal(self):
        return self.sim.turn_signal if self.signal_assumption else 0

    # ---- 입력
    def key(self, k):
        s = self.sim
        if k in ("Q", "E"):
            s.toggle_signal("L" if k == "Q" else "R")
            self.note(f"방향지시등 {'좌' if s.sig_l else '우' if s.sig_r else '끔'}")
        elif k in INJ_KEYS:
            self.inject(INJ_KEYS[k])
        elif k in ("[", "]"):
            s.adjust(-1 if k == "[" else 1)
            if s.inj and s.inj["kind"] in ("drift", "stuck"):
                self.injections[-1]["level"] = s.inj["level"]
        elif k in (",", "."):
            self.k_drain = float(np.clip(round(self.k_drain + (-0.1 if k == "," else 0.1), 1), *AC.K_DRAIN_RANGE))
            self.acc.k = self.k_drain
            self.note(f"k_drain = {self.k_drain:.1f} (미검증 설정)")
        elif k == "T":
            self.signal_assumption = not self.signal_assumption
            self.det.r2_enabled = self.signal_assumption and self.det.cfg["r2_calib_ok"]
            p = self.params[self.det.cfg["setting"]]
            self.acc.set(*(p["on"] if self.signal_assumption else p["off"]))
            self.note(f"신호 가정 {'on' if self.signal_assumption else 'off'} · R2 {self.r2_state} · {self.p_label}")
        elif k == "N":
            sm = 0.3 if s.sigma_meas == 0.1 else 0.1
            s.set_sigma_meas(sm)
            self.det = self.detector(sm)
            self.note(f"σ_meas={sm} → 탐지기 {self.det.cfg['setting']} 재시작(10 s 준비)")
        elif k == "C":
            self.ctl.next_backend(self.sim.ego)
            self.note(f"자차 제어 → {self.ctl.backend}")
        elif k == "R":
            self.reset()

    def inject(self, kind, sign=None):
        s = self.sim
        s.inject(kind, sign)
        if kind is None:
            self.note("이상 주입 해제 → 수동")
        else:
            self.injections.append(dict(kind=kind, level=s.inj["level"], k0=s.inj["k0"], cross_k=None, first={}))
            self.note(f"주입: {INJ_NAME[kind]} {self.level_str(s.inj)}")

    @staticmethod
    def level_str(inj):
        if inj is None:
            return ""
        L, kind = inj["level"], inj["kind"]
        if kind == "drift":
            return f"{L:.2f} m/s"
        if kind == "stuck":
            return f"r0={L:.4f} rad/s"
        if kind == "weave":
            return f"{L[0]:.1f} m / {L[1]:.1f} s" + ("  (L4: 경계 넘는 경우 적음)" if L[0] >= 1.2 else "")
        if kind == "accel":
            return f"+{L[0]:.1f} m/s² ({L[1]:.1f} s 램프 + 2 s)"
        return f"{SM.BRAKE_A:.0f} m/s² {SM.BRAKE_T:.0f} s"

    # ---- 한 스텝
    def step(self, held=()):
        o = self.sim.step(held, ego_u=self.u)
        out = self.det.step(o["a_obs"], o["r_obs"], o["d_obs"], self.sent_signal(), o["v_meas"])
        dx = o["x"] - o["ego"][0]
        zone = self.zone.update(dx)
        g = AC.Zone.g(zone, out["r_a"])
        a = self.acc.step(out["e"], out["e_lat"] + (out["e_long"] if g else 0.0), out["intent_window"])
        rule_a, tau_rule = out["alarm_rule"], out["tau_rule"]
        out.update(tau_a2=out["tau"], alarm_a2=out["alarm"], zone=zone, dx=dx, g=bool(g),
                   tau_det=min(a["tau_state"], tau_rule), alarm_det=bool(a["alarm_path"] or rule_a),
                   tau_eff=min(a["tau_state_eff"], tau_rule), alarm_eff=bool(a["alarm_eff_path"] or rule_a), **a)
        k = o["k"]
        self.control(o, out)
        if out["alarm_det"] and not self.prev["det"]:
            self.note("탐지 경보 시작" + ("" if out["alarm_eff"] else " (지금 반응 경보 없음)"), o["t"])
            self.ep_eff = False
        if out["alarm_eff"] and not self.prev["eff"]:
            self.note("반응 경보 시작 (자차 제어에 전달)", o["t"])
        if out["alarm_det"] and out["alarm_eff"]:
            self.ep_eff = True
        if not out["alarm_eff"] and self.prev["eff"]:
            self.note("반응 경보 해제", o["t"])
        if not out["alarm_det"] and self.prev["det"]:
            self.note("탐지 경보 해제" + ("" if getattr(self, "ep_eff", True) else "  [무시됨: 이번 탐지 경보 동안 자차 반응 경보 없음 — 종방향, 접근 아님]"), o["t"])
        self.prev = dict(det=out["alarm_det"], eff=out["alarm_eff"])
        if self.injections:
            j = self.injections[-1]
            if o["inj"] is not None and o["inj"]["k0"] == j["k0"] and o["inj"]["cross_k"] is not None:
                j["cross_k"] = o["inj"]["cross_k"]
            if j["k0"] <= k < j["k0"] + 600:
                for key, name in (("alarm_det", "탐지"), ("alarm_eff", "반응"), ("alarm_a2", "A2")):
                    if out[key] and name not in j["first"]:
                        j["first"][name] = k
        self.obs, self.out = o, out
        return o, out

    def control(self, o, out):
        """자차 제어기에는 τ_eff·반응 경보를 넘긴다 (README 4절). 이웃 상태 = BSM 관측 + 참 x·ψ (v13과 같음)."""
        ego = o["ego"]
        nb = dict(x=o["x"], y=o["y_obs"], psi=o["psi"], v=o["v_meas"], r=o["r_obs"])
        a, d = self.ctl.step(ego, nb, out["tau_eff"], out["alarm_eff"])
        self.u = (a, self.ctl.yaw_rate(d, ego[3]))
        dx, dy, g_lon, g_lat, g, hit = EC.gaps(ego, o["x"], o["y"])
        r = self.ego_rec
        if hit and not r["hit"]:
            r["n_coll"] += 1
            self.note(f"충돌 #{r['n_coll']} ({self.ctl.backend})", o["t"])
        st = self.ctl.info.get("state", "정상")
        engaged = bool(getattr(self.ctl.impl, "engaged", False))
        reacted = (self.ctl.backend == "rule" and engaged) or (self.ctl.backend == "nmpc" and (st != "정상" or a <= -1.0))
        r.update(a=a, steer=d, hit=hit, dx=dx, dy=dy, g_lon=g_lon, g_lat=g_lat, gap=g, gap_min=min(r["gap_min"], g),
                 ttc=EC.ttc(ego, o["x"], o["v"]), engaged=engaged, reacted=reacted, state=st)

    def inj_report(self, j=None):
        j = j or (self.injections[-1] if self.injections else None)
        if j is None:
            return None
        return dict(kind=j["kind"], level=j["level"], t0=j["k0"] * DT, cross=None if j["cross_k"] is None else (j["cross_k"] - j["k0"]) * DT,
                    delay={n: (k - j["k0"]) * DT for n, k in j["first"].items()})


def run_script(script, verbose=True, trace=None, backend=None, k_drain=None):
    """헤드리스: 스크립트 키 입력을 10 Hz로 넣는다. events: {"t", "key"} 또는 {"t", "hold", "dur"}."""
    ses = Session(seed=script.get("seed", 0), sigma_meas=script.get("sigma_meas", 0.1), signal_assumption=script.get("signal_assumption", True),
                  bg=script.get("bg", True), backend=backend or script.get("backend", "off"),
                  k_drain=k_drain if k_drain is not None else script.get("k_drain", AC.K_DRAIN_DEFAULT), **script.get("init", {}))
    taps = sorted((e["t"], e["key"]) for e in script.get("events", []) if "key" in e)
    holds = [(e["t"], e["t"] + e["dur"], e["hold"]) for e in script.get("events", []) if "hold" in e]
    rows, ti = [], 0
    for k in range(int(round(script.get("duration", 60.0) / DT))):
        t = k * DT
        while ti < len(taps) and taps[ti][0] < t + DT / 2:
            ses.key(taps[ti][1])
            ti += 1
        o, out = ses.step({h for a, b, h in holds if a - DT / 2 <= t < b - DT / 2})
        if trace is not None:
            rows.append({**{c: o[c] for c in ("t", "x", "y", "d_obs", "v", "turn_signal")},
                         **{c: out[c] for c in ("e", "e_long", "e_lat", "r_a", "zone", "g", "S", "S_prime", "S_eff", "clipped", "tau_det",
                                                "tau_eff", "alarm_det", "alarm_eff", "tau_a2", "alarm_a2", "intent_window")},
                         "ego_a": ses.ego_rec["a"], "ego_state": ses.ego_rec.get("state"), "ego_reacted": ses.ego_rec["reacted"]})
    if verbose:
        print(BANNER)
        print(f"탐지기 B14b(v14b) · {ses.p_label} · k_drain {ses.k_drain:.1f}{'' if ses.k_drain == 1.0 else ' (미검증 설정)'} · 자차 {ses.ctl.backend}")
        for t, m in ses.log:
            print(f"t={t:6.1f}s  {m}")
        for j in ses.injections:
            r = ses.inj_report(j)
            dl = "  ".join(f"{n} {r['delay'][n]:.1f}s" if n in r["delay"] else f"{n} —" for n in ("탐지", "반응", "A2"))
            print(f"주입 {INJ_NAME[r['kind']]} t={r['t0']:.1f}s  지연: {dl}")
        print(f"자차: 충돌 {ses.ego_rec['n_coll']}회, 최소 간격 {ses.ego_rec['gap_min']:.2f} m")
    if trace is not None:
        import csv
        with open(trace, "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(rows[0]))
            w.writeheader()
            w.writerows(rows)
    return ses


# ---------------------------------------------------------------- UI
def run_ui(seed, backend="off", k_drain=AC.K_DRAIN_DEFAULT):
    import pygame
    pygame.init()
    Wd, Hd = 1480, 920
    screen = pygame.display.set_mode((Wd, Hd))
    pygame.display.set_caption("demo_v15c — " + BANNER)
    fp = "/System/Library/Fonts/AppleSDGothicNeo.ttc"
    F = {s: (pygame.font.Font(fp, s) if Path(fp).exists() else pygame.font.SysFont(None, s + 4)) for s in (14, 15, 16, 18, 22)}
    clock = pygame.time.Clock()
    ses = Session(seed=seed, backend=backend, k_drain=k_drain)
    paused, acc = False, 0.0
    hist = deque(maxlen=600)
    C = dict(bg=(18, 20, 24), grass=(34, 52, 38), road=(52, 54, 60), line=(225, 225, 225), ego=(70, 150, 255), nb=(240, 200, 60),
             red=(235, 70, 70), amber=(255, 170, 40), green=(80, 200, 120), txt=(230, 230, 230), dim=(150, 150, 160), panel=(28, 30, 36),
             gru=(170, 120, 255), rule=(60, 200, 220), det=(120, 200, 255), eff=(255, 150, 60), a2=(150, 150, 160))
    RW, CX, EGO_PY, SY, SX = 620, 310, 640, 26.0, 10.0

    def w2s(x_rel, y):
        return CX - y * SY, EGO_PY - x_rel * SX

    def text(s, pos, size=16, col=None, center=False):
        img = F[size].render(s, True, col or C["txt"])
        r = img.get_rect()
        if center:
            r.center = pos
        else:
            r.topleft = pos
        screen.blit(img, r)

    def car(x_rel, y, psi, col, blink=None):
        L, Wc = 4.5, 1.8
        c, s = np.cos(psi), np.sin(psi)
        pts = [(x_rel + c * dx - s * dy, y + s * dx + c * dy) for dx, dy in ((L / 2, Wc / 2), (L / 2, -Wc / 2), (-L / 2, -Wc / 2), (-L / 2, Wc / 2))]
        pygame.draw.polygon(screen, col, [w2s(*p) for p in pts])
        pygame.draw.polygon(screen, (20, 20, 20), [w2s(*p) for p in pts], 2)
        if blink:
            side = 1 if blink == "L" else -1
            for dx in (L / 2 - 0.3, -L / 2 + 0.3):
                pygame.draw.circle(screen, C["amber"], [int(v) for v in w2s(x_rel + dx, y + side * Wc / 2)], 6)

    def bar(x, y, w, h, val, label, col, size=18):
        pygame.draw.rect(screen, (60, 62, 70), (x, y, w, h))
        pygame.draw.rect(screen, C["red"] if val <= TAU_ALARM else col, (x, y, int(w * np.clip(val, 0, 1)), h))
        for thr, cc in ((TAU_ALARM, C["txt"]), (0.8, C["dim"])):
            tx = x + int(w * thr)
            pygame.draw.line(screen, cc, (tx, y - 3), (tx, y + h + 3), 2 if thr == TAU_ALARM else 1)
        text(f"{label} {val:.2f}", (x + w + 10, y + h / 2 - size / 2 - 2), size)

    def draw():
        o, out = ses.obs, ses.out
        screen.fill(C["bg"])
        screen.fill(C["grass"], (0, 0, RW, Hd))
        x0, x1 = CX - 1.5 * SM.W * SY, CX + 1.5 * SM.W * SY
        pygame.draw.rect(screen, C["road"], (x0, 0, x1 - x0, Hd))
        for yl in (1.5 * SM.W, -1.5 * SM.W):
            pygame.draw.line(screen, C["line"], (CX - yl * SY, 0), (CX - yl * SY, Hd), 3)
        ex, ey, eth, ev = o["ego"]
        ph = ex % 12.0
        for yl in (SM.HALF, -SM.HALF):
            for m in np.arange(-30, 80, 12.0):
                pygame.draw.line(screen, C["line"], (CX - yl * SY, w2s(m - ph, 0)[1]), (CX - yl * SY, w2s(m - ph + 3.0, 0)[1]), 2)
        for dd in (AC.D_ZONE, -AC.D_ZONE):                                  # 나란히 구역 경계 (±8 m)
            yy = w2s(dd, 0)[1]
            for xx in range(int(x0), int(x1), 16):
                pygame.draw.line(screen, (90, 92, 100), (xx, yy), (xx + 8, yy), 1)
        xr = o["x"] - ex
        sg = "L" if ses.sim.sig_l else "R" if ses.sim.sig_r else None
        blink = (pygame.time.get_ticks() // 300) % 2 == 0
        xp = getattr(ses.ctl.impl, "x_pred", None)
        if ses.ctl.backend == "nmpc" and xp is not None:
            for k_ in range(xp.shape[1]):
                pygame.draw.circle(screen, C["ego"], [int(v) for v in w2s(xp[0, k_] - ex, xp[1, k_])], 3)
        car(0.0, ey, eth, C["red"] if ses.ego_rec["hit"] else C["ego"])
        py = w2s(xr, 0)[1]
        if 0 < py < Hd:
            for key, cn, pad in (("alarm_det", "det", 0.5), ("alarm_eff", "eff", 1.0)):
                if out[key]:
                    a, b = w2s(xr + 2.25 + pad, o["y"] + 0.9 + pad), w2s(xr - 2.25 - pad, o["y"] - 0.9 - pad)
                    pygame.draw.rect(screen, C[cn], (min(a[0], b[0]), min(a[1], b[1]), abs(b[0] - a[0]), abs(b[1] - a[1])), 2)
            car(xr, o["y"], o["psi"], C["nb"], sg if blink else None)
        else:
            up = py <= 0
            px, yy = w2s(0, o["y"])[0], (30 if up else Hd - 30)
            pygame.draw.polygon(screen, C["nb"], [(px, yy + (-14 if up else 14)), (px - 10, yy), (px + 10, yy)])
            text(f"{xr:+.0f} m", (px + 14, yy - 10), 16, C["nb"])
        text(f"자차 [{ses.ctl.backend}]", w2s(-3.5, ey), 16, C["ego"], center=True)
        pygame.draw.rect(screen, (0, 0, 0), (0, 0, RW, 30))
        text("합성 · 직선 · 시뮬레이션 | 신호 = 가정", (10, 5), 16, C["amber"])
        text("테두리: 하늘 = 탐지 경보, 주황 = 자차 반응 경보  · 점선 = 나란히 구역 ±8 m  (횡 2.6배 확대)", (10, Hd - 24), 14, C["dim"])
        pygame.draw.rect(screen, (0, 0, 0), (0, Hd - 98, RW, 46))
        for i, kn in enumerate("WASD"):
            on = kn in ui_held
            r_ = pygame.Rect(10 + 34 * i, Hd - 90, 30, 30)
            pygame.draw.rect(screen, C["amber"] if on else (70, 72, 80), r_, 0 if on else 2)
            text(kn, r_.center, 18, (20, 20, 20) if on else C["dim"], center=True)
        text(f"페달 {ses.sim.a_user:+.1f} m/s² (2 s 램프)  요레이트 명령 {ses.sim.r_cmd:+.3f}  횡속도 {o['v'] * np.sin(o['psi']):+.2f} m/s",
             (150, Hd - 84), 14)
        # ---- HUD
        X = RW + 20
        pygame.draw.rect(screen, C["panel"], (RW, 0, Wd - RW, Hd))
        c = ses.det.cfg
        th = c["theta"]
        text(f"t = {o['t']:6.1f} s   설정 {c['setting']}   σ_meas {ses.sim.sigma_meas:.1f} m   신호 가정 "
             f"{'ON' if ses.signal_assumption else 'OFF'}{'   [일시정지]' if paused else ''}", (X, 10), 18)
        text(f"k_drain = {ses.k_drain:.1f}" + ("  (v14b 값)" if ses.k_drain == 1.0 else "  ※ 미검증 설정") + "   [ , / . ] 조절",
             (X, 34), 16, C["txt"] if ses.k_drain == 1.0 else C["amber"])
        text(f"R2 {ses.r2_state}" + ("" if out["active"] else "   (준비 중: 시작 10 s 경보 없음)"), (X + 420, 34), 14, C["dim"])
        text(f"상대 d {o['d_obs']:+.2f} m   경계까지 {SM.HALF - abs(o['d_obs']):.2f} m   dx {out['dx']:+.1f} m → 구역 [{out['zone']}]   "
             f"방향지시등 { {'L': '좌 ◀', 'R': '우 ▶', None: '꺼짐'}[sg]}", (X, 58), 16, C["amber"] if sg else C["txt"])
        if out["intent_window"]:
            text("● 의도구간: 누적·규칙 동결 (신호 켠 채 이상은 못 잡음)", (X, 80), 14, C["amber"])
        if not out["g"]:
            text("● 종방향 이상 무시 (접근 아님): e_long을 τ_eff에서 제외", (X + 380, 80), 14, C["eff"])
        bar(X, 104, 360, 24, out["tau_det"], "τ 탐지 (이웃 이상, v14b)", C["det"])
        bar(X, 136, 360, 24, out["tau_eff"], "τ_eff 자차 반응", C["eff"])
        if out["alarm_det"]:
            text("● 탐지", (X + 640, 106), 18, C["red"])
        if out["alarm_eff"]:
            text("● 반응", (X + 640, 138), 18, C["red"])
        bar(X, 172, 300, 12, out["tau_a2"], "참고: v12 A2 τ (순간 GRU ∨ 규칙)", C["a2"], 14)
        bar(X, 192, 300, 12, out["tau_rule"], "τ_rule (공통)", C["rule"], 14)
        rows = [(f"e = {out['e'] / th:.3f}θ  =  e_long {out['e_long'] / th:.3f}θ (종가속)  +  e_lat {out['e_lat'] / th:.3f}θ (r_rel·d)   "
                 f"r_a {out['r_a']:+.2f} m/s²", False),
                (f"누적 S/h {out['S'] / ses.acc.h:4.2f} (탐지)   S_eff/h {out['S_eff'] / ses.acc.h:4.2f} (반응)"
                 + ("   [S′ > S → min으로 자름]" if out["clipped"] else ""), out["alarm_path"]),
                (f"   {ses.p_label}   홀드 3 s {'● 유지 중' if out['hold_on'] else '○'}", False),
                (f"R1 S {out['R1_S']:4.2f} / h_d {c['h_d']:.2f}    R2 T* "
                 f"{'2 s 내 없음' if not np.isfinite(out['R2_ttb']) else '{:.1f} s'.format(out['R2_ttb'])} / T_c {c['T_c']} s", out["alarm_rule"])]
        for i, (s, warn) in enumerate(rows):
            text(s, (X, 212 + 21 * i), 15, C["red"] if warn else C["txt"])
        for j, (key, cn, lab) in enumerate((("S", "det", "S"), ("S_eff", "eff", "S_eff"))):   # 양동이 게이지 2개
            gx0, gy0, gw0, gh0 = X + 712 + 50 * j, 104, 30, 130
            lvl = out[key] / ses.acc.h
            fill = min(lvl, 1.5) / 1.5
            pygame.draw.rect(screen, (40, 42, 50), (gx0, gy0, gw0, gh0))
            pygame.draw.rect(screen, C["red"] if lvl > 1 else C[cn], (gx0, gy0 + gh0 * (1 - fill), gw0, gh0 * fill))
            yh = gy0 + gh0 * (1 - 1 / 1.5)
            pygame.draw.line(screen, C["txt"], (gx0 - 3, yh), (gx0 + gw0 + 3, yh), 2)
            text(lab, (gx0 + 2, gy0 + gh0 + 3), 14, C["dim"])
        gx, gy, gw, gh = X, 304, 820, 140
        pygame.draw.rect(screen, (40, 42, 50), (gx, gy, gw, gh))
        for thr, cc in ((TAU_ALARM, (140, 60, 60)), (0.8, (80, 80, 90))):
            pygame.draw.line(screen, cc, (gx, gy + gh * (1 - thr)), (gx + gw, gy + gh * (1 - thr)), 1)
        n = hist.maxlen
        xs = lambda i: gx + gw * (i + n - len(hist)) / (n - 1)  # noqa: E731
        for key, col, wdt in (("tau_a2", C["a2"], 1), ("tau_det", C["det"], 2), ("tau_eff", C["eff"], 2)):
            if len(hist) > 1:
                pygame.draw.lines(screen, col, False, [(xs(i), gy + gh * (1 - h[key])) for i, h in enumerate(hist)], wdt)
        for i, h in enumerate(hist):
            for key, cn, off in (("alarm_det", "det", 10), ("alarm_eff", "eff", 4)):
                if h[key]:
                    pygame.draw.line(screen, C[cn], (xs(i), gy + gh - off - 4), (xs(i), gy + gh - off), 2)
        text("최근 60 s: 하늘 τ 탐지 · 주황 τ_eff 반응 · 회색 A2 참고  (선 0.7 경보, 0.8)  아래 눈금 = 경보", (gx, gy + gh + 4), 14, C["dim"])
        inj = ses.sim.inj
        y0 = 474
        text("이상 주입: " + ("없음(수동 조작)" if inj is None else f"{INJ_NAME[inj['kind']]} {ses.level_str(inj)}"), (X, y0), 18,
             C["amber"] if inj else C["txt"])
        text(f"다음 위빙 {SM.WEAVE_LEVELS[ses.sim.weave_i][0]:.1f} m · 드리프트 {SM.DRIFT_LEVELS[ses.sim.drift_i]:.2f} m/s · 조향 고정 "
             f"{SM.STUCK_LEVELS[ses.sim.stuck_i]:.4f} rad/s · 급가속 +{SM.ACCEL_A:.0f} m/s²   (위빙 1.2 m는 경계 넘는 경우 적음)", (X, y0 + 24), 14, C["dim"])
        r = ses.inj_report()
        if r:
            dl = "   ".join(f"{n} {r['delay'][n]:.1f}s" if n in r["delay"] else f"{n} —" for n in ("탐지", "반응", "A2"))
            cr = "" if r["kind"] in ("brake", "accel") else ("   경계 통과 없음" if r["cross"] is None else f"   경계 통과 +{r['cross']:.1f} s")
            text(f"마지막 주입 t={r['t0']:.1f}s 지연: {dl}{cr}", (X, y0 + 46), 15)
        er, inf = ses.ego_rec, ses.ctl.info
        cy = 552
        pygame.draw.line(screen, (70, 72, 80), (X, cy - 4), (X + 820, cy - 4), 1)
        rule_txt = (f"   접근 속도 {inf.get('closing', 0.0):+.1f} m/s · 위험 {'●' if inf.get('danger') else '○'}"
                    if ses.ctl.backend == "rule" else "")
        text(f"자차 제어 [{ses.ctl.backend}] (C 순환) — 입력 τ_eff   상태 {inf.get('state', '—')}{rule_txt}"
             + ("   ● 반응 중" if er["reacted"] else ""), (X, cy), 18, C["amber"] if er["reacted"] else C["txt"])
        tt = er.get("ttc", float("inf"))
        text(f"자차 v {ev:5.1f} m/s  a {er['a']:+.1f} m/s²  δ {er['steer']:+.3f} rad   간격 {er.get('gap', 0):5.1f} m (최소 {er['gap_min']:.1f})"
             f"   TTC {'∞' if not np.isfinite(tt) else f'{tt:.1f} s'}   충돌 누적 {er['n_coll']}회", (X, cy + 24), 15,
             C["red"] if er["hit"] else C["txt"])
        ly = 610
        text("로그", (X, ly), 16, C["dim"])
        for i, (t, m) in enumerate(list(ses.log)[-11:]):
            col = C["eff"] if "무시됨" in m else C["red"] if ("경보 시작" in m or "충돌" in m) else C["txt"]
            text(f"t={t:6.1f}s  {m}", (X, ly + 20 + 18 * i), 14, col)
        keys = ("W/S 가속·제동(2 s 램프)  A/D 조향  Q/E 지시등   1 드리프트  2 조향 고정  3 위빙(0.5→0.8→1.2 m)  4 급제동  5 급가속  0 해제  [ ] 크기",
                ", . k_drain   T 신호 가정   N σ_meas 0.1↔0.3   C 자차 제어 off/rule/nmpc   R 리셋   P 일시정지   Esc 종료")
        for i, s in enumerate(keys):
            text(s, (X, Hd - 46 + 20 * i), 14, C["dim"])
        pygame.display.flip()

    pygame.key.stop_text_input()
    scan = {pygame.KSCAN_W: "W", pygame.KSCAN_S: "S", pygame.KSCAN_A: "A", pygame.KSCAN_D: "D", pygame.KSCAN_Q: "Q", pygame.KSCAN_E: "E",
            pygame.KSCAN_1: "1", pygame.KSCAN_2: "2", pygame.KSCAN_3: "3", pygame.KSCAN_4: "4", pygame.KSCAN_5: "5", pygame.KSCAN_0: "0",
            pygame.KSCAN_LEFTBRACKET: "[", pygame.KSCAN_RIGHTBRACKET: "]", pygame.KSCAN_COMMA: ",", pygame.KSCAN_PERIOD: ".",
            pygame.KSCAN_T: "T", pygame.KSCAN_N: "N", pygame.KSCAN_C: "C", pygame.KSCAN_R: "R", pygame.KSCAN_P: "P", pygame.KSCAN_ESCAPE: "Esc"}
    pedal = {"W", "S", "A", "D"}
    held, tapped = set(), set()
    ui_held = set()
    ses.step(())
    running = True
    while running:
        acc += clock.tick(60) / 1000.0
        for ev in pygame.event.get():
            name = scan.get(getattr(ev, "scancode", None))
            if ev.type == pygame.QUIT or (ev.type == pygame.KEYDOWN and name == "Esc"):
                running = False
            elif ev.type == pygame.WINDOWFOCUSLOST:
                held.clear()
            elif ev.type == pygame.KEYUP and name in pedal:
                held.discard(name)
            elif ev.type == pygame.KEYDOWN and name in pedal:
                held.add(name)
                tapped.add(name)
            elif ev.type == pygame.KEYDOWN and name == "P":
                paused = not paused
            elif ev.type == pygame.KEYDOWN and name:
                ses.key(name)
                if name == "R":
                    hist.clear()
                    ses.step(())
        ui_held = held | tapped
        if paused:
            acc = 0.0
        acc = min(acc, 0.5)
        while acc >= DT:
            acc -= DT
            _, out = ses.step(held | tapped)
            tapped.clear()
            hist.append(out)
        draw()
    pygame.quit()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--headless", action="store_true")
    ap.add_argument("--script", default=str(V15 / "demo_v15c.json"))
    ap.add_argument("--trace", default=None, help="헤드리스: 스텝별 CSV 출력 경로")
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--backend", choices=EC.BACKENDS, default=None)
    ap.add_argument("--k-drain", type=float, default=None)
    a = ap.parse_args()
    torch.set_num_threads(1)
    if a.headless:
        run_script(json.load(open(a.script)), trace=a.trace, backend=a.backend, k_drain=a.k_drain)
    else:
        run_ui(a.seed, a.backend or "off", a.k_drain if a.k_drain is not None else AC.K_DRAIN_DEFAULT)
