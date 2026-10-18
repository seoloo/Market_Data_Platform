"""Local shared-weight TCN. No symbols, funding, trading session or order code."""

from __future__ import annotations

import copy
import json
import time
from pathlib import Path
from typing import Any


def network() -> Any:
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
        nn.Conv1d(10, 16, 1), *[Block(d) for d in (1, 2, 4, 8, 16)], nn.Conv1d(16, 3, 1)
    )


def scaler(x: Any) -> tuple[Any, Any]:
    import numpy as np

    mean, sd = x.mean(axis=(0, 1)), x.std(axis=(0, 1))
    return mean, np.where(sd > 0, sd, 1)


def normalize(x: Any, mean: Any, sd: Any) -> Any:
    import numpy as np

    return np.clip((x - mean) / sd, -8, 8).astype(np.float32)


def predict(model: Any, x: Any, device: str) -> Any:
    import numpy as np
    import torch

    model.eval()
    probs = []
    with torch.no_grad():
        for start in range(0, len(x), 256):
            tensor = torch.from_numpy(x[start : start + 256].transpose(0, 2, 1).copy()).to(device)
            probs.append(torch.softmax(model(tensor)[:, :, -1], 1).cpu().numpy())
    return np.concatenate(probs)


def train(x: Any, y: Any, split: Any, out: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    import joblib
    import numpy as np
    import sklearn
    import torch
    from sklearn.linear_model import LogisticRegression
    from sklearn.metrics import log_loss
    from threadpoolctl import threadpool_limits

    seed = 20261018
    torch.manual_seed(seed)
    np.random.seed(seed)
    torch.set_num_threads(4)
    torch.use_deterministic_algorithms(True)
    torch.backends.cudnn.benchmark = False
    device = "cuda" if torch.cuda.is_available() else "cpu"
    if device == "cuda":
        torch.cuda.set_per_process_memory_fraction(0.65)
    tr, va = (split == 0) & (y >= 0), (split == 1) & (y >= 0)
    mean, sd = scaler(x[split == 0])
    z = normalize(x, mean, sd)
    np.savez(out / "scaler.npz", mean=mean, sd=sd)
    lr = LogisticRegression(C=0.1, max_iter=1000)
    linear_began = time.monotonic()
    with threadpool_limits(limits=4):
        lr.fit(z[tr].reshape(sum(tr), -1), y[tr])
        lr_probs = lr.predict_proba(z.reshape(len(z), -1))
    linear_seconds = time.monotonic() - linear_began
    joblib.dump(lr, out / "lr.joblib")
    if list(lr.classes_) != [0, 1, 2]:
        raise ValueError("training lacks target class")
    model = network().to(device)
    params = sum(p.numel() for p in model.parameters())
    if params > 20000:
        raise ValueError("parameter budget exceeded")
    optim = torch.optim.AdamW(model.parameters(), lr=0.001, weight_decay=0.001)
    loss_fn = torch.nn.CrossEntropyLoss()
    inputs = torch.from_numpy(z[tr].transpose(0, 2, 1).copy())
    target = torch.from_numpy(y[tr]).long()
    generator = torch.Generator().manual_seed(seed)
    loader = torch.utils.data.DataLoader(
        torch.utils.data.TensorDataset(inputs, target),
        batch_size=128,
        shuffle=True,
        generator=generator,
    )
    best, stale, history = float("inf"), 0, []
    state = None
    began = time.monotonic()
    for epoch in range(30):
        model.train()
        total = 0.0
        for batch, labels in loader:
            optim.zero_grad()
            logits = model(batch.to(device))[:, :, -1]
            loss = loss_fn(logits, labels.to(device))
            loss.backward()
            torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
            optim.step()
            total += loss.item() * len(batch)
        validation_loss = float(log_loss(y[va], predict(model, z[va], device), labels=[0, 1, 2]))
        row = {
            "epoch": epoch + 1,
            "train_loss": total / len(target),
            "validation_loss": validation_loss,
            "elapsed_sec": time.monotonic() - began,
        }
        history.append(row)
        print(json.dumps(row), flush=True)
        if validation_loss < best - 0.0001:
            best, stale = validation_loss, 0
            state = copy.deepcopy(model.state_dict())
        else:
            stale += 1
        if stale >= 5 or time.monotonic() - began > (3600 if device == "cuda" else 7200):
            break
    if state is None:
        raise ValueError("no valid checkpoint")
    model.load_state_dict(state)
    torch.save({k: v.cpu() for k, v in state.items()}, out / "tcn.pt")
    probs = predict(model, z, device)
    restored = network().to(device)
    restored.load_state_dict(torch.load(out / "tcn.pt", weights_only=True, map_location=device))
    error = float(np.max(np.abs(predict(restored, z[:128], device) - probs[:128])))
    if error > 1e-6:
        raise ValueError("checkpoint replay mismatch")
    info = {
        "seed": seed,
        "device": device,
        "params": params,
        "history": history,
        "checkpoint_prediction_max_error": error,
        "torch": torch.__version__,
        "gpu": torch.cuda.get_device_name(0) if device == "cuda" else None,
        "numpy": np.__version__,
        "sklearn": sklearn.__version__,
        "gpu_peak_bytes": torch.cuda.max_memory_allocated() if device == "cuda" else 0,
        "training_seconds": time.monotonic() - began,
        "linear_training_seconds": linear_seconds,
    }
    return {"LR": lr_probs, "TCN": probs}, info
