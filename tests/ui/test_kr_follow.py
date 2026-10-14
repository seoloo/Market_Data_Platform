"""국장 주도주 '목록 따라가기' — 전날 목록을 고르게 든 지수 (가짜 자료)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from auto_trading.ui.kr_shadow_render import follow_lines, kospi_200


def _csv(path: Path, rows: list[tuple[str, float]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(f"{d},{c},1,1\n" for d, c in rows), encoding="utf-8")


def test_follow_uses_previous_evening_list(tmp_path: Path) -> None:
    base = tmp_path / "data" / "kr_shadow"
    _csv(base / "prices" / "A.csv", [("20261001", 100), ("20261002", 110), ("20261005", 121)])
    _csv(base / "prices" / "B.csv", [("20261001", 100), ("20261002", 90), ("20261005", 90)])
    base.joinpath("kospi.csv").write_text(
        "date,close\n20261001,1000\n20261002,1010\n20261005,1000\n", encoding="utf-8"
    )
    history: list[dict[str, Any]] = [
        {"date": "2026-10-01", "price_leaders": [{"code": "A"}, {"code": "B"}], "flow_leaders": []},
        {
            "date": "2026-10-02",
            "price_leaders": [{"code": "A"}],
            "ai": {"leaders": [{"code": "B"}]},
        },
        {"date": "2026-10-05", "price_leaders": [{"code": "B"}]},
    ]
    lines = follow_lines(tmp_path, history)
    # 10-02: 10-01 목록 A · B 평균 (+10% , −10%) = 0 → 100. 10-05: 10-02 목록 A +10% → 110
    assert [v for _, v in lines["price"]] == pytest.approx([100.0, 100.0, 110.0])
    assert [v for _, v in lines["ai"]] == pytest.approx([100.0, 100.0])  # 10-02 AI 목록 B 0%
    assert lines["flow"] == [("2026-10-01", 100.0)]  # 빈 목록은 점을 더하지 않는다
    assert [round(v, 2) for _, v in lines["kospi"]] == [100.0, 101.0, 100.0]


def test_follow_needs_two_days(tmp_path: Path) -> None:
    lines = follow_lines(tmp_path, [{"date": "2026-10-01"}])
    assert all(v == [] for v in lines.values())


def test_kospi_200_day_average(tmp_path: Path) -> None:
    base = tmp_path / "data" / "kr_shadow"
    base.mkdir(parents=True)
    lines = ["date,close"] + [
        f"2026{1 + i // 28:02d}{1 + i % 28:02d},{100 + i}" for i in range(250)
    ]
    base.joinpath("kospi.csv").write_text("\n".join(lines) + "\n", encoding="utf-8")
    closes, ma = kospi_200(tmp_path, days=10)
    assert len(closes) == 10 and len(ma) == 10
    assert ma[-1][1] == pytest.approx(sum(range(150, 350)) / 200)  # 마지막 200 개 평균
    assert closes[-1][1] == 349
