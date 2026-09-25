"""대시보드 데이터 — DB 를 **읽기 전용**으로 열고 보고서 파일을 읽는다. 쓰지 않는다.

import 는 core·storage 만 한다(import-linter 계약). 설정은 YAML 파일을
직접 읽는다. 그래서 원장 재계산·판정은 하지 않고,
그 결과를 남긴 곳(일일 점검 JSON, 훈련 보고서, 연구 기록)을 읽어 보여 준다.

읽다가 실패한 부분은 ``errors`` 에 적고 나머지를 계속 보여 준다 — 화면이 통째로 죽지 않게.
PAPER 원장과 TESTNET 자산은 **절대 합산하지 않는다**(각각 독립 원장).
"""

from __future__ import annotations

import json
import re
import sqlite3
from collections.abc import Sequence
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

from auto_trading.core.cash_yield import RateSchedule, Tier, with_cash_yield
from auto_trading.core.errors import AutoTradingError
from auto_trading.storage.db import connect_read_only
from auto_trading.storage.repositories.paper_days import PaperDay, PaperDayRepository
from auto_trading.storage.repositories.paper_overlay_days import (
    OverlayPaperDay,
    OverlayPaperDayRepository,
)
from auto_trading.ui.demo_data import DemoView, load_demo

__all__ = [
    "BacktestRow",
    "DashboardData",
    "DrillView",
    "Holding",
    "LedgerView",
    "Point",
    "ResearchEntry",
    "WatchView",
    "load_dashboard",
]

_DAY_MS = 86_400_000
_SYMBOLS = ("BTCUSDT", "ETHUSDT")


@dataclass(frozen=True, slots=True)
class Point:
    day_ms: int
    """일봉 open_ms (UTC 00:00)."""
    equity: float


@dataclass(frozen=True, slots=True)
class Holding:
    ledger: str
    symbol: str
    venue: str
    """현물 · 선물."""
    qty: float
    notional: float
    weight: float
    """자본 대비 명목."""
    protection: str


@dataclass(frozen=True, slots=True)
class LedgerView:
    key: str
    name: str
    subtitle: str
    points: tuple[Point, ...]
    status: str
    note: str
    peak: float
    exposure: float
    holdings: tuple[Holding, ...]
    trades_today: int
    stop_hits: int = 0
    liquidations: int = 0
    halted: bool = False
    idle: tuple[float, ...] = ()
    """날마다 마감 뒤 유휴 현금 / 자본 (이자 가정의 근거)."""
    with_yield: tuple[Point, ...] = ()
    """유휴 현금 이자를 더한 가정 경로 (config/cash_yield.yaml). 원장이 아니다."""

    @property
    def equity(self) -> float:
        return self.points[-1].equity if self.points else 0.0

    @property
    def start(self) -> float:
        return self.points[0].equity if self.points else 0.0

    @property
    def change(self) -> float | None:
        return self.equity / self.start - 1 if self.start else None

    @property
    def drawdown(self) -> float:
        return 1 - self.equity / self.peak if self.peak > 0 else 0.0


@dataclass(frozen=True, slots=True)
class DrillView:
    kind: str
    """drill(매일 훈련) · stop(스톱 발동) · stream(WS)."""
    at_ms: int
    ok: bool
    outcome: str
    checks: tuple[tuple[str, bool, str], ...]
    observations: dict[str, Any]

    @property
    def passed(self) -> int:
        return sum(ok for _, ok, _ in self.checks)


@dataclass(frozen=True, slots=True)
class WatchView:
    date: str
    exit_code: int | None
    findings: tuple[tuple[str, str, str], ...]
    """(level, ledger, message)."""
    lines: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ResearchEntry:
    title: str
    verdict: str
    date: str
    summary: str


@dataclass(frozen=True, slots=True)
class BacktestRow:
    name: str
    cagr: float
    mdd: float
    sharpe: float
    final_equity: float | None


@dataclass(frozen=True, slots=True)
class DashboardData:
    now_ms: int
    ledgers: tuple[LedgerView, ...]
    benchmark: tuple[Point, ...]
    """BTC·ETH 50/50 현물 보유 (O/v1 원장이 남긴 현물 종가로 계산, 첫날 10,000)."""
    drills: tuple[DrillView, ...]
    watch: tuple[WatchView, ...]
    """최근 점검 보고 (새것 먼저)."""
    research: tuple[ResearchEntry, ...]
    backtests: tuple[BacktestRow, ...]
    errors: tuple[str, ...] = field(default_factory=tuple)
    cash_yield: str = ""
    """이자 가정 설명 (없으면 빈 문자열)."""
    demo: DemoView = field(default_factory=DemoView)
    """데모·그림자·실거래 준비 (docs/research/51·55~59)."""


