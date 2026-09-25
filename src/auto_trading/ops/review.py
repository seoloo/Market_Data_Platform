"""10/23·10/24 점검 채점 (docs/research/65) — 순수 함수, I/O 없음.

기준은 문서 65 에 결과를 보기 전에 고정했다. 여기서는 그 기준을 기록에 대어 본다.
결과 상태: ``PASS`` · ``FAIL`` · ``CHECK``(사람이 원인을 확인해야 함) · ``NA``(자료 없음·해당 없음).
한 달 수익으로 전략을 고르지 않는다 — 수익은 보고만 한다.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

__all__ = [
    "Criterion",
    "LedgerReview",
    "LedgerRow",
    "demo_t2_review",
    "ledger_review",
    "normalize_demo_orders",
    "shadow_review",
    "watch_review",
]

DAY_MS = 86_400_000
MIN_DAYS = 28
MDD_STOP = 0.25
SLIPPAGE_BPS = 5.0


@dataclass(frozen=True, slots=True)
class Criterion:
    id: str
    status: str
    """PASS · FAIL · CHECK · NA."""
    detail: str


@dataclass(frozen=True, slots=True)
class LedgerRow:
    decision_ms: int
    status: str
    equity: float
    liquidations: int = 0
    halted: bool = False
    stop_hits: int = 0


@dataclass(frozen=True, slots=True)
class LedgerReview:
    name: str
    first: str
    last: str
    span_days: int
    recorded: int
    missing: tuple[str, ...]
    total_return: float
    mdd: float
    stop_hits: int
    criteria: tuple[Criterion, ...]
    equity: tuple[tuple[str, float], ...] = field(default_factory=tuple)


def _date(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, UTC).strftime("%Y-%m-%d")


def _mdd(values: Sequence[float]) -> float:
    peak, worst = 0.0, 0.0
    for v in values:
        peak = max(peak, v)
        if peak > 0:
            worst = max(worst, 1 - v / peak)
    return worst


def ledger_review(
    name: str,
    rows: Sequence[LedgerRow],
    *,
    until_ms: int,
    overlay: bool,
    verify_problems: int | None,
) -> LedgerReview | None:
    """P1~P5 (문서 65 §3). ``verify_problems`` 는 O 원장의 재계산 불일치 수(T/v2 는 None)."""
    rows = sorted((r for r in rows if r.decision_ms <= until_ms), key=lambda r: r.decision_ms)
    if not rows:
        return None
    first = rows[0].decision_ms
    days = {r.decision_ms // DAY_MS for r in rows}
    span = until_ms // DAY_MS - first // DAY_MS + 1
    missing = tuple(
        _date(d * DAY_MS) for d in range(first // DAY_MS, until_ms // DAY_MS + 1) if d not in days
    )
    equity = [r.equity for r in rows]
    mdd = _mdd(equity)
    liq = sum(r.liquidations for r in rows)
    halted = any(r.halted for r in rows)
    crit = [
        Criterion(
            "P1",
            "PASS" if span >= MIN_DAYS else "FAIL",
            f"첫 결정일 {_date(first)} 부터 {span}일 (기준 {MIN_DAYS}일)",
        ),
        Criterion(
            "P2",
            ("PASS" if liq == 0 and not halted else "FAIL") if overlay else "NA",
            f"청산 {liq} · 계좌 정지 {'있음' if halted else '없음'}" if overlay else "현물만",
        ),
        Criterion(
            "P3",
            "PASS" if mdd < MDD_STOP else "FAIL",
            f"종가 MDD {mdd:.2%} (중단선 {MDD_STOP:.0%})",
        ),
        Criterion(
            "P4",
            "NA" if verify_problems is None else ("PASS" if verify_problems == 0 else "FAIL"),
            "T/v2 는 그림자 S5(Paper 목표 일치)로 본다"
            if verify_problems is None
            else f"재계산 불일치 {verify_problems}건",
        ),
        Criterion(
            "P5",
            "PASS" if not missing else "CHECK",
            "누락 없음"
            if not missing
            else f"누락 {len(missing)}일: {', '.join(missing)} — 원인 기록 필요",
        ),
    ]
    return LedgerReview(
        name=name,
        first=_date(first),
        last=_date(rows[-1].decision_ms),
        span_days=span,
        recorded=len(rows),
        missing=missing,
        total_return=equity[-1] / equity[0] - 1 if equity[0] else 0.0,
        mdd=mdd,
        stop_hits=sum(r.stop_hits for r in rows),
        criteria=tuple(crit),
        equity=tuple((_date(r.decision_ms), r.equity) for r in rows),
    )


def watch_review(
    reports: Mapping[str, Mapping[str, Any]], *, first_ms: int, until_ms: int
) -> tuple[Criterion, dict[str, Any]]:
    """P6: 일일 점검 기록이 매일 있고 CRITICAL 이 모두 설명되는가. ``reports`` 는 {날짜: 보고서}."""
    expected = [_date(d * DAY_MS) for d in range(first_ms // DAY_MS, until_ms // DAY_MS + 1)]
    missing = [d for d in expected if d not in reports]
    critical: list[str] = []
    levels: dict[str, int] = {}
    for day in sorted(reports):
        if day not in expected:
            continue
        for f in reports[day].get("findings", []):
            level = str(f.get("level"))
            levels[level] = levels.get(level, 0) + 1
            if level == "CRITICAL":
                critical.append(f"{day} {f.get('ledger')}: {f.get('message')}")
    status = "PASS" if not missing and not critical else "CHECK"
    detail = f"기록 {len(expected) - len(missing)}/{len(expected)}일 · CRITICAL {len(critical)}건"
    return Criterion("P6", status, detail), {
        "missing": missing,
        "critical": critical,
        "levels": levels,
    }


def shadow_review(
    reports: Mapping[str, Mapping[str, Any]], *, until_ms: int
) -> tuple[list[Criterion], dict[str, Any]]:
    """S1~S5 (문서 51 §3). ``reports`` 는 {결정일: 그림자 보고서}."""
    days = sorted(d for d in reports if d <= _date(until_ms))
    if not days:
        na = [Criterion(k, "NA", "그림자 운영 기록 없음") for k in ("S1", "S2", "S3", "S4", "S5")]
        return na, {}
    first = datetime.strptime(days[0], "%Y-%m-%d").replace(tzinfo=UTC)
    first_ms = int(first.timestamp() * 1000)
    expected = [_date(first_ms + k * DAY_MS) for k in range((until_ms - first_ms) // DAY_MS + 1)]
    missing = [d for d in expected if d not in reports]
    critical: list[str] = []
    gaps: list[str] = []
    parity: list[str] = []
    today_bps: list[float] = []
    probe_bps: list[float] = []
    for day in days:
        rep = reports[day]
        if int(rep.get("orders_sent_to_exchange") or 0) != 0:
            critical.append(f"{day} 거래소 주문 기록")
        critical += [
            f"{day} {f.get('message')}"
            for f in rep.get("findings", [])
            if f.get("level") == "CRITICAL"
        ]
        for g in rep.get("tracking_gaps", []):
            (parity if "Paper" in g else gaps).append(f"{day} {g}")
        if str(rep.get("paper_parity", "")).startswith("Paper 기록 없음"):
            parity.append(f"{day} {rep['paper_parity']}")
        for book in (rep.get("slippage") or {}).values():
            for label, side in book.items():
                if not isinstance(side, Mapping):
                    continue
                vals = [
                    float(v)
                    for v in (side.get("buy_bps"), side.get("sell_bps"))
                    if v not in (None, "None")
                ]
                if label == "today_order":
                    today_bps += vals
                elif label == "probe_10000":
                    probe_bps += vals
    s4_vals, s4_src = (
        (today_bps, "그날 주문 크기") if today_bps else (probe_bps, "주문 없음 → 10,000 USDT 탐침")
    )
    worst = max(s4_vals) if s4_vals else None
    crit = [
        Criterion("S1", "PASS" if not missing else "CHECK", f"누락 {len(missing)}일 {missing}"),
        Criterion("S2", "PASS" if not critical else "FAIL", f"CRITICAL {len(critical)}건"),
        Criterion(
            "S3", "PASS" if not gaps else "CHECK", f"목표 차이 경고 {len(gaps)}건 — 원인 설명 필요"
        ),
        Criterion(
            "S4",
            "NA" if worst is None else ("PASS" if worst <= SLIPPAGE_BPS else "FAIL"),
            "호가 자료 없음"
            if worst is None
            else f"최악 {worst:.2f}bp ({s4_src}, 기준 {SLIPPAGE_BPS:g}bp)",
        ),
        Criterion(
            "S5", "PASS" if not parity else "FAIL", f"Paper 목표 불일치·누락 {len(parity)}일"
        ),
    ]
    return crit, {"missing": missing, "critical": critical, "gaps": gaps, "parity": parity}


def normalize_demo_orders(
    reports: Sequence[Mapping[str, Any]], legacy_rows: Sequence[Mapping[str, Any]]
) -> list[dict[str, float | bool | str]]:
    """데모 T/v2 주문을 한 모양으로: 명목·지정가 명목·가격 bp·수수료 bp·대기(분)·거절·추격."""
    out: list[dict[str, float | bool | str]] = []

    def num(x: Any) -> float | None:
        return None if x in (None, "None", "") else float(x)

    for rep in reports:
        for o in rep.get("orders", []):
            avg = num(o.get("avg_price"))
            maker, taker = num(o.get("maker_qty")) or 0.0, num(o.get("taker_qty")) or 0.0
            if avg is None or maker + taker <= 0:
                continue
            wait = num(o.get("wait_ms"))
            out.append(
                {
                    "symbol": str(o.get("symbol")),
                    "notional": (maker + taker) * avg,
                    "maker_notional": maker * avg,
                    "price_bps": num(o.get("cost_price_bps")) or 0.0,
                    "fee_bps": num(o.get("cost_fee_bps")) or 0.0,
                    "wait_min": -1.0 if wait is None else wait / 60_000,
                    "post_rejects": float(o.get("post_rejects") or 0),
                    "chased": str(o.get("chased")) == "True",
                }
            )
    for row in legacy_rows:
        for e in row.get("executed", []):
            avg = num(e.get("avg_price"))
            maker, taker = num(e.get("maker_qty")) or 0.0, num(e.get("taker_qty")) or 0.0
            if avg is None or maker + taker <= 0:
                continue
            out.append(
                {
                    "symbol": str(e.get("symbol")),
                    "notional": (maker + taker) * avg,
                    "maker_notional": maker * avg,
                    "price_bps": num(e.get("is_price_bps")) or 0.0,
                    "fee_bps": num(e.get("is_fee_bps")) or 0.0,
                    "wait_min": float(e.get("wait_min") or -1.0),
                    "post_rejects": float(e.get("post_rejects") or 0),
                    "chased": bool(e.get("chased")),
                }
            )
    return out


def demo_t2_review(
    orders: Sequence[Mapping[str, Any]], *, unresolved: int, degraded_days: Sequence[str]
) -> tuple[list[Criterion], dict[str, Any]]:
    """K1 필수 · K2~K4 측정 (문서 55 §3)."""
    k1_bad = unresolved > 0 or bool(degraded_days)
    notional = sum(float(o["notional"]) for o in orders)
    stats: dict[str, Any] = {"orders": len(orders), "notional": notional}
    crit = [
        Criterion(
            "K1",
            "PASS" if not k1_bad else "CHECK",
            f"해소 안 된 주문 {unresolved} · DEGRADED/HALTED {len(degraded_days)}일"
            f" {list(degraded_days)}",
        )
    ]
    if not orders:
        crit += [Criterion(k, "NA", "체결된 데모 주문 없음") for k in ("K2", "K3", "K4")]
        return crit, stats
    maker = sum(float(o["maker_notional"]) for o in orders) / notional
    waits = [float(o["wait_min"]) for o in orders if float(o["wait_min"]) >= 0]
    price = sum(float(o["price_bps"]) * float(o["notional"]) for o in orders) / notional
    fee = sum(float(o["fee_bps"]) * float(o["notional"]) for o in orders) / notional
    rejects = int(sum(float(o["post_rejects"]) for o in orders))
    chases = sum(1 for o in orders if o["chased"])
    stats |= {"maker_share": maker, "price_bps": price, "fee_bps": fee}
    crit += [
        Criterion(
            "K2",
            "PASS",
            f"지정가 체결 명목 {maker:.1%} · 평균 대기 "
            + (f"{sum(waits) / len(waits):.1f}분" if waits else "모름")
            + " (모형 99.2%, 1~2분)",
        ),
        Criterion(
            "K3",
            "PASS",
            f"평균 IS {price + fee:.2f}bp = 가격 {price:+.2f} + 수수료 {fee:.2f}"
            " (모형 10.23 · 가정 15 · 참고 11)",
        ),
        Criterion(
            "K4",
            "PASS",
            f"post-only 거절 {rejects}회 · 추격 {chases}건",
        ),
    ]
    return crit, stats
