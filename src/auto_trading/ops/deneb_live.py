"""🌟 Deneb 실거래 계산 — TQQQ + SCHD (+ 금) 매달 적립 (문서 182 M3 변동성 맞춤, 토스). 계산만 한다.

언제 쓰나: 평일 21:00 ``AutoTrading_Toss_Accumulate`` (``scripts/toss_accumulate.py``) 가 부른다.
읽고 쓰는 것: 없음 — 장부(``data/toss/DENEB.json``) 모양(:class:`DenebState`)과 계산만.
              주문 · 파일은 ``scripts/toss_accumulate.py``.

* **매달 계획** — 그달 첫 실행일(미국 거래일 밤)에 한 번. 지난달 말까지 QQQ 20 거래일 변동성 σ 로
  TQQQ 몫 = 60% × min(1, 0.20 / σ), 나머지 SCHD. 쉬운 말: 요즘 많이 흔들리면 TQQQ 를 덜 산다.
* 쓸 돈(전략 현금) = 매달 넣는 돈(원화 → 그날 환율로 달러) + 이월 현금 + 받은 배당(85%, 원천징수 뺌)
  + 판 돈. 1 주 단위 장 마감 지정가로만 사고판다.
* 수량은 목표 금액과의 차이(제곱합)가 가장 작은 정수 조합. 사는 돈은 지정가 × (1 + 수수료) 로
  잡아 지금 전략 현금을 넘지 않게 한다. 파는 것은 목표보다 1 주 값 넘게 많을 때만(세금 · 회전 줄임).
* **다음 날 채우기** (사용자 2026-10-18 "b로 변경", 참고 계산 ``reports/deneb_fraction_20261018``):
  그달 계획 다음 거래일부터 매일, 남은 전략 현금(지난 계획에서 남은 돈 · 판 돈 ·
  미체결로 돌아온 돈)으로 목표보다 가장 모자란 종목을 1 주씩 산다(:func:`plan_next_day_fill`,
  사기만). 놀고 있는 현금 평균 3 ~ 4% → 1% 아래, 모의 20 년 +18%(같은 낙폭).
* 체결은 주문 상세(체결 수량 · 금액 · 수수료)로만 장부에 넣는다.
  사용자가 따로 가진 같은 종목과 섞지 않는다.
* **금 섞기** (사용자 2026-10-19 "A로 진행해줘", 문서 285 TG `우위`):
  설정 ``deneb.gold_on`` 이 켜지면 TQQQ 를 뺀 나머지를 SCHD : IAUM = 1 : 1
  (``gold`` = 금 몫 0.5). 브레이크 · 되사기는 TQQQ 만 그대로.
  금 몫 0 이고 IAUM 을 안 들고 있으면 계산은 예전(TQQQ · SCHD 두 종목)과 같다.

용어: '몫' = 전체 평가액 중 그 종목 비율,
      '전략 현금' = Deneb 장부의 달러 현금(계좌 전체 달러가 아님).
"""

from __future__ import annotations

import itertools
import math
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, field
from typing import Any

from auto_trading.ops.mf_paper import LEV_MAX, VOL_REF, lev_share_vol, realized_vol

LEVERAGED_ETF = "TQQQ"
"""나스닥 100 3 배 레버리지 ETF."""
DIVIDEND_ETF = "SCHD"
"""미국 배당 ETF."""
GOLD_ETF = "IAUM"
"""금 ETF — 설정 ``deneb.gold_on`` 이 켜졌을 때만 산다."""
SYMBOLS = (LEVERAGED_ETF, DIVIDEND_ETF, GOLD_ETF)
"""Deneb 이 다루는 종목 전부 (가격 · 정리 · 위험 뉴스 · 크기 확인)."""
GOLD_MIX = 0.5
"""금 섞기 켜짐: TQQQ 를 뺀 나머지 중 금 몫 (문서 285 TG)."""
LIMIT_BUFFER = 0.03
"""장 마감 지정가를 마지막 가격에서 이만큼(3%) 띄운다 — 종가가 조금 움직여도 체결되게."""
WITHHOLDING = 0.15
"""미국 배당 원천징수 15% — 받은 배당은 85% 로 친다."""
DIV_WAIT_DAYS = 10
"""배당락 뒤 이 날수가 지나야 받은 것으로 친다(SCHD 는 락 뒤 며칠 안에 지급)."""


