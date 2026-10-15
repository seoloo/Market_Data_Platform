"""Altair(옛 AG4S2) 바이낸스 선물 **데모(가짜 돈)** 실행 — AG-CR4 롱 + 232 숏 2 배를 실제 주문으로.

    python -m scripts.altair_demo plan     # 지금 낼 주문 미리 보기 (주문 없음)
    python -m scripts.altair_demo tick     # 할 차례면 실행 (현황 봇이 2 분마다 부른다)
    python -m scripts.altair_demo status

사용자 (2026-10-15): "내가 100달러 넣을테니까 ags2를 실거래 할 수 있어?" → 100 달러는
최소 주문 금액(BTC 선물 100 USDC · ETH 20 USDC)에 걸려 규칙이 빠진다고 답함 →
"지금 바이낸스 선물 테스트넷에서 돌려줘".

* 계좌: 데모 선물(``demo-fapi``)의 **USDC 지갑만** 쓴다 — BTCUSDT 는 Phase 7 훈련,
  ETHUSDT 는 O/v2 데모가 같은 계좌에서 쓰므로 **BTCUSDC · ETHUSDC** 로 주문한다.
  신호는 메인넷 BTCUSDT · ETHUSDT 자료로 계산한다(엘리엇 데모와 같은 방식).
* 크기: 가상 **2,000 USDC** 장부(``ALLOC``, 처음 1,000 → 같은 날 사용자 요청으로 2,000) —
  BTC 주문 단위(0.001 BTC ≈ 81 달러) 오차가 20% 안에 드는 크기.
  보고에 "100 달러였다면 막혔을 주문" 도 적는다.
* 롱(AG-CR4): 매일 00:05 UTC 뒤 한 번, T/v2 목표 비중(BTC · ETH) × **4** 만큼 선물 롱으로.
  Paper 원장은 현물 1 배 + 선물 3 배로 나누지만 데모 현물 지갑은 따로라 **모두 선물로** 든다
  (같은 노출, 펀딩은 전체에 붙는 차이). 비상 스톱: 롱 평균가 −20% STOP_MARKET
  (포지션 전체, 사용자 규칙의 긴급 스톱 예외).
* 숏(문서 232 · AG4S2 Paper 와 같은 규칙): 4 시간봉 RSI7 이 70 위 → 아래로 꺾이고
  끝난 일봉 ADX14 > 25 · −DI > +DI, **그 코인의 롱 목표가 0 일 때만**.
  크기 = min(장부 10% × 2 배, 거래 위험 1% ÷ (3% + 0.39%)). 3% STOP_MARKET · 48 시간 뒤 정리 ·
  롱 목표가 다시 생기면 먼저 정리 · 청산 뒤 60 분 · 4 연패 240 분 쉼 · 하루 손실 2% 면 새 숏 금지 ·
  닫힌 숏 누적 손실이 고점 × 2.5% 면 숏 영구 차단(232 OWN).
* 일반 주문은 **지정가 IOC 만**(표시 가격 ± 0.3%). 상태 ``data/altair_demo/state.json``.
"""

from __future__ import annotations

import contextlib
import json
import sys
import time
from datetime import UTC, datetime
from decimal import ROUND_DOWN, Decimal
from pathlib import Path
from typing import Any

from auto_trading.config.schema import SpotTrendConfig
from auto_trading.config.secrets import BinanceCredentials, load_telegram_credentials, read_env_file
from auto_trading.core.errors import AutoTradingError, OrderRejectedError, OrderStateUnknownError
from auto_trading.exchange.binance_spot import BinanceSpotReadOnly
from auto_trading.exchange.binance_usdm_public import BinanceUsdmPublic
from auto_trading.exchange.binance_usdm_testnet import BinanceUsdmTestnet
from auto_trading.notify.channels import NotifyError, OutboxNotifier, TelegramNotifier
from auto_trading.ops.ag_short_paper import DAY, H4, Bar, short_signals
from auto_trading.strategy.spot_trend import spot_trend_targets

REPO = Path(__file__).resolve().parent.parent
DATA = REPO / "data" / "altair_demo"
STATE = DATA / "state.json"
REPORTS = REPO / "reports" / "altair_demo"
ALLOC = 2_000.0
"""가상 장부 크기 — 2026-10-15 사용자 "2000달러 기준으로해서 데모 진행해줘" (처음 1,000)."""
EXPOSURE = 4.0
SHORT_LEV = 2.0
LEVERAGE = 5
SIGNAL = {"BTC": "BTCUSDT", "ETH": "ETHUSDT"}
"""신호 자료(메인넷)."""
TRADE = {"BTC": "BTCUSDC", "ETH": "ETHUSDC"}
"""데모 주문 종목 (USDC 지갑)."""
LIMIT_PAD = 0.003
LONG_STOP = 0.20
SHORT_STOP = 0.03
HOLD_MS = 48 * 3_600_000
OWN_LIMIT = 0.025
RISK_PAD = 0.0039
BAND = 0.05
"""롱 노출이 장부의 5% 넘게 벗어날 때만 맞춘다(T/v2 밴드)."""
MINUTE = 60_000
SMALL = 100.0
"""사용자가 말한 실거래 금액 — 이 크기였다면 막혔을 주문을 보고에 적는다."""


