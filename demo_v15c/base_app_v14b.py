"""v14b 실시간 조작 비교판: 상대 차량을 WASD로 몰면서 v12 A2, v14 B-W, v14b B14b의 τ·경보를 동시에 본다.
합성 환경 · 직선 도로 · 시뮬레이션. 방향지시등 = 가정. demo_v13·scratch_v14(app_v14)는 import만 하고 수정하지 않는다.

B14b = ν p90 · 홀드 3 s · T_r 20 s · cap 2θ · 의도구간 동결 (v14b 검증 선택, 인덱스 10). ν·h는 v14b 결과 파일 값 그대로(재보정 없음):
  신호 가정 ON → p_sig 1.0 보정값, OFF → p_sig 0.0 보정값. σ_meas 0.3은 σ_meas 0.3 검증 재보정값.
v14b 시스템 경보 = B14b 경로 ∨ R1 ∨ R2. 보고서: weave_L2 88%이지만 급성 이상 뒤 경보가 약 1분 지속 — 관찰용이다.

사용:
  /opt/anaconda3/bin/python -I -B app_v14b.py
  /opt/anaconda3/bin/python -I -B app_v14b.py --headless --script ../scratch_v14/weave_v14.json [--trace out.csv]
"""
import argparse
import json
import sys
from collections import deque
from pathlib import Path

sys.dont_write_bytecode = True
V14B = Path(__file__).resolve().parent
ROOT = V14B.parent
sys.path[:0] = [str(V14B), str(ROOT / "scratch_v14"), str(ROOT / "demo_v13")]

import numpy as np  # noqa: E402
import torch  # noqa: E402

import accum_v14b as AB  # noqa: E402
import app_v14 as APP14  # noqa: E402  (scratch_v14, 수정 없음)
from app_v14 import A13, SM, TAU_ALARM, cross_str, ttb_str  # noqa: E402
from detector import ACT  # noqa: E402

DT = SM.DT
BANNER = "합성 환경 · 직선 도로 · 시뮬레이션  |  방향지시등 = 가정  |  B14b: 위빙 L2 탐지 ↑, 대신 경보가 오래 지속 — 관찰용"
B_IDX = 10


def load_b(setting):
    """v14b 결과에서 B14b 변형 정의와 (신호 가정 ON/OFF) ν·h를 읽는다."""
    R = json.load(open(V14B / "results" / "eval_v14b.json"))
    assert R["selection"]["W"]["idx"] == B_IDX, R["selection"]["W"]
    bv = R["settings"][setting]["b_variants"]
    th = R["settings"][setting]["theta"]
    on, off = bv["B14b p1.0"], bv["B14b p0.0"]
    v = AB.grid()[B_IDX]
    return dict(variant=v, name=AB.vname(v), theta=th, on=(on["nu_theta"] * th, on["h"]), off=(off["nu_theta"] * th, off["h"]))


class BOnline:
    """accum_v14b._step의 한 표본 판 (V = 1). 홀드: 마지막 증가 뒤 T_hold 동안 S가 줄지 않음. i < ACT에서는 갱신·경보 없음."""

    def __init__(self, v, theta, nu, h):
        self.rho = float(np.exp(-DT / v["Tr"]))
        self.cap = theta * v["cap"]
        self.hold = int(round(v["hold"] / DT))
        self.gate = bool(v["gate"])
        self.set(nu, h)
        self.reset()

    def set(self, nu, h):
        self.nu, self.h = nu, h

    def reset(self):
        self.S, self.tinc, self.i = 0.0, -10 ** 9, -1

    def step(self, e, intent):
        self.i += 1
        if self.i >= ACT and not (self.gate and intent):
            cand = max(0.0, self.rho * self.S + min(e, self.cap) - self.nu)
            if cand > self.S:
                self.S, self.tinc = cand, self.i
            elif self.i - self.tinc > self.hold:
                self.S = cand
        held = self.i - self.tinc <= self.hold
        alarm = self.i >= ACT and self.S > self.h
        tau = TAU_ALARM ** (self.S / self.h) if self.i >= ACT else 1.0
        return self.S, bool(alarm), tau, held