@dataclass(slots=True)
class DenebOrder:
    """Deneb 이 낸 주문 하나 (장부 ``orders`` 한 줄 — 키 이름은 파일에 저장돼 바꾸지 않는다)."""

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
    kind: str = ""
    """'' (매달) · fill (다음 날 채우기) · brake (브레이크 매도) · rebuy (보관금으로 되사기) ·
    harvest_sell · harvest_buy (12 월 공제 실현) · step_sell · step_buy (배당 단계)."""
    booked: bool = False
    """브레이크 · 재매수 체결을 보관금에 반영했는지."""


@dataclass(slots=True)
class DenebState:
    """Deneb 장부 전체 (``data/toss/DENEB.json``) — 키 이름은 파일에 저장돼 바꾸지 않는다."""

    start: str
    cash_usd: float = 0.0
    """전략 현금(달러)."""
    qty: dict[str, float] = field(
        default_factory=lambda: dict.fromkeys((LEVERAGED_ETF, DIVIDEND_ETF), 0.0)
    )
    done_month: str = ""
    """매달 계획을 마친 달 (YYYY-MM)."""
    deposits: list[list[Any]] = field(default_factory=list)
    """[날짜, 원화, 달러, 환율]."""
    dividends: list[list[Any]] = field(default_factory=list)
    """[배당락일, 종목, 주수, 주당, 받은 달러(세후)]."""
    months: list[list[Any]] = field(default_factory=list)
    """[달, 변동성, TQQQ 몫]."""
    orders: list[DenebOrder] = field(default_factory=list)
    amounts: dict[str, float] = field(default_factory=dict)
    """달 → 사용자가 텔레그램으로 정한 납입(원). 없으면 기본 금액."""
    ask_month: str = ""
    """금액을 물어본 달."""
    ask_ts: int = 0
    tg_offset: int = 0
    """텔레그램에서 어디까지 읽었나 (다음 읽을 메시지 번호)."""
    brake: dict[str, Any] = field(default_factory=dict)
    """T 브레이크 상태 (``ops.deneb_guard.Brake.to_dict``)."""
    proposals: list[dict[str, Any]] = field(default_factory=list)
    """텔레그램 제안 (``ops.deneb_guard.Proposal.to_dict``): 12 월 공제 실현 · 배당 단계."""
    div_steps: int = 0
    """지난 배당 단계 수 (월 10 · 30 · 50 · 100 만 원)."""
    lev_cap: float = LEV_MAX
    """TQQQ 목표 상한 — 배당 단계에서 낮아진다 (처음 60%)."""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> DenebState:
        """[쓰임] 장부 JSON → :class:`DenebState`. 옛 장부에 없는 칸은 기본값."""
        return cls(
            start=data["start"],
            cash_usd=float(data.get("cash_usd", 0.0)),
            qty={symbol: float(qty) for symbol, qty in (data.get("qty") or {}).items()},
            done_month=data.get("done_month", ""),
            deposits=list(data.get("deposits") or []),
            dividends=list(data.get("dividends") or []),
            months=list(data.get("months") or []),
            orders=[DenebOrder(**order) for order in data.get("orders") or []],
            amounts={month: float(krw) for month, krw in (data.get("amounts") or {}).items()},
            ask_month=data.get("ask_month", ""),
            ask_ts=int(data.get("ask_ts", 0)),
            tg_offset=int(data.get("tg_offset", 0)),
            brake=dict(data.get("brake") or {}),
            proposals=list(data.get("proposals") or []),
            div_steps=int(data.get("div_steps", 0)),
            lev_cap=float(data.get("lev_cap", LEV_MAX)),
        )


def due(state: DenebState, today: str) -> bool:
    """[쓰임] 이번 달 매달 계획을 아직 안 했나."""
    return state.done_month != today[:7]