def _day(decision_ms: int) -> int:
    return decision_ms + 1 - _DAY_MS


def _t2_view(rows: Sequence[PaperDay]) -> LedgerView:
    points = tuple(Point(_day(r.decision_ms), float(r.equity)) for r in rows)
    last = rows[-1]
    equity = float(last.equity)
    holdings: list[Holding] = []
    for symbol in _SYMBOLS:
        qty = float(last.holdings.get(symbol.removesuffix("USDT"), "0"))
        if qty:
            notional = qty * float(last.prices[symbol])
            holdings.append(
                Holding("T/v2", symbol, "현물", qty, notional, notional / equity, "추세 청산")
            )
    return LedgerView(
        key="t2",
        name="T/v2",
        subtitle="현물 추세",
        points=points,
        status=last.status,
        note=last.note or "",
        peak=max(p.equity for p in points),
        exposure=sum(h.weight for h in holdings),
        holdings=tuple(holdings),
        trades_today=len(last.executed),
        idle=tuple(float(r.holdings.get("USDT", "0")) / float(r.equity) for r in rows),
    )


def _overlay_idle(row: OverlayPaperDay, reserve: float) -> float:
    """선물 지갑 현금 − 선물 명목 × reserve (증거금으로 남길 몫) = 예치할 수 있는 현금."""
    futures = sum(
        float(q) * float(row.market["perp"][s][1]) for s, q in row.state["fut_qty"].items()
    )
    return max(float(row.state["wallet"]) - reserve * futures, 0.0) / row.equity


def _overlay_view(
    key: str, name: str, subtitle: str, rows: Sequence[OverlayPaperDay], reserve: float
) -> LedgerView:
    points = tuple(Point(_day(r.decision_ms), r.equity) for r in rows)
    last = rows[-1]
    protection = (
        "추세 청산 · 비상 스톱 −20%" if key in {"o2", "o3"} else "추세 청산 (가격 손절 없음)"
    )
    holdings: list[Holding] = []
    for symbol in _SYMBOLS:
        for venue, qty_key, price_key in (
            ("현물", "spot_qty", "spot"),
            ("선물", "fut_qty", "perp"),
        ):
            qty = float(last.state[qty_key].get(symbol, 0.0))
            if qty:
                price = float(last.market[price_key][symbol][1])
                notional = qty * price
                note = "추세 청산" if venue == "현물" else protection
                holdings.append(
                    Holding(name, symbol, venue, qty, notional, notional / last.equity, note)
                )
    return LedgerView(
        key=key,
        name=name,
        subtitle=subtitle,
        points=points,
        status=last.status,
        note=last.note or "",
        peak=float(last.state["peak"]),
        exposure=float(last.tally.get("exposure", 0.0)),
        holdings=tuple(holdings),
        trades_today=int(last.tally.get("trades", 0)),
        stop_hits=sum(int(r.tally.get("stop_hits", 0)) for r in rows),
        liquidations=sum(int(r.tally.get("liquidations", 0)) for r in rows),
        halted=last.state.get("halted_day") is not None,
        idle=tuple(_overlay_idle(r, reserve) for r in rows),
    )


def _benchmark(rows: Sequence[OverlayPaperDay]) -> tuple[Point, ...]:
    """BTC·ETH 현물 50/50 을 첫날 종가에 사서 들고 있었을 때 (비용 없음)."""
    if not rows:
        return ()
    first = {s: float(rows[0].market["spot"][s][1]) for s in _SYMBOLS}
    return tuple(
        Point(
            _day(r.decision_ms),
            10_000.0 * sum(0.5 * float(r.market["spot"][s][1]) / first[s] for s in _SYMBOLS),
        )
        for r in rows
    )