class Session14b(APP14.Session14):
    """Session14(A2 + B-W) + B14b 그림자 경로."""

    def __init__(self, **kw):
        self.bcfg2 = {st: load_b(st) for st in ("nom", "sm0.30")}
        self.B2 = {}
        super().__init__(**kw)

    def _b2_params(self):
        c = self.bcfg2[self.det.cfg["setting"]]
        return c["on"] if self.signal_assumption else c["off"]

    def detector(self, sm):
        d = super().detector(sm)
        st = d.cfg["setting"]
        c = self.bcfg2[st]
        if st not in self.B2:
            self.B2[st] = BOnline(c["variant"], c["theta"], *c["on"])
        self.b2 = self.B2[st]
        self.b2.reset()
        self.b2.set(*(c["on"] if self.signal_assumption else c["off"]))
        self.prev14b = False
        return d

    @property
    def b2_label(self):
        th = self.bcfg2[self.det.cfg["setting"]]["theta"]
        return (f"ν = {self.b2.nu / th:.3f}θ, h = {self.b2.h / th:.2f}θ "
                f"({'p_sig 1.0' if self.signal_assumption else 'p_sig 0.0'} 검증 보정)")

    def key(self, k):
        super().key(k)
        if k == "T":
            self.b2.set(*self._b2_params())
            self.note(f"B14b {self.b2_label}")

    def step(self, held=()):
        o, out = super().step(held)
        S_, a_b, tau_s, hold_on = self.b2.step(out["e"], out["intent_window"])
        alarm14b = bool(a_b or out["alarm_rule"])
        out.update(B2_S=S_, B2_h=self.b2.h, B2_nu=self.b2.nu, alarm_b2path=a_b, tau_state2=tau_s, hold_on=hold_on,
                   tau14b=min(tau_s, out["tau_rule"]), alarm14b=alarm14b)
        if alarm14b != self.prev14b:
            if alarm14b:
                src = " + ".join(n for n, x in (("B14b 경로", a_b), ("규칙", out["alarm_rule"])) if x)
                self.note(f"v14b 경보 시작 ({src})", o["t"])
            else:
                self.note("v14b 경보 해제", o["t"])
        self.prev14b = alarm14b
        if self.injections:
            j = self.injections[-1]
            k = o["k"]
            if k - j["k0"] < 600 and k >= j["k0"]:
                for key, name in (("alarm_b2path", "B14b경로"), ("alarm14b", "v14b 전체")):
                    if out[key] and name not in j["first"]:
                        j["first"][name] = k
        return o, out


def inj_line(r):
    d = r["delay"]
    f = lambda n: f"{d[n]:.1f}s" if n in d else "—"  # noqa: E731
    return f"A2 {f('전체')}   v14 {f('v14 전체')}   v14b {f('v14b 전체')} (B14b 경로 {f('B14b경로')})"