def deposited(state: DenebState, month: str) -> bool:
    """[쓰임] 그달(YYYY-MM) 납입을 이미 장부에 넣었나."""
    return any(str(deposit_row[0])[:7] == month for deposit_row in state.deposits)


def amount_target(state: DenebState, today: str) -> str | None:
    """[쓰임] 텔레그램 금액 답이 들어갈 달 — 이번 달 매수 전이면 이번 달, 아니면 물어본 다음 달.
    [이유] 이미 넣은 달의 금액을 답으로 바꾸면 장부와 어긋나서, 그런 달은 None (답을 받지 않음)."""
    if due(state, today) and not deposited(state, today[:7]):
        return today[:7]
    if state.ask_month > today[:7] and not deposited(state, state.ask_month):
        return state.ask_month
    return None


def next_weekday(today: str) -> str:
    """[쓰임] 다음 평일 (YYYY-MM-DD, 토 · 일 건너뜀 — 미국 휴장일은 보지 않는다)."""
    from datetime import date, timedelta

    day = date.fromisoformat(today) + timedelta(days=1)
    while day.weekday() >= 5:
        day += timedelta(days=1)
    return day.isoformat()


def active_symbols(qty: Mapping[str, float], gold: float) -> tuple[str, ...]:
    """[쓰임] 이번 계산에 쓸 종목 — 금 몫이 있거나 IAUM 을 들고 있으면 셋, 아니면 TQQQ · SCHD.
    [이유] 금을 안 쓰면 예전 두 종목 계산과 결과가 똑같아야 한다
           (2026-10-19 금 섞기 추가 때 약속)."""
    if gold > 0 or qty.get(GOLD_ETF, 0.0) > 0:
        return SYMBOLS
    return (LEVERAGED_ETF, DIVIDEND_ETF)


def targets(total: float, share: float, gold: float = 0.0) -> dict[str, float]:
    """[쓰임] 평가액 ``total`` → 종목별 목표 금액. 금 몫 0 이면 IAUM 목표 0 (들고 있으면 판다)."""
    rest = (1 - share) * total
    return {
        LEVERAGED_ETF: share * total,
        DIVIDEND_ETF: rest * (1 - gold),
        GOLD_ETF: rest * gold,
    }


def ratio_text(share: float, gold: float = 0.0) -> str:
    """[쓰임] 'TQQQ 60% : SCHD 40%' · 금 섞기면 'TQQQ 60% : SCHD 20% : IAUM(금) 20%'."""
    if gold <= 0:
        return f"TQQQ {share:.0%} : SCHD {1 - share:.0%}"
    rest = 1 - share
    return f"TQQQ {share:.0%} : SCHD {rest * (1 - gold):.0%} : IAUM(금) {rest * gold:.0%}"


