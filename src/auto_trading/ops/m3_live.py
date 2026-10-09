"""M3 실거래 (문서 182 M3 변동성 맞춤, 토스) — 순수 계산. 주문은 ``scripts/toss_accumulate.py``.

* 매달 첫 실행일(미국 거래일 밤)에 한 번: 지난달 말까지 QQQ 20 거래일 변동성 σ → TQQQ 몫
  = 60% × min(1, 0.20 / σ), 나머지 SCHD.
* 재원(전략 현금) = 매달 넣는 돈(원화 → 그날 환율로 달러) + 이월 현금 + 받은 배당(85%, 원천징수 뺌)
  + 판 돈. 1 주 단위 장 마감 지정가로만 사고판다(소수점 = 시장가뿐이라 쓰지 않는다).
* 수량은 목표 금액과의 차이(제곱합)가 가장 작은 정수 조합. 사는 돈은 지정가 × (1 + 수수료) 로
  잡아 지금 전략 현금을 넘지 않게 한다. 파는 것은 목표보다 1 주 값 넘게 많을 때만(세금 · 회전 줄임).
* 체결은 주문 상세(체결 수량 · 금액 · 수수료)로만 장부에 넣는다.
  사용자가 따로 가진 같은 종목과 섞지 않는다.
"""

from __future__ import annotations

import itertools
import math
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, field
from typing import Any

from auto_trading.ops.mf_paper import LEV_MAX, VOL_REF, lev_share_vol, realized_vol

LEV, DIV = "TQQQ", "SCHD"
SYMBOLS = (LEV, DIV)
LIMIT_BUFFER = 0.03
WITHHOLDING = 0.15
DIV_WAIT_DAYS = 10
"""배당락 뒤 이 날수가 지나야 받은 것으로 친다(SCHD 는 락 뒤 며칠 안에 지급)."""


@dataclass(slots=True)
class M3Order:
    date: str
    symbol: str
    side: str
    qty: int
    limit: float
    client_id: str
    order_id: str = ""
    status: str = "UNKNOWN"
    """UNKNOWN(보내기 전 저장) · SENT · FILLED · EXPIRED · REJECTED."""
    code: str = ""
    filled_qty: float = 0.0
    filled_usd: float = 0.0
    commission: float = 0.0


@dataclass(slots=True)
class M3State:
    start: str
    cash_usd: float = 0.0
    qty: dict[str, float] = field(default_factory=lambda: dict.fromkeys(SYMBOLS, 0.0))
    done_month: str = ""
    deposits: list[list[Any]] = field(default_factory=list)
    """[날짜, 원화, 달러, 환율]."""
    dividends: list[list[Any]] = field(default_factory=list)
    """[배당락일, 종목, 주수, 주당, 받은 달러(세후)]."""
    months: list[list[Any]] = field(default_factory=list)
    """[달, 변동성, TQQQ 몫]."""
    orders: list[M3Order] = field(default_factory=list)
    amounts: dict[str, float] = field(default_factory=dict)
    """달 → 사용자가 텔레그램으로 정한 납입(원). 없으면 기본 금액."""
    ask_month: str = ""
    ask_ts: int = 0
    tg_offset: int = 0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: Mapping[str, Any]) -> M3State:
        return cls(
            start=d["start"],
            cash_usd=float(d.get("cash_usd", 0.0)),
            qty={k: float(v) for k, v in (d.get("qty") or {}).items()},
            done_month=d.get("done_month", ""),
            deposits=list(d.get("deposits") or []),
            dividends=list(d.get("dividends") or []),
            months=list(d.get("months") or []),
            orders=[M3Order(**o) for o in d.get("orders") or []],
            amounts={k: float(v) for k, v in (d.get("amounts") or {}).items()},
            ask_month=d.get("ask_month", ""),
            ask_ts=int(d.get("ask_ts", 0)),
            tg_offset=int(d.get("tg_offset", 0)),
        )


def due(state: M3State, today: str) -> bool:
    return state.done_month != today[:7]


def deposited(state: M3State, month: str) -> bool:
    return any(str(d[0])[:7] == month for d in state.deposits)


def amount_target(state: M3State, today: str) -> str | None:
    """텔레그램 금액 답이 들어갈 달: 이번 달 매수 전이면 이번 달, 아니면 물어본 다음 달."""
    if due(state, today) and not deposited(state, today[:7]):
        return today[:7]
    if state.ask_month > today[:7] and not deposited(state, state.ask_month):
        return state.ask_month
    return None


