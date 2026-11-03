"""v15b 시뮬레이터 (v15 sim + 키 입력 완화(README 1절); demo_v13/sim.py 복사 후 수정: 이상 강도 목록·기본값(변경 2), W/S 0.5 s 램프(변경 4), 급가속 주입 키 5): 직선 3차로, 자차(가운데 차로 출발, ego_u로 제어) + 상대 차량(수동 조작 + 이상 주입) + BSM 센서. UI 없음.
합성 환경, 직선 도로, 시뮬레이션.

자차 = 운동학 [X, Y, θ, v]: X' = v cosθ, Y' = v sinθ, θ' = 요레이트, v' = a (NMPC 예측 모델과 같은 전진 오일러).
step(ego_u=None)이면 이전처럼 등속 직진(v_ego). 자차는 상대 차량 동역학·센서에 영향을 주지 않는다.

상대 차량 참 상태 = 운동학(ψ' = r, x' = v cosψ, y' = v sinψ) + 생성기 배경(gen_v10.base, 차선변경 없음) + 이상 주입 횡변위.
센서는 gen_v10.observe와 같은 잡음·필터를 한 표본씩 적용한다(test_equiv.py가 수동 입력 0일 때 observe와 같은지 확인).
"""
import sys
from pathlib import Path

sys.dont_write_bytecode = True
V15 = Path(__file__).resolve().parent
ROOT = V15.parent
sys.path[:0] = [str(ROOT / "scratch_v11h"), str(ROOT / "scratch_v10")]

import numpy as np  # noqa: E402
from scipy.signal import sosfilt, sosfilt_zi  # noqa: E402

import gen_v10 as G  # noqa: E402

DT = G.DT
W = G.W_LANE
HALF = W / 2
N_BG = 36000                # 배경 잡음 미리 생성 길이 (1 h), 넘으면 처음부터 반복

# 조작 (차량 모델 파라미터, 탐지기 보정과 무관)
A_MAX, B_MAX = 1.5, 3.0     # v15b 변경 A: W 목표 +1.5, S 목표 −3 [m/s²] (v15: +4/−8)
A_RAMP_T = 2.0              # v15b 변경 A: 목표가 바뀔 때마다 현재 값 → 새 목표 직선 램프 [s] (v15: 0.5)
R_RAMP, R_MAX = 0.05, 0.05  # A/D: 요레이트 목표 램프 [rad/s²] (v15b 변경 A: 0.10 → 0.05), 상한 [rad/s]
R_REL = 0.3                 # A/D 뗐을 때 요레이트 목표 1차 지연 [s]
T_PSI = 1.0                 # 방향각 정렬(운전자가 도로 방향으로 되돌림) [s]: r = r_cmd − ψ/T_PSI

DRIFT_LEVELS = [0.1, 0.15, 0.2]                         # 변경 2: v12 시험 범위 안, 기본 0.2
STUCK_LEVELS = [0.002, 0.003, 0.005, 0.0075, 0.01]      # 기본 0.01
WEAVE_LEVELS = [(0.5, 5.0), (0.8, 3.5), (1.2, 4.0)]     # (진폭 m, 주기 s): weave L2 → L3 → L4, 주기는 v10/v12 구간 중앙, 시작 0.8
ACCEL_A, ACCEL_R, ACCEL_HOLD = 3.0, 1.5, 20             # 키 5 급가속: v10 accel_ramp 중앙값 (A [m/s²], 램프 [s], 유지 표본)
DRIFT_R = 2.0               # 드리프트 스무스스텝 [s]
WEAVE_R = 7.5               # 위빙 포락 [s] (v10 U(5, 10) 중앙)
BRAKE_A, BRAKE_T = -6.0, 2.0