def run_script(script, verbose=True, trace=None):
    ses = Session14b(seed=script.get("seed", 0), sigma_meas=script.get("sigma_meas", 0.1),
                     signal_assumption=script.get("signal_assumption", True), bg=script.get("bg", True), **script.get("init", {}))
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
            rows.append({**{c: o[c] for c in ("t", "y", "d_obs", "turn_signal")},
                         **{c: out[c] for c in ("e", "intent_window", "tau", "alarm", "tau14", "alarm14", "B2_S", "B2_h", "tau_state2",
                                                "tau14b", "alarm_b2path", "alarm14b", "hold_on")}})
    if verbose:
        print(BANNER)
        print("B14b:", ses.bcfg2[ses.det.cfg["setting"]]["name"], "|", ses.b2_label)
        for t, m in ses.log:
            if m.endswith("GRU 경보"):
                continue
            print(f"t={t:6.1f}s  {m}")
        for j in ses.injections:
            r = ses.inj_report(j)
            print(f"주입 {A13.INJ_NAME[r['kind']]} t={r['t0']:.1f}s  탐지 지연: {inj_line(r)}"
                  + ("" if r["kind"] == "brake" else f"  | 경계 통과 {cross_str(r)}"))
    if trace is not None:
        import csv
        with open(trace, "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(rows[0]))
            w.writeheader()
            w.writerows(rows)
    return ses


# ---------------------------------------------------------------- UI
def run_ui(seed):
    import pygame
    pygame.init()
    Wd, Hd = 1440, 900
    screen = pygame.display.set_mode((Wd, Hd))
    pygame.display.set_caption("v14b A2 · B-W · B14b 실시간 비교 — " + BANNER)
    fp = "/System/Library/Fonts/AppleSDGothicNeo.ttc"
    F = {s: (pygame.font.Font(fp, s) if Path(fp).exists() else pygame.font.SysFont(None, s + 4)) for s in (14, 15, 16, 18, 22)}
    clock = pygame.time.Clock()
    ses = Session14b(seed=seed)
    paused, acc = False, 0.0
    hist = deque(maxlen=600)                        # 최근 60 s (B14b 경보가 오래 가므로 v14보다 길게)
    C = dict(bg=(18, 20, 24), grass=(34, 52, 38), road=(52, 54, 60), line=(225, 225, 225), ego=(70, 150, 255),
             nb=(240, 200, 60), red=(235, 70, 70), amber=(255, 170, 40), txt=(230, 230, 230), dim=(150, 150, 160),
             panel=(28, 30, 36), gru=(170, 120, 255), rule=(60, 200, 220), a2=(230, 230, 230), b14=(255, 150, 60), b14b=(120, 200, 255))
    RW, CX, EGO_PY, SY, SX = 620, 310, 640, 26.0, 10.0
    SYS = (("alarm", "tau", "v12 A2", "a2"), ("alarm14", "tau14", "v14 B-W", "b14"), ("alarm14b", "tau14b", "v14b B14b", "b14b"))

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
        ex, ey, eth, _ = o["ego"]
        ph = ex % 12.0
        for yl in (SM.HALF, -SM.HALF):
            for m in np.arange(-30, 80, 12.0):
                pygame.draw.line(screen, C["line"], (CX - yl * SY, w2s(m - ph, 0)[1]), (CX - yl * SY, w2s(m - ph + 3.0, 0)[1]), 2)
        xr = o["x"] - ex
        sg = "L" if ses.sim.sig_l else "R" if ses.sim.sig_r else None
        blink = (pygame.time.get_ticks() // 300) % 2 == 0
        car(0.0, ey, eth, C["ego"])
        py = w2s(xr, 0)[1]
        if 0 < py < Hd:
            for (key, _, _, cn), pad in zip(SYS, (0.4, 0.8, 1.2)):
                if out[key]:
                    a, b = w2s(xr + 2.25 + pad, o["y"] + 0.9 + pad), w2s(xr - 2.25 - pad, o["y"] - 0.9 - pad)
                    pygame.draw.rect(screen, C[cn], (min(a[0], b[0]), min(a[1], b[1]), abs(b[0] - a[0]), abs(b[1] - a[1])), 2)
            car(xr, o["y"], o["psi"], C["nb"], sg if blink else None)
        else:
            up = py <= 0
            px, yy = w2s(0, o["y"])[0], (30 if up else Hd - 30)
            pygame.draw.polygon(screen, C["nb"], [(px, yy + (-14 if up else 14)), (px - 10, yy), (px + 10, yy)])
            text(f"{xr:+.0f} m", (px + 14, yy - 10), 16, C["nb"])
        pygame.draw.rect(screen, (0, 0, 0), (0, 0, RW, 30))
        text("합성 · 직선 · 시뮬레이션 | 신호 = 가정", (10, 5), 16, C["amber"])
        text("상대 차량 테두리 = 경보: 흰 A2 · 주황 v14 · 하늘 v14b  (횡 2.6배 확대)", (10, Hd - 24), 14, C["dim"])
        pygame.draw.rect(screen, (0, 0, 0), (0, Hd - 98, RW, 46))
        for i, kn in enumerate("WASD"):
            on = kn in ui_held
            r_ = pygame.Rect(10 + 34 * i, Hd - 90, 30, 30)
            pygame.draw.rect(screen, C["amber"] if on else (70, 72, 80), r_, 0 if on else 2)
            text(kn, r_.center, 18, (20, 20, 20) if on else C["dim"], center=True)
        text(f"요레이트 명령 {ses.sim.r_cmd:+.3f} rad/s   횡속도 {o['v'] * np.sin(o['psi']):+.2f} m/s", (150, Hd - 84), 14)
        # ---- HUD
        X = RW + 20
        pygame.draw.rect(screen, C["panel"], (RW, 0, Wd - RW, Hd))
        c = ses.det.cfg
        th = c["theta"]
        text(f"t = {o['t']:6.1f} s   설정 {c['setting']}   σ_meas {ses.sim.sigma_meas:.1f} m   신호 가정 "
             f"{'ON' if ses.signal_assumption else 'OFF'}{'   [일시정지]' if paused else ''}", (X, 10), 18)
        text(f"R2: {ses.r2_state}" + ("" if out["active"] else "   (준비 중: 시작 10 s는 경보 없음)"), (X, 34), 16, C["dim"])
        text(f"상대 d = {o['d_obs']:+.2f} m   경계까지 {SM.HALF - abs(o['d_obs']):.2f} m   방향지시등 "
             f"{ {'L': '좌 ◀', 'R': '우 ▶', None: '꺼짐'}[sg]}", (X, 58), 18, C["amber"] if sg else C["txt"])
        if out["intent_window"]:
            text("● 의도구간: R1·R2·B-W·B14b 모두 동결 → 신호 켠 채 이상은 A2의 GRU 순간 경로만 잡음", (X, 82), 14, C["amber"])
        for i, (key, tk, nm, cn) in enumerate(SYS):
            bar(X, 108 + 32 * i, 360, 22, out[tk], f"{nm} τ", C[cn])
            if out[key]:
                text("● 경보", (X + 560, 108 + 32 * i), 18, C["red"])
        bar(X, 210, 300, 12, out["tau_gru"], "τ_GRU 순간 0.7^(e/θ)", C["gru"], 14)
        bar(X, 230, 300, 12, out["tau_state"], "τ_state B-W", C["b14"], 14)
        bar(X, 250, 300, 12, out["tau_state2"], "τ_state B14b", C["b14b"], 14)
        bar(X, 270, 300, 12, out["tau_rule"], "τ_rule (공통)", C["rule"], 14)
        lvl = out["B2_S"] / out["B2_h"]
        rows = [(f"e = {out['e']:7.1f} ({out['e'] / th:.3f}θ)    A2: e > θ   |   B-W: S {out['B_S'] / th:.2f}θ / h {out['B_h'] / th:.2f}θ, ν {out['B_nu'] / th:.2f}θ",
                 out["alarm_gru"] or out["alarm_bpath"]),
                (f"B14b 수위 S/h = {lvl:4.2f}   ({ses.b2_label})", out["alarm_b2path"]),
                (f"   S ← max(0, ρS + min(e, 2θ) - ν), ρ = exp(-0.1/20), 홀드 3 s {'● 유지 중(감소 멈춤)' if out['hold_on'] else '○'}", False),
                (f"R1 S = {out['R1_S']:4.2f} / h_d {c['h_d']:.2f}    R2 T* = {ttb_str(out['R2_ttb'])} / T_c {c['T_c']} s", out["alarm_rule"])]
        for i, (s, warn) in enumerate(rows):
            text(s, (X, 292 + 22 * i), 15, C["red"] if warn else C["txt"])
        # 양동이 게이지
        gx0, gy0, gw0, gh0 = X + 700, 108, 40, 150
        pygame.draw.rect(screen, (40, 42, 50), (gx0, gy0, gw0, gh0))
        fill = min(lvl, 1.5) / 1.5
        pygame.draw.rect(screen, C["red"] if lvl > 1 else C["b14b"], (gx0, gy0 + gh0 * (1 - fill), gw0, gh0 * fill))
        yh = gy0 + gh0 * (1 - 1 / 1.5)
        pygame.draw.line(screen, C["txt"], (gx0 - 4, yh), (gx0 + gw0 + 4, yh), 2)
        text("h", (gx0 + gw0 + 6, yh - 9), 14)
        text("B14b 수위", (gx0 - 10, gy0 + gh0 + 4), 14, C["dim"])
        # 시계열 60 s
        gx, gy, gw, gh = X, 386, 770, 150
        pygame.draw.rect(screen, (40, 42, 50), (gx, gy, gw, gh))
        for thr, cc in ((TAU_ALARM, (140, 60, 60)), (0.8, (80, 80, 90))):
            pygame.draw.line(screen, cc, (gx, gy + gh * (1 - thr)), (gx + gw, gy + gh * (1 - thr)), 1)
        n = hist.maxlen
        xs = lambda i: gx + gw * (i + n - len(hist)) / (n - 1)  # noqa: E731
        for key, col, wdt in (("tau_gru", C["gru"], 1), ("tau", C["a2"], 2), ("tau14", C["b14"], 2), ("tau14b", C["b14b"], 2)):
            if len(hist) > 1:
                pygame.draw.lines(screen, col, False, [(xs(i), gy + gh * (1 - h[key])) for i, h in enumerate(hist)], wdt)
        for i, h in enumerate(hist):
            for (key, _, _, cn), off in zip(SYS, (16, 10, 4)):
                if h[key]:
                    pygame.draw.line(screen, C[cn], (xs(i), gy + gh - off - 4), (xs(i), gy + gh - off), 2)
        text("최근 60 s: 흰 A2 · 주황 v14 · 하늘 v14b · 보라 τ_GRU 순간 (선: 0.7 경보, 0.8)   아래 눈금 = 각 시스템 경보",
             (gx, gy + gh + 4), 14, C["dim"])
        inj = ses.sim.inj
        y0 = 566
        text("이상 주입: " + ("없음(수동 조작)" if inj is None else f"{A13.INJ_NAME[inj['kind']]} {ses.level_str(inj)}"), (X, y0), 18,
             C["amber"] if inj else C["txt"])
        text(f"다음 위빙 {SM.WEAVE_LEVELS[ses.sim.weave_i][0]:.1f} m / {SM.WEAVE_LEVELS[ses.sim.weave_i][1]:.1f} s   "
             f"드리프트 {SM.DRIFT_LEVELS[ses.sim.drift_i]:.2f} m/s   조향 고정 {SM.STUCK_LEVELS[ses.sim.stuck_i]:.4f}", (X, y0 + 24), 14, C["dim"])
        r = ses.inj_report()
        if r:
            text(f"마지막 주입 t={r['t0']:.1f}s 탐지 지연 — {inj_line(r)}", (X, y0 + 46), 14)
            if r["kind"] != "brake":
                text(f"경계 통과 {cross_str(r)}", (X, y0 + 68), 14)
        ly = 662
        text("로그 (GRU 순간 경보 줄은 생략)", (X, ly), 16, C["dim"])
        logs = [(t, m) for t, m in ses.log if not m.endswith("GRU 경보")][-8:]
        for i, (t, m) in enumerate(logs):
            col = C["b14b"] if "v14b" in m else C["b14"] if "v14" in m else C["red"] if "경보" in m else C["txt"]
            text(f"t={t:6.1f}s  {m}", (X, ly + 20 + 18 * i), 14, col)
        keys = ("W/S 가속·제동  A/D 조향  Q/E 좌/우 지시등 토글   1 드리프트  2 조향 고정  3 위빙(0.3→0.5→0.8 m)  4 급제동  0 해제  [ ] 크기",
                "T 신호 가정 on/off (B-W·B14b 값도 p_sig 1.0↔0.0으로)   N σ_meas 0.1↔0.3   R 리셋   P 일시정지   Esc 종료")
        for i, s in enumerate(keys):
            text(s, (X, Hd - 46 + 20 * i), 14, C["dim"])
        pygame.display.flip()

    pygame.key.stop_text_input()
    scan = {pygame.KSCAN_W: "W", pygame.KSCAN_S: "S", pygame.KSCAN_A: "A", pygame.KSCAN_D: "D", pygame.KSCAN_Q: "Q",
            pygame.KSCAN_E: "E", pygame.KSCAN_1: "1", pygame.KSCAN_2: "2", pygame.KSCAN_3: "3", pygame.KSCAN_4: "4",
            pygame.KSCAN_0: "0", pygame.KSCAN_LEFTBRACKET: "[", pygame.KSCAN_RIGHTBRACKET: "]", pygame.KSCAN_T: "T",
            pygame.KSCAN_N: "N", pygame.KSCAN_R: "R", pygame.KSCAN_P: "P", pygame.KSCAN_ESCAPE: "Esc"}
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
    ap.add_argument("--script", default=str(ROOT / "scratch_v14" / "weave_v14.json"))
    ap.add_argument("--trace", default=None, help="헤드리스: 스텝별 CSV 출력 경로")
    ap.add_argument("--seed", type=int, default=0)
    a = ap.parse_args()
    torch.set_num_threads(1)
    if a.headless:
        run_script(json.load(open(a.script)), trace=a.trace)
    else:
        run_ui(a.seed)
