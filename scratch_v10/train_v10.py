"""PROTOCOL_v10 4절: 합성 학습 세트(시드 0~299, 10분)로 B0(2채널)·B1/B2(3채널) 재학습.
사용: /opt/anaconda3/bin/python -I -B train_v10.py <B0|B1|B2> <σ_d|nom> <seed> [rx0] → models/<name>.pt, .json"""
import json
import sys
import time
from pathlib import Path

sys.dont_write_bytecode = True
V10 = Path(__file__).resolve().parent
sys.path[:0] = [str(V10), str(V10.parent / "v2v_trust_planning" / "src")]

import numpy as np  # noqa: E402
import torch  # noqa: E402
from torch.utils.data import DataLoader, TensorDataset  # noqa: E402

import gen_v10 as G  # noqa: E402
from features_v10 import K, lane_windows, residuals  # noqa: E402
from v2v_trust_planning.anomaly import NextStepPredictor  # noqa: E402

EPOCHS, BATCH, LR, STRIDE = 15, 128, 1e-3, 5
SMEAS = {"B0": 0.1, "B1": 0.1, "B2": 0.3}   # B0는 d를 쓰지 않으므로 σ_meas가 결과에 영향 없음


def model_name(b, sd, seed, rx0=False):
    return f"{b}_sd{sd:.2f}{'_rx0' if rx0 else ''}_s{seed}"


def cfg_for(b, sd, rx0=False):
    return G.load_cfg(sigma_d=sd, sigma_meas=SMEAS[b], rx_scale=0.0 if rx0 else 1.0)


def load_model(name):
    info = json.load(open(V10 / "models" / f"{name}.json"))
    m = NextStepPredictor(info["C"], 32)
    m.load_state_dict(torch.load(V10 / "models" / f"{name}.pt", weights_only=True))
    return m.eval(), info


if __name__ == "__main__":
    b, sd_arg, seed = sys.argv[1], sys.argv[2], int(sys.argv[3])
    rx0 = len(sys.argv) > 4 and sys.argv[4] == "rx0"
    sd = G.load_cfg().sigma_d if sd_arg == "nom" else float(sd_arg)
    C = 2 if b == "B0" else 3
    cfg = cfg_for(b, sd, rx0)
    torch.set_num_threads(2)
    t0 = time.time()
    runs = [G.normal_run(s, G.TRAIN_N, cfg)["F"] for s in G.TRAIN_SEEDS]
    allF = np.concatenate([F[:, :C] for F in runs]).astype(np.float64)
    mu, sdv = allF.mean(0), allF.std(0)
    XS, YS = [], []
    for F in runs:
        X, Y = lane_windows(F, np.arange(K - 1, len(F) - 1, STRIDE), C)
        XS.append(X)
        YS.append(Y)
    X = (np.concatenate(XS) - mu.astype(np.float32)) / sdv.astype(np.float32)
    Y = (np.concatenate(YS) - mu.astype(np.float32)) / sdv.astype(np.float32)
    print(b, sd, seed, "rx0" if rx0 else "", "windows", len(X), "gen", round(time.time() - t0), "s", flush=True)
    torch.manual_seed(seed)
    ds = TensorDataset(torch.from_numpy(np.ascontiguousarray(X, np.float32)), torch.from_numpy(np.ascontiguousarray(Y, np.float32)))
    loader = DataLoader(ds, batch_size=BATCH, shuffle=True, generator=torch.Generator().manual_seed(seed))
    model = NextStepPredictor(C, 32)
    opt = torch.optim.Adam(model.parameters(), lr=LR)
    curve = []
    for ep in range(EPOCHS):
        model.train()
        tot = 0.0
        for w, y in loader:
            u_hat, u_rec = model(w)
            loss = torch.mean((u_hat - y) ** 2) + 0.3 * torch.mean((u_rec - w[:, -1, :]) ** 2)
            opt.zero_grad()
            loss.backward()
            opt.step()
            tot += loss.item()
        curve.append(dict(epoch=ep, train_loss=tot / len(loader), sec=time.time() - t0))
        print(f"epoch {ep:2d} loss={curve[-1]['train_loss']:.5f} ({curve[-1]['sec']:.0f}s)", flush=True)
    model.eval()
    R = np.concatenate([residuals(model, F, C, mu, sdv)[K:] for F in runs[:100]])
    sigma_c = np.sqrt((R ** 2).mean(0))
    name = model_name(b, sd, seed, rx0)
    (V10 / "models").mkdir(exist_ok=True)
    torch.save(model.state_dict(), V10 / "models" / f"{name}.pt")
    json.dump(dict(name=name, B=b, C=C, sigma_d=sd, sigma_meas=cfg.sigma_meas, rx_scale=cfg.rx_scale, seed=seed, mu=mu.tolist(),
                   sd=sdv.tolist(), sigma_c=sigma_c.tolist(), windows=len(X), params=sum(p.numel() for p in model.parameters()),
                   curve=curve), open(V10 / "models" / f"{name}.json", "w"), indent=1)
    print(name, "sigma_c", sigma_c, "params", sum(p.numel() for p in model.parameters()), "done", flush=True)
