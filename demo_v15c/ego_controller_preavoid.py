"""v13 자차 제어기: off / rule / nmpc 백엔드 교체형. 합성 환경, 직선 3차로, 시뮬레이션. 이웃 1대.

인터페이스 (고정):
    ctl = EgoController(backend="nmpc", v_set=25.0)
    a_cmd, steer = ctl.step(ego, nb, tau, alarm)   # ego = [X, Y, θ, v], nb = dict(x, y, psi, v, r), tau = τ_final, alarm = 경보 플래그
    ctl.info                                        # 백엔드 상태(풀이 시간, fallback, 상태머신 상태 등), 표시·기록용
출력 steer는 조향각 δ [rad]. 자차 운동학에는 planning.steer_to_yaw_rate(δ, v, L)로 요레이트를 만들어 넣는다.
한계(가속 +3, 감속 −5 m/s², 조향 ±0.5 rad)는 v2v_trust_planning Config 기본값 그대로 모든 백엔드 출력에 적용한다.

nmpc: v2v_trust_planning.planning.TrustAwareNMPC를 그대로 쓴다(재구현·재튜닝 없음, Config 기본값).
  - 이웃 궤적: 현재 위치에서 N(= Config.mpc_horizon) 스텝 등속·등요레이트 외삽, k = 0이 현재(SafeMPCPlanner 규약).
  - d_safe = dynamic_safety_margin(τ, v_rel), v_rel = 종방향 접근 속도(앞: v_ego − v_nb, 뒤: v_nb − v_ego, 음수는 함수가 0으로).
  - ref_trajectory = None → 기존 OvertakeStateMachine 기본 궤적. reset 직후 current_v_cmd = v_set(20 m/s 하드코딩 회피).
rule: README "rule 백엔드" 절에 고정한 규칙 그대로.
"""
import sys
import time
from pathlib import Path

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "v2v_trust_planning" / "src"))

import numpy as np  # noqa: E402

from v2v_trust_planning.config import Config  # noqa: E402

BACKENDS = ("off", "rule", "nmpc")
CAR_L, CAR_W = 4.5, 1.8          # 차체 (충돌·간격 판정, 화면과 같은 값)
TAU_ALARM = 0.7                  # Config.trust_threshold_avoid


# ---------------------------------------------------------------- 안전 지표 (모든 백엔드 공통)
def gaps(ego, nb_x, nb_y):
    """축 정렬 차체 박스 사이 간격. 반환 (dx 중심, dy 중심, 종 간격, 횡 간격, 간격, 충돌). 방향각은 작다고 보고 무시."""
    dx, dy = nb_x - ego[0], nb_y - ego[1]
    g_lon, g_lat = max(0.0, abs(dx) - CAR_L), max(0.0, abs(dy) - CAR_W)
    hit = abs(dx) < CAR_L and abs(dy) < CAR_W
    return dx, dy, g_lon, g_lat, float(np.hypot(g_lon, g_lat)), hit


def ttc(ego, nb_x, nb_v):
    """종방향 TTC [s]: 이웃이 앞에 있고 자차가 더 빠를 때만, (범퍼 간격)/(접근 속도). 그 외 inf. 횡방향 접근은 안 본다."""
    dx = nb_x - ego[0]
    closing = ego[3] - nb_v
    if dx <= 0 or closing <= 0:
        return float("inf")
    return max(0.0, dx - CAR_L) / closing


def closing_speed(ego, nb_x, nb_v):
    """종방향 접근 속도: 이웃이 앞이면 v_ego − v_nb, 뒤면 v_nb − v_ego (음수 그대로, 클립은 dynamic_safety_margin이 한다)."""
    return (ego[3] - nb_v) if nb_x - ego[0] > 0 else (nb_v - ego[3])


