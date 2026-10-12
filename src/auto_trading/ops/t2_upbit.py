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


# ---------------------------------------------- 알림 설명 (2026-10-12 사용자 시안 C+)

LOOKBACKS = (20, 60, 120)
LOOK_NAME = {20: "짧은", 60: "중간", 120: "긴"}
TARGET_VOL = Decimal("0.15")
MAX_WEIGHT = Decimal("0.5")
BAND = Decimal("0.05")
"""목표와 지금 비중이 이만큼 벌어질 때만 사고판다 (SpotTrendConfig.band)."""


def _sign(x: Decimal) -> int:
    return 1 if x > 0 else -1 if x < 0 else 0


def max_weight(vol: Decimal) -> Decimal:
    """변동성 맞춤 최대 비중 = min(15 % ÷ 60 일 변동성, 50 %)."""
    return min(TARGET_VOL / vol, MAX_WEIGHT) if vol > 0 else _ZERO


def score_of(signs: Sequence[int]) -> Decimal:
    """세 추세 부호의 평균 (−1 ~ 1). 롱만이라 비중에는 0 아래를 0 으로 쓴다."""
    return Decimal(sum(signs)) / Decimal(len(signs))


def score_text(score: Decimal) -> str:
    n = int((score * 3).to_integral_value())
    return "0" if n == 0 else f"{n}/3"


def krw_price(x: Decimal) -> str:
    """1억 넘으면 '1억 1,565만', 1천만 넘으면 '9,919만', 아니면 '3,695,000'."""
    if x >= 100_000_000:
        eok = int(x // 100_000_000)
        man = int(((x - eok * 100_000_000) / 10_000).to_integral_value())
        if man >= 10_000:
            eok, man = eok + 1, man - 10_000
        return f"{eok}억 {man:,}만" if man else f"{eok}억"
    if x >= 10_000_000:
        return f"{int((x / 10_000).to_integral_value()):,}만"
    return f"{x:,.0f}"


@dataclass(frozen=True, slots=True)
class Flip:
    """내일 마감가가 ``price`` 를 넘으면(켜짐) / 밑돌면(꺼짐) 바뀌는 추세 하나."""

    lookback: int
    turns_on: bool
    price: Decimal
    change: Decimal
    """price ÷ 오늘 마감가 − 1."""
    new_weight: Decimal
    """변동성이 그대로라고 볼 때 새 목표(세 코인 합 100 % 축소 전)."""


def next_flips(closes: Sequence[Decimal], sg: CoinSignal) -> tuple[Flip | None, Flip | None]:
    """(가장 가까운 '켜짐', 가장 가까운 '꺼짐') — 내일 판단 기준 (순수).

    내일의 n 일 수익 = 내일 마감 ÷ ``closes[-n]`` − 1 (기준 날이 하루 밀린다).
    """
    last = closes[-1]
    signs = [_sign(p) for p in sg.parts]
    cap = max_weight(sg.vol)
    up: Flip | None = None
    down: Flip | None = None
    for i, n in enumerate(LOOKBACKS):
        ref = closes[-n]
        if signs[i] <= 0:
            new = [*signs[:i], 1, *signs[i + 1 :]]
            w = max(score_of(new), _ZERO) * cap
            f = Flip(n, True, ref, ref / last - 1, w)
            if up is None or ref < up.price:
                up = f
        if signs[i] >= 0:
            new = [*signs[:i], -1, *signs[i + 1 :]]
            w = max(score_of(new), _ZERO) * cap
            f = Flip(n, False, ref, ref / last - 1, w)
            if down is None or ref > down.price:
                down = f
    return up, down


def flip_text(f: Flip) -> str:
    word = "위로" if f.turns_on else "아래로"
    state = "켜져" if f.turns_on else "꺼져"
    after = "0% (전량 매도)" if f.new_weight <= 0 else f"약 {f.new_weight:.0%}"
    return (
        f"{'🔺' if f.turns_on else '🔻'} {krw_price(f.price)} 원({f.change:+.1%}) {word} 마감"
        f" → {f.lookback}일 추세 {state} {after}"
    )


def next_lines(closes: Sequence[Decimal], sg: CoinSignal, *, only_big: bool) -> list[str]:
    """'다음' 줄들 (오르는 쪽 · 내리는 쪽). ``only_big`` 이면 목표가 5 %p 이상 바뀌는 쪽만
    (매매 안 한 코인 · 주간 요약, 사용자 2026-10-12)."""
    out = []
    for f in next_flips(closes, sg):
        if f is None:
            continue
        if only_big and abs(f.new_weight - sg.weight) < BAND:
            continue
        out.append(f"다음 {flip_text(f)}")
    return out


def short_size_text(sg: CoinSignal) -> str:
    return f"변동성 {sg.vol:.1%} → 최대 {max_weight(sg.vol):.1%}"


def trend_icons(sg: CoinSignal) -> str:
    return " · ".join(
        f"{n}일 {'🔺' if p > 0 else '🔻' if p < 0 else '➖'}{p:+.1%}"
        for n, p in zip(LOOKBACKS, sg.parts, strict=True)
    )


def size_text(sg: CoinSignal) -> str:
    cap = max_weight(sg.vol)
    sc = score_of([_sign(p) for p in sg.parts])
    head = f"60일 변동성 {sg.vol:.1%} → 최대 비중 15% ÷ {sg.vol:.1%} = {cap:.1%}"
    if sc <= 0:
        tail = f" → 점수 {score_text(sc)} (롱만이라 0) = 0%"
    else:
        tail = f" → × 점수 {score_text(sc)} = {sc * cap:.1%}"
    if sg.weight < sg.raw - Decimal("0.005"):
        tail += f" → 세 코인 합 100% 넘어 {sg.weight:.1%}"
    return head + tail


def why_text(
    closes: Sequence[Decimal], sg: CoinSignal, prev_parts: Sequence[Decimal] | None
) -> str | None:
    """바뀐 추세를 가격으로 — '20일 전 1억 1,712만 → 어제 1억 1,336만 (-3.2%) — 짧은 추세가 꺼짐'.

    지난 매매 때의 세 추세(``prev_parts``)와 부호가 달라진 것만. 없으면 None."""
    if prev_parts is None or len(prev_parts) != 3:
        return None
    out = []
    for i, n in enumerate(LOOKBACKS):
        before, now = _sign(prev_parts[i]), _sign(sg.parts[i])
        if before == now:
            continue
        state = "켜짐" if now > 0 else "꺼짐" if now < 0 else "평평해짐"
        out.append(
            f"{n}일 전 {krw_price(closes[-1 - n])} → 어제 {krw_price(closes[-1])}"
            f" ({sg.parts[i]:+.1%}) — {LOOK_NAME[n]} 추세가 {state}"
        )
    return " · ".join(out) if out else None
