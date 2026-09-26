"""문서 65 점검 채점 — 기준 경계와 자료 없음 처리."""

from __future__ import annotations

from typing import Any

from auto_trading.ops.review import (
    DAY_MS,
    LedgerRow,
    demo_t2_review,
    ledger_review,
    normalize_demo_orders,
    shadow_review,
    watch_review,
)

D0 = 1_790_121_599_999  # 2026-09-22 결정일 (UTC 하루 끝)


def _rows(n: int, equity: list[float] | None = None, **kw: object) -> list[LedgerRow]:
    eq = equity or [10_000.0] * n
    return [LedgerRow(D0 + k * DAY_MS, "OK", eq[k], **kw) for k in range(n)]  # type: ignore[arg-type]


def _status(review: object, cid: str) -> str:
    return str(next(c.status for c in review.criteria if c.id == cid))  # type: ignore[attr-defined]


def test_p1_needs_28_days_and_p5_flags_missing_days() -> None:
    rows = _rows(28)
    ok = ledger_review("T", rows, until_ms=D0 + 27 * DAY_MS, overlay=False, verify_problems=None)
    assert ok is not None and _status(ok, "P1") == "PASS" and _status(ok, "P5") == "PASS"
    short = ledger_review(
        "T", rows[:27], until_ms=D0 + 26 * DAY_MS, overlay=False, verify_problems=None
    )
    assert short is not None and _status(short, "P1") == "FAIL"
    gap = ledger_review(
        "T", rows[:5] + rows[6:], until_ms=D0 + 27 * DAY_MS, overlay=False, verify_problems=None
    )
    assert gap is not None and _status(gap, "P5") == "CHECK" and len(gap.missing) == 1


def test_p2_p3_p4_for_overlay() -> None:
    eq = [10_000.0, 9_000.0, 7_400.0] + [8_000.0] * 25
    bad = ledger_review(
        "O",
        _rows(28, eq, liquidations=0),
        until_ms=D0 + 27 * DAY_MS,
        overlay=True,
        verify_problems=2,
    )
    assert bad is not None
    assert _status(bad, "P3") == "FAIL"  # 26% 낙폭
    assert _status(bad, "P4") == "FAIL"
    liq = _rows(28)
    liq[3] = LedgerRow(liq[3].decision_ms, "OK", 10_000.0, liquidations=1)
    rev = ledger_review("O", liq, until_ms=D0 + 27 * DAY_MS, overlay=True, verify_problems=0)
    assert rev is not None and _status(rev, "P2") == "FAIL" and _status(rev, "P4") == "PASS"


def test_watch_review_missing_day_and_critical_need_a_human() -> None:
    reports: dict[str, dict[str, Any]] = {
        "2026-09-22": {"findings": []},
        "2026-09-23": {"findings": [{"level": "CRITICAL", "ledger": "T/v2", "message": "밀림"}]},
    }
    crit, info = watch_review(reports, first_ms=D0, until_ms=D0 + 2 * DAY_MS)
    assert crit.status == "CHECK"
    assert info["missing"] == ["2026-09-24"] and len(info["critical"]) == 1
    clean, _ = watch_review(
        {"2026-09-22": {"findings": [{"level": "WARN"}]}}, first_ms=D0, until_ms=D0
    )
    assert clean.status == "PASS"


def _shadow(bps: str, *, today: bool, gaps: list[str] | None = None) -> dict[str, object]:
    book: dict[str, dict[str, str | None]] = {
        "probe_10000": {"buy_bps": "0.10", "sell_bps": "0.20"}
    }
    if today:
        book["today_order"] = {"buy_bps": bps, "sell_bps": None}
    return {
        "orders_sent_to_exchange": 0,
        "findings": [],
        "tracking_gaps": gaps or [],
        "paper_parity": "목표 최대 차이 0.00e+0",
        "slippage": {"BTCUSDT": book},
    }


def test_shadow_s4_uses_todays_order_and_s5_catches_paper_mismatch() -> None:
    reports = {"2026-09-22": _shadow("6.0", today=True), "2026-09-23": _shadow("0", today=False)}
    crit, _ = shadow_review(reports, until_ms=D0 + DAY_MS)
    by = {c.id: c.status for c in crit}
    assert by == {"S1": "PASS", "S2": "PASS", "S3": "PASS", "S4": "FAIL", "S5": "PASS"}
    reports = {
        "2026-09-22": _shadow("0", today=False, gaps=["Paper 와 목표가 다름 (최대 차이 1e-3)"])
    }
    crit, _ = shadow_review(reports, until_ms=D0 + DAY_MS)
    by = {c.id: c.status for c in crit}
    assert by["S5"] == "FAIL" and by["S4"] == "PASS" and by["S1"] == "CHECK"
    none, _ = shadow_review({"2026-10-30": _shadow("0", today=False)}, until_ms=D0)
    assert all(c.status == "NA" for c in none)


def test_demo_orders_from_new_and_legacy_records() -> None:
    new = {
        "orders": [
            {
                "symbol": "BTCUSDT",
                "avg_price": "100",
                "maker_qty": "1",
                "taker_qty": "1",
                "cost_price_bps": "2.0",
                "cost_fee_bps": "10.0",
                "wait_ms": "120000",
                "post_rejects": "1",
                "chased": "True",
            }
        ]
    }
    legacy = {
        "executed": [
            {
                "symbol": "ETHUSDT",
                "avg_price": "200",
                "maker_qty": "1",
                "taker_qty": "0",
                "is_price_bps": "0",
                "is_fee_bps": "10",
                "wait_min": 0.5,
                "post_rejects": 0,
                "chased": False,
            }
        ]
    }
    orders = normalize_demo_orders([new], [legacy])
    assert len(orders) == 2
    crit, stats = demo_t2_review(orders, unresolved=0, degraded_days=[])
    by = {c.id: c.status for c in crit}
    assert by["K1"] == "PASS"
    assert abs(stats["maker_share"] - 300 / 400) < 1e-12
    assert abs(stats["price_bps"] - 1.0) < 1e-12  # 명목 가중 (2·200 + 0·200) / 400
    bad, _ = demo_t2_review([], unresolved=1, degraded_days=["2026-09-25"])
    assert bad[0].status == "CHECK" and bad[1].status == "NA"


def test_o3_uses_its_own_raised_mdd_line() -> None:
    """문서 82 §4 B (2026-09-26): O/v3 는 중단선 35%.

    같은 26% 낙폭이 O/v2 에선 FAIL, O/v3 에선 PASS.
    """
    eq = [10_000.0, 9_000.0, 7_400.0] + [8_000.0] * 25
    rows = _rows(28, eq, liquidations=0)
    o2 = ledger_review("O/v2", rows, until_ms=D0 + 27 * DAY_MS, overlay=True, verify_problems=0)
    o3 = ledger_review(
        "O/v3", rows, until_ms=D0 + 27 * DAY_MS, overlay=True, verify_problems=0, mdd_stop=0.35
    )
    assert o2 is not None and o3 is not None
    assert _status(o2, "P3") == "FAIL" and _status(o3, "P3") == "PASS"