def next_weekday(today: str) -> str:
    from datetime import date, timedelta

    d = date.fromisoformat(today) + timedelta(days=1)
    while d.weekday() >= 5:
        d += timedelta(days=1)
    return d.isoformat()


def share_reason(vol: float | None, share: float) -> str:
    """TQQQ 몫을 왜 그렇게 정했는지 한 줄 (182 M3: 60% × min(1, 0.20 / σ20))."""
    if vol is None or vol <= 0:
        return f"QQQ 20일 변동성 자료 부족 → 기본 TQQQ {share:.0%}"
    if share >= LEV_MAX - 1e-9:
        return f"QQQ 20일 변동성 {vol:.1%} ≤ 기준 {VOL_REF:.0%} → TQQQ 최대 {share:.0%}"
    return (
        f"QQQ 20일 변동성 {vol:.1%} > 기준 {VOL_REF:.0%}"
        f" → TQQQ {LEV_MAX:.0%} × {VOL_REF:.0%}/{vol:.1%} = {share:.0%} 로 줄임"
    )


def plan_message(
    *,
    today: str,
    krw: float,
    source: str,
    fx_rate: float,
    vol: float | None,
    share: float,
    plan: Mapping[str, int],
    prices: Mapping[str, float],
    fee: float,
    usd_now: float,
    need_all: float,
    sections: Sequence[Sequence[str]] = (),
) -> str:
    """텔레그램 답장: 오늘 밤 M3 매수 계획 + 덧붙일 구역(보유 · 배당) (순수).

    ``need_all`` = 오늘 밤 모든 매수(다른 적립 포함)에 필요한 달러(지정가 기준)."""
    from auto_trading.notify.style import RULE, sub

    lines = [f"📈 M3 · 오늘({today}) 밤 매수 예정", RULE]
    if krw > 0:
        lines.append(f"이번 달 납입 {krw:,.0f}원 ({source}) ≈ ${krw / fx_rate:,.2f}")
    else:
        lines.append("이번 달 납입 0원 — 이월 현금 · 배당만으로")
    lines.append(f"비율 TQQQ {share:.0%} : SCHD {1 - share:.0%}")
    lines.append(sub(f"이유: {share_reason(vol, share)}"))
    need_m3 = 0.0
    for sym in SYMBOLS:
        n = plan.get(sym, 0)
        if n > 0:
            approx = n * prices[sym] * (1 + fee)
            need_m3 += n * limit_price(prices[sym], "BUY") * (1 + fee)
            lines.append(f"🛒 {sym} {n}주 · 약 ${approx:,.2f}")
        elif n < 0:
            lines.append(f"↩ {sym} {-n}주 매도 · 약 ${-n * prices[sym]:,.2f}")
        else:
            lines.append(f"· {sym} 이번 달 안 삼")
    if not any(n > 0 for n in plan.values()):
        lines.append(sub("돈이 1 주 값보다 적어 다음 달로 이월"))
    lines += ["", f"💵 현재 보유 달러 ${usd_now:,.2f} → 필요 달러 ${need_m3:,.2f}"]
    if need_all > need_m3 + 0.005:
        lines.append(sub(f"오늘 밤 다른 적립 매수까지 합치면 ${need_all:,.2f}"))
    short = need_all - usd_now
    if short > 0:
        lines.append(
            f"👉 약 ${math.ceil(short):,} (약 {math.ceil(short) * fx_rate:,.0f}원) 환전해 주세요"
        )
        lines.append(sub("15:30 까지 95% 우대 · 못 하면 밤에 거절 → 23:40 · 03:30 다시"))
    else:
        lines.append(sub("달러 충분 — 환전 필요 없음"))
    for sec in sections:
        lines += ["", RULE, *sec]
    lines += ["", "✏️ 금액을 바꾸려면 오늘 21:00 전에 다시 보내 주세요"]
    return "\n".join(lines)


