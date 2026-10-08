"""Paper watchdog 판정: 실행 누락·DEGRADED·중단선·청산·정지·원장 불일치를 놓치지 않는다."""

from __future__ import annotations

import dataclasses
from decimal import Decimal
from typing import Any

from auto_trading.ops.paper_watch import (
    STOP_DRAWDOWN,
    Finding,
    check_o1,
    check_shadow,
    check_t2,
    check_testnet_drill,
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


class TestO2:
    """O/v2 원장은 O/v2 규칙으로 재계산해야 한다 (docs/research/28)."""

    def _rows(self) -> list[OverlayPaperDay]:
        from auto_trading.backtest.overlay import O2_STOP
        from auto_trading.execution.paper_overlay import PaperOverlayRunner
        from auto_trading.storage.db import connect
        from auto_trading.storage.migrations import apply_migrations
        from tests.execution.test_paper_overlay import TestO2Ledger

        conn = connect(":memory:")
        apply_migrations(conn, now_ms=1)
        repo = OverlayPaperDayRepository(conn)
        runner = PaperOverlayRunner(
            TestO2Ledger.crash_market(), repo, clock=lambda: 1, cfg=O2_STOP, strategy_id="o2_stop"
        )
        for k in range(130):
            runner.run_once(now_after(P0 + k * D))
        return repo.all("o2_stop")

    def test_o2_rows_verify_with_o2_rules_only(self) -> None:
        from auto_trading.backtest.overlay import O2_STOP

        rows = self._rows()
        now = now_after(P0 + 129 * D)
        _, with_o2 = check_o1(rows, now, ledger="O/v2", cfg=O2_STOP)
        assert not any("불일치" in f.message for f in with_o2)
        _, with_o1 = check_o1(rows, now, ledger="O/v2")
        assert any(f.level == "CRITICAL" and "불일치" in f.message for f in with_o1)

    def test_stop_day_warns(self) -> None:
        from auto_trading.backtest.overlay import O2_STOP

        rows = self._rows()
        hit = next(i for i, r in enumerate(rows) if r.tally.get("stop_hits"))
        rows = rows[: hit + 1]
        _, findings = check_o1(
            rows, now_after(rows[-1].decision_ms + 1 - D), ledger="O/v2", cfg=O2_STOP
        )
        assert any(f.level == "WARN" and "비상 스톱" in f.message for f in findings)


class TestO3Limits:
    """문서 82 §4 B (2026-09-26): O/v3 원장만 중단선 35% · 경고선 30%, 정지 40% 문구."""

    def test_same_drawdown_is_critical_for_o2_but_warn_for_o3(self) -> None:
        from auto_trading.ops.paper_watch import _drawdown_findings

        assert [f.level for f in _drawdown_findings("O/v2", 0.31)] == ["CRITICAL"]
        assert [f.level for f in _drawdown_findings("O/v3", 0.31, 0.35, 0.30)] == ["WARN"]
        assert [f.level for f in _drawdown_findings("O/v3", 0.36, 0.35, 0.30)] == ["CRITICAL"]
        assert _drawdown_findings("O/v3", 0.29, 0.35, 0.30) == []

    def test_watchdog_registers_o3_with_raised_limits(self) -> None:
        from scripts.paper_watchdog import _EXTRA_LEDGERS

        o3 = next(x for x in _EXTRA_LEDGERS if x[0] == "O/v3")
        assert o3[2].halt_drawdown == 0.40 and o3[3:] == (0.35, 0.30)
        o2 = next(x for x in _EXTRA_LEDGERS if x[0] == "O/v2")
        assert o2[2].halt_drawdown == 0.30 and o2[3:] == (0.25, 0.20)


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


def drill(at: str, *, ok: bool = True, failed: tuple[str, ...] = ()) -> dict[str, object]:
    checks = [{"step": s, "pass": False} for s in failed] + [{"step": "기본", "pass": True}]
    return {"at": at, "ok": ok and not failed, "checks": checks, "error": None}


def at_utc(stamp: str) -> int:
    """``20260925T001400Z`` → ms."""
    from datetime import UTC, datetime

    return int(datetime.strptime(stamp, "%Y%m%dT%H%M%SZ").replace(tzinfo=UTC).timestamp() * 1000)


START = at_utc("20260925T000000Z")


class TestTestnetDrill:
    def test_no_reports_is_only_info(self) -> None:
        lines, findings = check_testnet_drill([], START)
        assert lines == [] and [f.level for f in findings] == ["INFO"]

    def test_todays_passing_drill_is_clean(self) -> None:
        now = at_utc("20260925T002000Z")
        lines, findings = check_testnet_drill([drill("20260925T001400Z")], now, since_ms=START)
        assert findings == []
        assert "1/1 ✓" in lines[0] and "실행 1일 · 통과 1일" in lines[0]

    def test_a_failed_drill_warns_with_the_failing_step(self) -> None:
        now = at_utc("20260925T002000Z")
        report = drill("20260925T001400Z", failed=("재보호",))
        _, findings = check_testnet_drill([report], now, since_ms=START)
        assert [f.level for f in findings] == ["WARN"]
        assert "재보호" in findings[0].message

    def test_error_without_steps_is_reported(self) -> None:
        now = at_utc("20260925T002000Z")
        report = {"at": "20260925T001400Z", "ok": False, "checks": [], "error": "ExchangeError"}
        _, findings = check_testnet_drill([report], now, since_ms=START)
        assert "ExchangeError" in findings[0].message

    def test_other_drill_uses_its_own_label_and_ledger(self) -> None:
        """O/v2 선물 실행기 훈련(54)도 같은 형식이다 — 이름만 바꿔 쓴다."""
        now = at_utc("20260927T002000Z")
        report = drill("20260925T001400Z", failed=("스톱",))
        lines, findings = check_testnet_drill(
            [report], now, since_ms=START, ledger="선물 실행기", label="선물 실행기 훈련"
        )
        assert lines[0].startswith("선물 실행기 훈련: ")
        assert {f.ledger for f in findings} == {"선물 실행기"}

    def test_a_missed_day_warns(self) -> None:
        now = at_utc("20260927T002000Z")
        _, findings = check_testnet_drill([drill("20260925T001400Z")], now, since_ms=START)
        assert any("2일 밀림" in f.message for f in findings)

    def test_failures_never_escalate_beyond_warn(self) -> None:
        """훈련은 Paper 원장의 안전과 별개다 — 조치 필요(2)로 올리지 않는다."""
        now = at_utc("20260930T002000Z")
        report = drill("20260925T001400Z", failed=("a", "b"))
        _, findings = check_testnet_drill([report], now, since_ms=START)
        assert exit_code(findings) == 1

    def test_runs_before_the_start_do_not_count(self) -> None:
        """등록 전 개발 중 실행(1·2차 실패)은 기간 통계에 넣지 않는다."""
        now = at_utc("20260925T002000Z")
        reports = [
            drill("20260924T023621Z", failed=("x",)),
            drill("20260924T024226Z"),
            drill("20260925T001400Z"),
        ]
        lines, _ = check_testnet_drill(reports, now, since_ms=START)
        assert "실행 1일 · 통과 1일" in lines[0]

    def test_the_last_run_of_a_day_decides_that_day(self) -> None:
        now = at_utc("20260926T002000Z")
        reports = [
            drill("20260925T001400Z", failed=("x",)),
            drill("20260925T030000Z"),  # 같은 날 다시 돌려 통과
            drill("20260926T001400Z"),
        ]
        lines, findings = check_testnet_drill(reports, now, since_ms=START)
        assert "실행 2일 · 통과 2일" in lines[0] and findings == []

    def test_before_the_start_says_so(self) -> None:
        now = at_utc("20260924T030000Z")
        lines, findings = check_testnet_drill([drill("20260924T024226Z")], now, since_ms=START)
        assert "운영 기간 전" in lines[0] and findings == []

    def test_report_includes_the_drill_line(self) -> None:
        rows = t2_rows([10_000, 10_050])
        summary, findings = check_t2(rows, now_for(rows))
        text = render_report([summary], findings, now_for(rows), extra=["테스트넷 훈련: 요약"])
        assert "• 테스트넷 훈련\n   └ 요약" in text


class TestShadow:
    NOW = 1_790_000_000_000

    def report(self, **kw: object) -> dict[str, object]:
        base: dict[str, object] = {
            "at_ms": self.NOW,
            "status": "OK",
            "allocation": "1000",
            "orders_sent_to_exchange": 0,
            "findings": [],
            "day": {"equity": "1000.5"},
        }
        base.update(kw)
        return base

    def test_no_report_is_info(self) -> None:
        lines, found = check_shadow(None, self.NOW)
        assert lines == [] and [f.level for f in found] == ["INFO"]

    def test_line_and_clean_report(self) -> None:
        lines, found = check_shadow(self.report(), self.NOW)
        assert found == []
        assert lines[0].startswith("그림자 운영: ") and "예정 1000 USDT" in lines[0]
        assert "1,000.50" in lines[0]

    def test_behind_and_failed_are_warnings(self) -> None:
        old = self.report(at_ms=self.NOW - 2 * 86_400_000, status="FAILED", error="x")
        assert [f.level for f in check_shadow(old, self.NOW)[1]] == ["WARN", "WARN"]

    def test_security_findings_pass_through_as_critical(self) -> None:
        rep = self.report(
            findings=[
                {"level": "CRITICAL", "message": "IP 제한 없음"},
                {"level": "INFO", "message": "잔고"},
            ]
        )
        assert [f.level for f in check_shadow(rep, self.NOW)[1]] == ["CRITICAL"]

    def test_any_order_sent_is_critical(self) -> None:
        found = check_shadow(self.report(orders_sent_to_exchange=1), self.NOW)[1]
        assert [f.level for f in found] == ["CRITICAL"]


# ---------------------------------------------------------------- 데모 (55·56·57)


def test_demo_t2_line_summarises_orders_and_flags_problems() -> None:
    from auto_trading.ops.paper_watch import check_demo_t2

    now = 1_790_300_000_000
    report: dict[str, Any] = {
        "at_ms": now - 3_600_000,
        "decision_date": "2026-09-24",
        "status": "OK",
        "equity": "5000",
        "orders": [
            {"chased": "False", "cost_price_bps": "-0.1", "cost_fee_bps": "10.0"},
            {"chased": "True", "cost_price_bps": "2.1", "cost_fee_bps": "10.0"},
        ],
    }
    lines, findings = check_demo_t2(report, 0, now)
    assert "주문 2건(지정가만 1)" in lines[0] and "평균 비용 11.0bp" in lines[0]
    assert findings == []
    _, findings = check_demo_t2({**report, "status": "DEGRADED", "note": "x"}, 2, now)
    assert {f.level for f in findings} == {"WARN"} and len(findings) == 2
    _, findings = check_demo_t2(report, 0, now + 2 * 86_400_000)
    assert "밀림" in findings[0].message


def test_demo_ew_flags_dead_runner_errors_and_slow_protection() -> None:
    from auto_trading.ops.paper_watch import check_demo_ew

    now = 1_790_300_000_000
    state = {
        "phase": "OPEN",
        "last_scan_ms": now - 3_600_000,
        "entry": "61000",
        "stop": "58000",
        "target": "70708",
    }
    events = [
        {"kind": "trigger", "at_ms": now - 100, "setup": {"direction": 1}},
        {"kind": "entry_filled", "at_ms": now - 50, "protect_ms": 1200},
    ]
    lines, findings = check_demo_ew(state, events, now)
    assert "보유 중" in lines[0] and "셋업 1" in lines[0] and findings == []
    stale = {**state, "last_scan_ms": now - 6 * 3_600_000}
    slow = [
        *events,
        {"kind": "entry_filled", "at_ms": now - 10, "protect_ms": 7000},
        {"kind": "error", "at_ms": now - 5, "error": "timeout"},
    ]
    _, findings = check_demo_ew(stale, slow, now)
    messages = " | ".join(f.message for f in findings)
    assert "소식 없음" in messages and "7.0초" in messages and "오류 1건" in messages
    assert {f.level for f in findings} == {"WARN"}


def test_demo_o2_line_and_fired_stop_warning() -> None:
    from auto_trading.ops.paper_watch import check_demo_o2

    now = 1_790_300_000_000
    report: dict[str, Any] = {
        "at_ms": now - 3_600_000,
        "decision_date": "2026-09-24",
        "report": {
            "state": "OK",
            "fired_stops": [],
            "notes": [],
            "legs": [{"symbol": "ETHUSDT", "after": "0.236", "stop": "2149.32 교체"}],
        },
    }
    lines, findings = check_demo_o2(report, now)
    assert "ETH 0.236" in lines[0] and findings == []
    fired = {**report, "report": {**report["report"], "fired_stops": ["ETHUSDT PS 2149 → 0.236"]}}
    _, findings = check_demo_o2(fired, now)
    assert findings[0].level == "WARN" and "비상 스톱" in findings[0].message
