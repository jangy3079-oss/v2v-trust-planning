"""v13 실시간 시연 UI (pygame, 60 FPS 렌더, 시뮬·탐지·자차 제어 10 Hz 고정 스텝). 합성 환경 · 직선 도로 · 시뮬레이션.
사용:
  /opt/anaconda3/bin/python -I -B app.py [--backend off|rule|nmpc]         # 창 (C 키로 백엔드 순환)
  /opt/anaconda3/bin/python -I -B app.py --headless --script demo.json --backend nmpc   # UI 없이 같은 시나리오
"""
import argparse
import json
import sys
from collections import deque
from pathlib import Path

sys.dont_write_bytecode = True
V13 = Path(__file__).resolve().parent
sys.path.insert(0, str(V13))

import numpy as np  # noqa: E402
import torch  # noqa: E402

import sim as SM  # noqa: E402
from detector import TAU_ALARM, Detector  # noqa: E402
import ego_controller as EC  # noqa: E402

DT = SM.DT
BANNER = "합성 환경 · 직선 도로 · 시뮬레이션  |  방향지시등 = 가정"
SM_SETTING = {0.1: "nom", 0.3: "sm0.30"}
INJ_KEYS = {"1": "drift", "2": "stuck", "3": "weave", "4": "brake", "0": None}
INJ_NAME = {"drift": "드리프트", "stuck": "조향 고정", "weave": "위빙", "brake": "급제동"}
ALARMS = (("alarm_gru", "GRU"), ("alarm_r1", "R1"), ("alarm_r2", "R2"))


