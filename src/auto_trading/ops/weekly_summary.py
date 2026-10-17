"""토요일 주간 요약 — Vega(현물 추세) · Altair(선물) (I/O 없음).

사용자 (2026-10-17): 앱의 평균 원가(예전 2025 거래가 섞인 −19%)로는 Vega 성적을 보기 힘들다 →
"vega 주간 요약만" · "altair 도 각각 얼마에 얼마만큼 들어가서 수익을 보여줘" · 노출 줄은 빼기.

* 평단은 **그 전략이 실제로 산 체결**만으로 계산한다(Vega 장부 ``orders``). 장부 밖에서 줄어든 몫
  (예: 2026-10-16 시험 사고 매도)은 먼저 산 것부터 빠진 것으로 본다.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

__all__ = ["Holding", "Leg", "avg_costs", "change_since", "summary_text"]


@dataclass(frozen=True, slots=True)
class Holding:
    asset: str
    qty: float
    avg: float
    price: float

    @property
    def pnl(self) -> float:
        return (self.price - self.avg) * self.qty

    @property
    def pct(self) -> float:
        return self.price / self.avg - 1 if self.avg > 0 else 0.0


@dataclass(frozen=True, slots=True)
class Leg:
    """Altair 포지션 한 줄 (수량 + 롱, − 숏)."""

    asset: str
    qty: float
    entry: float
    mark: float

    @property
    def pnl(self) -> float:
        return (self.mark - self.entry) * self.qty

    @property
    def pct(self) -> float:
        side = 1 if self.qty > 0 else -1
        return side * (self.mark / self.entry - 1) if self.entry > 0 else 0.0


def avg_costs(
    orders: Sequence[Mapping[str, object]], held: Mapping[str, float]
) -> dict[str, tuple[float, float]]:
    """자산 → (장부 수량, 평단 USDT). 매수는 (수량, 체결 금액 + 수수료), 매도는 평단으로 뺀다.
    장부 체결로 쌓인 수량이 지금 보유(``held``)보다 많으면 먼저 산 것부터 줄인다."""
    lots: dict[str, list[list[float]]] = {}
    for o in orders:
        if o.get("status") != "FILLED":
            continue
        parts = str(o.get("id", "")).split("-")
        if len(parts) < 5:
            continue
        asset, side = parts[4], str(o.get("side", ""))
        qty = float(str(o.get("executed_qty") or 0))
        cost = float(str(o.get("quote_qty") or 0)) + float(str(o.get("fee_usdt") or 0))
        if qty <= 0:
            continue
        book = lots.setdefault(asset, [])
        if side == "BUY":
            book.append([qty, cost])
        elif side == "SELL":
            total_q = sum(q for q, _ in book)
            if total_q <= 0:
                continue
            keep = max(0.0, 1 - qty / total_q)
            lots[asset] = [[q * keep, c * keep] for q, c in book]
    out: dict[str, tuple[float, float]] = {}
    for asset, book in lots.items():
        have = held.get(asset, 0.0)
        extra = sum(q for q, _ in book) - have
        trimmed: list[list[float]] = []
        for q, c in book:
            if extra > 1e-12:
                cut = min(q, extra)
                extra -= cut
                if q - cut <= 1e-12:
                    continue
                c *= (q - cut) / q
                q -= cut
            trimmed.append([q, c])
        q_sum = sum(q for q, _ in trimmed)
        if q_sum > 1e-12:
            out[asset] = (q_sum, sum(c for _, c in trimmed) / q_sum)
    return out


def change_since(points: Sequence[tuple[str, float]], start: str, now: float) -> tuple[float, str]:
    """단위 가치(nav) 기준 (그 사이 변화율, 기준 날짜). ``start`` 이전 마지막 값, 없으면 첫 값.
    입금 · 출금은 단위 수만 바꾸므로 변화율에 섞이지 않는다."""
    before = [p for p in points if p[0] <= start]
    base = before[-1] if before else (points[0] if points else (start, now))
    return (now / base[1] - 1 if base[1] else 0.0), base[0]


def _money(v: float) -> str:
    return f"{'+' if v >= 0 else '−'}{abs(v):,.2f}"


def _pct(v: float, digits: int = 1) -> str:
    return f"{'+' if v >= 0 else '−'}{abs(v) * 100:.{digits}f}%"


def _px(v: float) -> str:
    return f"${v:,.2f}" if v < 1_000 else f"${v:,.0f}" if v >= 10_000 else f"${v:,.2f}"


def summary_text(
    *,
    period: str,
    vega: Mapping[str, object] | None,
    altair: Mapping[str, object] | None,
    todo: Sequence[str],
    polaris: Mapping[str, object] | None = None,
) -> str:
    """두 전략 칸 + 합계 + 할 일. 칸 자료가 없으면(꺼짐 · 조회 실패) 그 칸에 한 줄로 알린다."""
    lines = [f"🗓 [주간 요약 · {period}]", "━━━━━━━━━━━━"]
    total = deposit = week = 0.0
    if vega is not None:
        eq, dep = float(str(vega["equity"])), float(str(vega["deposits"]))
        wk = float(str(vega["week"]))
        total, deposit, week = total + eq, deposit + dep, week + wk
        lines += [
            "🌌 Vega  (바이낸스 현물 · 코인 추세)",
            f"💼 평가 {eq:,.2f} USDT · 넣은 돈 {dep:,.2f}",
            f"   └ 이번 주 {_money(wk)} ({_pct(float(str(vega['week_pct'])))})"
            f" · 시작부터 {_pct(float(str(vega['since_pct'])))}",
            f"   └ 최고점 대비 −{float(str(vega['dd'])) * 100:.1f}%  (멈춤선 −30%)",
            "📦 보유 (평단은 수수료 포함)",
        ]
        holds: Sequence[Holding] = vega["holdings"]  # type: ignore[assignment]
        for h in holds:
            lines.append(
                f"   └ {h.asset} {h.qty:.8g} · 평단 {_px(h.avg)} → {_px(h.price)}"
                f" · {_money(h.pnl).replace('+', '+$').replace('−', '−$')} ({_pct(h.pct)})"
            )
        if not holds:
            lines.append("   └ 코인 없음 (추세 꺼짐 — 전부 현금)")
        lines.append(
            f"   └ 현금 {float(str(vega['cash'])):,.2f} USDT"
            f" (💤 유동형 {float(str(vega['earn'])):,.2f})"
        )
        trend: Mapping[str, bool] = vega["trend"]  # type: ignore[assignment]
        marks = " · ".join(f"{a} {'✅' if on else '❌'}" for a, on in trend.items())
        lines.append(f"🔄 이번 주: 매수 {vega['buys']} · 매도 {vega['sells']}  (추세: {marks})")
    else:
        lines.append("🌌 Vega — 장부를 못 읽음 (꺼짐 · 조회 실패)")
    lines.append("")
    if altair is not None:
        eq, start = float(str(altair["equity"])), float(str(altair["start"]))
        wk = float(str(altair["week"]))
        total, deposit, week = total + eq, deposit + start, week + wk
        lines += [
            "☄️ Altair  (바이낸스 선물 · 롱 4배 + 숏)",
            f"💼 장부 {eq:,.2f} USDC · 시작 {start:,.2f}",
            f"   └ 이번 주 {_money(wk)} ({_pct(float(str(altair['week_pct'])))})"
            f" · 시작부터 {_pct(float(str(altair['since_pct'])))}",
            f"   └ 최고점 대비 −{float(str(altair['dd'])) * 100:.1f}%  (멈춤선 −30%)",
            "📦 포지션",
        ]
        legs: Sequence[Leg] = altair["legs"]  # type: ignore[assignment]
        for g in legs:
            side = "롱" if g.qty > 0 else "숏"
            lines.append(
                f"   └ {g.asset} {side} {abs(g.qty):g} · 진입 {_px(g.entry)} → {_px(g.mark)}"
                f" · {_money(g.pnl).replace('+', '+$').replace('−', '−$')} ({_pct(g.pct)})"
            )
        if not any(g.qty < 0 for g in legs):
            lines.append("   └ 숏 없음")
        stops: Mapping[str, float] = altair["stops"]  # type: ignore[assignment]
        if stops:
            lines.append(
                "🛡 거래소 비상 스톱: " + " · ".join(f"{a} {_px(v)}" for a, v in stops.items())
            )
        lines.append(
            f"🔄 이번 주: 롱 맞추기 {altair['long_trades']}번 · 숏 {altair['short_trades']}번"
        )
    else:
        lines.append("☄️ Altair — 꺼져 있거나 조회 실패")
    if polaris is not None:  # 나무 — 원화, 바이낸스 합계와 섞지 않는다 (사용자 2026-10-17 '별개로')
        value, paid = float(str(polaris["value_krw"])), float(str(polaris["cost_krw"]))
        gain = value - paid
        lines += [
            "",
            "🧭 Polaris  (나무 · 안정 적립 · 원화)",
            f"💼 평가 {value:,.0f}원 · 산 돈 {paid:,.0f}원"
            f" · {'+' if gain >= 0 else '−'}{abs(gain):,.0f}원"
            f" ({_pct(value / paid - 1 if paid else 0)})",
            "📦 보유 (평단은 수수료 포함, 직접 산 종목은 빼고)",
        ]
        plines: Sequence[str] = polaris["lines"]  # type: ignore[assignment]
        lines += list(plines) or ["   └ 아직 없음"]
        lines.append(f"🔄 회차 {polaris['done']}/{polaris['rounds']}")
    lines += ["", "━━━━━━━━━━━━"]
    if deposit:  # 사용자 2026-10-17: 잃은 · 번 금액도 함께
        lines += [
            f"📊 바이낸스 합계 {total:,.2f} · 넣은 돈 {deposit:,.2f}",
            f"   └ 손익 {_money(total - deposit)} ({_pct(total / deposit - 1)})"
            f" · 이번 주 {_money(week)}",
        ]
    lines.append("✅ 할 일 없음" if not todo else "\n".join(f"⚠️ {x}" for x in todo))
    return "\n".join(lines)
