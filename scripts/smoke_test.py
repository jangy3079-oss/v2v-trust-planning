"""전체 파이프라인 배선 확인용 스모크 테스트 (실제 탐지 성능 검증이 아님).

이웃 차량 1대가 시간이 지나며 졸음운전 패턴을 보이는 시나리오에서, tau(신뢰도)가
학습된 predictor 기준으로 어떻게 움직이는지 눈으로 확인한다.
학습된 predictor_normal.pt가 없으면 랜덤 초기화로 배선만 확인한다 (경고 출력).

실행:
    python scripts/train_predictor.py   # 먼저 실행 권장
    python scripts/smoke_test.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import numpy as np
import torch

from v2v_trust_planning.agent import EgoVehicleAgent
from v2v_trust_planning.anomaly import AnomalyDetector, NextStepPredictor
from v2v_trust_planning.config import Config
from v2v_trust_planning.datasets import SyntheticConfig, synthetic_drowsy_sequence
from v2v_trust_planning.messaging import BSMMessage, SingleHopChannel
from v2v_trust_planning.planning import SafeMPCPlanner


def build_agent(vehicle_id: str, config: Config, channel: SingleHopChannel) -> EgoVehicleAgent:
    predictor = NextStepPredictor(config.feature_dim, config.predictor_hidden_dim)
    weights_path = Path(__file__).resolve().parent / "predictor_normal.pt"
    if weights_path.exists():
        predictor.load_state_dict(torch.load(weights_path))
        print(f"[{vehicle_id}] 학습된 predictor 로드: {weights_path}")
    else:
        print(f"[{vehicle_id}] 경고: 학습된 predictor 없음 — 랜덤 초기화로 배선만 확인 "
              f"(scripts/train_predictor.py 먼저 실행 권장)")

    detector = AnomalyDetector(
        predictor, config.history_len, config.feature_dim, config.cusum_nu, config.trust_lambda, config.recon_loss_weight
    )
    planner = SafeMPCPlanner(config.lambda1_uncertainty, config.lambda2_comfort, config.mpc_horizon)
    return EgoVehicleAgent(vehicle_id, config, channel, detector, planner)


def main() -> None:
    config = Config()
    channel = SingleHopChannel(comm_range_m=config.comm_range_fog_m)

    ego = build_agent("ego", config, channel)

    scfg = SyntheticConfig(length=200)
    neighbor_seq = synthetic_drowsy_sequence(scfg)

    ego_position = np.zeros(2)
    neighbor_position = np.array([20.0, 0.0])

    print("\nt(step)  tau(neighbor1)  u_i")
    for t in range(len(neighbor_seq)):
        neighbor_msg = BSMMessage("neighbor1", t * config.dt, neighbor_position, neighbor_seq[t])
        channel.broadcast(neighbor_msg)

        u_i = ego.step(
            t=t * config.dt,
            own_position=ego_position,
            own_u=np.zeros(2),
            visibility_m=150.0,  # 안개 시나리오 가정: 시야 저하
            sensor_estimates={"neighbor1": None},  # 센서 미탐지 -> V2V 정보만 사용(3절 c_i=0에 해당)
            u_nominal=np.zeros(2),
        )
        if t % 20 == 0:
            tau = ego.anomaly.cusum.trust("neighbor1")
            print(f"{t:6d}   {tau:12.3f}   {u_i}")


if __name__ == "__main__":
    main()
