"""🌌 Vega 계산 — 업비트 BTC · ETH · SOL 원화 추세 따라가기 (T/v2, 2026-10-09 결정, 문서 190).

언제 쓰나: 매일 09:05 ``AutoTrading_Vega`` (``scripts/vega_upbit.py``) 가 부른다.
읽고 쓰는 것: 없음 — 계산만 (조회 · 주문 · 장부는 ``scripts/vega_upbit.py``).

* 종목: BTC · ETH · SOL 원화. 규칙은 T/v2 그대로
  (20 · 60 · 120 일 부호 평균 · 60 일 변동성 15% 목표 · 종목당 최대 50% · 밴드 5%p).
  쉬운 말: 최근 오른 코인을, 덜 흔들릴수록 더 많이 담는다.
  세 종목 목표 합이 100% 를 넘으면 **비율대로 줄인다**(사용자 선택).
* 적립식: 사용자가 원화를 넣고 뺀다. 그래서 낙폭은 '단위 가치(NAV)' 로 잰다 — 우리 주문으로 설명되지
  않는 잔고 변화(입금 · 출금 · 직접 매매)는 그날 가치로 단위를 늘리거나 줄인다.
* 계좌 낙폭 30% 면 새 매수를 멈춘다(추세 매도는 그대로). 자동 해제 없음.
  정지 30 일째부터 30 일마다 다시 알린다(문서 199: 풀기만 하면 언제 풀든 비슷, 안 풀면 크게 손해).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date
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
MARKETS = {coin: f"KRW-{coin}" for coin in COINS}
"""코인 → 업비트 마켓 이름 (KRW-BTC 등)."""
STEP = Decimal("0.00000001")
"""업비트 코인 수량 최소 단위."""
MIN_KRW = Decimal(5000)
"""업비트 최소 주문 금액 (원)."""
DD_STOP = Decimal("0.30")
"""계좌 낙폭이 이만큼(30%) 되면 새 매수를 멈춘다."""
STOP_REMIND_DAYS = 30
_ZERO = Decimal(0)


def targets(daily: Mapping[str, Sequence[Candle]], as_of_ms: int) -> dict[str, Decimal | None]:
    """[쓰임] 코인 → 목표 비중 (판단 불가면 None).
    [이유] 세 코인 목표 합이 100% 를 넘으면 원화를 빌릴 수 없어서 비율대로 줄인다 (사용자 선택)."""
    weights: dict[str, Decimal | None] = {}
    for coin in COINS:
        cfg = SpotTrendConfig(symbols=(coin,))
        target = spot_trend_targets({coin: daily.get(coin, ())}, as_of_ms=as_of_ms, cfg=cfg)[coin]
        weights[coin] = target.weight
    known = [weight for weight in weights.values() if weight is not None]
    total = sum(known, _ZERO)
    if total > 1:
        weights = {
            coin: (None if weight is None else weight / total) for coin, weight in weights.items()
        }
    return weights


def plan(
    weights: Mapping[str, Decimal | None],
    holdings: Mapping[str, Decimal],
    prices: Mapping[str, Decimal],
    equity: Decimal,
    *,
    allow_buy: bool,
    step: Mapping[str, Decimal] | None = None,
    min_notional: Mapping[str, Decimal] | None = None,
) -> list[SpotOrderIntent]:
    """[쓰임] 목표 비중 → 주문 의도 목록 (매도 먼저).

    ``allow_buy`` 가 False 면(낙폭 정지) 매수는 뺀다.

    ``step`` · ``min_notional`` 을 주지 않으면 업비트 값(0.00000001 · 5,000 원).
    (옛 바이낸스 Vega 는 종목별 LOT_SIZE · NOTIONAL 을 넘겼다, 2026-10-15.)"""
    spot_targets = {coin: SpotTarget(coin, weight) for coin, weight in weights.items()}
    step_size = dict(step) if step is not None else dict.fromkeys(COINS, STEP)
    minimum = dict(min_notional) if min_notional is not None else dict.fromkeys(COINS, MIN_KRW)
    intents = plan_spot_orders(
        spot_targets,
        holdings=holdings,
        prices=prices,
        equity=equity,
        cfg=SpotTrendConfig(),
        step_size=step_size,
        min_notional=minimum,
    )
    return [intent for intent in intents if allow_buy or intent.side.value == "SELL"]


def stop_reminder(since: str, today: str, reminded: Sequence[int]) -> int | None:
    """[쓰임] 정지 시작일 ``since`` 부터 30 · 60 · 90 … 일째에 한 번씩 다시 알릴 날수, 아니면 None.
    [이유] 하루 이틀 실행을 거른 날이 있어도 다음 실행 때 그 단계를 한 번 알린다(문서 199)."""
    days = (date.fromisoformat(today) - date.fromisoformat(since)).days
    due_days = days // STOP_REMIND_DAYS * STOP_REMIND_DAYS
    if due_days >= STOP_REMIND_DAYS and due_days not in reminded:
        return due_days
    return None


def equity(krw: Decimal, holdings: Mapping[str, Decimal], prices: Mapping[str, Decimal]) -> Decimal:
    """[쓰임] 계좌 평가액(원) = 원화 + 코인 수량 × 가격."""
    coins_krw = sum((holdings.get(coin, _ZERO) * prices[coin] for coin in COINS), _ZERO)
    return krw + coins_krw


@dataclass(frozen=True, slots=True)
class NavStep:
    """단위 가치 계산 한 번의 결과."""

    units: Decimal
    nav: Decimal
    """1 단위 값 (처음 1,000)."""
    peak: Decimal
    dd: Decimal
    """고점 대비 낙폭 (0.3 = 30%)."""
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
    """[쓰임] 지난 실행 뒤 '있어야 할' 잔고와 지금 잔고를 비교해 입출금을 걸러 내고
    단위 가치를 잰다.
    [이유] 입금을 수익으로, 출금을 손실로 착각하면 낙폭 정지가 엉뚱하게 걸린다 —
           설명되지 않는 차이는 그날 단위 값으로 단위를 늘리거나 줄인다 (계산만)."""
    equity_now = equity(krw, holdings, prices)
    equity_expected = equity(expected_krw, expected, prices)
    flow = equity_now - equity_expected
    if units <= 0:
        if equity_now <= 0:
            return NavStep(_ZERO, start_nav, start_nav, _ZERO, flow)
        units = equity_now / start_nav
        return NavStep(units, start_nav, start_nav, _ZERO, flow)
    nav_before = equity_expected / units if units > 0 else start_nav
    if nav_before > 0 and flow != 0:
        units += flow / nav_before
    nav = equity_now / units if units > 0 else start_nav
    peak = max(peak, nav)
    drawdown = 1 - nav / peak if peak > 0 else _ZERO
    return NavStep(units, nav, peak, drawdown, flow)


@dataclass(frozen=True, slots=True)
class CoinBook:
    """코인 하나의 원가 장부."""

    qty: Decimal
    cost: Decimal
    """지금 들고 있는 수량의 매입 원가(수수료 포함, 이동평균)."""
    realized: Decimal
    """판 것의 실현 손익 누계(수수료 뺌)."""


def books(orders: Sequence[Mapping[str, object]]) -> dict[str, CoinBook]:
    """[쓰임] 체결 기록(``t2u-<날짜>-<코인>-<B|S>``) → 코인별 원가 · 실현 손익 (이동평균, 계산만).
    [이유] 판 만큼 원가를 평균 단가로 빼야 남은 코인의 수익률이 맞다."""
    qty = dict.fromkeys(COINS, _ZERO)
    cost = dict.fromkeys(COINS, _ZERO)
    realized = dict.fromkeys(COINS, _ZERO)
    for order in orders:
        identifier = str(order.get("identifier") or order.get("id") or "")
        parts = identifier.split("-")
        if len(parts) < 3 or parts[-2] not in COINS:
            continue
        coin = parts[-2]
        volume = Decimal(str(order.get("executed_volume") or "0"))
        if volume <= 0:
            continue
        funds = Decimal(str(order.get("funds") or "0"))
        fee = Decimal(str(order.get("paid_fee") or "0"))
        if order.get("side") == "bid":
            qty[coin] += volume
            cost[coin] += funds + fee
        else:
            average = cost[coin] / qty[coin] if qty[coin] > 0 else _ZERO
            sold = min(volume, qty[coin])
            realized[coin] += funds - fee - average * sold
            cost[coin] -= average * sold
            qty[coin] -= sold
    return {coin: CoinBook(qty[coin], cost[coin], realized[coin]) for coin in COINS}


@dataclass(frozen=True, slots=True)
class CoinSignal:
    """알림용 판단 근거 (그날 마감 일봉 기준)."""

    ups: int
    """20 · 60 · 120 일 수익이 플러스인 개수."""
    parts: tuple[Decimal, Decimal, Decimal]
    """20 · 60 · 120 일 수익률."""
    vol: Decimal
    """60 일 변동성 (연율)."""
    raw: Decimal
    """합 100% 축소 전 목표 비중."""
    weight: Decimal
    """최종 목표 비중."""
    week: Decimal
    """최근 7 일 가격 변화."""


def signals(daily: Mapping[str, Sequence[Candle]], as_of_ms: int) -> dict[str, CoinSignal]:
    """[쓰임] 코인별 추세 · 변동성 · 목표 비중 (판단 불가 코인은 빠짐, 계산만)."""
    from auto_trading.features.tsmom import realized_vol_annual

    final = targets(daily, as_of_ms)
    result: dict[str, CoinSignal] = {}
    for coin in COINS:
        closes = [candle.close for candle in daily.get(coin, ()) if candle.close_ms <= as_of_ms]
        weight = final.get(coin)
        if weight is None or len(closes) < 121:
            continue
        last = closes[-1]
        returns = tuple(last / closes[-1 - days] - 1 for days in (20, 60, 120))
        vol = realized_vol_annual(closes, 60) or _ZERO
        cfg = SpotTrendConfig(symbols=(coin,))
        raw = spot_trend_targets({coin: daily[coin]}, as_of_ms=as_of_ms, cfg=cfg)[coin].weight
        result[coin] = CoinSignal(
            ups=sum(1 for value in returns if value > 0),
            parts=(returns[0], returns[1], returns[2]),
            vol=vol,
            raw=raw or _ZERO,
            weight=weight,
            week=last / closes[-8] - 1,
        )
    return result


def reason(
    coin: str,
    now: CoinSignal,
    prev: Mapping[str, str] | None,
    current_w: Decimal,
    *,
    flow: Decimal,
) -> tuple[str, str]:
    """[쓰임] (아이콘, 이유 한 줄) — 비중을 바꾼 까닭 (계산만).
    [이유] 사용자가 '왜 샀나 · 왜 팔았나' 를 알림만 보고 알 수 있게, 가장 큰 까닭 하나만 고른다.

    🟢 추세 켜짐 · 🔴 추세 꺼짐 · 🟡 추세 강도 · 변동성 변화 · ⚖️ 합 100% 축소 ·
    💰 입금 · 출금 · 🔵 가격이 움직여 비중이 벗어남 · 🆕 첫 매수."""
    returns_text = " · ".join(
        f"{days}일 {value:+.1%}" for days, value in zip((20, 60, 120), now.parts, strict=True)
    )
    if prev is None:
        return "🆕", f"첫 매수: {returns_text} (상승 {now.ups}/3) · 변동성 {now.vol:.0%}"
    prev_ups = int(prev.get("ups", "0"))
    was_on = prev_ups >= 2
    is_on = now.ups >= 2
    if was_on != is_on:
        if is_on:
            return "🟢", f"추세 켜짐: {returns_text} (상승 {now.ups}/3)"
        return "🔴", f"추세 꺼짐: {returns_text} (상승 {now.ups}/3)"
    if prev_ups != now.ups:
        return "🟡", f"추세 강도 변화: 상승 {prev_ups}/3 → {now.ups}/3 ({returns_text})"
    prev_vol = Decimal(prev.get("vol", "0"))
    prev_raw = Decimal(prev.get("raw", "0"))
    if abs(now.raw - prev_raw) >= Decimal("0.03") and prev_vol > 0:
        word = "커져 담는 양 줄임" if now.vol > prev_vol else "작아져 담는 양 늘림"
        return "🟡", f"변동성 {prev_vol:.0%} → {now.vol:.0%} 로 {word}"
    if now.weight < now.raw - Decimal("0.005"):
        return (
            "⚖️",
            f"세 코인 목표 합이 100% 를 넘어 비율대로 줄임 ({now.raw:.1%} → {now.weight:.1%})",
        )
    if flow > 0:
        return "💰", "입금으로 원화가 늘어 비중이 낮아짐 → 목표까지 채움"
    if flow < 0:
        return "💰", "출금으로 원화가 줄어 비중이 높아짐 → 목표로 맞춤"
    gap = now.weight - current_w
    return (
        "🔵",
        f"가격이 움직여 비중이 목표보다 {abs(gap):.1%}p"
        f" {'낮아짐' if gap > 0 else '높아짐'} → 다시 맞춤",
    )


# ---------------------------------------------- 알림 설명 (2026-10-12 사용자 시안 B)

LOOKBACKS = (20, 60, 120)
TARGET_VOL = Decimal("0.15")
MAX_WEIGHT = Decimal("0.5")


def _sign(value: Decimal) -> int:
    if value > 0:
        return 1
    if value < 0:
        return -1
    return 0


def signs_of(signal: CoinSignal) -> list[int]:
    """[쓰임] 20 · 60 · 120 일 수익률의 부호 [+1 · 0 · −1]."""
    return [_sign(value) for value in signal.parts]


def max_weight(vol: Decimal) -> Decimal:
    """[쓰임] 변동성 맞춤 최대 비중 = min(15 % ÷ 60 일 변동성, 50 %)."""
    return min(TARGET_VOL / vol, MAX_WEIGHT) if vol > 0 else _ZERO


def score_of(signs: Sequence[int]) -> Decimal:
    """[쓰임] 세 추세 부호의 평균 (−1 ~ 1). 롱만이라 비중에는 0 아래를 0 으로 쓴다."""
    return Decimal(sum(signs)) / Decimal(len(signs))


def score_text(score: Decimal) -> str:
    """[쓰임] 점수를 '2/3' 꼴로 (0 이면 '0')."""
    thirds = int((score * 3).to_integral_value())
    return "0" if thirds == 0 else f"{thirds}/3"


def _arrow(value: Decimal) -> str:
    if value > 0:
        return "🔺"
    if value < 0:
        return "🔻"
    return "➖"


def trend_icons(signal: CoinSignal) -> str:
    """[쓰임] '20일 🔻-3.2% · 60일 🔺+26.5% · 120일 🔺+16.3%'."""
    return " · ".join(
        f"{days}일 {_arrow(value)}{value:+.1%}"
        for days, value in zip(LOOKBACKS, signal.parts, strict=True)
    )


def trend_arrows(signal: CoinSignal) -> str:
    """[쓰임] '🔻🔺🔺' (20 · 60 · 120 일 순서)."""
    return "".join(_arrow(value) for value in signal.parts)


def score_calc(signal: CoinSignal, prev: Decimal | None = None) -> str:
    """[쓰임] 비중 계산 한 줄 — '점수 3/3 → 1/3 (오름 2 − 내림 1) × 최대 비중 40.8% = 13.6%'."""
    signs = signs_of(signal)
    score = score_of(signs)
    if prev is not None and prev != score:
        head = f"점수 {score_text(prev)} → {score_text(score)}"
    else:
        head = f"점수 {score_text(score)}"
    head += f" (오름 {signs.count(1)} − 내림 {signs.count(-1)})"
    if score <= 0:
        return head + " → 롱만이라 0%"
    cap = max_weight(signal.vol)
    text = head + f" × 최대 비중 {cap:.1%} = {score * cap:.1%}"
    if signal.weight < signal.raw - Decimal("0.005"):
        text += f" → 세 코인 합 100% 넘어 {signal.weight:.1%}"
    return text
