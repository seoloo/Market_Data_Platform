"""🌟 Deneb 그림자 기록 (문서 280) — 실거래 Deneb 규칙을 정지와 상관없이 매일 따라 한다. 주문 없음.

실거래 ``scripts/toss_accumulate.py`` 의 Deneb 판단과 **같은 순수 함수**를 쓴다:
월 비율(``deneb_live.lev_share`` · ``choose``) · T 브레이크
(``deneb_guard.brake_sell`` · ``rebuy_due``) · 배당 재투자(``credit_dividends``).
주문 대신 실거래와 같은 장 마감 지정가(LOC)를 그날 종가로 체결한 것으로 본다
— 지정가(전날 종가 ±3%) 밖이면 미체결.

빠진 것(실거래에서 사람이 버튼으로 하는 것): 배당 단계 TQQQ 상한 낮추기 · 12 월 공제 실현.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Mapping, Sequence

from auto_trading.ops.deneb_guard import (
    STAGE_SHARE,
    Brake,
    brake_sell,
    drawdown,
    rebought,
    rebuy_due,
    sold,
)
from auto_trading.ops.deneb_live import (
    DIV,
    LEV,
    SYMBOLS,
    DenebOrder,
    DenebState,
    apply_fill,
    choose,
    credit_dividends,
    deposit,
    due,
    lev_share,
    limit_price,
)
from auto_trading.ops.mf_paper import LEV_MAX

Closes = Mapping[str, Sequence[tuple[str, float]]]
"""종목 → (미국 날짜, 수정 종가) 오래된 것 먼저."""


def _close_on(closes: Closes, sym: str, day: str) -> float | None:
    for d, px in closes[sym]:
        if d == day:
            return px
    return None


def _prev_close(closes: Closes, sym: str, day: str) -> tuple[str, float] | None:
    before = [(d, px) for d, px in closes[sym] if d < day]
    return before[-1] if before else None


def _fill(
    state: DenebState, day: str, sym: str, side: str, qty: int, limit: float, close: float,
    fee: float, kind: str,
) -> DenebOrder:  # fmt: skip
    """장 마감 지정가: 사기는 종가 ≤ 지정가, 팔기는 종가 ≥ 지정가면 종가에 전량 체결."""
    ok = close <= limit if side == "BUY" else close >= limit
    o = DenebOrder(day, sym, side, qty, limit, f"SH-{day}-{sym}-{side[0]}-{kind or 'm'}")
    o.kind = kind
    if ok:
        o.status, o.filled_qty = "FILLED", float(qty)
        o.filled_usd, o.commission = qty * close, qty * close * fee
    else:
        o.status = "EXPIRED"
    state.orders.append(o)
    return o


def value_usd(state: DenebState, brake: Brake, closes: Closes, day: str) -> float:
    held = 0.0
    for s in SYMBOLS:
        px = _close_on(closes, s, day) or (_prev_close(closes, s, day) or ("", 0.0))[1]
        held += state.qty.get(s, 0.0) * px
    return state.cash_usd + brake.reserve_usd + held


def step(
    state: DenebState,
    brake: Brake,
    day: str,
    closes: Closes,
    *,
    monthly_krw: float,
    fx: float,
    fee: float,
    events: Sequence[tuple[str, str, float]],
    trend_on: Callable[[Sequence[float]], bool | None],
    days_since: Callable[[str, str], int],
) -> list[str]:
    """미국 거래일 ``day`` 하루 (그날 종가가 있어야 함). 실거래 21:00 판단 → 그날 LOC 체결 순서."""
    lines: list[str] = []
    lev_close, prev = _close_on(closes, LEV, day), _prev_close(closes, LEV, day)
    if lev_close is None or prev is None:
        return [f"{day} TQQQ 종가 없음 — 건너뜀"]
    # 1) T 브레이크 (TQQQ 를 산 뒤부터)
    first = min((o.date for o in state.orders if o.symbol == LEV and o.side == "BUY"
                 and o.filled_qty > 0), default=None)  # fmt: skip
    if first is not None:
        for d, px in closes[LEV]:
            if (
                d < day
                and d >= max(first, brake.last_day)
                and (d > brake.last_day or brake.lev_peak == 0)
            ):
                brake.lev_peak = max(brake.lev_peak, px)
                brake.last_day = d
        dd = drawdown(brake, prev[1])
        stage_before = brake.stage
        n = brake_sell(brake, dd, state.qty.get(LEV, 0.0))
        if n > 0:
            o = _fill(state, day, LEV, "SELL", n, limit_price(prev[1], "SELL"), lev_close, fee,
                      "brake")  # fmt: skip
            if o.status == "FILLED":
                state.qty[LEV] = state.qty.get(LEV, 0.0) - n
                sold(brake, day, dd or 0.0, n, o.filled_usd - o.commission)
                lines.append(f"🛑 브레이크 −{(dd or 0):.1%} → TQQQ {n}주 매도 ${o.filled_usd:,.2f}")
            else:
                brake.stage = stage_before
                lines.append("브레이크 매도 지정가 밖 — 미체결")
        elif brake.stage > 0 and brake.sales:
            qqq = [px for d, px in closes["QQQ"] if d < day]
            if rebuy_due(brake, trend_on(qqq)):
                limit = limit_price(prev[1], "BUY")
                q = math.floor(brake.reserve_usd / (limit * (1 + fee)))
                if q >= 1:
                    o = _fill(state, day, LEV, "BUY", q, limit, lev_close, fee, "rebuy")
                    if o.status == "FILLED":
                        state.qty[LEV] = state.qty.get(LEV, 0.0) + q
                        state.cash_usd += brake.reserve_usd - o.filled_usd - o.commission
                        rebought(brake, day, 0.0, lev_close)
                        lines.append(f"↩ 추세 회복 → TQQQ {q}주 되삼 ${o.filled_usd:,.2f}")
                else:
                    state.cash_usd += brake.reserve_usd
                    rebought(brake, day, 0.0, lev_close)
                    lines.append("보관금이 1 주 값보다 적어 현금으로 합침")
    # 2) 매달 첫 거래일 비율 맞추기 (실거래 _deneb_plan · _deneb_run 과 같은 순서)
    if due(state, day):
        vol, share = lev_share(list(closes["QQQ"]), day)
        share *= min(state.lev_cap, LEV_MAX) / LEV_MAX
        share *= STAGE_SHARE[int(brake.stage)]
        lines += credit_dividends(state, events, day, days_since)
        usd = deposit(state, day, monthly_krw, fx)
        if usd:
            lines.append(f"납입 {monthly_krw:,.0f}원 = ${usd:,.2f} (환율 {fx:,.1f})")
        last = {s: _prev_close(closes, s, day) for s in SYMBOLS}
        if all(v is not None for v in last.values()):
            last_px = {s: v[1] for s, v in last.items() if v is not None}
            plan = choose(dict(state.qty), state.cash_usd, last_px, share, fee)
            for sym, n in sorted(plan.items(), key=lambda kv: kv[1]):  # 파는 것 먼저
                side = "BUY" if n > 0 else "SELL"
                close = _close_on(closes, sym, day)
                if close is None:
                    continue
                o = _fill(
                    state, day, sym, side, abs(n), limit_price(last_px[sym], side), close, fee, ""
                )
                apply_fill(state, o)
                word = "매수" if side == "BUY" else "매도"
                if o.status == "FILLED":
                    lines.append(f"{sym} {abs(n)}주 {word} ${o.filled_usd:,.2f}")
                else:
                    lines.append(f"{sym} {word} 지정가 밖 — 미체결")
        state.done_month = day[:7]
        if not any(str(m[0]) == day[:7] for m in state.months):
            state.months.append([day[:7], vol, share])
        vol_txt = f"{vol:.1%}" if vol is not None else "모름"
        lines.insert(0, f"{day[:7]} TQQQ {share:.0%} / SCHD {1 - share:.0%} (σ20 {vol_txt})")
    return lines


def holdings(state: DenebState) -> dict[str, float]:
    return {s: state.qty.get(s, 0.0) for s in (LEV, DIV)}
