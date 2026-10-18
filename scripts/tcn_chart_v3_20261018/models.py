"""V3 TCN: V2 와 같은 몸통 · 학습, 첫 1×1 conv 입력만 12 채널."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from scripts.tcn_chart_v2_20261018 import models as v2

CHANNELS = 12
SEED, STABILITY_SEEDS, BUDGET_SEC = v2.SEED, v2.STABILITY_SEEDS, v2.BUDGET_SEC
ridge, predict = v2.ridge, v2.predict
_BASE = v2.network  # 바꿔 끼우기 전 원본 (재귀 방지)


def network() -> Any:
    from torch import nn

    body = _BASE()  # Conv1d(10,16,1) → 블록 5 개 → Conv1d(16,1,1)
    body[0] = nn.Conv1d(CHANNELS, 16, 1)
    return body


@contextmanager
def _twelve() -> Iterator[None]:
    original = v2.network
    v2.network = network
    try:
        yield
    finally:
        v2.network = original


def train_tcn(
    z: Any, zy: Any, tr: Any, va: Any, seed: int, out: Path, deadline: float, batch: int = 128
) -> tuple[Any, dict[str, Any]]:
    with _twelve():
        return v2.train_tcn(z, zy, tr, va, seed, out, deadline, batch)
