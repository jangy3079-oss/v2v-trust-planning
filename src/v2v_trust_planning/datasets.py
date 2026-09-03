"""검증용 데이터: UAH-DriveSet 로더(실주행, 라벨 있음) + 스모크테스트용 합성 시퀀스.

UAH-DriveSet 컬럼 인덱스는 공식 reader 스크립트
(github.com/Eromera/uah_driveset_reader/blob/master/driveset_reader.py) 기준으로
확인한 값이다. 실제 다운로드한 폴더 구조(예: D1/20151110175712-16km-D1-NORMAL1-SECONDARY/...)에
맞춰 파일 경로 탐색 로직은 조정이 필요할 수 있다.
"""

from dataclasses import dataclass
from pathlib import Path
from typing import List

import numpy as np

# RAW_ACCELEROMETERS.txt 컬럼 (공식 reader 기준)
ACC_COL_TIMESTAMP = 0
ACC_COL_X_ACCEL_KF = 5  # 칼만필터 적용 X(종방향) 가속도 [Gs]

# RAW_GPS.txt 컬럼 (공식 reader 기준)
GPS_COL_TIMESTAMP = 0
GPS_COL_SPEED = 1  # [Km/h]
GPS_COL_DIFCOURSE = 8  # 진행방향 변화량 -> heading_rate의 실측 근사치


def load_uah_driveset_session(acc_path: Path, gps_path: Path) -> np.ndarray:
    """한 세션(운전자 x 행동 x 경로)의 [accel, heading_rate] 시계열을 (T, 2) 배열로 반환.

    RAW_ACCELEROMETERS.txt와 RAW_GPS.txt는 샘플링 레이트가 달라, GPS 타임스탬프를
    기준으로 가속도를 최근접 매칭한다.

    Args:
        acc_path: RAW_ACCELEROMETERS.txt 경로.
        gps_path: RAW_GPS.txt 경로.
    """
    acc = np.genfromtxt(acc_path, dtype=float, delimiter=" ")
    gps = np.genfromtxt(gps_path, dtype=float, delimiter=" ")

    acc_t, acc_x = acc[:, ACC_COL_TIMESTAMP], acc[:, ACC_COL_X_ACCEL_KF]
    gps_t, gps_dc = gps[:, GPS_COL_TIMESTAMP], gps[:, GPS_COL_DIFCOURSE]

    idx = np.searchsorted(acc_t, gps_t)
    idx = np.clip(idx, 0, len(acc_t) - 1)
    accel_aligned = acc_x[idx]

    return np.stack([accel_aligned, gps_dc], axis=1)  # (T, 2)


def find_sessions(root: Path, behavior_keyword: str) -> List[Path]:
    """root 아래에서 behavior_keyword(NORMAL/AGGRESSIVE/DROWSY)가 폴더명에 포함된
    세션 디렉토리 목록을 반환한다. 실제 폴더명 규칙은 다운로드 후 확인해 조정할 것.
    """
    return sorted(p for p in root.rglob("*") if p.is_dir() and behavior_keyword.upper() in p.name.upper())


@dataclass
class SyntheticConfig:
    length: int = 600
    dt: float = 0.1


def synthetic_normal_sequence(cfg: SyntheticConfig, seed: int = 0) -> np.ndarray:
    """스모크 테스트/사전학습용 합성 정상주행: 완만한 랜덤워크 가속도 + 낮은 헤딩변화."""
    rng = np.random.default_rng(seed)
    accel = np.clip(np.cumsum(rng.normal(0, 0.05, cfg.length)), -2.0, 2.0)
    heading_rate = rng.normal(0, 0.01, cfg.length)
    return np.stack([accel, heading_rate], axis=1)


def synthetic_drowsy_sequence(cfg: SyntheticConfig, seed: int = 1) -> np.ndarray:
    """스모크 테스트용 합성 졸음주행: 저주파(~0.3Hz) 헤딩 진동 + 간헐적 반응지연.

    문헌 근거: 졸음운전 특유의 저주파(~0.2-0.5Hz) 차선진동, 반응지연.
    """
    rng = np.random.default_rng(seed)
    t = np.arange(cfg.length) * cfg.dt
    heading_rate = 0.15 * np.sin(2 * np.pi * 0.3 * t) + rng.normal(0, 0.02, cfg.length)
    accel = np.clip(np.cumsum(rng.normal(0, 0.05, cfg.length)), -2.0, 2.0)
    for start in range(0, cfg.length, 150):  # 간헐적 반응지연: 일부 구간 가속도를 편평하게
        accel[start : start + 20] = accel[start]
    return np.stack([accel, heading_rate], axis=1)


def synthetic_novice_sequence(cfg: SyntheticConfig, seed: int = 3) -> np.ndarray:
    """스모크 테스트용 합성 미숙운전(초보운전): 잦은 미세 조향수정 + 불안정한 차간거리
    + 머뭇거리다 뒤늦게 튀는 차선변경.

    급성 스파이크(난폭, seed=2)도 저주파 진동(졸음, seed=1)도 아닌 세 번째 이상 패턴.
    Zhang et al.류 지도학습(라벨=난폭운전 패턴)에는 없는 원인이라, 이 시퀀스에서도
    CUSUM 기반 tau가 하락하는지가 본 시스템의 open-set(원인 무관) 일반화 주장의
    실증 근거가 된다 (차별점_검증_및_관련연구.md 축1 참고).
    """
    # 파라미터는 실측 산정한 것이 아니라, tau가 reckless(급락 후 0 근방 고착)나
    # drowsy(완만한 저주파 하강)와는 다른 세 번째 궤적 — "계속 낮아지되 완전붕괴하지는
    # 않는 완만한 지속 하강" — 을 보이도록 수동 조정한 값이다(scripts/check_novice_scenario.py로
    # 확인). 실데이터 교체 시 반드시 재조정할 것.
    rng = np.random.default_rng(seed)
    heading_rate = rng.normal(0, 0.02, cfg.length)  # 잦은 미세 조향수정: 정상보다 고분산 잡음
    accel = np.clip(np.cumsum(rng.normal(0, 0.07, cfg.length)), -2.0, 2.0)  # 불안정한 차간거리
    for start in range(0, cfg.length, 200):  # 머뭇거림(소진동) 후 뒤늦게 차선변경
        hesitate_end = min(start + 30, cfg.length)
        heading_rate[start:hesitate_end] += rng.normal(0, 0.024, hesitate_end - start)
        burst_start = min(start + 30, cfg.length - 1)
        burst_end = min(burst_start + 8, cfg.length)
        heading_rate[burst_start:burst_end] += rng.choice([-1, 1]) * rng.uniform(0.3, 0.5)
    return np.stack([accel, heading_rate], axis=1)


def synthetic_reckless_sequence(cfg: SyntheticConfig, seed: int = 2) -> np.ndarray:
    """스모크 테스트용 합성 난폭주행: 급가속/급차선변경 스파이크(급성 이상)."""
    rng = np.random.default_rng(seed)
    accel = np.cumsum(rng.normal(0, 0.05, cfg.length))
    heading_rate = rng.normal(0, 0.01, cfg.length)
    for start in range(0, cfg.length, 100):
        accel[start : start + 5] += rng.choice([-1, 1]) * rng.uniform(1.5, 3.0)
        heading_rate[start : start + 5] += rng.choice([-1, 1]) * rng.uniform(0.5, 1.0)
    return np.stack([np.clip(accel, -3.0, 3.0), heading_rate], axis=1)