def share_reason(vol: float | None, share: float) -> str:
    """[쓰임] TQQQ 몫을 왜 그렇게 정했는지 한 줄 (182 M3: 60% × min(1, 0.20 / σ20))."""
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
    gold: float = 0.0,
    binance: str = "",
) -> str:
    """[쓰임] 텔레그램 답장 — 오늘 밤 매수 계획 + 덧붙일 구역(보유 · 배당) (계산만).
    [이유] 환전이 모자라면 밤 주문이 거절되니, 필요한 달러 · 환전할 금액을 같이 알려 준다.

    ``need_all`` = 오늘 밤 모든 매수(다른 적립 포함)에 필요한 달러(지정가 기준).
    ``binance`` = 업비트(Vega) 에 넣을 금액 한 줄 (이름은 옛것 그대로)."""
    from auto_trading.notify.style import RULE, sub

    lines = [f"🌟 Deneb · 오늘({today}) 밤 매수 예정", RULE]
    # 1 단계: 납입 · 비율
    if krw > 0:
        lines.append(f"이번 달 납입 {krw:,.0f}원 ({source}) ≈ ${krw / fx_rate:,.2f}")
        if binance:
            lines.append(sub(binance))
    else:
        lines.append("이번 달 납입 0원 — 이월 현금 · 배당만으로")
    lines.append(f"비율 {ratio_text(share, gold)}")
    lines.append(sub(f"이유: {share_reason(vol, share)}"))
    # 2 단계: 종목별 살 것 · 팔 것
    need_deneb = 0.0
    shown_symbols = SYMBOLS if gold > 0 or plan.get(GOLD_ETF) else (LEVERAGED_ETF, DIVIDEND_ETF)
    for symbol in shown_symbols:
        qty = plan.get(symbol, 0)
        if qty > 0:
            approx = qty * prices[symbol] * (1 + fee)
            need_deneb += qty * limit_price(prices[symbol], "BUY") * (1 + fee)
            lines.append(f"🛒 {symbol} {qty}주 · 약 ${approx:,.2f}")
        elif qty < 0:
            lines.append(f"↩ {symbol} {-qty}주 매도 · 약 ${-qty * prices[symbol]:,.2f}")
        else:
            lines.append(f"· {symbol} 이번 달 안 삼")
    if not any(qty > 0 for qty in plan.values()):
        lines.append(sub("돈이 1 주 값보다 적어 다음 달로 이월"))
    # 3 단계: 달러 · 환전 안내
    lines += ["", f"💵 현재 보유 달러 ${usd_now:,.2f} → 필요 달러 ${need_deneb:,.2f}"]
    if need_all > need_deneb + 0.005:
        lines.append(sub(f"오늘 밤 다른 적립 매수까지 합치면 ${need_all:,.2f}"))
    short_usd = need_all - usd_now
    if short_usd > 0:
        lines.append(
            f"👉 약 ${math.ceil(short_usd):,} (약 {math.ceil(short_usd) * fx_rate:,.0f}원)"
            " 환전해 주세요"
        )
        lines.append(sub("15:30 까지 95% 우대 · 못 하면 밤에 거절 → 23:40 · 03:30 다시"))
    else:
        lines.append(sub("달러 충분 — 환전 필요 없음"))
    # 4 단계: 덧붙일 구역
    for section in sections:
        lines += ["", RULE, *section]
    lines += ["", "✏️ 금액을 바꾸려면 오늘 21:00 전에 다시 보내 주세요"]
    return "\n".join(lines)


def ask_message(today: str, default_krw: float) -> str:
    """[쓰임] 매수 당일 아침 금액 묻기 글 (계산만)."""
    from auto_trading.notify.style import RULE, sub

    return "\n".join(
        [
            f"🌟 Deneb · 오늘({today}) 밤 매수 예정",
            RULE,
            f"{today[:4]}년 {int(today[5:7])}월, 얼마를 매수하시겠습니까?",
            sub("예시 답: 30만원  (0 = 이번 달 쉬기)"),
            sub(f"답이 없으면 {default_krw:,.0f}원으로 삽니다"),
            sub("답하면 오늘 밤 매수 계획 · 보유 · 배당을 바로 보내 드려요"),
        ]
    )


def cost_basis(state: DenebState) -> dict[str, float]:
    """[쓰임] 종목 → Deneb 몫의 남은 원가(달러, 수수료 포함). 판 것은 평균 단가로 뺀다 (계산만).
    [이유] 수익률을 '평균 단가 대비' 로 보여 주려고 — 판 만큼 원가를 같은 비율로 줄인다."""
    qty_by_symbol: dict[str, float] = {}
    cost_by_symbol: dict[str, float] = {}
    for order in sorted(state.orders, key=lambda row: row.date):
        if order.filled_qty <= 0:
            continue
        held = qty_by_symbol.get(order.symbol, 0.0)
        cost = cost_by_symbol.get(order.symbol, 0.0)
        if order.side == "BUY":
            qty_by_symbol[order.symbol] = held + order.filled_qty
            cost_by_symbol[order.symbol] = cost + order.filled_usd + order.commission
        elif held > 0:
            remaining = max(held - order.filled_qty, 0.0)
            qty_by_symbol[order.symbol] = remaining
            cost_by_symbol[order.symbol] = cost * remaining / held
    return cost_by_symbol


