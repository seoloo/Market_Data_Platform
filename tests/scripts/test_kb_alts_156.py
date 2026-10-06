from __future__ import annotations

import io
import zipfile
from pathlib import Path

import pytest


def test_load_4h_reads_klines_skips_header_and_fixes_microseconds(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import scripts.run_kb_alts_156 as m

    folder = tmp_path / "XUSDT" / "4h"
    folder.mkdir(parents=True)
    rows = (
        "open_time,open,high,low,close,volume\n1000,1,2,0.5,1.5,10\n2000000000000000,2,3,1,2.5,20\n"
    )
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("XUSDT-4h-2025-01.csv", rows)
    (folder / "XUSDT-4h-2025-01.zip").write_bytes(buf.getvalue())
    monkeypatch.setattr(m, "VISION", tmp_path)
    b = m.load_4h("XUSDT")
    assert list(b.t) == [1000, 2_000_000_000_000]
    assert list(b.c) == [1.5, 2.5] and list(b.v) == [10.0, 20.0]
