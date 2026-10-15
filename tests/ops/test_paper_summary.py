"""텔레그램 'Paper' 요약 · 명령 인식 (가짜 보고서)."""

from __future__ import annotations

import json
from pathlib import Path

from auto_trading.notify.inbox import InboxMessage, is_command
from auto_trading.ops.paper_summary import paper_text


def _save(root: Path, rel: str, body: object) -> None:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(body), encoding="utf-8")


def test_summary_reads_saved_ledgers(tmp_path: Path) -> None:
    _save(
        tmp_path,
        "reports/ag_paper/latest.json",
        {"books": {"AG-CR": {"value_usd": 908.2, "return": -0.0918}}},
    )
    _save(
        tmp_path,
        "reports/ags_paper/latest.json",
        {"books": {"AG4S2": {"long": "AG-CR4", "short_leverage": 2.0, "return_since_start": None}}},
    )
    _save(
        tmp_path,
        "reports/nq_paper/latest.json",
        {"equity_usd": 981.4, "return_since_start": -0.019},
    )
    _save(
        tmp_path,
        "data/mf_paper/state.json",
        {"books": {"GBG": {"history": [["2026-10-14", 974.3]]}}},
    )
    _save(
        tmp_path,
        "reports/kr_shadow/latest.json",
        {
            "date": "2026-10-14",
            "kospi_above_200d": False,
            "paper": {
                "price_rule": {"value_krw": 10_000_000, "positions": []},
                "equal_weight_krw": 9_197_438,
            },
        },
    )
    text = paper_text(tmp_path, coins=lambda: [("T/v2", 10_000.0, 9_761.0)])
    assert "T/v2 9,761 (-2.4%)" in text
    assert "AG-CR $908 (-9.2%)" in text and "AG4S2 (AG-CR4 + 숏 2 배) 시작 전" in text
    assert "NQ $981 (-1.9%)" in text and "Glide·채권·금 $974 (-2.6%)" in text
    assert "① 가격 10,000,000원 · 보유 0 종목" in text and "아래 — 새로 안 삼" in text


def test_empty_root_says_nothing_saved(tmp_path: Path) -> None:
    assert "아직 없어요" in paper_text(tmp_path, coins=list)


def test_paper_words_are_bot_commands() -> None:
    for word in ("paper", "/paper", "페이퍼", "Paper"):
        assert is_command(InboxMessage(update_id=1, date=0, text=word))
