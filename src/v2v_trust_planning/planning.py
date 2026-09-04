"""6절: 신뢰도 기반 강건 회피 계획 (Kinematic Bicycle Model + CasADi Opti NMPC).

수학적 정식화:
- 상태 x = [X, Y, theta, v]^T
- 제어 u = [a, delta]^T
- 기구학 자전거 모델(Kinematic Bicycle Model) 이산화
- 신뢰도 기반 동적 안전마진(Dynamic Safety Margin): d_safe = d0 + beta * (1 - tau) * v_rel
- 비볼록 충돌 회피 제약: dist(ego, obs) + epsilon >= d_safe (Soft Constraint via Slack)
"""

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

import casadi as ca
import numpy as np

from .config import Config


def dynamic_safety_margin(
    tau_j: float,
    v_rel: float,
    d0: float,
    beta_base: float = 3.0,
    beta_speed: float = 1.0,
) -> float:
    """d_safe = d0 + beta_base * (1 - tau_j) + beta_speed * (1 - tau_j) * v_rel.

    신뢰도가 낮을수록:
    1. 상대속도와 무관하게 절대 공간 마진(beta_base)을 즉각 확보
    2. 상대속도에 비례하는 동적 마진(beta_speed * v_rel)을 추가 확장
    """
    uncertainty = 1.0 - tau_j
    return float(d0 + beta_base * uncertainty + beta_speed * uncertainty * max(0.0, v_rel))


def steer_to_yaw_rate(delta: float, v: float, wheelbase: float) -> float:
    """조향각(delta [rad])을 요레이트(dot_theta [rad/s])로 변환: dot_theta = v * tan(delta) / L."""
    return float(v * np.tan(delta) / max(wheelbase, 1e-3))


def yaw_rate_to_steer(yaw_rate: float, v: float, wheelbase: float) -> float:
    """요레이트(dot_theta [rad/s])를 조향각(delta [rad])으로 변환: delta = atan(L * dot_theta / v)."""
    if abs(v) < 0.1:
        return 0.0
    return float(np.arctan(wheelbase * yaw_rate / v))


@dataclass
class ObstacleTrajectory:
    neighbor_id: str
    trajectory: np.ndarray  # shape (2, N) : [X, Y] 위치 궤적
    d_safe: float
    trust: float


@dataclass
class PlanningContext:
    z_i: np.ndarray  # 5절 장면 표현
    safety_margins: Dict[str, float]  # {neighbor_id: d_safe}
    relative_positions: Dict[str, np.ndarray]
    u_nominal: np.ndarray  # 목표 제어입력 [accel, steer 또는 heading_rate]
    ego_state: Optional[np.ndarray] = None  # [X, Y, theta, v]
    ref_trajectory: Optional[np.ndarray] = None  # (4, N+1)
    obstacles: Optional[List[ObstacleTrajectory]] = None
    target_velocity: float = 15.0  # 기본 목표 속도 [m/s] (~54 km/h)