def lateral_steer(ego, y_ref, cfg, wn=1.0, zeta=1.0):
    """off·rule 공용 횡방향 추종: θ' = (ωn²/v)(y_ref − Y) − 2ζωn·θ → δ = atan(L θ'/v). 횡위치 2차계 ωn 1 rad/s, ζ 1."""
    from v2v_trust_planning.planning import yaw_rate_to_steer
    X, Y, th, v = ego
    yr = (wn ** 2 / max(v, 1.0)) * (y_ref - Y) - 2 * zeta * wn * th
    return yaw_rate_to_steer(yr, v, cfg.wheelbase_m)


# ---------------------------------------------------------------- 백엔드
class OffBackend:
    """직진 고정: a = 0, δ = 0 (이전 v13와 같은 등속 직진)."""
    name = "off"

    def __init__(self, cfg, v_set):
        self.info = {}

    def reset(self, ego):
        pass

    def step(self, ego, nb, tau, alarm):
        return 0.0, 0.0


class RuleBackend:
    """demo_v15a README 1절에 고정한 규칙 (v13 규칙 + 접근 조건 + 위험 해소 해제).
    - 위험: 경보 중 그리고 dx ∈ [−10, +60] m 그리고 접근 중(closing_speed > 0.5 m/s).
    - 진입: 위험 → 개입. 횡: 원래 차로 중심에서 이웃 반대쪽으로 0.5 m (v13 그대로).
    - 개입 중: 위험 ∧ 앞(dx > 0) ∧ TTC < 5 s → a = −3 (TTC < 2 s이면 −5). 그 밖에는 a = 0 (뒤·옆 접근에는 감속하지 않음).
    - 해제: (τ > 0.7 또는 위험 아님)이 2 s 연속 → 원래 속도(a = clip(0.5·(v_set − v), ±1.5)), 차로 중심(Y = 0)."""
    name = "rule"
    WIN = (-10.0, 60.0)
    A_BRAKE, A_HARD, TTC_HARD = -3.0, -5.0, 2.0
    CLOSING_MIN, TTC_BRAKE = 0.5, 5.0          # v15a 새 상수 (README 1절, 코드 전 고정)
    OFFSET = 0.5
    RELEASE_S = 2.0
    K_V, A_REC = 0.5, 1.5

    def __init__(self, cfg, v_set):
        self.cfg, self.v_set = cfg, v_set
        self.reset(None)

    def reset(self, ego):
        self.engaged, self.ok_n, self.y_ref = False, 0, 0.0
        self.info = dict(state="정상", ttc=float("inf"), closing=0.0, danger=False)

    def step(self, ego, nb, tau, alarm):
        dx = nb["x"] - ego[0]
        T = ttc(ego, nb["x"], nb["v"])
        closing = closing_speed(ego, nb["x"], nb["v"])
        danger = bool(alarm) and self.WIN[0] <= dx <= self.WIN[1] and closing > self.CLOSING_MIN
        self.ok_n = self.ok_n + 1 if (tau > TAU_ALARM or not danger) else 0
        if danger and not self.engaged:
            self.engaged = True
            self.y_ref = -self.OFFSET * (1.0 if nb["y"] >= 0.0 else -1.0)      # 원래 차로 중심 0 기준 이웃 반대쪽
        elif self.engaged and self.ok_n >= int(round(self.RELEASE_S / self.cfg.dt)):
            self.engaged, self.y_ref = False, 0.0
        if self.engaged:
            brake = danger and dx > 0 and T < self.TTC_BRAKE
            a = (self.A_HARD if T < self.TTC_HARD else self.A_BRAKE) if brake else 0.0
            state = "회피(감속)" if brake else ("회피(가속 중지)" if danger else "회피(대기)")
        else:
            a = float(np.clip(self.K_V * (self.v_set - ego[3]), -self.A_REC, self.A_REC))
            state = "정상" if abs(ego[1]) < 0.1 and abs(self.v_set - ego[3]) < 0.5 else "복귀"
        self.info = dict(state=state, ttc=T, y_ref=self.y_ref, closing=closing, danger=danger)
        return a, lateral_steer(ego, self.y_ref, self.cfg)


_NMPC = {}


