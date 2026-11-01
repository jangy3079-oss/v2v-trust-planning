"""PROTOCOL_v10 1절 입력 규약과 차선 기준 창 (정의만, import 시 파일을 쓰지 않음)."""
import numpy as np
import torch

K = 30
W_LANE = 3.5


def make_features(a_long, yaw_rate, v, kappa_road, d):
    """탐지기 입력 (이웃 차량 한 대, 10 Hz). 도로 좌표계(Frenet) 기준.

    Args:
        a_long: 종가속도 [m/s²], shape (T,).
        yaw_rate: 측정 요레이트 r [rad/s], shape (T,).
        v: 속도 [m/s], shape (T,).
        kappa_road: 도로 곡률 [1/m], 스칼라 또는 (T,). 직선 도로는 0을 넘긴다.
        d: 현재 차선 중심 대비 횡위치 [m], 왼쪽 +, shape (T,). 차선이 바뀌면 ±차로폭만큼 점프하는 값을 그대로 넘긴다
           (점프는 lane_windows가 창마다 펼친다).

    Returns:
        np.ndarray[T, 3] float32: [a_long, r_rel, d], r_rel = r − v·κ_road (도로를 따라 도는 성분을 뺀 요레이트).
        2채널 모델은 앞 두 열만 쓴다.
    """
    r_rel = np.asarray(yaw_rate, np.float64) - np.asarray(v, np.float64) * kappa_road
    return np.stack([np.asarray(a_long, np.float64), r_rel, np.asarray(d, np.float64)], 1).astype(np.float32)


def unwrap_lane(d, w=W_LANE):
    """|Δd| > w/2인 점프를 차선변경으로 보고 펼친다. 반환 (y = d + off, off): y는 연속 횡위치, off는 차선 보정 [m]."""
    d = np.asarray(d, np.float64)
    jump = np.diff(d)
    k = np.where(np.abs(jump) > w / 2, -np.round(jump / w), 0.0)
    off = w * np.r_[0.0, np.cumsum(k)]
    return d + off, off


def lane_windows(F, ends, C, k=K, w=W_LANE, target=True):
    """창 [t−K+1, t]와 목표 t+1. d 열은 창 마지막 입력 시점 t의 차선을 기준으로 다시 표현한다(인과).
    반환 X (m, K, C), Y (m, C) 또는 None."""
    F = np.asarray(F, np.float32)
    ends = np.asarray(ends)
    rows = ends[:, None] + np.arange(-k + 1, 1)[None, :]
    X = F[rows, :C].astype(np.float32)
    Y = F[ends + 1, :C].astype(np.float32) if target else None
    if C == 3:
        y, off = unwrap_lane(F[:, 2], w)
        X[:, :, 2] = (y[rows] - off[ends][:, None]).astype(np.float32)
        if target:
            Y[:, 2] = (y[ends + 1] - off[ends]).astype(np.float32)
    return X, Y


@torch.no_grad()
def residuals(model, F, C, mu, sd, k=K, w=W_LANE, batch=20000):
    """1-스텝 예측 잔차 (원 단위). r[t] = u[t] − û[t | t−K..t−1], t ≥ K. 앞 K개는 0."""
    n = len(F)
    R = np.zeros((n, C))
    ends = np.arange(k - 1, n - 1)
    mu, sd = np.asarray(mu, np.float32), np.asarray(sd, np.float32)
    for a in range(0, len(ends), batch):
        e = ends[a:a + batch]
        X, Y = lane_windows(F, e, C, k, w)
        uh = model(torch.from_numpy((X - mu) / sd))[0].numpy() * sd + mu
        R[e + 1] = Y - uh
    return R


def e_score(model, F, C, mu, sd, sigma_c):
    """e = Σ_c (잔차_c/σ_c)² (v9 정의 그대로, d 채널도 같은 방식)."""
    return ((residuals(model, F, C, mu, sd) / np.asarray(sigma_c)) ** 2).sum(1)