class TrustAwareNMPC:
    """CasADi Opti + IPOPT 기반의 신뢰가중 비선형 모델 예측 제어기."""

    def __init__(self, config: Config, max_obstacles: int = 4):
        self.cfg = config
        self.N = config.mpc_horizon
        self.dt = config.dt
        self.L = config.wheelbase_m
        self.max_obs = max_obstacles

        self.last_u = np.zeros(2)  # [a, delta]
        self._build_opti()

    def _build_opti(self) -> None:
        self.opti = ca.Opti()

        # 결정 변수 (Decision Variables)
        self.X = self.opti.variable(4, self.N + 1)  # [X, Y, theta, v]
        self.U = self.opti.variable(2, self.N)  # [a, delta]
        # 각 장애물별 슬랙 변수 epsilon >= 0
        self.eps = [self.opti.variable(1, self.N) for _ in range(self.max_obs)]

        # 파라미터 (Parameters)
        self.x0 = self.opti.parameter(4, 1)
        self.u_prev = self.opti.parameter(2, 1)
        self.ref_traj = self.opti.parameter(4, self.N + 1)  # [X_ref, Y_ref, theta_ref, v_ref]

        # 장애물 파라미터
        self.obs_traj = [self.opti.parameter(2, self.N) for _ in range(self.max_obs)]
        self.obs_dsafe = [self.opti.parameter(1, 1) for _ in range(self.max_obs)]
        self.obs_active = [self.opti.parameter(1, 1) for _ in range(self.max_obs)]

        # 1. 초기 상태 제약
        self.opti.subject_to(self.X[:, 0] == self.x0)

        cost = 0.0

        for k in range(self.N):
            # 2. Kinematic Bicycle Model 동역학 제약
            x_curr = self.X[0, k]
            y_curr = self.X[1, k]
            theta_curr = self.X[2, k]
            v_curr = self.X[3, k]

            a_k = self.U[0, k]
            delta_k = self.U[1, k]

            x_next = x_curr + v_curr * ca.cos(theta_curr) * self.dt
            y_next = y_curr + v_curr * ca.sin(theta_curr) * self.dt
            theta_next = theta_curr + (v_curr * ca.tan(delta_k) / self.L) * self.dt
            v_next = v_curr + a_k * self.dt

            self.opti.subject_to(self.X[0, k + 1] == x_next)
            self.opti.subject_to(self.X[1, k + 1] == y_next)
            self.opti.subject_to(self.X[2, k + 1] == theta_next)
            self.opti.subject_to(self.X[3, k + 1] == v_next)

            # 3. 제어 입력 물리 한계 제약
            self.opti.subject_to(self.opti.bounded(self.cfg.min_accel_mps2, a_k, self.cfg.max_accel_mps2))
            self.opti.subject_to(self.opti.bounded(self.cfg.min_steer_rad, delta_k, self.cfg.max_steer_rad))

            # 4. 충돌 회피 제약 (수치 안정화 슬랙 Soft Constraint)
            # dist + epsilon >= active * d_safe
            for j in range(self.max_obs):
                obs_x = self.obs_traj[j][0, k]
                obs_y = self.obs_traj[j][1, k]
                dist_sq_reg = (x_next - obs_x) ** 2 + (y_next - obs_y) ** 2 + self.cfg.slack_reg
                dist_reg = ca.sqrt(dist_sq_reg)

                self.opti.subject_to(dist_reg + self.eps[j][0, k] >= self.obs_active[j] * self.obs_dsafe[j])
                self.opti.subject_to(self.eps[j][0, k] >= 0.0)

                # 슬랙 변수 무거운 페널티
                cost += self.cfg.weight_slack * (self.eps[j][0, k] ** 2)

            # 5. 비용 함수 (Reference 추종 + 제어 노력 + 저크 억제)
            cost += self.cfg.weight_track_pos * (
                (x_next - self.ref_traj[0, k + 1]) ** 2 + (y_next - self.ref_traj[1, k + 1]) ** 2
            )
            cost += self.cfg.weight_track_vel * ((v_next - self.ref_traj[3, k + 1]) ** 2)
            cost += self.cfg.weight_control_accel * (a_k ** 2)
            cost += self.cfg.weight_control_steer * (delta_k ** 2)

            if k == 0:
                cost += self.cfg.weight_delta_control * (
                    (a_k - self.u_prev[0, 0]) ** 2 + (delta_k - self.u_prev[1, 0]) ** 2
                )
            else:
                cost += self.cfg.weight_delta_control * (
                    (a_k - self.U[0, k - 1]) ** 2 + (delta_k - self.U[1, k - 1]) ** 2
                )

        self.opti.minimize(cost)

        p_opts = {"expand": True, "print_time": False}
        s_opts = {
            "max_iter": 100,
            "print_level": 0,
            "sb": "yes",
            "tol": 1e-3,
            "acceptable_tol": 1e-2,
        }
        self.opti.solver("ipopt", p_opts, s_opts)

    def solve(
        self,
        ego_state: np.ndarray,
        ref_trajectory: Optional[np.ndarray] = None,
        obstacles: Optional[List[ObstacleTrajectory]] = None,
        target_velocity: float = 15.0,
    ) -> Tuple[np.ndarray, np.ndarray]:
        """NMPC 풀이 실행.

        Args:
            ego_state: 자차 상태 [X, Y, theta, v], shape (4,).
            ref_trajectory: 기준 경로 (4, N+1). None이면 전방 직진 등속 기준 궤적 생성.
            obstacles: 주변 장애물(이웃 차량) 정보 리스트.
            target_velocity: 기준 목표 속도 [m/s].

        Returns:
            u_opt: 최적 제어 입력 [a_0, delta_0], shape (2,).
            x_pred: 예측 궤적 [X, Y, theta, v], shape (4, N+1).
        """
        # 기준 궤적 생성 (기본값: 현재 헤딩 방향으로 직진 등속)
        if ref_trajectory is None:
            ref_traj = np.zeros((4, self.N + 1))
            curr_x, curr_y, curr_theta, _ = ego_state
            for k in range(self.N + 1):
                ref_traj[0, k] = curr_x + target_velocity * np.cos(curr_theta) * k * self.dt
                ref_traj[1, k] = curr_y + target_velocity * np.sin(curr_theta) * k * self.dt
                ref_traj[2, k] = curr_theta
                ref_traj[3, k] = target_velocity
        else:
            ref_traj = ref_trajectory

        self.opti.set_value(self.x0, ego_state.reshape(4, 1))
        self.opti.set_value(self.u_prev, self.last_u.reshape(2, 1))
        self.opti.set_value(self.ref_traj, ref_traj)

        # 장애물 설정
        obs_list = obstacles or []
        for j in range(self.max_obs):
            if j < len(obs_list):
                obs = obs_list[j]
                self.opti.set_value(self.obs_traj[j], obs.trajectory)
                self.opti.set_value(self.obs_dsafe[j], float(obs.d_safe))
                self.opti.set_value(self.obs_active[j], 1.0)
            else:
                self.opti.set_value(self.obs_traj[j], np.ones((2, self.N)) * 1000.0)
                self.opti.set_value(self.obs_dsafe[j], 0.0)
                self.opti.set_value(self.obs_active[j], 0.0)

        try:
            sol = self.opti.solve()
            a_val = float(np.clip(sol.value(self.U[0, 0]), self.cfg.min_accel_mps2, self.cfg.max_accel_mps2))
            steer_val = float(np.clip(sol.value(self.U[1, 0]), self.cfg.min_steer_rad, self.cfg.max_steer_rad))
            u_opt = np.array([a_val, steer_val], dtype=float)
            x_pred = sol.value(self.X)
            self.last_u = u_opt.copy()
            return u_opt, x_pred
        except RuntimeError:
            # 해를 찾지 못한 경우(Infeasible/Max Iter) 안전 감속 비상 제어
            fallback_a = float(np.clip(-3.0, self.cfg.min_accel_mps2, self.cfg.max_accel_mps2))
            fallback_u = np.array([fallback_a, 0.0])
            self.last_u = fallback_u.copy()
            fallback_x = np.repeat(ego_state.reshape(4, 1), self.N + 1, axis=1)
            return fallback_u, fallback_x