def holdings_lines(state: DenebState, prices: Mapping[str, float]) -> list[str]:
    """[쓰임] '📦 현재 보유' 구역 — 종목마다 주수 · 평가액 · 평균 단가 대비 % (계산만)."""
    from auto_trading.notify.style import arrow, sub

    cost_by_symbol = cost_basis(state)
    lines = ["📦 현재 보유 (Deneb 몫)"]
    value_all = 0.0
    cost_all = 0.0
    for symbol in SYMBOLS:
        qty = state.qty.get(symbol, 0.0)
        if qty <= 0 and symbol == GOLD_ETF:
            continue  # 금을 안 들고 있으면 줄을 만들지 않는다
        if qty <= 0:
            lines.append(f"· {symbol} 0주")
            continue
        value = qty * prices[symbol]
        cost = cost_by_symbol.get(symbol, 0.0)
        change = value / cost - 1 if cost > 0 else None
        value_all += value
        cost_all += cost
        tail = f" ({change:+.1%})" if change is not None else ""
        lines.append(f"{arrow(change)} {symbol} {qty:g}주 · ${value:,.2f}{tail}")
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
    state: DenebState,
    events: Sequence[tuple[str, str, float]],
    credited: Sequence[Sequence[Any]],
    after_qty: Mapping[str, float],
) -> list[str]:
    """[쓰임] '💰 배당' 구역 — 받은 누적 · 이번 달 반영분 · 다음 예상 (계산만).

    ``credited`` = 이번 계산에서 새로 넣은 배당 줄, ``after_qty`` = 오늘 밤 매수 뒤 주수.
    다음 예상은 직전 배당락 + 91 일 · 직전 주당 배당으로 어림한다(분기 배당 가정)."""
    from datetime import date, timedelta

    from auto_trading.notify.style import sub

    received = sum(float(row[4]) for row in state.dividends)
    lines = ["💰 배당 (세후 85%)", f"· 받은 배당 누적 ${received:,.2f}"]
    for row in credited:
        lines.append(
            f"· 이번 반영: {row[1]} {float(row[2]):g}주 × ${float(row[3]):.4f}"
            f" = ${float(row[4]):,.2f}"
        )
        lines.append(sub("오늘 매수 재원에 포함"))
    for symbol in SYMBOLS:
        past = sorted((ex_date, per_share) for ex_date, sym, per_share in events if sym == symbol)
        qty = after_qty.get(symbol, 0.0)
        if not past or qty <= 0:
            continue
        ex_date, per_share = past[-1]
        next_date = date.fromisoformat(ex_date) + timedelta(days=91)
        expected = qty * per_share * (1 - WITHHOLDING)
        lines.append(f"· 다음 예상 {symbol}: {next_date:%Y-%m} 배당락 무렵 · 약 ${expected:,.2f}")
        lines.append(sub(f"{qty:g}주 × 직전 주당 ${per_share:.4f} ({ex_date}) — 분기 배당 가정"))
    if len(lines) == 2 and not credited:
        lines.append(sub("아직 받은 배당 없음"))
    return lines


def lev_share(closes: Sequence[tuple[str, float]], today: str) -> tuple[float | None, float]:
    """[쓰임] 지난달 말까지(``today`` 달 전) QQQ 종가로 (20 일 변동성 σ20, TQQQ 몫).
    [이유] 이번 달 종가는 쓰지 않는다 — 한 달 안에서 몫이 바뀌지 않게 (문서 182 규칙)."""
    before = [price for day, price in sorted(closes) if day[:7] < today[:7]]
    vol = realized_vol(before)
    return vol, lev_share_vol(vol)


def deposit(state: DenebState, today: str, krw: float, fx_rate: float) -> float:
    """[쓰임] 이번 달 납입을 장부에 한 번만 넣는다. 넣은 달러를 돌려준다 (이미 넣었으면 0).
    [이유] 재시도(23:40 · 03:30)가 같은 달 납입을 두 번 넣지 않게."""
    if deposited(state, today[:7]) or krw <= 0 or fx_rate <= 0:
        return 0.0
    usd = krw / fx_rate
    state.deposits.append([today, krw, round(usd, 4), fx_rate])
    state.cash_usd += usd
    return usd