def _drills(folder: Path, errors: list[str]) -> tuple[DrillView, ...]:
    out: list[DrillView] = []
    for path in sorted(folder.glob("*.json")) if folder.is_dir() else []:
        kind = path.stem.split("-", 1)[0]
        if kind not in {"drill", "stop", "stream"}:
            continue
        try:
            d = json.loads(path.read_text(encoding="utf-8"))
            at = datetime.strptime(str(d["at"]), "%Y%m%dT%H%M%SZ").replace(tzinfo=UTC)
            checks = tuple(
                (str(c.get("step", "")), bool(c.get("pass")), str(c.get("detail", "")))
                for c in d.get("checks", [])
            )
            outcome = str(d.get("outcome") or ("PASS" if d.get("ok") else "FAIL"))
            out.append(
                DrillView(
                    kind,
                    int(at.timestamp() * 1000),
                    bool(d.get("ok")),
                    outcome,
                    checks,
                    dict(d.get("observations") or {}),
                )
            )
        except (OSError, ValueError, KeyError, TypeError) as exc:
            errors.append(f"훈련 기록 {path.name} 읽기 실패: {type(exc).__name__}")
    return tuple(sorted(out, key=lambda v: v.at_ms))


def _watch(folder: Path, errors: list[str], limit: int = 30) -> tuple[WatchView, ...]:
    out: list[WatchView] = []
    paths = sorted(folder.glob("????-??-??.json"), reverse=True)[:limit] if folder.is_dir() else []
    for path in paths:
        try:
            d = json.loads(path.read_text(encoding="utf-8"))
            text_path = path.with_suffix(".txt")
            lines = (
                tuple(text_path.read_text(encoding="utf-8").splitlines())
                if text_path.is_file()
                else ()
            )
            out.append(
                WatchView(
                    str(d["date"]),
                    d.get("exit_code"),
                    tuple(
                        (str(f["level"]), str(f["ledger"]), str(f["message"]))
                        for f in d.get("findings", [])
                    ),
                    lines,
                )
            )
        except (OSError, ValueError, KeyError, TypeError) as exc:
            errors.append(f"점검 기록 {path.name} 읽기 실패: {type(exc).__name__}")
    return tuple(out)


_HEADING = re.compile(r"^## (.+?) — ([A-Z_]+)(.*)$")
_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")


def _summary(block: Sequence[str]) -> str:
    """판정을 담은 줄: 판정 → REJECT/PASS 가 적힌 줄 → Failure → TRAIN. 마크다운 기호는 뺀다."""
    picks = (
        lambda s: s.startswith("판정"),
        lambda s: "REJECT" in s or "PASS" in s,
        lambda s: s.startswith("Failure"),
        lambda s: s.startswith("TRAIN"),
    )
    for pick in picks:
        for line in block:
            if pick(line) and not line.startswith("Hypothesis"):
                return line.replace("**", "").replace("`", "")
    return ""


def _research(path: Path, errors: list[str]) -> tuple[ResearchEntry, ...]:
    """RESEARCH_HISTORY 의 ``## 이름 — 판정 (날짜)`` 항목과 그 아래 첫 판정 줄."""
    if not path.is_file():
        return ()
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        errors.append(f"연구 기록 읽기 실패: {type(exc).__name__}")
        return ()
    out: list[ResearchEntry] = []
    for i, line in enumerate(lines):
        m = _HEADING.match(line)
        if not m:
            continue
        block: list[str] = []
        for follow in lines[i + 1 : i + 12]:
            if follow.startswith("## "):
                break
            block.append(follow)
        date = _DATE.search(m.group(3))
        summary = _summary(block)
        out.append(ResearchEntry(m.group(1), m.group(2), date.group(0) if date else "", summary))
    return tuple(out)


def _backtests(reports: Path, errors: list[str]) -> tuple[BacktestRow, ...]:
    """이미 1회 판정한 결과 파일만 읽는다 (2020-03 ~ 2026-08, 이미 본 구간)."""
    rows: list[BacktestRow] = []

    def add(name: str, block: dict[str, Any] | None, final: float | None = None) -> None:
        if not block:
            return
        full = block.get("full", block)
        rows.append(
            BacktestRow(
                name,
                float(full["cagr"]),
                float(full["mdd"]),
                float(full["annual_sharpe"]),
                final if final is not None else block.get("final_equity"),
            )
        )

    try:
        o = reports / "o" / "run" / "verdict.json"
        if o.is_file():
            d = json.loads(o.read_text(encoding="utf-8"))
            add("T/v2 단독", d.get("a_t2_alone"))
            add("O/v1 (1.5배)", d.get("b_overlay"))
            add("BTC·ETH 50/50 보유", (d.get("report") or {}).get("hold_fixed_qty_50_50"))
        o2 = reports / "o2" / "run" / "verdict.json"
        if o2.is_file():
            add("O/v2 (1.5배 + 비상 스톱)", json.loads(o2.read_text(encoding="utf-8")).get("b2_o2"))
    except (OSError, ValueError, KeyError, TypeError) as exc:
        errors.append(f"백테스트 결과 읽기 실패: {type(exc).__name__}")
    return tuple(rows)


