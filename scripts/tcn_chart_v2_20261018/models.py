"""V2 회귀 모델: V1 TCN 몸통 + 출력 1 개(MSE) · Ridge. 종목 · 날짜 · 펀딩 입력 없음."""

from __future__ import annotations

import copy
import json
import time
from pathlib import Path
from typing import Any

SEED = 20261018
STABILITY_SEEDS = (20261019, 20261020, 20261021, 20261022)  # 보고만, 선택 없음
RIDGE_ALPHA = 10.0  # V1 LR C=0.1 의 1/C
BUDGET_SEC = {"cuda": 3600, "cpu": 7200}  # 다섯 번 학습 합계


def network() -> Any:
    """V1 models.network 와 같은 몸통. 바뀐 것은 마지막 conv 16→1 뿐."""
    import torch
    from torch import nn

    class Block(nn.Module):
        def __init__(self, dilation: int) -> None:
            super().__init__()
            self.pad = 2 * dilation
            self.a = nn.Conv1d(16, 16, 3, dilation=dilation)
            self.b = nn.Conv1d(16, 16, 3, dilation=dilation)
            self.drop = nn.Dropout(0.1)

        def forward(self, x: Any) -> Any:
            z = self.drop(torch.relu(self.a(nn.functional.pad(x, (self.pad, 0)))))
            z = self.drop(torch.relu(self.b(nn.functional.pad(z, (self.pad, 0)))))
            return torch.relu(x + z)

    return nn.Sequential(
        nn.Conv1d(10, 16, 1), *[Block(d) for d in (1, 2, 4, 8, 16)], nn.Conv1d(16, 1, 1)
    )


def predict(model: Any, z: Any, device: str) -> Any:
    import numpy as np
    import torch

    model.eval()
    out = []
    with torch.no_grad():
        for s in range(0, len(z), 256):
            x = torch.from_numpy(z[s : s + 256].transpose(0, 2, 1).copy()).to(device)
            out.append(model(x)[:, 0, -1].cpu().numpy())
    return np.concatenate(out).astype(np.float64)


def ridge(z: Any, zy: Any, tr: Any) -> tuple[Any, Any]:
    from sklearn.linear_model import Ridge
    from threadpoolctl import threadpool_limits

    m = Ridge(alpha=RIDGE_ALPHA, fit_intercept=True)
    with threadpool_limits(limits=4):
        m.fit(z[tr].reshape(int(tr.sum()), -1), zy[tr])
        return m, m.predict(z.reshape(len(z), -1)).astype("float64")


def train_tcn(
    z: Any, zy: Any, tr: Any, va: Any, seed: int, out: Path, deadline: float, batch: int = 128
) -> tuple[Any, dict[str, Any]]:
    """학습 후 전체 행 ẑ 와 기록. 가장 낮은 VALIDATION MSE 가중치 하나."""
    import numpy as np
    import torch

    torch.manual_seed(seed)
    np.random.seed(seed)
    device = "cuda" if torch.cuda.is_available() else "cpu"
    model = network().to(device)
    params = sum(p.numel() for p in model.parameters())
    if params > 20000:
        raise ValueError("parameter budget exceeded")
    optim = torch.optim.AdamW(model.parameters(), lr=0.001, weight_decay=0.001)
    loss_fn = torch.nn.MSELoss()
    loader = torch.utils.data.DataLoader(
        torch.utils.data.TensorDataset(
            torch.from_numpy(z[tr].transpose(0, 2, 1).copy()),
            torch.from_numpy(zy[tr].astype(np.float32)),
        ),
        batch_size=batch,
        shuffle=True,
        generator=torch.Generator().manual_seed(seed),
    )
    best, stale, history, state = float("inf"), 0, [], None
    began = time.monotonic()
    for epoch in range(30):
        model.train()
        total = 0.0
        for x, t in loader:
            optim.zero_grad()
            loss = loss_fn(model(x.to(device))[:, 0, -1], t.to(device))
            loss.backward()
            torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
            optim.step()
            total += loss.item() * len(x)
        val = float(np.mean((predict(model, z[va], device) - zy[va]) ** 2))
        row = {
            "epoch": epoch + 1,
            "train_mse": total / int(tr.sum()),
            "validation_mse": val,
            "elapsed_sec": time.monotonic() - began,
        }
        history.append(row)
        print(json.dumps({"seed": seed, **row}), flush=True)
        if val < best - 0.0001:
            best, stale, state = val, 0, copy.deepcopy(model.state_dict())
        else:
            stale += 1
        if stale >= 5 or time.monotonic() > deadline:
            break
    if state is None:
        raise ValueError("no valid checkpoint")
    model.load_state_dict(state)
    path = out / f"tcn_seed{seed}.pt"
    torch.save({k: v.cpu() for k, v in state.items()}, path)
    pred = predict(model, z, device)
    restored = network().to(device)
    restored.load_state_dict(torch.load(path, weights_only=True, map_location=device))
    err = float(np.max(np.abs(predict(restored, z[:256], device) - pred[:256])))
    if err > 1e-6:
        raise ValueError("checkpoint replay mismatch")
    return pred, {
        "seed": seed,
        "device": device,
        "batch": batch,
        "params": params,
        "history": history,
        "best_epoch": int(np.argmin([h["validation_mse"] for h in history])) + 1,
        "stopped_by_budget": time.monotonic() > deadline,
        "checkpoint_replay_max_error": err,
        "training_seconds": time.monotonic() - began,
        "gpu_peak_bytes": torch.cuda.max_memory_allocated() if device == "cuda" else 0,
    }
