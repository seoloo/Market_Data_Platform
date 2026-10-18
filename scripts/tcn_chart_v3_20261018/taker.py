"""1 분봉 ZIP 의 매수 체결량(열 9)을 4 시간봉으로 — V1 manifest 해시로 같은 파일인지 확인."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

BAR_MINUTES = 240


def taker_4h(symbol: str, minutes: Any, manifest: dict[str, Any], root: Path) -> Any:
    """V1 manifest 해시와 같은 1 분봉 ZIP 의 열 9 를 읽어 240 분씩 합한다."""
    import numpy as np
    from scripts.tcn_chart_20261018 import data

    parts = []
    for month in data.months():
        p = data.VISION / "um" / symbol / "1m" / f"{symbol}-1m-{month}.zip"
        if (
            hashlib.sha256(p.read_bytes()).hexdigest()
            != manifest["sources"][str(p.relative_to(root))]
        ):
            raise ValueError(f"{p.name}: digest differs from V1 manifest")
        parts.append(data.archive(p, (0, 9)))
    a = np.concatenate(parts)
    if len(a) != len(minutes) or not np.array_equal(a[:, 0], minutes[:, 0]):
        raise ValueError(f"{symbol}: taker minutes differ from V1 cache")
    if np.any(a[:, 1] < 0) or np.any(a[:, 1] > minutes[:, 5] + 1e-9):
        raise ValueError(f"{symbol}: taker volume outside [0, volume]")
    return a[:, 1].reshape(-1, BAR_MINUTES).sum(axis=1)
