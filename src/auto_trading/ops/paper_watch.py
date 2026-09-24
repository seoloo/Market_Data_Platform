"""Paper 원장 감시 — 일일 보고와 중단 기준 판정 (Phase 6 watchdog).

실행기와 **독립**이다. 두 원장(T/v2 ``paper_days``, O/v1 ``paper_overlay_days``)의
행만 받아 판정하고 아무것도 쓰지 않는다. 스크립트는 DB를 읽기 전용으로 연다.

판정 기준은 각 전향 등록에서 온다 — 여기서 새로 만들지 않는다.

| 원장 | 즉시 중단 | 매달 중단 | 근거 |
|---|---|---|---|
| T/v2 | — | MDD ≥ 25% | 16 §3 |
| O/v1 | 선물 청산 · 계좌 정지 | MDD ≥ 25% · 원장 재계산 불일치 | 25 §3 |

T/v2 의 공식 MDD 는 연구 재생(``run_t_trend.py forward``)으로 매달 잰다.
여기서는 Paper 원장 자본으로 **매일** 먼저 경고한다(조기 경보).
경고선 20% 는 중단선 25% 에 닿기 전에 알리려는 운영값이다.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from itertools import pairwise
from typing import Any, Literal

from auto_trading.backtest.overlay import OverlayConfig
from auto_trading.execution.paper_overlay import verify_ledger
from auto_trading.storage.repositories.paper_days import PaperDay
from auto_trading.storage.repositories.paper_overlay_days import OverlayPaperDay

__all__ = [
    "DRILL_WINDOW_DAYS",
    "STOP_DRAWDOWN",
    "WARN_DRAWDOWN",
    "Finding",
    "LedgerSummary",
    "check_o1",
    "check_t2",
    "check_testnet_drill",
    "exit_code",
    "expected_decision_ms",
    "render_report",
]

_DAY_MS = 86_400_000
STOP_DRAWDOWN = 0.25
"""16 §3·25 §3 의 전향 관찰 중단선."""
WARN_DRAWDOWN = 0.20
"""중단선 전 조기 경보 (운영값, 판단 기준 아님)."""

Level = Literal["CRITICAL", "WARN", "INFO"]


@dataclass(frozen=True, slots=True)
class Finding:
    level: Level
    ledger: str
    message: str


@dataclass(frozen=True, slots=True)
class LedgerSummary:
    ledger: str
    last_decision_ms: int
    status: str
    equity: float
    day_change: float | None
    """전날 대비 수익률. 전날 행이 없으면 None."""
    drawdown: float
    """고점 대비 하락 (0 이상)."""
    days: int
    detail: str


def expected_decision_ms(now_ms: int) -> int:
    """지금 시각에 이미 마감돼 있어야 할 가장 최근 일봉의 close_ms."""
    return now_ms - now_ms % _DAY_MS - 1


def _date(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, tz=UTC).strftime("%Y-%m-%d")


def _freshness(ledger: str, last_ms: int, now_ms: int) -> list[Finding]:
    behind = (expected_decision_ms(now_ms) - last_ms) // _DAY_MS
    if behind > 0:
        return [Finding("CRITICAL", ledger, f"{behind}일 밀림 — 마지막 기록 {_date(last_ms)}")]
    return []


def _gaps(ledger: str, decisions: Sequence[int]) -> list[Finding]:
    missing = sum(max((b - a) // _DAY_MS - 1, 0) for a, b in pairwise(decisions))
    if missing:
        return [Finding("WARN", ledger, f"기록 사이 빈 날 {missing}일 (전체 기간)")]
    return []


def _drawdown_findings(ledger: str, drawdown: float) -> list[Finding]:
    if drawdown >= STOP_DRAWDOWN:
        return [
            Finding(
                "CRITICAL",
                ledger,
                f"고점 대비 −{drawdown:.1%} ≥ 중단선 {STOP_DRAWDOWN:.0%} — 관찰 중단·재검토",
            )
        ]
    if drawdown >= WARN_DRAWDOWN:
        return [Finding("WARN", ledger, f"고점 대비 −{drawdown:.1%} (중단선 {STOP_DRAWDOWN:.0%})")]
    return []


def check_t2(
    rows: Sequence[PaperDay], now_ms: int, *, ledger: str = "T/v2"
) -> tuple[LedgerSummary | None, list[Finding]]:
    """T/v2 Paper 원장. ``rows``는 결정일 오름차순."""
    if not rows:
        return None, [Finding("CRITICAL", ledger, "기록이 없다")]
    last = rows[-1]
    findings = _freshness(ledger, last.decision_ms, now_ms)
    findings += _gaps(ledger, [r.decision_ms for r in rows])
    if last.status == "DEGRADED":
        findings.append(Finding("CRITICAL", ledger, f"DEGRADED — {last.note or '사유 없음'}"))
    elif last.status == "NO_PRIOR_DECISION" and len(rows) > 1:
        findings.append(Finding("WARN", ledger, "전날 기록 없음 — 오늘 체결하지 않았다"))
    equities = [float(r.equity) for r in rows]
    peak = max(equities)
    drawdown = 1 - equities[-1] / peak if peak > 0 else 0.0
    findings += _drawdown_findings(ledger, drawdown)
    change = equities[-1] / equities[-2] - 1 if len(rows) > 1 and equities[-2] > 0 else None
    targets = {s: (w if w is None else f"{float(w):.3f}") for s, w in sorted(last.targets.items())}
    summary = LedgerSummary(
        ledger=ledger,
        last_decision_ms=last.decision_ms,
        status=last.status,
        equity=equities[-1],
        day_change=change,
        drawdown=drawdown,
        days=len(rows),
        detail=f"체결 {len(last.executed)}건 · 내일 목표 {targets}",
    )
    return summary, findings


def check_o1(
    rows: Sequence[OverlayPaperDay],
    now_ms: int,
    *,
    ledger: str = "O/v1",
    recent_days: int = 7,
    cfg: OverlayConfig | None = None,
) -> tuple[LedgerSummary | None, list[Finding]]:
    """O/v1·O/v2 Paper 원장. ``rows``는 결정일 오름차순 전체 (원장 재계산에 첫날부터 필요).

    ``cfg`` 는 원장을 다시 계산할 규칙이다 (O/v2 는 ``O2_STOP``).
    """
    if not rows:
        return None, [Finding("CRITICAL", ledger, "기록이 없다")]
    last = rows[-1]
    findings = _freshness(ledger, last.decision_ms, now_ms)
    liquidated = [r for r in rows if r.tally.get("liquidations")]
    if liquidated:
        findings.append(
            Finding(
                "CRITICAL",
                ledger,
                f"선물 청산 {len(liquidated)}회 (첫 {_date(liquidated[0].decision_ms)})"
                " — 25 §3 즉시 중단",
            )
        )
    if last.state.get("halted_day") is not None:
        findings.append(Finding("CRITICAL", ledger, "계좌 정지(봉 안 −30%) 발생 — 25 §3 즉시 중단"))
    problems = verify_ledger(rows, cfg=cfg)
    if problems:
        findings.append(
            Finding("CRITICAL", ledger, f"원장 재계산 불일치 {len(problems)}건 — {problems[0]}")
        )
    cutoff = last.decision_ms - recent_days * _DAY_MS
    missed = [r for r in rows if r.status == "MISSED" and r.decision_ms > cutoff]
    if missed:
        findings.append(
            Finding("WARN", ledger, f"최근 {recent_days}일 중 실행 누락 보정 {len(missed)}일")
        )
    if last.status == "NO_PRIOR_DECISION" and len(rows) > 1:
        findings.append(Finding("WARN", ledger, "오래된 목표 미체결 (누락 뒤 첫 실행)"))
    if last.tally.get("stop_hits"):
        findings.append(Finding("WARN", ledger, "선물 비상 스톱 발동 — 추세 신호대로 다시 든다"))
    if last.tally.get("loss_limit_days"):
        findings.append(Finding("INFO", ledger, "일 손실 2% — 오늘 노출 증가 거래 없음"))
    peak = float(last.state["peak"])
    drawdown = 1 - last.equity / peak if peak > 0 else 0.0
    findings += _drawdown_findings(ledger, drawdown)
    prev = rows[-2].equity if len(rows) > 1 else None
    change = last.equity / prev - 1 if prev else None
    tally = last.tally
    summary = LedgerSummary(
        ledger=ledger,
        last_decision_ms=last.decision_ms,
        status=last.status,
        equity=last.equity,
        day_change=change,
        drawdown=drawdown,
        days=len(rows),
        detail=(
            f"노출 {float(tally['exposure']):.2f}배 · funding {float(tally['funding']):+.2f}"
            f" · 거래 {tally['trades']}건"
        ),
    )
    return summary, findings


DRILL_WINDOW_DAYS = 14
"""Phase 7 운영 관찰 기간 (docs/research/26)."""


def _drill_ms(stamp: str) -> int:
    """훈련 보고서의 ``at`` (``20260924T024226Z``) → epoch ms."""
    moment = datetime.strptime(stamp, "%Y%m%dT%H%M%SZ").replace(tzinfo=UTC)
    return int(moment.timestamp() * 1000)


def check_testnet_drill(
    reports: Sequence[Mapping[str, Any]],
    now_ms: int,
    *,
    since_ms: int = 0,
    window_days: int = DRILL_WINDOW_DAYS,
) -> tuple[list[str], list[Finding]]:
    """테스트넷 훈련 보고서들로 한 줄 요약과 판정을 만든다.

    훈련 실패는 **WARN 까지만** 올린다 — Paper 원장의 안전과는 별개인 Phase 7 운영 문제다.
    ``since_ms`` 이전 기록(등록 전 개발 중 실행)은 기간 통계에 넣지 않는다.
    """
    ledger = "테스트넷"
    if not reports:
        return [], [Finding("INFO", ledger, "훈련 기록이 아직 없다")]
    ordered = sorted(reports, key=lambda r: str(r["at"]))
    latest = ordered[-1]
    latest_ms = _drill_ms(str(latest["at"]))
    checks = list(latest.get("checks", []))
    passed = sum(bool(c.get("pass")) for c in checks)
    ok = bool(latest.get("ok"))
    findings: list[Finding] = []
    behind = now_ms // _DAY_MS - latest_ms // _DAY_MS
    if now_ms >= since_ms and behind >= 1:
        findings.append(
            Finding("WARN", ledger, f"훈련 {behind}일 밀림 — 마지막 {_date(latest_ms)}")
        )
    if not ok:
        failed = [str(c.get("step")) for c in checks if not c.get("pass")]
        reason = ", ".join(failed) if failed else str(latest.get("error") or "원인 미기록")
        findings.append(
            Finding("WARN", ledger, f"최근 훈련 실패 {passed}/{len(checks)} — {reason}")
        )
    first_day = max(now_ms // _DAY_MS - window_days + 1, since_ms // _DAY_MS)
    by_day: dict[int, bool] = {}
    for r in ordered:
        at = _drill_ms(str(r["at"]))
        if at >= since_ms and at // _DAY_MS >= first_day:
            by_day[at // _DAY_MS] = bool(r.get("ok"))  # 그날 마지막 실행
    mark = "✓" if ok else "✗"
    line = f"테스트넷 훈련: {_date(latest_ms)} {passed}/{len(checks)} {mark}"
    if now_ms >= since_ms:
        line += f" · 최근 {window_days}일 중 실행 {len(by_day)}일 · 통과 {sum(by_day.values())}일"
    else:
        line += " · 운영 기간 전"
    return [line], findings


def exit_code(findings: Sequence[Finding]) -> int:
    """작업 스케줄러 '마지막 결과'로 보이게 한다: CRITICAL 2, WARN 1, 이상 없음 0."""
    levels = {f.level for f in findings}
    if "CRITICAL" in levels:
        return 2
    return 1 if "WARN" in levels else 0


def render_report(
    summaries: Sequence[LedgerSummary | None],
    findings: Sequence[Finding],
    now_ms: int,
    *,
    extra: Sequence[str] = (),
) -> str:
    """텔레그램·로컬 기록에 그대로 보내는 일일 보고 (평문). ``extra``는 원장 요약 뒤에 붙는 줄."""
    code = exit_code(findings)
    head = {0: "✅ 이상 없음", 1: "⚠️ 경고", 2: "🚨 조치 필요"}[code]
    lines = [f"[일일 점검 {_date(expected_decision_ms(now_ms))}] {head}"]
    for s in summaries:
        if s is None:
            continue
        change = "—" if s.day_change is None else f"{s.day_change:+.2%}"
        lines.append(
            f"{s.ledger}: {s.equity:,.2f} USDT ({change}) · 고점 대비 −{s.drawdown:.1%}"
            f" · {s.status} · {s.days}일"
        )
        lines.append(f"  {s.detail}")
    lines.extend(extra)
    order = {"CRITICAL": 0, "WARN": 1, "INFO": 2}
    for f in sorted(findings, key=lambda f: order[f.level]):
        lines.append(f"- {f.level} [{f.ledger}] {f.message}")
    return "\n".join(lines)