# ------------------------------------------------------------------ 바깥


def _client() -> BinanceUsdmTestnet:
    env = read_env_file(REPO / ".env")
    creds = BinanceCredentials(env["BINANCE_TESTNET_API_KEY"], env["BINANCE_TESTNET_API_SECRET"])
    return BinanceUsdmTestnet(creds)


def _notify(text: str) -> None:
    REPORTS.mkdir(parents=True, exist_ok=True)
    OutboxNotifier(REPORTS / "outbox.log").send(text)
    creds = load_telegram_credentials(REPO / ".env")
    if creds is None:
        return
    try:
        TelegramNotifier(creds).send(text)
    except NotifyError as exc:
        print(f"텔레그램 실패: {exc}")


def _load() -> dict[str, Any]:
    try:
        return dict(json.loads(STATE.read_text(encoding="utf-8")))
    except (OSError, ValueError):
        return {}


def _save(st: dict[str, Any]) -> None:
    DATA.mkdir(parents=True, exist_ok=True)
    tmp = STATE.with_suffix(".tmp")
    tmp.write_text(json.dumps(st, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(STATE)


def _iso(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, UTC).strftime("%m-%d %H:%M")


def long_targets(now_ms: int) -> tuple[dict[str, float], dict[str, dict[str, Any]]]:
    """T/v2 목표 비중(마지막으로 끝난 일봉) — AG-CR4 Paper 와 같은 함수 + 알림용 근거."""
    from auto_trading.features.tsmom import realized_vol_annual

    as_of = now_ms - now_ms % DAY - 1
    spot = BinanceSpotReadOnly(None)
    try:
        daily = {SIGNAL[c]: spot.closed_daily(SIGNAL[c], as_of) for c in SIGNAL}
    finally:
        spot.close()
    tg = spot_trend_targets(daily, as_of_ms=as_of, cfg=SpotTrendConfig())
    info: dict[str, dict[str, Any]] = {}
    for c, sym in SIGNAL.items():
        closes = [float(x.close) for x in daily[sym]]
        if len(closes) < 121:
            continue
        vol = float(realized_vol_annual([x.close for x in daily[sym]], 60) or 0)
        info[c] = {
            "parts": [closes[-1] / closes[-1 - k] - 1 for k in (20, 60, 120)],
            "vol": vol,
            "cap": min(0.15 / vol, 0.5) if vol > 0 else 0.0,
        }
    return {c: float(tg[SIGNAL[c]].weight or 0) for c in SIGNAL}, info


def _bars(api: BinanceUsdmPublic, symbol: str, now_ms: int) -> tuple[list[Bar], list[Bar]]:
    rows = api._get("/fapi/v1/klines", {"symbol": symbol, "interval": "4h", "limit": 400})
    h4 = [
        Bar(int(r[0]), float(r[1]), float(r[2]), float(r[3]), float(r[4]))
        for r in rows
        if int(r[6]) < now_ms
    ]
    daily = [
        Bar(c.open_ms, float(c.open), float(c.high), float(c.low), float(c.close))
        for c in api.closed_daily(symbol, now_ms, limit=300)
    ]
    return h4, daily


def signal_now(h4: list[Bar], daily: list[Bar]) -> bool:
    """마지막으로 끝난 4 시간봉에서 숏 신호가 났나 (순수)."""
    return bool(h4) and (len(h4) - 1) in short_signals(h4, daily)


# ------------------------------------------------------------------ 계산 (순수)


def short_notional(equity: float, open_short_notional: float, funding_rate: float) -> float:
    """232 크기: min(장부 10% × 2 배 − 이미 든 숏, 위험 1% ÷ (3% + 0.39% + 음수 펀딩 × 6))."""
    risk_pct = SHORT_STOP + RISK_PAD + max(0.0, -funding_rate) * 6
    return max(
        0.0,
        min(
            equity * 0.10 * SHORT_LEV - open_short_notional,
            0.01 * equity / risk_pct,
        ),
    )


def round_qty(notional: float, price: float, step: Decimal) -> Decimal:
    if price <= 0 or notional <= 0:
        return Decimal(0)
    q = Decimal(str(notional / price))
    return (q / step).to_integral_value(rounding=ROUND_DOWN) * step


def blocked_small(plans: list[tuple[str, float]], mins: dict[str, float]) -> list[str]:
    """(코인, 주문 금액 USDC) 를 100 달러 장부로 줄였을 때 최소 금액에 걸리는 것."""
    scale = SMALL / ALLOC
    return [
        f"{c} {v * scale:,.0f} < {mins[c]:,.0f} USDC" for c, v in plans if abs(v) * scale < mins[c]
    ]


def trend_lines(info: dict[str, Any], w: float) -> list[str]:
    """롱 비중의 근거 — 추세 점수 · 세 기간 수익 · 변동성 맞춤 최대 비중 · 4 배 (순수)."""
    parts = info["parts"]
    ups = sum(1 for x in parts if x > 0) - sum(1 for x in parts if x < 0)
    score = "0" if ups <= 0 else f"{ups}/3"
    trend = " · ".join(
        f"{n}일 {'🔺' if x > 0 else '🔻'}{x:+.1%}"
        for n, x in zip((20, 60, 120), parts, strict=True)
    )
    if w <= 0:
        return [f" 왜? 추세 점수 {score}", f"   {trend}", "   → 롱만이라 0%"]
    return [
        f" 왜? 추세 점수 {score}",
        f"   {trend}",
        f"   × 최대 비중 {info['cap']:.1%} (변동성 {info['vol']:.0%}) = {w:.1%}",
        f"   × AG4 4배 = {EXPOSURE * w:.0%}",
    ]


def position_lines(
    rows: list[tuple[str, float, float, float]], eq: float, alloc: float
) -> list[str]:
    """(코인, 부호 수량, 평균가, 표시 가격) → '📋 지금 포지션' 칸 (순수)."""
    out = ["📋 지금 포지션"]
    long_value = 0.0
    for coin, q, entry, mark in rows:
        if q == 0:
            continue
        side = "롱" if q > 0 else "숏"
        value = abs(q) * mark
        pnl = (mark / entry - 1) * (1 if q > 0 else -1) if entry else 0.0
        out.append(f" {coin} {side} {abs(q):g} · {value:,.0f} USDC ({pnl:+.1%})")
        if q > 0:
            long_value += value
    if len(out) == 1:
        out.append(" 없음 (모두 현금)")
    out.append(f" 현금 {eq - long_value:,.0f} · 장부 {eq:,.1f} USDC (시작 {alloc:,.0f})")
    out.append(liquidation_line(eq, rows))
    return out


MMR = 0.005
"""유지 증거금률 근사 — 바이낸스 BTC · ETH 선물의 작은 금액 구간(0.4 ~ 0.5%) 중 보수적인 쪽."""


def liquidation_drop(equity: float, long_value: float, short_value: float) -> float | None:
    """BTC · ETH 가 **함께** 몇 % 움직이면 cross 계좌가 강제 청산되는지 (순수, 근사).

    계좌에 장부 돈(``equity``)만 있다고 보고, 순노출(롱 − 숏) 방향으로 같은 비율 d 만큼 움직일 때
    equity − d × 순노출 = 유지 증거금(MMR × 총명목 × (1 ∓ d)) 이 되는 d. 순노출이 0 이하거나
    100% 넘게 움직여야 하면 None(사실상 청산 없음)."""
    net = long_value - short_value
    gross = long_value + short_value
    if net <= 0 or equity <= 0:
        return None
    d = (equity - MMR * gross) / (net - MMR * gross)
    return d if 0 < d < 1 else None


def liquidation_line(equity: float, rows: list[tuple[str, float, float, float]]) -> str:
    longs = sum(q * m for _c, q, _e, m in rows if q > 0)
    shorts = sum(-q * m for _c, q, _e, m in rows if q < 0)
    d = liquidation_drop(equity, longs, shorts)
    if longs <= 0 and shorts <= 0:
        return " 청산 거리: 포지션 없음"
    exposure = (longs + shorts) / equity if equity > 0 else 0.0
    if d is None:
        return (
            f" 청산 거리: 노출 {exposure:.0%} — 코인 값이 0 이 돼도 청산 없음"
            f" ({equity:,.0f} 계좌 기준)"
        )
    return (
        f" 청산 거리: BTC · ETH 함께 -{d:.0%} ({equity:,.0f} 계좌 · cross · 노출 {exposure:.0%})"
        f" — 비상 스톱 -{LONG_STOP:.0%} 이 먼저"
        if d > LONG_STOP
        else f" ⚠ 청산 거리: BTC · ETH 함께 -{d:.0%} — 비상 스톱(-{LONG_STOP:.0%})보다 가깝다"
    )


def kst(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000 + 9 * 3600, UTC).strftime("%m/%d %H:%M")


# ------------------------------------------------------------------ 주문


def _price(mark: Decimal, side: str, tick: Decimal) -> Decimal:
    pad = Decimal(str(1 + LIMIT_PAD if side == "BUY" else 1 - LIMIT_PAD))
    return (mark * pad / tick).to_integral_value(rounding=ROUND_DOWN) * tick


def _order(
    c: BinanceUsdmTestnet, coin: str, side: str, qty: Decimal, ident: str, *, reduce: bool
) -> tuple[Decimal, Decimal, str]:
    """지정가 IOC → (체결 수량, 평균가, 알림 줄)."""
    sym = TRADE[coin]
    f = c.filters(sym)
    price = _price(c.mark_price(sym), side, f.tick_size)
    try:
        r = c.place_limit_ioc(sym, side, qty, price, ident, reduce_only=reduce)
    except OrderStateUnknownError as exc:
        return Decimal(0), Decimal(0), f"⚠ {coin} 결과 모름 — {exc}"
    except OrderRejectedError as exc:
        return Decimal(0), Decimal(0), f"❌ {coin} 거절 — {exc}"
    word = {"BUY": "매수", "SELL": "매도"}[side]
    if r.executed_qty <= 0:
        return Decimal(0), Decimal(0), f"⏸ {coin} {word} 체결 없음 (IOC)"
    return r.executed_qty, r.avg_price, f"🛒 {coin} {word} {r.executed_qty:g} @ {r.avg_price:,.2f}"


def _replace_stop(
    c: BinanceUsdmTestnet, st: dict[str, Any], coin: str, side: str, trigger: Decimal
) -> str:
    """그 코인의 이전 스톱을 지우고 포지션 전체 STOP_MARKET 을 새로 건다."""
    sym = TRADE[coin]
    stops = st.setdefault("stops", {})
    old = stops.pop(coin, None)
    if old:
        with contextlib.suppress(AutoTradingError):
            c.cancel_stop(sym, old)
    tick = c.filters(sym).tick_size
    trig = (trigger / tick).to_integral_value(rounding=ROUND_DOWN) * tick
    ident = f"altair-st-{coin}-{int(time.time())}"
    try:
        c.place_stop_market(sym, side, trig, ident)
    except AutoTradingError as exc:
        return f"⚠ {coin} 스톱 실패 — {exc}"
    stops[coin] = ident
    return f"🛡 {coin} 스톱 {trig:,.2f} ({'롱 −20%' if side == 'SELL' else '숏 +3%'})"


def _clear_stop(c: BinanceUsdmTestnet, st: dict[str, Any], coin: str) -> None:
    old = st.setdefault("stops", {}).pop(coin, None)
    if old:
        with contextlib.suppress(AutoTradingError):
            c.cancel_stop(TRADE[coin], old)


def _ensure_cross(c: BinanceUsdmTestnet) -> bool:
    """두 종목이 cross 인지 보고, 아니면 cross 로 바꾼다. 걸린 주문이 있으면 거래소가 바꾸기를
    거절하므로(-4067) 그때는 False — 다음 실행에서 다시 본다."""
    ok = True
    for sym in TRADE.values():
        rows = c._request("/fapi/v2/positionRisk", {"symbol": sym}, signed=True)
        if all(str(r.get("marginType")).lower() == "cross" for r in rows):
            continue
        try:
            c.set_margin_type(sym, "CROSSED")
        except AutoTradingError:
            ok = False
    return ok


def _equity(c: BinanceUsdmTestnet, st: dict[str, Any]) -> tuple[float, dict[str, float]]:
    """장부 평가액과 (코인 → 부호 수량).

    데모: 가상 장부 = ALLOC + (USDC 지갑 변화) + 우리 포지션 미실현 손익.
    실계좌(``st["live"]``): USDC 선물 지갑 + 미실현 손익 — 지갑에 Altair 돈만 둔다."""
    wallet = float(c.asset_balance("USDC"))
    live = bool(st.get("live"))
    if not live:
        st.setdefault("wallet0", wallet)
        st["alloc"] = ALLOC
    acct = c.account()
    pos: dict[str, float] = {}
    unreal = 0.0
    for coin, sym in TRADE.items():
        p = acct.positions.get(sym)
        if p is None:
            continue
        q = float(p.quantity)
        pos[coin] = q
        unreal += q * (float(c.mark_price(sym)) - float(p.entry_price))
    if live:
        return wallet + unreal, pos
    return ALLOC + wallet - float(st["wallet0"]) + unreal, pos


# ------------------------------------------------------------------ 할 일


def rebalance_long(
    c: BinanceUsdmTestnet,
    st: dict[str, Any],
    now_ms: int,
    *,
    dry: bool = False,
    alert: list[str] | None = None,
) -> list[str]:
    """하루 한 번 — 롱 목표(× 4)로 맞추고, 롱 목표가 생긴 코인의 숏은 먼저 정리."""
    lines: list[str] = []
    alert = alert if alert is not None else []
    w, info = long_targets(now_ms)
    st["long_w"] = w
    eq, pos = _equity(c, st)
    plans: list[tuple[str, float]] = []
    mins: dict[str, float] = {}
    done: dict[str, dict[str, Any]] = {}
    for coin in TRADE:
        sym = TRADE[coin]
        f = c.filters(sym)
        mins[coin] = float(f.min_notional)
        mark = float(c.mark_price(sym))
        held = pos.get(coin, 0.0)
        if held < 0 and w[coin] > 0:  # 숏 중인데 롱 목표 → 먼저 숏 정리
            lines.append(f"↩ {coin} 롱 목표 {w[coin]:.1%} — 숏 먼저 정리")
            if not dry:
                _q, _p, line = _order(
                    c, coin, "BUY", Decimal(str(-held)), _id("cl", coin, now_ms), reduce=True
                )
                lines.append(line)
                net = _close_short(st, coin, now_ms, float(_p or mark), "LONG_PRIORITY")
                alert.append(f"↩ {coin} 숏 정리 · 추세 다시 켜짐 {net:+,.1f} USDC → 롱으로 돌아감")
                _clear_stop(c, st, coin)
                held = 0.0
        target = EXPOSURE * w[coin] * eq
        cur = max(held, 0.0) * mark
        diff = target - cur
        if abs(diff) < max(BAND * eq, float(f.min_notional)) and not (target == 0 and cur > 0):
            continue
        if diff > 0 and st.get("no_new"):  # 실계좌 정지(낙폭 · 전체 정지) — 줄이기 · 정리만
            lines.append(f"⛔ {coin} 롱 늘리기 {diff:+,.0f} USDC 안 함 — {st['no_new']}")
            alert.append(f"⛔ {coin} 롱 늘리기 안 함 — {st['no_new']}")
            continue
        plans.append((coin, diff))
        side = "BUY" if diff > 0 else "SELL"
        qty = round_qty(abs(diff), mark, f.step_size)
        if target == 0:
            qty = Decimal(str(max(held, 0.0)))
        if qty <= 0 or (float(qty) * mark < float(f.min_notional) and target > 0):
            lines.append(f"⏸ {coin} 롱 조정 {diff:+,.0f} USDC — 최소 주문 금액 아래")
            continue
        lines.append(
            f"📐 {coin} 목표 {w[coin]:.1%} × 4 = {EXPOSURE * w[coin]:.0%} → {target:,.0f} USDC"
            f" (지금 {cur:,.0f})"
        )
        if dry:
            continue
        q, p, line = _order(c, coin, side, qty, _id("lg", coin, now_ms), reduce=side == "SELL")
        lines.append(line)
        if q > 0:
            before = max(held, 0.0)
            done[coin] = {
                "before": before,
                "after": before + float(q) * (1 if side == "BUY" else -1),
                "q": float(q),
                "p": float(p),
                "side": side,
                "target": target,
                "cur": cur,
            }
        elif line.startswith(("⚠", "❌")):
            alert.append(line)
    stops: dict[str, str] = {}
    if not dry:
        _, pos = _equity(c, st)
        acct = c.account()
        for coin in TRADE:
            held_pos = acct.positions.get(TRADE[coin])
            if held_pos is not None and held_pos.quantity > 0:
                line = _replace_stop(c, st, coin, "SELL", held_pos.entry_price * Decimal("0.8"))
                lines.append(line)
                stops[coin] = line
            elif pos.get(coin, 0.0) == 0:
                _clear_stop(c, st, coin)
    for coin, d in done.items():
        alert += long_block(coin, d, info.get(coin), w[coin], stops.get(coin, ""))
    small = blocked_small(plans, mins)
    if small:
        lines.append("💡 100 달러였다면 막혔을 주문: " + " · ".join(small))
    return lines


def long_block(
    coin: str, d: dict[str, Any], info: dict[str, Any] | None, w: float, stop_line: str
) -> list[str]:
    """롱 주문 한 건의 알림 칸 — 무엇을(수량 변화) · 왜(추세 근거) · 비상 스톱 (순수)."""
    if d["after"] <= 0:
        head = f"📉 {coin} 롱 정리  {d['before']:g} → 0  (-{d['q']:g} @ {d['p']:,.2f})"
    else:
        buy = d["side"] == "BUY"
        head = (
            f"{'📈' if buy else '📉'} {coin} 롱 {'늘림' if buy else '줄임'}"
            f"  {d['before']:g} → {d['after']:g} {coin}"
            f"  ({'+' if buy else '-'}{d['q']:g} @ {d['p']:,.2f})"
        )
    out = [head]
    if info is not None:
        body = trend_lines(info, w)
        if w > 0:
            body[-1] += f" → 목표 {d['target']:,.0f} USDC (전 {d['cur']:,.0f})"
        out += body
    if d["after"] <= 0:
        out += [
            f" 👀 이제 {coin} 숏 대기",
            "   4시간봉 RSI7이 70 위에서 꺾이고, 일봉이 하락 추세",
            "   (ADX > 25, -DI > +DI)일 때만 들어감",
        ]
    elif stop_line.startswith("🛡"):
        out.append(f" 🛡 비상 스톱 {stop_line.split()[3]} (평균가 -20%)")
    elif stop_line:
        out.append(f" {stop_line}")
    return [*out, ""]


def _id(kind: str, coin: str, now_ms: int) -> str:
    return f"altair-{kind}-{coin}-{now_ms // MINUTE}"


def _close_short(st: dict[str, Any], coin: str, at_ms: int, px: float, reason: str) -> float:
    """숏 장부 정리 → 순손익(USDC, 수수료 근사 포함)."""
    s = st.setdefault("shorts", {}).pop(coin, None)
    if s is None:
        return 0.0
    net = s["qty"] * (s["entry"] - px) - s["qty"] * (s["entry"] + px) * 0.0005
    st.setdefault("short_trades", []).append(
        {"coin": coin, "entry_ms": s["entry_ms"], "exit_ms": at_ms, "net": net, "reason": reason}
    )
    st["cooldown_ms"] = max(int(st.get("cooldown_ms", 0)), at_ms + 61 * MINUTE)
    st["streak"] = int(st.get("streak", 0)) + 1 if net < 0 else 0
    if st["streak"] >= 4:
        st["cooldown_ms"] = max(st["cooldown_ms"], at_ms + 241 * MINUTE)
        st["streak"] = 0
    st["cum"] = float(st.get("cum", 0.0)) + net
    st["top"] = max(float(st.get("top", 0.0)), st["cum"])
    if st.get("own_halted") is None and st["top"] - st["cum"] >= OWN_LIMIT * float(
        st.get("peak", ALLOC)
    ):
        st["own_halted"] = at_ms
    return float(net)


def _indicators(h4: list[Bar], daily: list[Bar]) -> dict[str, float]:
    from auto_trading.research import indicators_229 as ind

    r7 = ind.rsi([b.c for b in h4], 7)
    adx, pdi, mdi = ind.adx([d.h for d in daily], [d.low for d in daily], [d.c for d in daily])
    return {"rsi_prev": r7[-2], "rsi": r7[-1], "adx": adx[-1], "pdi": pdi[-1], "mdi": mdi[-1]}


def short_block(
    coin: str,
    q: float,
    p: float,
    eq: float,
    k: dict[str, float],
    rate: float,
    notional: float,
    stop: float,
    deadline_ms: int,
) -> list[str]:
    """숏 진입 알림 칸 — 무엇을 · 세 조건 근거 · 크기 계산 · 손절 · 정리 시각 (순수)."""
    risk_pct = SHORT_STOP + RISK_PAD + max(0.0, -rate) * 6
    value = q * p
    return [
        f"🔻 {coin} 숏 진입  {q:g} {coin} @ {p:,.2f}"
        f"  (약 {value:,.0f} USDC = 장부 {value / eq:.0%})",
        " 왜? 세 조건 모두 맞음",
        f"   ① {coin} 롱 목표 0% (추세 꺼짐)",
        f"   ② 4시간봉 RSI7 {k['rsi_prev']:.1f} → {k['rsi']:.1f} (70 위에서 아래로 꺾임)",
        f"   ③ 일봉 ADX {k['adx']:.1f} > 25 · -DI {k['mdi']:.1f} > +DI {k['pdi']:.1f} (하락 추세)",
        f" 크기: 작은 쪽 = min(장부 10%×2배 {eq * 0.2:,.0f},"
        f" 위험 1%÷{risk_pct:.1%} {0.01 * eq / risk_pct:,.0f}) → {notional:,.0f}",
        f" 🛡 손절 {stop:,.2f} (+3%) · 늦어도 {kst(deadline_ms)} 정리 (48시간)",
        "",
    ]


def check_shorts(
    c: BinanceUsdmTestnet,
    st: dict[str, Any],
    now_ms: int,
    *,
    dry: bool = False,
    alert: list[str] | None = None,
    skipped: list[str] | None = None,
) -> list[str]:
    """4 시간봉이 끝날 때마다 — 숏 정리(스톱 · 48 시간 · 롱 목표) · 새 신호."""
    lines: list[str] = []
    alert = alert if alert is not None else []
    skipped = skipped if skipped is not None else []
    api = BinanceUsdmPublic()
    eq, pos = _equity(c, st)
    st["peak"] = max(float(st.get("peak", ALLOC)), eq)
    day = now_ms // DAY
    if st.get("day") != day:
        st["day"], st["day_start"] = day, eq
    w = st.get("long_w") or long_targets(now_ms)[0]
    shorts = st.setdefault("shorts", {})
    try:
        for coin in TRADE:
            held = pos.get(coin, 0.0)
            s = shorts.get(coin)
            mark = float(c.mark_price(TRADE[coin]))
            if s is not None and held >= 0:  # 거래소에서 사라짐 = 스톱 발동
                px = s["entry"] * (1 + SHORT_STOP)
                net = _close_short(st, coin, now_ms, px, "STOP")
                st.setdefault("stops", {}).pop(coin, None)
                lines.append(f"🛑 {coin} 숏 손절(3%) 확인")
                alert.append(
                    f"🛑 {coin} 숏 손절 (+3%)  {s['entry']:,.2f} → {px:,.2f}"
                    f" · {net:+,.1f} USDC → 60분 쉼"
                )
                continue
            if s is not None and now_ms >= s["entry_ms"] + HOLD_MS:
                lines.append(f"⏱ {coin} 숏 48 시간 — 정리")
                if not dry:
                    _q, p, line = _order(
                        c, coin, "BUY", Decimal(str(-held)), _id("cs", coin, now_ms), reduce=True
                    )
                    lines.append(line)
                    exit_px = float(p or mark)
                    net = _close_short(st, coin, now_ms, exit_px, "48H")
                    _clear_stop(c, st, coin)
                    alert.append(
                        f"✅ {coin} 숏 정리 · 48시간 지남  {s['entry']:,.2f} → {exit_px:,.2f}"
                        f" · {net:+,.1f} USDC"
                    )
                continue
            if s is not None or held != 0:
                continue
            h4, daily = _bars(api, SIGNAL[coin], now_ms)
            if not signal_now(h4, daily):
                continue
            why = None
            if st.get("no_new"):
                why = str(st["no_new"])
            elif float(w.get(coin, 0)) > 0:
                why = "롱 목표 있음"
            elif st.get("own_halted") is not None:
                why = "숏 영구 차단 · 누적 손실 2.5%"
            elif now_ms < int(st.get("cooldown_ms", 0)):
                why = "쉬는 중"
            elif eq <= float(st.get("day_start", eq)) * 0.98:
                why = "오늘 손실 2%"
            head = f"📉 {coin} 숏 신호 (RSI7 70 꺾임 · 일봉 하락 추세)"
            lines.append(head + (f" — {why}" if why else ""))
            if why:
                skipped.append(f"{coin}({why})")
                continue
            open_n = sum(abs(x["qty"]) * float(c.mark_price(TRADE[k])) for k, x in shorts.items())
            rates = api.funding(SIGNAL[coin], now_ms - DAY, now_ms)
            rate = rates[-1][1] if rates else 0.0
            notional = short_notional(eq, open_n, rate)
            f = c.filters(TRADE[coin])
            qty = round_qty(notional, mark, f.step_size)
            if float(qty) * mark < float(f.min_notional):
                lines.append(f"⏸ {coin} 숏 {notional:,.0f} USDC — 최소 주문 금액 아래")
                skipped.append(f"{coin}(최소 주문 금액 아래)")
                continue
            if notional * SMALL / ALLOC < float(f.min_notional):
                small = notional * SMALL / ALLOC
                lines.append(f"💡 100 달러였다면 이 숏({small:,.0f} USDC)은 막힘")
            if dry:
                continue
            q, p, line = _order(c, coin, "SELL", qty, _id("os", coin, now_ms), reduce=False)
            lines.append(line)
            if q <= 0:
                alert.append(line)
                continue
            shorts[coin] = {"qty": float(q), "entry": float(p), "entry_ms": now_ms}
            stop = p * Decimal(str(1 + SHORT_STOP))
            lines.append(_replace_stop(c, st, coin, "BUY", stop))
            alert += short_block(
                coin,
                float(q),
                float(p),
                eq,
                _indicators(h4, daily),
                rate,
                notional,
                float(stop),
                now_ms + HOLD_MS,
            )
    finally:
        api.close()
    return lines


# ------------------------------------------------------------------ 명령


def alert_text(
    now_ms: int,
    blocks: list[str],
    skipped: list[str],
    long_w: dict[str, float],
    shorts_open: bool,
    rows: list[tuple[str, float, float, float]],
    eq: float,
    alloc: float,
    title: str = "🧪 ☄️ Altair 선물 데모 · 가짜 돈",
) -> str:
    """텔레그램 한 통 (순수) — 무엇을 · 왜 · 지금 포지션."""
    body = list(blocks)
    while body and not body[-1]:
        body.pop()
    out = [f"{title} · {kst(now_ms)}", "━━━━━━━━━━━━", *body, ""]
    if skipped:
        out.append("🚫 숏 신호 났지만 안 함: " + " · ".join(skipped))
    elif not shorts_open and long_w and all(v > 0 for v in long_w.values()):
        out.append("🚫 숏 없음 — BTC·ETH 모두 롱 목표가 있어 규칙상 숏 안 함")
    out += ["", *position_lines(rows, eq, alloc)]
    return "\n".join(out)


def tick(now_ms: int | None = None) -> list[str]:
    """할 차례인 일만: 하루 첫 실행(00:05 UTC 뒤) 롱 맞추기, 4 시간봉 마감(+3 분) 뒤 숏 점검.

    주문 · 손절 · 정리 · 오류가 있을 때만 알린다(2026-10-15 사용자 승인 형식)."""
    now_ms = now_ms or int(time.time() * 1000)
    st = _load()
    lines: list[str] = []
    alert: list[str] = []
    skipped: list[str] = []
    day = now_ms // DAY
    bar = now_ms // H4
    due_long = st.get("long_day") != day and now_ms % DAY >= 5 * MINUTE
    due_short = st.get("short_bar") != bar and now_ms % H4 >= 3 * MINUTE
    if not (due_long or due_short):
        return []
    c = _client()
    try:
        if not st.get("setup"):
            for sym in TRADE.values():
                c.set_leverage(sym, LEVERAGE)
            st["setup"] = True
            st["start_ms"] = now_ms
        if not st.get("cross"):  # 거래소 기본값에 기대지 않고 cross 로 못 박는다(10-15 사용자 논의)
            st["cross"] = _ensure_cross(c)
        if due_long:
            lines += rebalance_long(c, st, now_ms, alert=alert)
            st["long_day"] = day
        if due_short:
            lines += check_shorts(c, st, now_ms, alert=alert, skipped=skipped)
            st["short_bar"] = bar
        eq, _pos = _equity(c, st)
        acct = c.account()
        rows = [
            (
                coin,
                float(acct.positions[sym].quantity),
                float(acct.positions[sym].entry_price),
                float(c.mark_price(sym)),
            )
            for coin, sym in TRADE.items()
            if sym in acct.positions
        ]
        st.setdefault("history", []).append([now_ms, round(eq, 2)])
        st["history"] = st["history"][-2000:]
    finally:
        c.close()
        _save(st)
    if any(x.strip() for x in alert):
        long_w = st.get("long_w") or {}
        _notify(alert_text(now_ms, alert, skipped, long_w, bool(st.get("shorts")), rows, eq, ALLOC))
    return lines


def plan() -> dict[str, Any]:
    now_ms = int(time.time() * 1000)
    st = _load()
    c = _client()
    try:
        lines = rebalance_long(c, dict(st), now_ms, dry=True)
        lines += check_shorts(c, dict(st), now_ms, dry=True)
        eq, pos = _equity(c, dict(st))
    finally:
        c.close()
    return {"equity": round(eq, 2), "positions": pos, "lines": lines}


def status() -> dict[str, Any]:
    st = _load()
    hist = st.get("history") or []
    return {
        "start": _iso(st["start_ms"]) if st.get("start_ms") else None,
        "equity": hist[-1][1] if hist else None,
        "margin": "CROSSED" if st.get("cross") else "아직 설정 전",
        "long_w": st.get("long_w"),
        "shorts": st.get("shorts"),
        "short_trades": len(st.get("short_trades") or []),
        "own_halted": st.get("own_halted"),
    }


def main(argv: list[str]) -> int:
    if argv == ["plan"]:
        out: Any = plan()
    elif argv == ["tick"]:
        out = tick() or ["할 차례 아님"]
    elif argv == ["status"]:
        out = status()
    else:
        raise SystemExit("usage: python -m scripts.altair_demo plan | tick | status")
    sys.stdout.write(json.dumps(out, ensure_ascii=False, indent=1, default=str) + "\n")
    return 0


if __name__ == "__main__":
    for _stream in (sys.stdout, sys.stderr):
        _reconfigure = getattr(_stream, "reconfigure", None)
        if _reconfigure is not None:
            _reconfigure(encoding="utf-8", errors="replace")
    raise SystemExit(main(sys.argv[1:]))