class NMPCBackend:
    """TrustAwareNMPC 어댑터 (이웃 1대). 같은 프로세스에서는 Opti 객체를 재사용하고 reset()만 부른다."""
    name = "nmpc"
    SM_NAME = {"NORMAL": "정상", "AVOIDING": "회피", "RETURNING": "복귀"}

    def __init__(self, cfg, v_set):
        from v2v_trust_planning.planning import TrustAwareNMPC
        self.cfg, self.v_set = cfg, v_set
        if "nmpc" not in _NMPC:
            _NMPC["nmpc"] = TrustAwareNMPC(cfg, max_obstacles=1)
        self.nmpc = _NMPC["nmpc"]
        self.reset(None)

    def reset(self, ego):
        self.nmpc.reset()
        self.nmpc.state_machine.current_v_cmd = self.v_set                  # 결정 1(a): 20 m/s 하드코딩 시작값 대신 자차 목표속도
        self.x_pred = None
        self.info = dict(state="정상", solve_ms=0.0, fallback=0, fallback_now=False, d_safe=self.cfg.d0_m)

    def obstacle_traj(self, nb):
        """k = 0 … N−1, k = 0 = 현재 (SafeMPCPlanner 규약; 제약식은 X_{k+1}과 비교하므로 0.1 s 지연, README 한계)."""
        N, dt = self.cfg.mpc_horizon, self.cfg.dt
        x, y, psi, v, r = nb["x"], nb["y"], nb["psi"], nb["v"], nb["r"]
        tr = np.zeros((2, N))
        for k in range(N):
            tr[:, k] = x, y
            x, y, psi = x + v * np.cos(psi) * dt, y + v * np.sin(psi) * dt, psi + r * dt
        return tr

    def step(self, ego, nb, tau, alarm):
        from v2v_trust_planning.planning import ObstacleTrajectory, dynamic_safety_margin
        c = self.cfg
        d_safe = dynamic_safety_margin(tau, closing_speed(ego, nb["x"], nb["v"]), c.d0_m, c.beta_base, c.beta_speed)
        ob = ObstacleTrajectory("nb", self.obstacle_traj(nb), d_safe, float(tau))
        fb0 = self.nmpc.fallback_count
        t0 = time.perf_counter()
        u, self.x_pred = self.nmpc.solve(np.asarray(ego, float), None, [ob], target_velocity=self.v_set)
        ms = (time.perf_counter() - t0) * 1e3
        st = self.nmpc.state_machine.state.value
        self.info = dict(state=self.SM_NAME[st], sm=st, solve_ms=ms, fallback=self.nmpc.fallback_count,
                         fallback_now=self.nmpc.fallback_count > fb0, d_safe=d_safe, target_y=self.nmpc.state_machine.target_y)
        return float(u[0]), float(u[1])


# ---------------------------------------------------------------- 공개 인터페이스
class EgoController:
    def __init__(self, backend="off", v_set=25.0, cfg=None):
        self.cfg = cfg or Config()
        self.v_set = v_set
        self.set_backend(backend)

    def set_backend(self, backend, ego=None):
        assert backend in BACKENDS, backend
        self.backend = backend
        self.impl = {"off": OffBackend, "rule": RuleBackend, "nmpc": NMPCBackend}[backend](self.cfg, self.v_set)
        self.impl.reset(ego)

    def next_backend(self, ego=None):
        self.set_backend(BACKENDS[(BACKENDS.index(self.backend) + 1) % len(BACKENDS)], ego)

    def reset(self, ego=None):
        self.impl.reset(ego)

    @property
    def info(self):
        return self.impl.info

    def step(self, ego, nb, tau, alarm):
        """반환 (a_cmd, δ). Config 한계로 클립(완화 없음)."""
        a, d = self.impl.step(np.asarray(ego, float), nb, float(tau), bool(alarm))
        c = self.cfg
        return float(np.clip(a, c.min_accel_mps2, c.max_accel_mps2)), float(np.clip(d, c.min_steer_rad, c.max_steer_rad))

    def yaw_rate(self, steer, v):
        from v2v_trust_planning.planning import steer_to_yaw_rate
        return steer_to_yaw_rate(steer, v, self.cfg.wheelbase_m)
