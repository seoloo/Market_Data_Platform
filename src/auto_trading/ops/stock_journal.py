"""💹 개별주 기록 · 월간 복기 (I/O 없음) — 사용자 2026-10-16 "1 -> 3 -> 2" 의 2번.

'그래도 매수(규칙 밖)' 를 열었으니 **규칙대로 산 것과 규칙 밖에서 산 것의 성적**을 나눠 봐야
규칙을 믿을지 정할 수 있다. 추천 기록(``journal.jsonl``)과 💹 장부(예약 · 체결 · 손절)를
달마다 묶는다.

* 추천: 몇 번 받았고 그중 몇 번이 규칙 통과였나.
* 매수: 규칙대로 · 규칙 밖 · 앱 등록(손절만)으로 나눔.
* 끝난 매수: 손절 · 정리로 넘김 등 이유별, 평균 손익 · 이긴 비율 — 규칙대로 / 규칙 밖 따로.
* 들고 있는 것: 평가 손익(시세가 있으면).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

__all__ = ["Closed", "closed_trades", "month_of", "review_text"]

KST = timedelta(hours=9)


def month_of(ms: int) -> str:
    return (datetime.fromtimestamp(ms / 1000, UTC) + KST).strftime("%Y-%m")


@dataclass(frozen=True, slots=True)
class Closed:
    symbol: str
    name: str
    override: bool
    ret: float
    reason: str
    closed_ms: int


def closed_trades(tickets: Sequence[Any]) -> list[Closed]:
    """팔린(손절 · 매도 체결) 💹 매수 → 손익. 체결 없이 끝난 예약 · 감시 해제는 뺀다."""
    out = []
    for t in tickets:
        sold = sum(o.filled for o in t.sells)
        if sold <= 0 or t.avg <= 0:
            continue
        got = sum(o.amount - o.fee for o in t.sells)
        out.append(
            Closed(
                t.symbol,
                t.name,
                bool(getattr(t, "override", False)),
                got / (t.avg * sold) - 1,
                "손절" if t.status == "closed" else t.status,
                int(t.closed_ms or t.created_ms),
            )
        )
    return out


def _group(rows: Sequence[Closed]) -> str:
    if not rows:
        return "없음"
    wins = sum(1 for r in rows if r.ret > 0)
    avg = sum(r.ret for r in rows) / len(rows)
    return f"{len(rows)}건 · 평균 {avg:+.1%} · 이긴 비율 {wins / len(rows):.0%}"


def review_text(
    month: str,
    recs: Sequence[Mapping[str, Any]],
    tickets: Sequence[Any],
    last: Mapping[str, float],
) -> str:
    """그 달(YYYY-MM)의 복기 한 통 (순수). ``last`` = 종목 → 지금 가격(들고 있는 것 평가용)."""
    m_recs = [r for r in recs if month_of(int(r["at"])) == month]
    ok = sum(1 for r in m_recs if r.get("ok"))
    bought = [
        t
        for t in tickets
        if month_of(int(t.created_ms)) == month and sum(o.filled for o in t.buys) > 0
    ]
    app = [t for t in tickets if month_of(int(t.created_ms)) == month and t.source == "app"]
    rule = [t for t in bought if not getattr(t, "override", False)]
    out_rule = [t for t in bought if getattr(t, "override", False)]
    closed = [c for c in closed_trades(tickets) if month_of(c.closed_ms) == month]
    lines = [
        f"💹 개별주 복기 · {month}",
        "",
        f"추천 {len(m_recs)}번 (규칙 통과 {ok} · 비추천 {len(m_recs) - ok})",
        f"매수 {len(bought)}건 — 규칙대로 {len(rule)} · 규칙 밖(그래도 매수) {len(out_rule)}"
        + (f" · 앱에서 산 것 손절만 {len(app)}" if app else ""),
        "",
        "끝난 매수 (이달 팔림)",
        f"• 규칙대로: {_group([c for c in closed if not c.override])}",
        f"• 규칙 밖: {_group([c for c in closed if c.override])}",
    ]
    for c in sorted(closed, key=lambda c: c.closed_ms):
        tag = " (규칙 밖)" if c.override else ""
        lines.append(f"  └ {c.name} {c.ret:+.1%} · {c.reason}{tag}")
    holding = [t for t in tickets if t.status in ("holding", "selling") and t.avg > 0]
    if holding:
        lines += ["", "들고 있는 것"]
        for t in holding:
            px = last.get(t.symbol)
            pnl = f"{px / t.avg - 1:+.1%}" if px else "시세 없음"
            tag = " (규칙 밖)" if getattr(t, "override", False) else ""
            lines.append(
                f"  └ {t.name} {t.filled - sum(o.filled for o in t.sells):g}주 · {pnl}{tag}"
            )
    lines += [
        "",
        "규칙대로와 규칙 밖의 차이가 몇 달 쌓이면 '그래도 매수' 를 계속 쓸지 판단할 수 있어요.",
        "(연구 238: 진입가 −3% 손절만으로는 오른 뒤 나갈 장치가 없어요 —"
        " 직접 매도 시점도 같이 보세요)",
    ]
    return "\n".join(lines)