class Session:
    """시뮬 + 탐지기 + 경보 로그 + 주입별 탐지 지연·경계 전 경보 집계. UI와 헤드리스가 같이 쓴다."""

    def __init__(self, seed=0, sigma_meas=0.1, signal_assumption=True, bg=True, gap=40.0, lane=2, v0=25.0, backend="off",
                 v_ego=25.0):
        self.init = dict(seed=seed, sigma_meas=sigma_meas, bg=bg, gap=gap, lane=lane, v0=v0, v_ego=v_ego)
        self.signal_assumption = signal_assumption
        self.dets = {}
        self.ctl = EC.EgoController(backend, v_set=v_ego)
        self.reset()

    def detector(self, sm):
        st = SM_SETTING[sm]
        if st not in self.dets:
            self.dets[st] = Detector(st)
        d = self.dets[st]
        d.reset()
        d.r2_enabled = self.signal_assumption and d.cfg["r2_calib_ok"]
        return d

    def reset(self):
        self.sim = SM.Sim(**self.init)
        self.det = self.detector(self.sim.sigma_meas)
        self.log = deque(maxlen=200)
        self.injections = []
        self.prev = {k: False for k, _ in ALARMS}
        self.obs = self.out = None
        self.ctl.reset(self.sim.ego)
        self.u = None                   # 자차에 다음 스텝 적용할 (a, 요레이트)
        self.ego_rec = dict(a=0.0, steer=0.0, n_coll=0, hit=False, gap_min=float("inf"), solve_ms=[])
        self.note(f"리셋 (자차 제어 {self.ctl.backend})")

    def note(self, msg, t=None):
        """t 기본값 = 키 입력이 처음 반영되는 다음 스텝 시각."""
        self.log.append(((self.sim.k + 1) * DT if t is None else t, msg))

    @property
    def r2_state(self):
        c = self.det.cfg
        if not c["r2_calib_ok"]:
            return "보정 실패(v12) · 경보 미반영"
        return "사용" if self.det.r2_enabled else "꺼짐(신호 가정 off, v12) · 경보 미반영"

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
        elif k == "T":
            self.signal_assumption = not self.signal_assumption
            self.det.r2_enabled = self.signal_assumption and self.det.cfg["r2_calib_ok"]
            self.note(f"신호 가정 {'on' if self.signal_assumption else 'off(turn_signal=0 전송)'} · R2 {self.r2_state}")
        elif k == "N":
            sm = 0.3 if s.sigma_meas == 0.1 else 0.1
            s.set_sigma_meas(sm)
            self.det = self.detector(sm)
            self.prev = {k: False for k, _ in ALARMS}
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
            self.injections.append(dict(kind=kind, level=s.inj["level"], k0=s.inj["k0"], cross_k=None, first={},
                                        sig=self.sent_signal()))
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
            return f"{L[0]:.1f} m / {L[1]:.1f} s"
        return f"{SM.BRAKE_A:.0f} m/s² {SM.BRAKE_T:.0f} s"

    def sent_signal(self):
        return self.sim.turn_signal if self.signal_assumption else 0

    def step(self, held=()):
        o = self.sim.step(held, ego_u=self.u)
        out = self.det.step(o["a_obs"], o["r_obs"], o["d_obs"], self.sent_signal(), o["v_meas"])
        k = o["k"]
        self.control(o, out)
        for key, name in ALARMS:
            if out[key] and not self.prev[key]:
                self.note(f"{name} 경보", o["t"])
            self.prev[key] = out[key]
        if self.injections:
            j = self.injections[-1]
            if o["inj"] is not None and o["inj"]["k0"] == j["k0"] and o["inj"]["cross_k"] is not None:
                j["cross_k"] = o["inj"]["cross_k"]
            if k - j["k0"] < 600:                                     # onset 후 60 s (v12 탐지 창)
                for key, name in ALARMS + (("alarm_rule", "규칙"), ("alarm", "전체")):
                    if out[key] and name not in j["first"] and k >= j["k0"]:
                        j["first"][name] = k
        self.obs, self.out = o, out
        return o, out

    def control(self, o, out):
        """이웃 상태 = BSM 관측(y_obs, v_meas, r_obs) + 참 x·ψ(종위치·방향각 잡음은 모델링 안 함). 결과는 다음 스텝에 적용."""
        ego = o["ego"]
        nb = dict(x=o["x"], y=o["y_obs"], psi=o["psi"], v=o["v_meas"], r=o["r_obs"])
        a, d = self.ctl.step(ego, nb, out["tau"], out["alarm"])
        self.u = (a, self.ctl.yaw_rate(d, ego[3]))
        dx, dy, g_lon, g_lat, g, hit = EC.gaps(ego, o["x"], o["y"])            # 지표는 참 위치로
        r = self.ego_rec
        if hit and not r["hit"]:
            r["n_coll"] += 1
            self.note(f"충돌 #{r['n_coll']} ({self.ctl.backend})", o["t"])
        if self.ctl.info.get("fallback_now"):
            self.note(f"NMPC fallback #{self.ctl.info['fallback']}", o["t"])
        if "solve_ms" in self.ctl.info:
            r["solve_ms"].append(self.ctl.info["solve_ms"])
        r.update(a=a, steer=d, hit=hit, dx=dx, dy=dy, g_lon=g_lon, g_lat=g_lat, gap=g, gap_min=min(r["gap_min"], g),
                 ttc=EC.ttc(ego, o["x"], o["v"]))

    def inj_report(self, j=None):
        """주입별 탐지 지연 [s]과 경계 통과 전 경보 여부(시스템 = GRU ∨ 규칙)."""
        j = j or (self.injections[-1] if self.injections else None)
        if j is None:
            return None
        r = dict(kind=j["kind"], level=j["level"], t0=j["k0"] * DT,
                 cross=None if j["cross_k"] is None else (j["cross_k"] - j["k0"]) * DT,
                 delay={n: (k - j["k0"]) * DT for n, k in j["first"].items()})
        f = j["first"].get("전체")
        if j["kind"] != "brake":
            if f is not None and (j["cross_k"] is None or f < j["cross_k"]):
                r["before_cross"] = True
                r["lead"] = None if j["cross_k"] is None else (j["cross_k"] - f) * DT
            elif f is not None or j["cross_k"] is not None:
                r["before_cross"] = False
            else:
                r["before_cross"] = None
        return r


