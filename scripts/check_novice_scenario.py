"""축1 open-set 일반화 주장의 배선 확인: 미숙운전(초보운전) 시나리오에서도 tau가 하락하는지 확인.

smoke_test.py(졸음 시나리오)와 동일한 파이프라인을 미숙운전 합성 시퀀스에 적용한다.
predictor는 정상데이터만으로 학습되었고 "미숙운전"이라는 라벨을 본 적이 없다 —
그런데도 tau가 하락한다면, 원인 라벨 없이(open-set) 정상 전이 패턴 이탈만으로
음주/졸음 외의 원인도 잡아낸다는 차별점_검증_및_관련연구.md 축1 주장의 배선 증거가 된다.

실제 논문에 쓸 수치가 아니라 배선/방향성 확인용이며, 정식 검증은 CARLA/SUMO 시나리오와
Zhang et al. 스타일 지도학습 베이스라인(난폭운전 라벨만 학습) 비교로 별도 수행할 것.

실행:
    python scripts/train_predictor.py   # 먼저 실행 권장
    python scripts/check_novice_scenario.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import numpy as np
import torch

from v2v_trust_planning.agent import EgoVehicleAgent
from v2v_trust_planning.anomaly import AnomalyDetector, NextStepPredictor
from v2v_trust_planning.config import Config
from v2v_trust_planning.datasets import SyntheticConfig, synthetic_novice_sequence
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
    neighbor_seq = synthetic_novice_sequence(scfg)

    ego_position = np.zeros(2)
    neighbor_position = np.array([20.0, 0.0])

    print("\n미숙운전(초보운전) 시나리오 — predictor는 이 패턴을 학습 시 본 적 없음")
    print("t(step)  tau(neighbor1)  u_i")
    for t in range(len(neighbor_seq)):
        neighbor_msg = BSMMessage("neighbor1", t * config.dt, neighbor_position, neighbor_seq[t])
        channel.broadcast(neighbor_msg)

        u_i = ego.step(
            t=t * config.dt,
            own_position=ego_position,
            own_u=np.zeros(2),
            visibility_m=150.0,
            sensor_estimates={"neighbor1": None},
            u_nominal=np.zeros(2),
        )
        if t % 20 == 0:
            tau = ego.anomaly.cusum.trust("neighbor1")
            print(f"{t:6d}   {tau:12.3f}   {u_i}")


if __name__ == "__main__":
    main()
