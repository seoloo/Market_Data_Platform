"""T/v2 업비트 실거래 — 순수 계산 (2026-10-09 사용자 결정, 문서 190 §4). I/O 없음.

* 종목: BTC · ETH · SOL 원화. 규칙은 T/v2 그대로
  (20 · 60 · 120 일 부호 평균 · 60 일 변동성 15% 목표 · 종목당 최대 50% · 밴드 5%p).
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
MARKETS = {c: f"KRW-{c}" for c in COINS}
STEP = Decimal("0.00000001")
MIN_KRW = Decimal(5000)
DD_STOP = Decimal("0.30")
STOP_REMIND_DAYS = 30
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


def stop_reminder(since: str, today: str, reminded: Sequence[int]) -> int | None:
    """정지 시작일 ``since`` 부터 30 · 60 · 90 … 일째에 한 번씩 다시 알릴 날수, 아니면 None (순수).

    하루 이틀 실행을 거른 날이 있어도 다음 실행 때 그 단계를 한 번 알린다(문서 199)."""
    days = (date.fromisoformat(today) - date.fromisoformat(since)).days
    due = days // STOP_REMIND_DAYS * STOP_REMIND_DAYS
    if due >= STOP_REMIND_DAYS and due not in reminded:
        return due
    return None


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


@dataclass(frozen=True, slots=True)
class CoinSignal:
    """알림용 판단 근거 (그날 마감 일봉 기준)."""

    ups: int
    """20 · 60 · 120 일 수익이 플러스인 개수."""
    parts: tuple[Decimal, Decimal, Decimal]
    vol: Decimal
    raw: Decimal
    """합 100% 축소 전 목표 비중."""
    weight: Decimal
    """최종 목표 비중."""
    week: Decimal
    """최근 7 일 가격 변화."""


def signals(daily: Mapping[str, Sequence[Candle]], as_of_ms: int) -> dict[str, CoinSignal]:
    """코인별 추세 · 변동성 · 목표 비중 (판단 불가 코인은 빠짐, 순수)."""
    from auto_trading.features.tsmom import realized_vol_annual

    final = targets(daily, as_of_ms)
    out: dict[str, CoinSignal] = {}
    for coin in COINS:
        closes = [c.close for c in daily.get(coin, ()) if c.close_ms <= as_of_ms]
        w = final.get(coin)
        if w is None or len(closes) < 121:
            continue
        last = closes[-1]
        parts = tuple(last / closes[-1 - k] - 1 for k in (20, 60, 120))
        vol = realized_vol_annual(closes, 60) or _ZERO
        cfg = SpotTrendConfig(symbols=(coin,))
        raw = spot_trend_targets({coin: daily[coin]}, as_of_ms=as_of_ms, cfg=cfg)[coin].weight
        out[coin] = CoinSignal(
            ups=sum(1 for p in parts if p > 0),
            parts=(parts[0], parts[1], parts[2]),
            vol=vol,
            raw=raw or _ZERO,
            weight=w,
            week=last / closes[-8] - 1,
        )
    return out


def reason(
    coin: str,
    now: CoinSignal,
    prev: Mapping[str, str] | None,
    current_w: Decimal,
    *,
    flow: Decimal,
) -> tuple[str, str]:
    """(아이콘, 이유 한 줄) — 비중을 바꾼 까닭 (순수).

    🟢 추세 켜짐 · 🔴 추세 꺼짐 · 🟡 추세 강도 · 변동성 변화 · ⚖️ 합 100% 축소 ·
    💰 입금 · 출금 · 🔵 가격이 움직여 비중이 벗어남 · 🆕 첫 매수.
    """
    p = " · ".join(f"{d}일 {x:+.1%}" for d, x in zip((20, 60, 120), now.parts, strict=True))
    if prev is None:
        return "🆕", f"첫 매수: {p} (상승 {now.ups}/3) · 변동성 {now.vol:.0%}"
    p_ups = int(prev.get("ups", "0"))
    p_on, n_on = p_ups >= 2, now.ups >= 2
    if p_on != n_on:
        if n_on:
            return "🟢", f"추세 켜짐: {p} (상승 {now.ups}/3)"
        return "🔴", f"추세 꺼짐: {p} (상승 {now.ups}/3)"
    if p_ups != now.ups:
        return "🟡", f"추세 강도 변화: 상승 {p_ups}/3 → {now.ups}/3 ({p})"
    p_vol = Decimal(prev.get("vol", "0"))
    p_raw = Decimal(prev.get("raw", "0"))
    if abs(now.raw - p_raw) >= Decimal("0.03") and p_vol > 0:
        word = "커져 담는 양 줄임" if now.vol > p_vol else "작아져 담는 양 늘림"
        return "🟡", f"변동성 {p_vol:.0%} → {now.vol:.0%} 로 {word}"
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


def _sign(x: Decimal) -> int:
    return 1 if x > 0 else -1 if x < 0 else 0


def signs_of(sg: CoinSignal) -> list[int]:
    return [_sign(p) for p in sg.parts]


def max_weight(vol: Decimal) -> Decimal:
    """변동성 맞춤 최대 비중 = min(15 % ÷ 60 일 변동성, 50 %)."""
    return min(TARGET_VOL / vol, MAX_WEIGHT) if vol > 0 else _ZERO


def score_of(signs: Sequence[int]) -> Decimal:
    """세 추세 부호의 평균 (−1 ~ 1). 롱만이라 비중에는 0 아래를 0 으로 쓴다."""
    return Decimal(sum(signs)) / Decimal(len(signs))


def score_text(score: Decimal) -> str:
    n = int((score * 3).to_integral_value())
    return "0" if n == 0 else f"{n}/3"


def trend_icons(sg: CoinSignal) -> str:
    """'20일 🔻-3.2% · 60일 🔺+26.5% · 120일 🔺+16.3%'."""
    return " · ".join(
        f"{n}일 {'🔺' if p > 0 else '🔻' if p < 0 else '➖'}{p:+.1%}"
        for n, p in zip(LOOKBACKS, sg.parts, strict=True)
    )


def trend_arrows(sg: CoinSignal) -> str:
    """'🔻🔺🔺' (20 · 60 · 120 일 순서)."""
    return "".join("🔺" if p > 0 else "🔻" if p < 0 else "➖" for p in sg.parts)


def score_calc(sg: CoinSignal, prev: Decimal | None = None) -> str:
    """'점수 3/3 → 1/3 (오름 2 − 내림 1) × 최대 비중 40.8% = 13.6%'."""
    signs = signs_of(sg)
    sc = score_of(signs)
    head = (
        f"점수 {score_text(prev)} → {score_text(sc)}"
        if prev is not None and prev != sc
        else f"점수 {score_text(sc)}"
    )
    head += f" (오름 {signs.count(1)} − 내림 {signs.count(-1)})"
    if sc <= 0:
        return head + " → 롱만이라 0%"
    cap = max_weight(sg.vol)
    out = head + f" × 최대 비중 {cap:.1%} = {sc * cap:.1%}"
    if sg.weight < sg.raw - Decimal("0.005"):
        out += f" → 세 코인 합 100% 넘어 {sg.weight:.1%}"
    return out