class Sim:
    def __init__(self, seed=0, sigma_meas=0.1, v_ego=25.0, v0=25.0, gap=40.0, lane=2, bg=True):
        self.seed, self.sigma_meas, self.v_ego, self.bg_on = seed, sigma_meas, v_ego, bg
        self.cfg = G.load_cfg(sigma_meas=sigma_meas)
        if bg:
            b = G.base(seed, N_BG, self.cfg, lc_until=0)                       # 차선변경 없는 생성기 배경
            self.bg_y = b["y"] - W * np.round(b["y"][0] / W)                      # 차선유지 흔들림 (σ_d OU)
            self.bg_a, self.bg_rx = b["a_cmd"], b["r_extra"]
        else:
            self.bg_y = self.bg_a = self.bg_rx = np.zeros(N_BG)
        self.rng = [np.random.RandomState(seed * 10 + j) for j in (1, 2, 3)]   # observe와 같은 난수 흐름
        self.k = -1
        self.ego = np.array([0.0, 0.0, 0.0, v_ego])                             # [X, Y, θ, v], 가운데 차로 중심 Y = 0
        self.x, self.yk, self.psi, self.v = gap, (lane - 1) * W, 0.0, v0
        self.v_set = v0
        self.a_user = self.r_cmd = 0.0
        self.a_tgt, self.ramp_from, self.ramp_k = 0.0, 0.0, 0
        self.y_inj = self.vy_inj = 0.0
        self.bg_prev = 0.0
        self.psi_tot_prev = None
        self.y_prev = None
        self.zi = None
        self.x2_prev = None
        self.sig_l = self.sig_r = False
        self.inj = None
        self.drift_i, self.stuck_i, self.weave_i = 2, 4, 1
        self.a = 0.0

    # --- 입력 ---
    def toggle_signal(self, side):
        if side == "L":
            self.sig_l, self.sig_r = not self.sig_l, False
        else:
            self.sig_r, self.sig_l = not self.sig_r, False

    @property
    def turn_signal(self):
        return int(self.sig_l or self.sig_r)

    def adjust(self, step):
        """[ / ] : 현재(또는 다음) 드리프트·조향 고정 크기 조정."""
        kind = self.inj["kind"] if self.inj else "drift"
        if kind == "stuck":
            self.stuck_i = int(np.clip(self.stuck_i + step, 0, len(STUCK_LEVELS) - 1))
        else:
            self.drift_i = int(np.clip(self.drift_i + step, 0, len(DRIFT_LEVELS) - 1))
        if self.inj and kind in ("drift", "stuck"):
            self.inj["level"] = DRIFT_LEVELS[self.drift_i] if kind == "drift" else STUCK_LEVELS[self.stuck_i]

    def inject(self, kind, sign=None):
        """kind ∈ drift | stuck | weave | brake | None(해제). sign: 횡방향 부호(기본 = 도로 가운데 쪽)."""
        self.clear_injection()
        if kind is None:
            return
        if sign is None:
            lane_c = W * np.round(self.y_true() / W)
            sign = -1 if lane_c > 0 else 1
        level = {"drift": DRIFT_LEVELS[self.drift_i], "stuck": STUCK_LEVELS[self.stuck_i], "accel": (ACCEL_A, ACCEL_R)}.get(kind)
        if kind == "weave":
            level = WEAVE_LEVELS[self.weave_i]
            self.weave_i = (self.weave_i + 1) % len(WEAVE_LEVELS)
        self.inj = dict(kind=kind, k0=self.k + 1, sign=sign, level=level, lane_c=float(W * np.round(self.y_true() / W)),
                        cross_k=None, y0=self.y_inj)

    def clear_injection(self):
        """해제: 주입 횡변위·방향각을 운동학 상태로 넘겨 위치·방향이 끊기지 않게 한다."""
        if self.inj is None:
            return
        if self.inj["kind"] not in ("brake", "accel"):
            self.yk += self.y_inj
            self.psi += self.vy_inj / max(self.v, 1.0)
            if self.y_prev is not None:
                self.y_prev -= self.y_inj
        self.y_inj, self.vy_inj = 0.0, 0.0
        self.v_set = self.v
        self.inj = None

    @property
    def x_ego(self):
        return float(self.ego[0])

    def step_ego(self, ego_u):
        """ego_u = (a [m/s²], 요레이트 [rad/s]) 또는 None(등속 직진). 한계 클립은 제어기 쪽(Config 값)에서 한다."""
        X, Y, th, v = self.ego
        a, yr = (0.0, 0.0) if ego_u is None else ego_u
        self.ego = np.array([X + v * np.cos(th) * DT, Y + v * np.sin(th) * DT, th + yr * DT, max(0.0, v + a * DT)])

    def y_true(self):
        return self.yk + self.bg_y[max(self.k, 0) % N_BG] + self.y_inj

    # --- 한 스텝 (10 Hz) ---
    def step(self, keys=(), ego_u=None):
        """keys: 누르고 있는 키 집합 ⊂ {W, S, A, D}. ego_u: 자차 (a, 요레이트). 반환 dict(참 상태, BSM 관측, 자차 상태)."""
        self.k += 1
        k, kb = self.k, self.k % N_BG
        lat_on = self.inj is not None and self.inj["kind"] not in ("brake", "accel")
        # 종방향 페달 (변경 4): 목표 a_tgt로 A_RAMP_T 직선 램프
        tgt = A_MAX if ("W" in keys and "S" not in keys) else (-B_MAX if "S" in keys else 0.0)
        if tgt != self.a_tgt:
            self.a_tgt, self.ramp_from, self.ramp_k = tgt, self.a_user, 0
        self.ramp_k += 1
        self.a_user = self.ramp_from + (self.a_tgt - self.ramp_from) * min(1.0, self.ramp_k * DT / A_RAMP_T)
        # 조향 (횡방향 주입 중에는 무시)
        steer = 0 if lat_on else ("A" in keys) - ("D" in keys)
        if steer:
            self.r_cmd = float(np.clip(self.r_cmd + steer * R_RAMP * DT, -R_MAX, R_MAX))
        else:
            self.r_cmd *= np.exp(-DT / R_REL)
        # 이상 주입
        tt = (k - self.inj["k0"]) * DT if self.inj else 0.0
        a_inj = 0.0
        if self.inj and self.inj["kind"] == "brake":
            if tt < BRAKE_T:
                a_inj = BRAKE_A
            else:
                self.clear_injection()
        elif self.inj and self.inj["kind"] == "accel":                 # gen_v10 accel_ramp 식: 램프 r 표본 후 20 표본 유지
            A, R = self.inj["level"]
            r, j = int(round(R / DT)), k - self.inj["k0"]
            if j < r:
                a_inj = A * (j + 1) / r
            elif j < r + ACCEL_HOLD:
                a_inj = A
            else:
                self.clear_injection()
        pedal = abs(self.a_user) > 0.05 or a_inj != 0.0
        if pedal:
            self.v_set = self.v
        # 종방향: gen_v10.speed_loop와 같은 꼴 (배경 a_cmd + 조절기) + 사용자·주입 가속
        a = self.bg_a[kb] - G.K_V * (self.v - self.v_set) + self.a_user + a_inj
        self.v = float(np.clip(self.v + a * DT, *G.V_LIM))
        self.a = a
        # 횡방향 운동학
        r_kin = self.r_cmd - self.psi / T_PSI
        self.psi += r_kin * DT
        self.x += self.v * np.cos(self.psi) * DT
        self.yk += self.v * np.sin(self.psi) * DT
        self.step_ego(ego_u)
        # 횡방향 주입 (gen_v10 / gen_v11h 식)
        if lat_on:
            sg, L = self.inj["sign"], self.inj["level"]
            kind = self.inj["kind"]
            if kind == "drift":
                self.y_inj += sg * L * G.smoothstep(tt / DRIFT_R) * DT
            elif kind == "stuck":
                self.y_inj += self.v * sg * L * tt * DT
            elif kind == "weave":
                A, P = L
                self.y_inj = self.inj["y0"] + G.smoothstep(tt / WEAVE_R) * A * np.sin(2 * np.pi * tt / P)
        y = self.yk + self.bg_y[kb] + self.y_inj
        # 참 요레이트: 운동학 ψ + (배경·주입 횡변위가 만드는 방향각, observe처럼 후진 차분) 의 차분 + r_extra
        y_add = self.bg_y[kb] + self.y_inj
        yd_add = 0.0 if self.y_prev is None else (y_add - self.y_prev) / DT
        self.vy_inj = yd_add - (0.0 if self.y_prev is None else (self.bg_y[kb] - self.bg_prev) / DT)
        self.y_prev, self.bg_prev = y_add, self.bg_y[kb]
        psi_tot = self.psi + yd_add / self.v
        r = (0.0 if self.psi_tot_prev is None else (psi_tot - self.psi_tot_prev) / DT) + self.bg_rx[kb]
        self.psi_tot_prev = psi_tot
        if self.inj and self.inj["kind"] not in ("brake", "accel") and self.inj["cross_k"] is None and abs(y - self.inj["lane_c"]) >= HALF:
            self.inj["cross_k"] = k                                       # gen_v11h.crossing 정의
        # BSM 관측 (gen_v10.observe와 같은 잡음과 A2 필터를 한 표본씩)
        v_meas = self.v + G.SIG_V_MEAS * self.rng[0].normal()
        if self.zi is None:
            z0 = sosfilt_zi(G.A2_SOS) * v_meas
            self.zi = [z0.copy(), z0.copy()]
        x1, self.zi[0] = sosfilt(G.A2_SOS, [v_meas], zi=self.zi[0])
        x2, self.zi[1] = sosfilt(G.A2_SOS, x1, zi=self.zi[1])
        a_obs = 0.0 if self.x2_prev is None else (x2[0] - self.x2_prev) / DT
        self.x2_prev = x2[0]
        r_obs = r + G.SIG_N_YAW * self.rng[1].normal()
        y_obs = y + self.sigma_meas * self.rng[2].normal()
        d_obs = y_obs - W * np.round(y_obs / W)
        d_true = y - W * np.round(y / W)
        return dict(k=k, t=k * DT, x=self.x, y=y, d_true=d_true, psi=psi_tot, v=self.v, a=a, r=r,
                    a_obs=float(a_obs), r_obs=float(r_obs), d_obs=float(d_obs), v_meas=float(v_meas), y_obs=float(y_obs),
                    turn_signal=self.turn_signal, x_ego=self.x_ego, ego=self.ego.copy(),
                    inj=None if self.inj is None else dict(self.inj))

    def set_sigma_meas(self, sm):
        self.sigma_meas = sm