def run_script(script, verbose=True, trace=None, backend=None):
    """헤드리스: 스크립트의 키 입력을 같은 Session에 10 Hz로 넣는다.
    events: {"t": s, "key": "Q"} (한 번 누름) 또는 {"t": s, "hold": "D", "dur": s} (누르고 있기)."""
    init = script.get("init", {})
    ses = Session(seed=script.get("seed", 0), sigma_meas=script.get("sigma_meas", 0.1),
                  signal_assumption=script.get("signal_assumption", True), bg=script.get("bg", True),
                  backend=backend or script.get("backend", "off"), **init)
    taps = sorted((e["t"], e["key"]) for e in script.get("events", []) if "key" in e)
    holds = [(e["t"], e["t"] + e["dur"], e["hold"]) for e in script.get("events", []) if "hold" in e]
    n = int(round(script.get("duration", 60.0) / DT))
    ti = 0
    rows = []
    for k in range(n):
        t = k * DT
        while ti < len(taps) and taps[ti][0] < t + DT / 2:
            ses.key(taps[ti][1])
            ti += 1
        held = {h for a, b, h in holds if a - DT / 2 <= t < b - DT / 2}
        o, out = ses.step(held)
        if trace is not None:
            rows.append({**{k2: o[k2] for k2 in ("t", "y", "d_true", "d_obs", "v", "turn_signal")},
                         **{k2: out[k2] for k2 in ("tau", "tau_gru", "tau_rule", "e", "R1_S", "R2_ttb", "alarm_gru", "alarm_r1",
                                                   "alarm_r2", "alarm_r2_shadow", "alarm", "intent_window")},
                         **dict(zip(("ego_x", "ego_y", "ego_th", "ego_v"), map(float, o["ego"]))),
                         **{f"ego_{k2}": ses.ego_rec[k2] for k2 in ("a", "steer", "gap", "hit", "ttc")},
                         "ctl_state": ses.ctl.info.get("state"), "solve_ms": ses.ctl.info.get("solve_ms")})
    if verbose:
        print(BANNER)
        for t, m in ses.log:
            print(f"t={t:6.1f}s  {m}")
        for j in ses.injections:
            print("주입 결과:", ses.inj_report(j))
        r = ses.ego_rec
        sm = np.array(r["solve_ms"])
        print(f"자차 제어 {ses.ctl.backend}: 충돌 {r['n_coll']}회, 최소 간격 {r['gap_min']:.2f} m"
              + (f", 풀이 {np.median(sm):.1f}/{np.percentile(sm, 95):.1f}/{sm.max():.1f} ms (중앙/p95/최대), "
                 f"fallback {ses.ctl.info['fallback']}" if len(sm) else ""))
    if trace is not None:
        import csv
        with open(trace, "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(rows[0]))
            w.writeheader()
            w.writerows(rows)
    return ses


# ---------------------------------------------------------------- UI
def run_ui(seed, backend="off"):
    import pygame
    pygame.init()
    Wd, Hd = 1440, 880
    screen = pygame.display.set_mode((Wd, Hd))
    pygame.display.set_caption("v13 이상운전 탐지 시연 — " + BANNER)
    fp = "/System/Library/Fonts/AppleSDGothicNeo.ttc"
    F = {s: (pygame.font.Font(fp, s) if Path(fp).exists() else pygame.font.SysFont(None, s + 4)) for s in (14, 16, 18, 22, 26)}
    clock = pygame.time.Clock()
    ses = Session(seed=seed, backend=backend)
    paused, acc = False, 0.0
    hist = deque(maxlen=300)
    tau_win = deque(maxlen=30)                       # 위험 박스 표시용 3 s 최소 τ (표시 전용)
    ui = dict(held=set(), focused=True)
    C = dict(bg=(18, 20, 24), grass=(34, 52, 38), road=(52, 54, 60), line=(225, 225, 225), ego=(70, 150, 255),
             nb=(240, 200, 60), red=(235, 70, 70), amber=(255, 170, 40), green=(80, 200, 120), txt=(230, 230, 230),
             dim=(150, 150, 160), panel=(28, 30, 36), gru=(170, 120, 255), rule=(60, 200, 220))
    RX0, RW = 0, 620
    CX, EGO_PY = RX0 + RW // 2, 640
    SY, SX = 26.0, 10.0                              # px/m (횡 2.6배 확대)

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
        return r

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

    def bar(x, y, w, h, val, label, col):
        pygame.draw.rect(screen, (60, 62, 70), (x, y, w, h))
        pygame.draw.rect(screen, C["red"] if val <= TAU_ALARM else col, (x, y, int(w * np.clip(val, 0, 1)), h))
        tx = x + int(w * TAU_ALARM)
        pygame.draw.line(screen, C["txt"], (tx, y - 3), (tx, y + h + 3), 2)
        text(f"{label} {val:.2f}", (x + w + 10, y - 2), 18)

    def draw_road(o):
        screen.fill(C["grass"], (RX0, 0, RW, Hd))
        x0, x1 = CX - 1.5 * SM.W * SY, CX + 1.5 * SM.W * SY
        pygame.draw.rect(screen, C["road"], (x0, 0, x1 - x0, Hd))
        for yl in (1.5 * SM.W, -1.5 * SM.W):
            px = CX - yl * SY
            pygame.draw.line(screen, C["line"], (px, 0), (px, Hd), 3)
        ph = (o["ego"][0] % 12.0)
        for yl in (SM.HALF, -SM.HALF):
            px = CX - yl * SY
            for m in np.arange(-30, 80, 12.0):
                _, a = w2s(m - ph, 0)
                _, b = w2s(m - ph + 3.0, 0)
                pygame.draw.line(screen, C["line"], (px, a), (px, b), 2)

    def draw():
        o, out = ses.obs, ses.out
        screen.fill(C["bg"])
        draw_road(o)
        risk = min(tau_win) if tau_win else 1.0
        ex, ey, eth, ev = o["ego"]
        xr = o["x"] - ex
        blink = (pygame.time.get_ticks() // 300) % 2 == 0
        sg = "L" if ses.sim.sig_l else "R" if ses.sim.sig_r else None
        if risk <= TAU_ALARM:
            m = (TAU_ALARM - risk) / TAU_ALARM
            ml, mx = 0.6 + 1.6 * m, 3.0 + 8.0 * m
            a, b = w2s(xr + 2.25 + mx, o["y"] + 0.9 + ml), w2s(xr - 2.25 - mx, o["y"] - 0.9 - ml)
            surf = pygame.Surface((abs(b[0] - a[0]), abs(b[1] - a[1])), pygame.SRCALPHA)
            surf.fill((235, 70, 70, 70))
            screen.blit(surf, (min(a[0], b[0]), min(a[1], b[1])))
            pygame.draw.rect(screen, C["red"], (min(a[0], b[0]), min(a[1], b[1]), abs(b[0] - a[0]), abs(b[1] - a[1])), 2)
        xp = getattr(ses.ctl.impl, "x_pred", None)
        if ses.ctl.backend == "nmpc" and xp is not None:                 # NMPC 예측 궤적
            for k_ in range(xp.shape[1]):
                pygame.draw.circle(screen, C["ego"], [int(v) for v in w2s(xp[0, k_] - ex, xp[1, k_])], 3)
        car(0.0, ey, eth, C["red"] if ses.ego_rec["hit"] else C["ego"])
        _, py = w2s(xr, 0)
        if 0 < py < Hd:
            car(xr, o["y"], o["psi"], C["nb"], sg if blink else None)
        else:
            up = py <= 0
            px = w2s(0, o["y"])[0]
            yy = 30 if up else Hd - 30
            pygame.draw.polygon(screen, C["nb"], [(px, yy + (-14 if up else 14)), (px - 10, yy), (px + 10, yy)])
            text(f"{xr:+.0f} m", (px + 14, yy - 10), 16, C["nb"])
        text(f"자차 [{ses.ctl.backend}]", w2s(-3.5, ey), 16, C["ego"], center=True)
        pygame.draw.rect(screen, (0, 0, 0), (RX0, 0, RW, 30))
        text(BANNER, (RX0 + 10, 5), 16, C["amber"])
        text("횡방향 2.6배 확대 표시", (RX0 + 10, Hd - 24), 14, C["dim"])
        # 입력 확인 (왼쪽 아래): 눌린 키, 페달 가속, 요레이트 명령, 횡속도
        s_ = ses.sim
        pygame.draw.rect(screen, (0, 0, 0), (RX0, Hd - 98, RW, 46))
        for i, kn in enumerate("WASD"):
            on = kn in ui["held"]
            r_ = pygame.Rect(RX0 + 10 + 34 * i, Hd - 90, 30, 30)
            pygame.draw.rect(screen, C["amber"] if on else (70, 72, 80), r_, 0 if on else 2)
            text(kn, r_.center, 18, (20, 20, 20) if on else C["dim"], center=True)
        vy = o["v"] * np.sin(o["psi"])
        text(f"페달 {s_.a_user:+.1f} m/s²  요레이트 명령 {s_.r_cmd:+.3f} rad/s  횡속도 {vy:+.2f} m/s", (RX0 + 150, Hd - 84), 14)
        if not ui["focused"]:
            text("창이 키보드 포커스를 잃었음 — 창을 한 번 클릭하세요", (RX0 + 10, Hd - 120), 18, C["red"])
        # ---- HUD
        X = RW + 20
        pygame.draw.rect(screen, C["panel"], (RW, 0, Wd - RW, Hd))
        c = ses.det.cfg
        text(f"t = {o['t']:6.1f} s   설정 {c['setting']}   σ_meas {ses.sim.sigma_meas:.1f} m   신호 가정 "
             f"{'ON' if ses.signal_assumption else 'OFF'}{'   [일시정지]' if paused else ''}", (X, 10), 18)
        text(f"R2: {ses.r2_state}" + ("" if out["active"] else "   (준비 중: 시작 10 s는 경보 없음)"), (X, 34), 16, C["dim"])
        dd = o["d_obs"]
        text(f"상대 d = {dd:+.2f} m (관측)   경계까지 {SM.HALF - abs(dd):.2f} m   v = {o['v']:.1f} m/s   상대 거리 {xr:+.1f} m",
             (X, 64), 18)
        sgs = {"L": "좌 ◀", "R": "우 ▶", None: "꺼짐"}[sg]
        text(f"방향지시등 {sgs}  (전송 turn_signal={ses.sent_signal()})", (X, 90), 18, C["amber"] if sg else C["txt"])
        text("의도구간(게이팅) " + ("● 켜짐: 규칙 경보 동결" if out["intent_window"] else "○"), (X + 400, 90), 18,
             C["amber"] if out["intent_window"] else C["dim"])
        bar(X, 130, 360, 22, out["tau"], "τ (최종 = min)", C["green"])
        bar(X, 166, 360, 16, out["tau_gru"], "τ_GRU", C["gru"])
        bar(X, 194, 360, 16, out["tau_rule"], "τ_rule = min(τ_R1, τ_R2)", C["rule"])
        text(f"τ_R1 {out['tau_r1']:.2f}   τ_R2 {out['tau_r2']:.2f}   (표시용 τ = 0.7^(점수/임계), τ ≤ 0.7 ⇔ v12 경보)", (X, 220), 14, C["dim"])
        ts = out["R2_ttb"]
        rows = [(f"GRU e = {out['e']:8.1f}   /  θ = {c['theta']:.1f}", out["e"] > c["theta"]),
                (f"R1 누적합 S = {out['R1_S']:5.2f} m·s  /  h_d = {c['h_d']:.2f}   (ν_d {c['nu_d']} m, W_s {c['ws']} s)",
                 out["alarm_r1"]),
                (f"R2 경계 도달 예측 T* = {'2 s 내 없음' if not np.isfinite(ts) else f'{ts:.1f} s'}  /  T_c = {c['T_c']} s"
                 f"   (τ_f {c['tau_f']} s)", ts <= c["T_c"])]
        for i, (s, warn) in enumerate(rows):
            text(s, (X, 248 + 26 * i), 18, C["red"] if warn else C["txt"])
        # τ 시계열 (최근 30 s)
        gx, gy, gw, gh = X, 340, 760, 110
        pygame.draw.rect(screen, (40, 42, 50), (gx, gy, gw, gh))
        yt = gy + gh * (1 - TAU_ALARM)
        pygame.draw.line(screen, (120, 60, 60), (gx, yt), (gx + gw, yt), 1)
        for key, col in (("tau_gru", C["gru"]), ("tau_rule", C["rule"]), ("tau", C["txt"])):
            if len(hist) > 1:
                pts = [(gx + gw * (i + 300 - len(hist)) / 299, gy + gh * (1 - h[key])) for i, h in enumerate(hist)]
                pygame.draw.lines(screen, col, False, pts, 2 if key == "tau" else 1)
        for i, h in enumerate(hist):
            if h["alarm"]:
                xx = gx + gw * (i + 300 - len(hist)) / 299
                pygame.draw.line(screen, C["red"], (xx, gy + gh - 6), (xx, gy + gh), 2)
        text("τ 최근 30 s  (흰 τ, 보라 τ_GRU, 청록 τ_rule, 빨간 눈금 = 경보)", (gx, gy + gh + 4), 14, C["dim"])
        # 주입 상태 + 결과
        inj = ses.sim.inj
        y0 = 480
        text("이상 주입: " + ("없음(수동 조작)" if inj is None else f"{INJ_NAME[inj['kind']]} {ses.level_str(inj)}"), (X, y0), 18,
             C["amber"] if inj else C["txt"])
        text(f"[ ] 크기: 드리프트 {SM.DRIFT_LEVELS[ses.sim.drift_i]:.2f} m/s · 조향 고정 {SM.STUCK_LEVELS[ses.sim.stuck_i]:.4f} rad/s · "
             f"다음 위빙 {SM.WEAVE_LEVELS[ses.sim.weave_i][0]:.1f} m", (X, y0 + 24), 14, C["dim"])
        r = ses.inj_report()
        if r:
            dl = "  ".join(f"{n} {r['delay'][n]:.1f}s" if n in r["delay"] else f"{n} —" for n in ("GRU", "R1", "R2", "전체"))
            text(f"마지막 주입 t={r['t0']:.1f}s {INJ_NAME[r['kind']]}  탐지 지연: {dl}", (X, y0 + 48), 16)
            if r["kind"] != "brake":
                cr = "아직 안 넘음" if r["cross"] is None else f"+{r['cross']:.1f} s"
                bc = r.get("before_cross")
                verdict = ("경계 전 경보: 예" + (f" (여유 {r['lead']:.1f} s)" if r.get("lead") is not None else "")) if bc else (
                    "경계 전 경보: 아니오" if bc is False else "대기 중")
                text(f"경계 통과 {cr}   →   {verdict}", (X, y0 + 70), 16, C["green"] if bc else C["red"] if bc is False else C["dim"])
        # 자차 제어
        er, inf = ses.ego_rec, ses.ctl.info
        cy = 572
        pygame.draw.line(screen, (70, 72, 80), (X, cy - 4), (X + 760, cy - 4), 1)
        sm_txt = f"   풀이 {inf['solve_ms']:5.1f} ms   fallback {inf['fallback']}회" if ses.ctl.backend == "nmpc" else ""
        text(f"자차 제어 [{ses.ctl.backend}] (C 순환)   상태 {inf.get('state', '—')}{sm_txt}", (X, cy), 18,
             C["red"] if inf.get("fallback_now") else C["amber"] if inf.get("state", "정상") != "정상" else C["txt"])
        text(f"자차 v {ev:5.1f} m/s   Y {ey:+.2f} m   a {er['a']:+.1f} m/s²   δ {er['steer']:+.3f} rad", (X, cy + 24), 16)
        tt = er.get("ttc", float("inf"))
        text(f"이웃 간격 종 {er.get('g_lon', 0):5.1f} m  횡 {er.get('g_lat', 0):4.2f} m   (중심 dx {er.get('dx', 0):+.1f}, dy {er.get('dy', 0):+.2f})"
             f"   최소 간격 {er['gap_min']:.2f} m   TTC {'∞' if not np.isfinite(tt) else f'{tt:.1f} s'}", (X, cy + 46), 16)
        text(f"충돌 {'● 지금' if er['hit'] else '○'}   누적 {er['n_coll']}회", (X, cy + 68), 16,
             C["red"] if er["hit"] or er["n_coll"] else C["txt"])
        # 경보 로그
        ly = 668
        text("로그", (X, ly), 16, C["dim"])
        for i, (t, m) in enumerate(list(ses.log)[-6:]):
            text(f"t={t:6.1f}s  {m}", (X, ly + 20 + 18 * i), 14, C["red"] if "경보" in m else C["txt"])
        keys = ("W/S 가속·제동  A/D 조향  Q/E 좌/우 지시등 토글   1 드리프트  2 조향 고정  3 위빙  4 급제동  0 해제  [ ] 크기",
                "T 신호 가정 on/off   N σ_meas 0.1↔0.3   C 자차 제어 off/rule/nmpc   R 리셋   P 일시정지   Esc 종료")
        for i, s in enumerate(keys):
            text(s, (X, Hd - 46 + 20 * i), 14, C["dim"])
        pygame.display.flip()

    # 물리 키 위치(scancode)로 받는다: 한글 입력 소스·자판 배열과 무관. IME가 키를 가져가지 않게 텍스트 입력을 끈다.
    pygame.key.stop_text_input()
    scan = {pygame.KSCAN_W: "W", pygame.KSCAN_S: "S", pygame.KSCAN_A: "A", pygame.KSCAN_D: "D", pygame.KSCAN_Q: "Q",
            pygame.KSCAN_E: "E", pygame.KSCAN_1: "1", pygame.KSCAN_2: "2", pygame.KSCAN_3: "3", pygame.KSCAN_4: "4",
            pygame.KSCAN_0: "0", pygame.KSCAN_LEFTBRACKET: "[", pygame.KSCAN_RIGHTBRACKET: "]", pygame.KSCAN_T: "T",
            pygame.KSCAN_N: "N", pygame.KSCAN_C: "C", pygame.KSCAN_R: "R", pygame.KSCAN_P: "P", pygame.KSCAN_ESCAPE: "Esc"}
    pedal = {"W", "S", "A", "D"}
    held, tapped = set(), set()          # tapped: 두 스텝 사이에 눌렀다 뗀 짧은 입력도 한 스텝은 반영
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
                    tau_win.clear()
                    ses.step(())
        ui["held"], ui["focused"] = held | tapped, pygame.key.get_focused()
        if paused:
            acc = 0.0
        acc = min(acc, 0.5)
        while acc >= DT:
            acc -= DT
            _, out = ses.step(held | tapped)
            tapped.clear()
            hist.append(out)
            tau_win.append(out["tau"])
        draw()
    pygame.quit()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--headless", action="store_true")
    ap.add_argument("--script", default=str(V13 / "demo.json"))
    ap.add_argument("--trace", default=None, help="헤드리스: 스텝별 CSV 출력 경로")
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--backend", choices=EC.BACKENDS, default=None, help="자차 제어 (기본: 스크립트 값 또는 off)")
    a = ap.parse_args()
    torch.set_num_threads(1)
    if a.headless:
        run_script(json.load(open(a.script)), trace=a.trace, backend=a.backend)
    else:
        run_ui(a.seed, a.backend or "off")
