"""T/v2 업비트 실거래 — 순수 계산 (2026-10-09 사용자 결정, 문서 190 §4). I/O 없음.

* 종목: BTC · ETH · SOL 원화. 규칙은 T/v2 그대로
  (20 · 60 · 120 일 부호 평균 · 60 일 변동성 15% 목표 · 종목당 최대 50% · 밴드 5%p).
  세 종목 목표 합이 100% 를 넘으면 **비율대로 줄인다**(사용자 선택).
* 적립식: 사용자가 원화를 넣고 뺀다. 그래서 낙폭은 '단위 가치(NAV)' 로 잰다 — 우리 주문으로 설명되지
  않는 잔고 변화(입금 · 출금 · 직접 매매)는 그날 가치로 단위를 늘리거나 줄인다.
* 계좌 낙폭 30% 면 새 매수를 멈춘다(추세 매도는 그대로). 자동 해제 없음.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal

from auto_trading.config.schema import SpotTrendConfig
from auto_trading.core.types import Candle
from auto_trading.strategy.spot_trend import (
    SpotOrderIntent,
    SpotTarget,
    plan_spot_orders,
    spot_trend_targets,
)

COINS = ("BTC", "ETH", "SOL")
MARKETS = {c: f"KRW-{c}" for c in COINS}
STEP = Decimal("0.00000001")
MIN_KRW = Decimal(5000)
DD_STOP = Decimal("0.30")
_ZERO = Decimal(0)


def targets(daily: Mapping[str, Sequence[Candle]], as_of_ms: int) -> dict[str, Decimal | None]:
    """종목 → 목표 비중 (판단 불가면 None). 합 > 1 이면 비율대로 줄인다."""
    out: dict[str, Decimal | None] = {}
    for coin in COINS:
        cfg = SpotTrendConfig(symbols=(coin,))
        t = spot_trend_targets({coin: daily.get(coin, ())}, as_of_ms=as_of_ms, cfg=cfg)[coin]
        out[coin] = t.weight
    known = [w for w in out.values() if w is not None]
    total = sum(known, _ZERO)
    if total > 1:
        out = {c: (None if w is None else w / total) for c, w in out.items()}
    return out


def plan(
    weights: Mapping[str, Decimal | None],
    holdings: Mapping[str, Decimal],
    prices: Mapping[str, Decimal],
    equity: Decimal,
    *,
    allow_buy: bool,
) -> list[SpotOrderIntent]:
    """주문 의도 (매도 먼저). ``allow_buy`` 가 False 면(낙폭 정지) 매수는 뺀다."""
    tg = {c: SpotTarget(c, w) for c, w in weights.items()}
    intents = plan_spot_orders(
        tg,
        holdings=holdings,
        prices=prices,
        equity=equity,
        cfg=SpotTrendConfig(),
        step_size=dict.fromkeys(COINS, STEP),
        min_notional=dict.fromkeys(COINS, MIN_KRW),
    )
    return [i for i in intents if allow_buy or i.side.value == "SELL"]


def equity(krw: Decimal, holdings: Mapping[str, Decimal], prices: Mapping[str, Decimal]) -> Decimal:
    return krw + sum((holdings.get(c, _ZERO) * prices[c] for c in COINS), _ZERO)


@dataclass(frozen=True, slots=True)
class NavStep:
    units: Decimal
    nav: Decimal
    peak: Decimal
    dd: Decimal
    flow: Decimal
    """우리 주문으로 설명되지 않는 잔고 변화의 원화 가치(+ 입금 · − 출금)."""


def nav_step(
    *,
    units: Decimal,
    peak: Decimal,
    expected_krw: Decimal,
    expected: Mapping[str, Decimal],
    krw: Decimal,
    holdings: Mapping[str, Decimal],
    prices: Mapping[str, Decimal],
    start_nav: Decimal = Decimal(1000),
) -> NavStep:
    """지난 실행 뒤 '있어야 할' 잔고와 지금 잔고를 비교해 입출금을 걸러 내고 단위 가치를 잰다.

    순수 함수."""
    eq_now = equity(krw, holdings, prices)
    eq_expected = equity(expected_krw, expected, prices)
    flow = eq_now - eq_expected
    if units <= 0:
        if eq_now <= 0:
            return NavStep(_ZERO, start_nav, start_nav, _ZERO, flow)
        units = eq_now / start_nav
        return NavStep(units, start_nav, start_nav, _ZERO, flow)
    nav_before = eq_expected / units if units > 0 else start_nav
    if nav_before > 0 and flow != 0:
        units += flow / nav_before
    nav = eq_now / units if units > 0 else start_nav
    peak = max(peak, nav)
    dd = 1 - nav / peak if peak > 0 else _ZERO
    return NavStep(units, nav, peak, dd, flow)


@dataclass(frozen=True, slots=True)
class CoinBook:
    qty: Decimal
    cost: Decimal
    """지금 들고 있는 수량의 매입 원가(수수료 포함, 이동평균)."""
    realized: Decimal
    """판 것의 실현 손익 누계(수수료 뺌)."""


def books(orders: Sequence[Mapping[str, object]]) -> dict[str, CoinBook]:
    """체결 기록(``t2u-<날짜>-<코인>-<B|S>``) → 코인별 원가 · 실현 손익 (이동평균, 순수)."""
    qty = dict.fromkeys(COINS, _ZERO)
    cost = dict.fromkeys(COINS, _ZERO)
    realized = dict.fromkeys(COINS, _ZERO)
    for o in orders:
        ident = str(o.get("identifier") or o.get("id") or "")
        parts = ident.split("-")
        if len(parts) < 3 or parts[-2] not in COINS:
            continue
        coin = parts[-2]
        vol = Decimal(str(o.get("executed_volume") or "0"))
        if vol <= 0:
            continue
        funds = Decimal(str(o.get("funds") or "0"))
        fee = Decimal(str(o.get("paid_fee") or "0"))
        if o.get("side") == "bid":
            qty[coin] += vol
            cost[coin] += funds + fee
        else:
            avg = cost[coin] / qty[coin] if qty[coin] > 0 else _ZERO
            sold = min(vol, qty[coin])
            realized[coin] += funds - fee - avg * sold
            cost[coin] -= avg * sold
            qty[coin] -= sold
    return {c: CoinBook(qty[c], cost[c], realized[c]) for c in COINS}