def ask_message(today: str, default_krw: float) -> str:
    """매수 당일 아침 금액 묻기 (순수)."""
    from auto_trading.notify.style import RULE, sub

    return "\n".join(
        [
            f"📈 M3 · 오늘({today}) 밤 매수 예정",
            RULE,
            f"{today[:4]}년 {int(today[5:7])}월, 얼마를 매수하시겠습니까?",
            sub("예시 답: 30만원  (0 = 이번 달 쉬기)"),
            sub(f"답이 없으면 {default_krw:,.0f}원으로 삽니다"),
            sub("답하면 오늘 밤 매수 계획 · 보유 · 배당을 바로 보내 드려요"),
        ]
    )


def cost_basis(state: M3State) -> dict[str, float]:
    """종목 → M3 몫의 남은 원가(달러, 수수료 포함). 판 것은 평균 단가로 뺀다 (순수)."""
    qty: dict[str, float] = {}
    cost: dict[str, float] = {}
    for o in sorted(state.orders, key=lambda x: x.date):
        if o.filled_qty <= 0:
            continue
        q0, c0 = qty.get(o.symbol, 0.0), cost.get(o.symbol, 0.0)
        if o.side == "BUY":
            qty[o.symbol], cost[o.symbol] = q0 + o.filled_qty, c0 + o.filled_usd + o.commission
        elif q0 > 0:
            keep = max(q0 - o.filled_qty, 0.0)
            qty[o.symbol], cost[o.symbol] = keep, c0 * keep / q0
    return cost


def holdings_lines(state: M3State, prices: Mapping[str, float]) -> list[str]:
    """현재 보유(M3 몫): 종목마다 주수 · 평가액 · 평균 단가 대비 % (순수)."""
    from auto_trading.notify.style import arrow, sub

    cost = cost_basis(state)
    lines = ["📦 현재 보유 (M3 몫)"]
    value_all = cost_all = 0.0
    for sym in SYMBOLS:
        q = state.qty.get(sym, 0.0)
        if q <= 0:
            lines.append(f"· {sym} 0주")
            continue
        value = q * prices[sym]
        c = cost.get(sym, 0.0)
        pct = value / c - 1 if c > 0 else None
        value_all, cost_all = value_all + value, cost_all + c
        tail = f" ({pct:+.1%})" if pct is not None else ""
        lines.append(f"{arrow(pct)} {sym} {q:g}주 · ${value:,.2f}{tail}")
    if cost_all > 0:
        lines.append(
            sub(
                f"합계 ${value_all:,.2f} ({value_all / cost_all - 1:+.1%})"
                f" · 이월 현금 ${state.cash_usd:,.2f}"
            )
        )
    else:
        lines.append(sub(f"아직 산 것 없음 · 이월 현금 ${state.cash_usd:,.2f}"))
    return lines


def dividend_lines(
    state: M3State,
    events: Sequence[tuple[str, str, float]],
    credited: Sequence[Sequence[Any]],
    after_qty: Mapping[str, float],
) -> list[str]:
    """배당: 받은 누적 · 이번 달 반영분 · 다음 예상 (순수).

    ``credited`` = 이번 계산에서 새로 넣은 배당 줄, ``after_qty`` = 오늘 밤 매수 뒤 주수."""
    from datetime import date, timedelta

    from auto_trading.notify.style import sub

    got = sum(float(d[4]) for d in state.dividends)
    lines = ["💰 배당 (세후 85%)", f"· 받은 배당 누적 ${got:,.2f}"]
    for d in credited:
        lines.append(
            f"· 이번 반영: {d[1]} {float(d[2]):g}주 × ${float(d[3]):.4f} = ${float(d[4]):,.2f}"
        )
        lines.append(sub("오늘 매수 재원에 포함"))
    for sym in SYMBOLS:
        past = sorted((ex, dps) for ex, s, dps in events if s == sym)
        n = after_qty.get(sym, 0.0)
        if not past or n <= 0:
            continue
        ex, dps = past[-1]
        nxt = date.fromisoformat(ex) + timedelta(days=91)
        lines.append(
            f"· 다음 예상 {sym}: {nxt:%Y-%m} 배당락 무렵 · 약 ${n * dps * (1 - WITHHOLDING):,.2f}"
        )
        lines.append(sub(f"{n:g}주 × 직전 주당 ${dps:.4f} ({ex}) — 분기 배당 가정"))
    if len(lines) == 2 and not credited:
        lines.append(sub("아직 받은 배당 없음"))
    return lines


def lev_share(closes: Sequence[tuple[str, float]], today: str) -> tuple[float | None, float]:
    """지난달 말까지(``today`` 달 전) 종가로 (σ20, TQQQ 몫)."""
    before = [px for d, px in sorted(closes) if d[:7] < today[:7]]
    vol = realized_vol(before)
    return vol, lev_share_vol(vol)