def limit_price(last: float, side: str) -> float:
    """[쓰임] 장 마감 지정가 — 사기는 마지막 가격 +3% 내림, 팔기는 −3% 올림 (센트 단위).
    [이유] 장 마감 지정가는 종가로 체결되니, 띄워 둔 만큼은 '이 값까지는 괜찮다' 는
           상한일 뿐이다."""
    if side == "BUY":
        return math.floor(last * (1 + LIMIT_BUFFER) * 100) / 100
    return math.ceil(last * (1 - LIMIT_BUFFER) * 100) / 100


def plan_monthly_rebalance(
    qty: Mapping[str, float],
    cash: float,
    last: Mapping[str, float],
    share: float,
    fee: float,
    gold: float = 0.0,
) -> dict[str, int]:
    """[쓰임] 매달 계획 — 종목 → 사고팔 주수(+ 사기, − 팔기). 목표와의 차이 제곱합이 가장 작은
    정수 조합을 모두 따져 고른다 (계산만).
    [이유] 1 주 단위라 비율을 정확히 못 맞춰서, '목표에 가장 가까운' 조합을 고른다. 같은 차이면
           덜 사고파는 쪽. 파는 것은 목표보다 1 주 값 넘게 많을 때만 (세금 · 회전 줄임).

    사는 수는 목표까지 모자란 주수(올림)를 넘지 않는다 — 그보다 더 사면 차이도 돈도 늘 뿐이라
    가장 좋은 조합은 바뀌지 않고, 세 종목일 때 찾는 범위만 줄어든다."""
    symbols = active_symbols(qty, gold)
    total = cash + sum(qty.get(symbol, 0.0) * last[symbol] for symbol in symbols)
    target = targets(total, share, gold)
    # 1 단계: 종목마다 따져 볼 주수 범위 (팔 수 있는 만큼 ~ 살 수 있는 만큼)
    ranges = []
    for symbol in symbols:
        held = qty.get(symbol, 0.0)
        over_usd = held * last[symbol] - target[symbol]
        lowest = -math.floor(over_usd / last[symbol]) if over_usd > last[symbol] else 0
        lowest = max(lowest, -math.floor(held))
        buy_cost = limit_price(last[symbol], "BUY") * (1 + fee)
        highest = math.floor(cash / buy_cost) if cash > 0 else 0
        highest = min(highest, max(0, math.ceil(-over_usd / last[symbol])))
        ranges.append(range(lowest, max(lowest, highest) + 1))
    # 2 단계: 모든 조합 중 돈이 되는 것 · 목표에 가장 가까운 것
    best: tuple[float, dict[str, int]] | None = None
    for combo in itertools.product(*ranges):
        change = dict(zip(symbols, combo, strict=True))
        spend = 0.0
        for symbol, count in change.items():
            if count > 0:
                spend += count * limit_price(last[symbol], "BUY") * (1 + fee)
        if spend > cash + 1e-9:
            continue
        error = sum(
            (target[symbol] - (qty.get(symbol, 0.0) + change[symbol]) * last[symbol]) ** 2
            for symbol in symbols
        )
        key = (round(error, 6), sum(abs(count) for count in combo))
        if best is None:
            best = (error, change)
            continue
        best_key = (round(best[0], 6), sum(abs(count) for count in best[1].values()))
        if key < best_key:
            best = (error, change)
    if best is None:
        return {}
    return {symbol: count for symbol, count in best[1].items() if count != 0}


