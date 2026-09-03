"""2절: 송신 — 각 차량은 자신이 원본으로 생성한 정보만 브로드캐스트한다.

남에게서 받은 정보를 재전달(relay)하지 않는다 — 멀티홉 릴레이는 설계에서 폐기됨(이중계산 문제 회피).
단일홉 통신만 시뮬레이션한다.
"""

from dataclasses import dataclass
from typing import Dict, Optional

import numpy as np


@dataclass
class BSMMessage:
    """단일홉으로 브로드캐스트되는 메시지 (BSM 표준과 유사한 최소 페이로드).

    Attributes:
        vehicle_id: 발신 차량 ID.
        timestamp: 브로드캐스트 시각 [s].
        position: 발신 차량 위치, shape (2,) — 수신측의 상대기하 계산에 사용.
        u: 압축 운동정보 [accel, heading_rate], shape (2,).
        opinions: (선택) 발신 차량이 직접 관측해 산출한 이웃 신뢰도
            {neighbor_id: tau}. 원본 릴레이가 아닌 파생정보이므로 이중계산 문제가 없다.
    """

    vehicle_id: str
    timestamp: float
    position: np.ndarray
    u: np.ndarray
    opinions: Optional[Dict[str, float]] = None

    def __post_init__(self) -> None:
        if self.u.shape != (2,):
            raise ValueError(f"u는 [accel, heading_rate] 2차원이어야 함, got shape={self.u.shape}")
        if self.position.shape != (2,):
            raise ValueError(f"position은 2차원이어야 함, got shape={self.position.shape}")


class SingleHopChannel:
    """단일홉 V2V 통신 채널 시뮬레이션.

    각 차량은 최신 메시지 하나만 채널에 등록하고(broadcast), 수신측은 통신범위 R 내의
    이웃 메시지만 조회한다(receive). 멀티홉 전파는 구현하지 않는다.
    """

    def __init__(self, comm_range_m: float):
        self.comm_range_m = comm_range_m
        self._latest: Dict[str, BSMMessage] = {}

    def broadcast(self, msg: BSMMessage) -> None:
        self._latest[msg.vehicle_id] = msg

    def receive(self, ego_id: str, ego_position: np.ndarray) -> Dict[str, BSMMessage]:
        """단일홉 범위 R 내 이웃 메시지만 반환 (자기 자신 제외)."""
        out: Dict[str, BSMMessage] = {}
        for vid, msg in self._latest.items():
            if vid == ego_id:
                continue
            if np.linalg.norm(msg.position - ego_position) <= self.comm_range_m:
                out[vid] = msg
        return out