def deposit(state: M3State, today: str, krw: float, fx_rate: float) -> float:
    """이번 달 납입을 한 번만 넣는다. 넣은 달러(이미 넣었으면 0)."""
    if any(str(d[0])[:7] == today[:7] for d in state.deposits) or krw <= 0 or fx_rate <= 0:
        return 0.0
    usd = krw / fx_rate
    state.deposits.append([today, krw, round(usd, 4), fx_rate])
    state.cash_usd += usd
    return usd


def limit_price(last: float, side: str) -> float:
    if side == "BUY":
        return math.floor(last * (1 + LIMIT_BUFFER) * 100) / 100
    return math.ceil(last * (1 - LIMIT_BUFFER) * 100) / 100


def choose(
    qty: Mapping[str, float],
    cash: float,
    last: Mapping[str, float],
    share: float,
    fee: float,
) -> dict[str, int]:
    """종목 → 사고팔 주수(+ 사기, − 팔기). 목표와의 차이 제곱합이 가장 작은 정수 조합 (순수)."""
    total = cash + sum(qty.get(s, 0.0) * last[s] for s in SYMBOLS)
    target = {LEV: share * total, DIV: (1 - share) * total}
    ranges = []
    for s in SYMBOLS:
        hold = qty.get(s, 0.0)
        over = hold * last[s] - target[s]
        lo = -math.floor(over / last[s]) if over > last[s] else 0
        lo = max(lo, -math.floor(hold))
        hi = math.floor(cash / (limit_price(last[s], "BUY") * (1 + fee))) if cash > 0 else 0
        ranges.append(range(lo, hi + 1))
    best: tuple[float, dict[str, int]] | None = None
    for combo in itertools.product(*ranges):
        d = dict(zip(SYMBOLS, combo, strict=True))
        spend = sum(n * limit_price(last[s], "BUY") * (1 + fee) for s, n in d.items() if n > 0)
        if spend > cash + 1e-9:
            continue
        err = sum((target[s] - (qty.get(s, 0.0) + d[s]) * last[s]) ** 2 for s in SYMBOLS)
        key = (round(err, 6), sum(abs(n) for n in combo))
        if best is None or key < (round(best[0], 6), sum(abs(n) for n in best[1].values())):
            best = (err, d)
    return {s: n for s, n in (best[1] if best else {}).items() if n != 0}


def apply_fill(state: M3State, order: M3Order) -> None:
    """체결분을 장부에 넣는다 (매수: 현금 −(금액 + 수수료), 매도: 현금 +(금액 − 수수료))."""
    if order.filled_qty <= 0:
        return
    sign = 1 if order.side == "BUY" else -1
    state.qty[order.symbol] = state.qty.get(order.symbol, 0.0) + sign * order.filled_qty
    state.cash_usd += -sign * order.filled_usd - order.commission


def held_on(state: M3State, symbol: str, ex_date: str) -> float:
    """배당락일 전날까지 체결된 전략 보유 주수."""
    n = 0.0
    for o in state.orders:
        if o.symbol == symbol and o.filled_qty > 0 and o.date < ex_date:
            n += o.filled_qty if o.side == "BUY" else -o.filled_qty
    return max(n, 0.0)


def credit_dividends(
    state: M3State, events: Sequence[tuple[str, str, float]], today: str, days_since: Any
) -> list[str]:
    """(배당락일, 종목, 주당 배당) 중 아직 안 넣은 것을 세후로 넣는다.

    ``days_since(a, b)`` = b − a 일수."""
    notes = []
    seen = {(str(d[0]), str(d[1])) for d in state.dividends}
    for ex, sym, dps in sorted(events):
        if (ex, sym) in seen or ex < state.start or days_since(ex, today) < DIV_WAIT_DAYS:
            continue
        shares = held_on(state, sym, ex)
        if shares <= 0:
            continue
        usd = shares * dps * (1 - WITHHOLDING)
        state.dividends.append([ex, sym, shares, dps, round(usd, 4)])
        state.cash_usd += usd
        notes.append(f"{sym} 배당 {shares:g}주 × ${dps:.4f} × 85% = ${usd:,.2f} 재투자 재원에 넣음")
    return notes
