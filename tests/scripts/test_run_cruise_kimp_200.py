from __future__ import annotations

from pathlib import Path

import pytest
import scripts.run_cruise_kimp_200 as mod


def test_no_kimp_uses_previous_fx_and_kimp_stats(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "BTCUSDT.csv").write_text(
        "date,open,high,low,close,volume\n"
        "2024-01-05,0,0,0,10,0\n2024-01-06,0,0,0,11,0\n2024-01-07,0,0,0,12,0\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(mod, "BIN", tmp_path)
    fx = {"2024-01-05": 1000.0, "2024-01-08": 1100.0}  # 주말엔 금요일 환율
    days = ["2024-01-05", "2024-01-06", "2024-01-07"]
    n = mod.no_kimp("BTC", days, fx)
    assert n == {"2024-01-05": 10_000.0, "2024-01-06": 11_000.0, "2024-01-07": 12_000.0}
    u = {"BTC": {d: v * 1.05 for d, v in n.items()}}
    k = mod.kimp_stats(u, {"BTC": n})["BTC"]
    assert k["median"] == pytest.approx(0.05) and k["first"][1] == pytest.approx(0.05)