def _schedule(path: Path, errors: list[str]) -> tuple[RateSchedule | None, float, str]:
    """``config/cash_yield.yaml`` → (이자율 일정, 선물 증거금 남길 비율, 설명)."""
    if not path.is_file():
        return None, 0.5, ""
    try:
        doc = yaml.safe_load(path.read_text(encoding="utf-8"))
        entries = []
        for item in doc["rates"]:
            start = datetime.fromisoformat(str(item["from"])).replace(tzinfo=UTC)
            tiers = tuple(
                Tier(None if t["up_to"] is None else float(t["up_to"]), float(t["apr"]))
                for t in item["tiers"]
            )
            entries.append((int(start.timestamp() * 1000), tiers))
        schedule = RateSchedule(tuple(entries))
    except (OSError, ValueError, KeyError, TypeError, yaml.YAMLError) as exc:
        errors.append(f"이자 가정 읽기 실패: {type(exc).__name__}")
        return None, 0.5, ""
    latest = doc["rates"][-1]
    label = " · ".join(
        f"{t['apr'] * 100:.2f}%" + (f"(≤{t['up_to']:,} USDT)" if t["up_to"] is not None else "")
        for t in latest["tiers"]
    )
    return schedule, float(doc.get("margin_reserve", 0.5)), f"{label} · {latest['from']} 확인"


def _apply_yield(view: LedgerView, schedule: RateSchedule) -> LedgerView:
    path = with_cash_yield(
        [p.day_ms for p in view.points], [p.equity for p in view.points], view.idle, schedule
    )
    return replace(
        view,
        with_yield=tuple(Point(p.day_ms, e) for p, e in zip(view.points, path, strict=True)),
    )


def load_dashboard(root: Path, now_ms: int) -> DashboardData:
    """저장소 루트 아래의 DB·보고서를 읽는다. 쓰기 없음."""
    errors: list[str] = []
    ledgers: list[LedgerView] = []
    benchmark: tuple[Point, ...] = ()
    demo = DemoView()
    schedule, reserve, yield_note = _schedule(root / "config" / "cash_yield.yaml", errors)
    db = root / "data" / "trading.db"
    try:
        conn = connect_read_only(db)
    except AutoTradingError as exc:
        errors.append(f"DB 를 열 수 없다: {exc}")
        demo = load_demo(root, None)
    else:
        try:
            demo = load_demo(root, conn)
            t2 = list(reversed(PaperDayRepository(conn).recent("t2_long", 1_000_000)))
            if t2:
                ledgers.append(_t2_view(t2))
            overlay = OverlayPaperDayRepository(conn)
            o1 = overlay.all("o1_overlay")
            if o1:
                ledgers.append(_overlay_view("o1", "O/v1", "추가 노출", o1, reserve))
                benchmark = _benchmark(o1)
            o2 = overlay.all("o2_stop")
            if o2:
                ledgers.append(_overlay_view("o2", "O/v2", "추가 노출 + 비상 스톱", o2, reserve))
            o3 = overlay.all("o3_2x")
            if o3:
                ledgers.append(
                    _overlay_view("o3", "O/v3", "2배 + 비상 스톱 · 한 달 관찰", o3, reserve)
                )
        except (sqlite3.Error, KeyError, ValueError, TypeError) as exc:
            errors.append(f"원장 읽기 실패: {type(exc).__name__}: {exc}")
        finally:
            conn.close()
    if schedule is not None:
        ledgers = [_apply_yield(lg, schedule) for lg in ledgers]
    reports = root / "reports"
    return DashboardData(
        now_ms=now_ms,
        ledgers=tuple(ledgers),
        benchmark=benchmark,
        drills=_drills(reports / "testnet", errors),
        watch=_watch(reports / "watchdog", errors),
        research=_research(root / "docs" / "current" / "RESEARCH_HISTORY.md", errors),
        backtests=_backtests(reports, errors),
        errors=(*errors, *demo.errors),
        cash_yield=yield_note,
        demo=demo,
    )