class SafeMPCPlanner:
    """신뢰가중 강건 회피 플래너.

    TrustAwareNMPC를 백엔드로 사용하며, 기존 스켈레톤 인터페이스(solve(ctx, trust))와 호환된다.
    """

    def __init__(
        self,
        lambda1: float = 1.0,
        lambda2: float = 0.1,
        horizon: int = 10,
        config: Optional[Config] = None,
        max_obstacles: int = 4,
    ):
        self.cfg = config or Config(mpc_horizon=horizon, lambda1_uncertainty=lambda1, lambda2_comfort=lambda2)
        self.nmpc = TrustAwareNMPC(self.cfg, max_obstacles=max_obstacles)

    def solve(self, ctx: PlanningContext, trust: Dict[str, float]) -> np.ndarray:
        """기존 인터페이스 호환 풀이 메서드.

        Args:
            ctx: 계획 컨텍스트 (ego_state, relative_positions, safety_margins 등 포함)
            trust: {neighbor_id: tau_j}

        Returns:
            u_i: 최적 제어 입력 [accel, steer 또는 heading_rate]
        """
        # 자차 상태 구성
        if ctx.ego_state is not None:
            ego_state = ctx.ego_state.copy()
        else:
            # ego_state가 전달되지 않은 경우 u_nominal 및 기본값으로 상태 근사
            v_init = ctx.target_velocity
            ego_state = np.array([0.0, 0.0, 0.0, v_init])

        # 장애물 궤적 구성
        obstacles: List[ObstacleTrajectory] = []
        if ctx.obstacles is not None:
            obstacles = ctx.obstacles
        else:
            # relative_positions로부터 장애물의 N스텝 상대 궤적 생성 (기구학적 등속 전개)
            ego_x, ego_y, ego_theta, ego_v = ego_state
            for nid, rel_pos in ctx.relative_positions.items():
                d_safe = ctx.safety_margins.get(nid, self.cfg.d0_m)
                tau = trust.get(nid, 1.0)
                # 상대 위치를 글로벌 위치로 변환
                obs_x0 = ego_x + rel_pos[0]
                obs_y0 = ego_y + rel_pos[1]
                traj = np.zeros((2, self.cfg.mpc_horizon))
                for k in range(self.cfg.mpc_horizon):
                    # 장애물의 미래 궤적 (기본: 자차와 동일 방향 등속 전개)
                    traj[0, k] = obs_x0 + ego_v * np.cos(ego_theta) * k * self.cfg.dt
                    traj[1, k] = obs_y0 + ego_v * np.sin(ego_theta) * k * self.cfg.dt
                obstacles.append(ObstacleTrajectory(nid, traj, d_safe, tau))

        u_opt, _ = self.nmpc.solve(
            ego_state=ego_state,
            ref_trajectory=ctx.ref_trajectory,
            obstacles=obstacles,
            target_velocity=ctx.target_velocity,
        )

        return u_opt