def plan_next_day_fill(
    qty: Mapping[str, float],
    cash: float,
    last: Mapping[str, float],
    share: float,
    fee: float,
    max_qty: Mapping[str, int] | None = None,
    gold: float = 0.0,
) -> dict[str, int]:
    """[쓰임] 다음 날 채우기 — 종목 → 살 주수 (사기만, 계산만). 1 주도 못 사면 빈 dict.
    [이유] 매달 계획에서 남은 현금이 한 달 내내 놀지 않게, 매일 1 주씩 가장 모자란 종목부터 산다
           (사용자 2026-10-18 "b로 변경").

    살 돈을 지정가(+3%) × (1 + 수수료) 로 잡아 남은 현금으로 1 주씩, 그때마다 목표(평가액 × 몫)보다
    가장 모자란 종목부터 — 그 종목 1 주 값이 안 되면 살 수 있는 다음 종목(참고 계산과 같은 규칙).
    목표가 0 인 종목은 사지 않는다. ``max_qty`` (종목마다 한 번 주문 상한)에 닿으면
    그날은 멈춘다."""
    symbols = active_symbols(qty, gold)
    held = {symbol: qty.get(symbol, 0.0) for symbol in symbols}
    cash_left = cash
    buys: dict[str, int] = {}
    buy_cost = {symbol: limit_price(last[symbol], "BUY") * (1 + fee) for symbol in symbols}

    def shortfall(symbol: str) -> float:
        return target[symbol] - held[symbol] * last[symbol]

    while True:
        value = cash_left + sum(held[symbol] * last[symbol] for symbol in symbols)
        target = targets(value, share, gold)
        for symbol in sorted(symbols, key=lambda name: -shortfall(name)):
            if target[symbol] <= 0:  # 목표 0 (브레이크 2 단계의 TQQQ 등)은 사지 않는다
                continue
            if max_qty and buys.get(symbol, 0) >= max_qty.get(symbol, 10**9):
                return buys  # 가장 모자란 종목이 한 번 상한 — 나머지는 다음 날
            if cash_left + 1e-9 < buy_cost[symbol]:
                continue
            held[symbol] += 1
            cash_left -= buy_cost[symbol]
            buys[symbol] = buys.get(symbol, 0) + 1
            break
        else:
            return buys


def apply_fill(state: DenebState, order: DenebOrder) -> None:
    """[쓰임] 체결분을 장부에 넣는다 (매수: 현금 −(금액 + 수수료), 매도: 현금 +(금액 − 수수료))."""
    if order.filled_qty <= 0:
        return
    sign = 1 if order.side == "BUY" else -1
    state.qty[order.symbol] = state.qty.get(order.symbol, 0.0) + sign * order.filled_qty
    state.cash_usd += -sign * order.filled_usd - order.commission


def held_on(state: DenebState, symbol: str, ex_date: str) -> float:
    """[쓰임] 배당락일 전날까지 체결된 Deneb 보유 주수 (그 배당을 받을 주수)."""
    shares = 0.0
    for order in state.orders:
        if order.symbol == symbol and order.filled_qty > 0 and order.date < ex_date:
            shares += order.filled_qty if order.side == "BUY" else -order.filled_qty
    return max(shares, 0.0)


def credit_dividends(
    state: DenebState, events: Sequence[tuple[str, str, float]], today: str, days_since: Any
) -> list[str]:
    """[쓰임] (배당락일, 종목, 주당 배당) 중 아직 안 넣은 것을 세후로 장부 현금에 넣는다.
    알림에 붙일 줄 목록을 돌려준다.
    [이유] 토스 배당 입금 기록을 읽지 않고, 배당락 + 10 일 뒤 '받았다' 고 친다 — 받은 돈을
           다음 매수 재원으로 바로 쓰려고. Deneb 시작 전 배당 · 그날 안 들고 있던 배당은 뺀다.

    ``days_since(a, b)`` = b − a 일수."""
    notes = []
    seen = {(str(row[0]), str(row[1])) for row in state.dividends}
    for ex_date, symbol, per_share in sorted(events):
        if (ex_date, symbol) in seen or ex_date < state.start:
            continue
        if days_since(ex_date, today) < DIV_WAIT_DAYS:
            continue
        shares = held_on(state, symbol, ex_date)
        if shares <= 0:
            continue
        usd = shares * per_share * (1 - WITHHOLDING)
        state.dividends.append([ex_date, symbol, shares, per_share, round(usd, 4)])
        state.cash_usd += usd
        notes.append(
            f"{symbol} 배당 {shares:g}주 × ${per_share:.4f} × 85% = ${usd:,.2f} 재투자 재원에 넣음"
        )
    return notes
