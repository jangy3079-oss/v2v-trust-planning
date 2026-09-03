"""정상데이터만으로 f_phi(NextStepPredictor)를 자기지도 학습한다 (4.1절).

실행:
    python scripts/train_predictor.py

TODO: synthetic_normal_sequence를 UAH-DriveSet의 실제 NORMAL 세션들
(datasets.find_sessions + load_uah_driveset_session)로 교체할 것.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import torch
from torch.utils.data import DataLoader, Dataset

from v2v_trust_planning.anomaly import NextStepPredictor
from v2v_trust_planning.config import Config
from v2v_trust_planning.datasets import SyntheticConfig, synthetic_normal_sequence


class WindowedNormalDataset(Dataset):
    """정상 시퀀스들을 (윈도우, 다음스텝 정답) 쌍으로 슬라이싱."""

    def __init__(self, sequences, k: int):
        self.samples = []
        for seq in sequences:
            for t in range(k, len(seq)):
                self.samples.append((seq[t - k : t], seq[t]))

    def __len__(self) -> int:
        return len(self.samples)

    def __getitem__(self, idx: int):
        window, target = self.samples[idx]
        return torch.from_numpy(window).float(), torch.from_numpy(target).float()


def main() -> None:
    cfg = Config()

    # TODO: UAH-DriveSet normal 세션으로 교체 (datasets.find_sessions(root, "NORMAL"))
    sequences = [synthetic_normal_sequence(SyntheticConfig(), seed=s) for s in range(20)]

    dataset = WindowedNormalDataset(sequences, cfg.history_len)
    loader = DataLoader(dataset, batch_size=64, shuffle=True)

    model = NextStepPredictor(cfg.feature_dim, cfg.predictor_hidden_dim)
    optimizer = torch.optim.Adam(model.parameters(), lr=1e-3)

    for epoch in range(20):
        total_loss = 0.0
        for window, target in loader:
            u_hat, u_recon = model(window)
            loss = torch.mean((u_hat - target) ** 2)
            if u_recon is not None:
                loss = loss + cfg.recon_loss_weight * torch.mean((u_recon - window[:, -1, :]) ** 2)

            optimizer.zero_grad()
            loss.backward()
            optimizer.step()
            total_loss += loss.item()

        print(f"epoch {epoch:2d}: loss={total_loss / len(loader):.5f}")

    out_path = Path(__file__).resolve().parent / "predictor_normal.pt"
    torch.save(model.state_dict(), out_path)
    print(f"저장 완료: {out_path}")


if __name__ == "__main__":
    main()
