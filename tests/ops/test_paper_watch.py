"""Paper watchdog 판정: 실행 누락·DEGRADED·중단선·청산·정지·원장 불일치를 놓치지 않는다."""

from __future__ import annotations

import dataclasses
from decimal import Decimal

from auto_trading.ops.paper_watch import (
    STOP_DRAWDOWN,
    Finding,
    check_o1,
    check_t2,
    exit_code,
    render_report,
)
from auto_trading.storage.repositories.paper_days import PaperDay
from auto_trading.storage.repositories.paper_overlay_days import (
    OverlayPaperDay,
    OverlayPaperDayRepository,
)
from tests.execution.test_paper_overlay import P0, now_after, setup

D = 86_400_000


def t2_rows(equities: list[float], *, first_day: int = P0, status: str = "OK") -> list[PaperDay]:
    rows = []
    for k, eq in enumerate(equities):
        rows.append(
            PaperDay(
                strategy_id="t2_long",
                decision_ms=first_day + (k + 1) * D - 1,
                status="NO_PRIOR_DECISION" if k == 0 else status,
                equity=Decimal(repr(eq)),
                holdings={},
                prices={},
                targets={"BTCUSDT": "0.3", "ETHUSDT": None},
                executed=[],
                note=None,
                created_ms=1,
            )
        )
    return rows


def now_for(rows: list[PaperDay]) -> int:
    """마지막 행이 '오늘 실행분'인 시각."""
    return rows[-1].decision_ms + 1 + 10 * 60_000


def levels(findings: list[Finding]) -> set[str]:
    return {f.level for f in findings}


class TestT2:
    def test_a_healthy_ledger_has_no_findings(self) -> None:
        rows = t2_rows([10_000, 10_050, 10_020])
        summary, findings = check_t2(rows, now_for(rows))
        assert findings == []
        assert summary is not None
        assert summary.day_change is not None
        assert round(summary.day_change, 6) == round(10_020 / 10_050 - 1, 6)

    def test_a_day_behind_is_critical(self) -> None:
        rows = t2_rows([10_000, 10_050])
        _, findings = check_t2(rows, now_for(rows) + D)
        assert "CRITICAL" in levels(findings)
        assert any("밀림" in f.message for f in findings)

    def test_degraded_is_critical(self) -> None:
        rows = t2_rows([10_000, 10_050])
        rows[-1] = dataclasses.replace(rows[-1], status="DEGRADED", note="미해소 주문")
        _, findings = check_t2(rows, now_for(rows))
        assert any(f.level == "CRITICAL" and "DEGRADED" in f.message for f in findings)

    def test_stop_drawdown_is_critical(self) -> None:
        rows = t2_rows([10_000, 12_000, 12_000 * (1 - STOP_DRAWDOWN)])
        _, findings = check_t2(rows, now_for(rows))
        assert any(f.level == "CRITICAL" and "중단선" in f.message for f in findings)

    def test_approaching_the_stop_line_warns_first(self) -> None:
        rows = t2_rows([10_000, 7_800])
        _, findings = check_t2(rows, now_for(rows))
        assert levels(findings) == {"WARN"}

    def test_a_gap_in_history_warns(self) -> None:
        rows = t2_rows([10_000, 10_010, 10_020])
        rows = [rows[0], rows[2]]
        _, findings = check_t2(rows, now_for(rows))
        assert any("빈 날 1일" in f.message for f in findings)

    def test_no_rows_is_critical(self) -> None:
        summary, findings = check_t2([], P0)
        assert summary is None
        assert levels(findings) == {"CRITICAL"}


class TestO1:
    def _rows(self, days: list[int]) -> list[OverlayPaperDay]:
        runner, _, conn = setup()
        for k in days:
            runner.run_once(now_after(P0 + k * D))
        return OverlayPaperDayRepository(conn).all("o1_overlay")

    def test_a_healthy_ledger_has_no_findings(self) -> None:
        rows = self._rows(list(range(140)))
        summary, findings = check_o1(rows, now_after(P0 + 139 * D))
        assert [f for f in findings if f.level != "INFO"] == []
        assert summary is not None
        assert "노출" in summary.detail

    def test_recent_catch_up_warns(self) -> None:
        rows = self._rows([*range(135), 138])  # 135~137일 3일 누락
        _, findings = check_o1(rows, now_after(P0 + 138 * D))
        assert any(f.level == "WARN" and "누락 보정 3일" in f.message for f in findings)

    def test_a_liquidation_anywhere_is_critical(self) -> None:
        rows = self._rows(list(range(10)))
        tally = dict(rows[4].tally, liquidations=1)
        rows[4] = dataclasses.replace(rows[4], tally=tally)
        _, findings = check_o1(rows, now_after(P0 + 9 * D))
        assert any(f.level == "CRITICAL" and "청산" in f.message for f in findings)

    def test_an_account_halt_is_critical(self) -> None:
        rows = self._rows(list(range(5)))
        state = dict(rows[-1].state, halted_day=P0 + 4 * D)
        rows[-1] = dataclasses.replace(rows[-1], state=state)
        _, findings = check_o1(rows, now_after(P0 + 4 * D))
        assert any(f.level == "CRITICAL" and "계좌 정지" in f.message for f in findings)

    def test_a_tampered_ledger_is_critical(self) -> None:
        rows = self._rows(list(range(140)))
        rows[70] = dataclasses.replace(rows[70], equity=rows[70].equity * 1.01)
        _, findings = check_o1(rows, now_after(P0 + 139 * D))
        assert any(f.level == "CRITICAL" and "불일치" in f.message for f in findings)

    def test_a_missed_run_today_is_critical(self) -> None:
        rows = self._rows(list(range(5)))
        _, findings = check_o1(rows, now_after(P0 + 6 * D))
        assert any(f.level == "CRITICAL" and "2일 밀림" in f.message for f in findings)


class TestReport:
    def test_exit_codes_follow_the_worst_level(self) -> None:
        assert exit_code([]) == 0
        assert exit_code([Finding("INFO", "x", "")]) == 0
        assert exit_code([Finding("WARN", "x", ""), Finding("INFO", "x", "")]) == 1
        assert exit_code([Finding("WARN", "x", ""), Finding("CRITICAL", "x", "")]) == 2

    def test_report_lists_both_ledgers_and_puts_critical_first(self) -> None:
        rows = t2_rows([10_000, 10_050])
        summary, _ = check_t2(rows, now_for(rows))
        findings = [
            Finding("INFO", "O/v1", "정보"),
            Finding("CRITICAL", "T/v2", "심각"),
            Finding("WARN", "O/v1", "경고"),
        ]
        text = render_report([summary, None], findings, now_for(rows))
        assert text.splitlines()[0].endswith("🚨 조치 필요")
        assert "T/v2: 10,050.00 USDT" in text
        body = [line for line in text.splitlines() if line.startswith("- ")]
        assert [line.split()[1] for line in body] == ["CRITICAL", "WARN", "INFO"]

    def test_clean_report_says_so(self) -> None:
        rows = t2_rows([10_000, 10_050])
        summary, findings = check_t2(rows, now_for(rows))
        assert (
            render_report([summary], findings, now_for(rows))
            .splitlines()[0]
            .endswith("✅ 이상 없음")
        )
