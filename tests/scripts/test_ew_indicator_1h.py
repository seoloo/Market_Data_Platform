from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import scripts.ew_indicator_1h as mod
from scripts.demo_ew_btc import Store


def test_stretch_maps_real_hour_to_engine_four_hours() -> None:
    rows = [[3_600_000 * 10 + k * 60_000, "1", "2", "0.5", "1.5", "8"] for k in range(60)]
    out = mod.stretch(rows)
    assert len(out) == 240
    assert out[0].open_ms == 3_600_000 * 40 == 14_400_000 * 10
    assert out[-1].close_ms == 14_400_000 * 11 - 1
    assert str(out[0].volume) == "2"
    assert mod.real_ms(14_400_000 * 11 - 1) == 3_600_000 * 11 - 1


def test_check_once_alerts_only_new_pivots_with_pattern(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sent: list[str] = []
    view = {"top": 10, "trend_up": 5, "trend_down": 5, "kinds": {"하락 충격파 끝": 5}, "best": None}
    info_hit = {"pivot": "저점", "price": "80000", "top": 10, "up": 0, "down": 5, "view": view}
    info_miss = {"pivot": "고점", "price": "81000", "top": 10, "up": 0, "down": 0, "view": view}
    rows: list[Any] = [(4 * 1000 - 1, None, 10, info_miss)]
    monkeypatch.setattr(mod, "fetch_real_minutes", lambda c, n: [])
    monkeypatch.setattr(mod, "scan", lambda m, t: rows)
    store = Store(tmp_path)
    assert mod.check_once(None, store, sent.append, 0) == 0  # 첫 실행: 기록만
    rows.extend([(4 * 2000 - 1, None, 10, info_miss), (4 * 3000 - 1, None, 10, info_hit)])
    assert mod.check_once(None, store, sent.append, 0) == 1
    assert "1h 저점 확정 @80,000.0" in sent[0] and "참고용" in sent[0]
    assert mod.check_once(None, store, sent.append, 0) == 0
