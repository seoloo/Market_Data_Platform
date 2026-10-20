"""🌌 Vega 실행기 — 업비트 BTC · ETH · SOL 원화 추세 따라가기 **실거래** (T/v2, 2026-10-09 결정).

언제 실행: 매일 09:05 KST ``AutoTrading_Vega`` (``run``, 일봉 마감 5 분 뒤) · 토 09:30
           ``AutoTrading_Vega_Weekly`` (``weekly``).
읽고 쓰는 것: 업비트 API → 장부 ``data/upbit_t2/state.json`` ·
              보고 ``reports/upbit_t2/<날짜>.json`` · 알림 텔레그램 ·
              기록 ``reports/upbit_t2/outbox.log``.

사용 예::

    python -m scripts.vega_upbit plan    # 계획만 — 주문 · 기록 · 알림 없음 (첫날 확인)
    python -m scripts.vega_upbit run     # 실행 (예약 매일 09:05 KST, 일봉 마감 5 분 뒤)
    python -m scripts.vega_upbit status
    python -m scripts.vega_upbit release-dd "사유"  # 계좌 낙폭 30% 정지 해제 (손으로)

* 사용자 (2026-10-09): "그냥 지금 paper 돌고 있어도 실거래 진행하는 것이 맞는 것 같아", 업비트,
  BTC + ETH + SOL, 목표 합이 100% 넘으면 비율대로 줄임, 적립식으로 30 만 원부터.
* 규칙 · 계산은 :mod:`auto_trading.ops.t2_upbit`. 결정 = 방금 마감된 일봉(00:00 UTC = 09:00 KST),
  그 자리에서 체결한다.
* 주문은 **지정가 IOC 만** (2026-10-09 당시 사용자 규칙 '시장가 금지' — 10-18 에 지워졌지만 그대로).
  가격은 호가창 세 번째 칸(매수 = 매도 호가 3, 매도 = 매수 호가 3) — 바로 체결되되 멀리
  미끄러지지 않게. 남은 수량은 다음 날 밴드로 다시 본다.
* 매도 먼저, 매수는 가진 원화 안에서. 원화 계좌 전체를 이 전략의 현금으로 본다
  (다른 용도의 원화는 업비트에 두지 않는다). BTC · ETH · SOL 외 코인은 건드리지 않는다.
* 정지 파일(``data/HALT`` · ``HALT_vega``)이면 주문하지 않는다.
  계좌 낙폭(NAV) 30% 면 새 매수를 멈춘다.
  정지는 **BTC 목표 비중이 0(추세 꺼짐)이 된 뒤 다시 0 보다 커지면 스스로 풀고** 고점을 그날
  단위 가치로 다시 잡는다 (옛 바이낸스판 ``retired/scripts/vega_live.py`` 와 같은 규칙, 2026-10-18).
  정지 30 일째부터 30 일마다 텔레그램으로 다시 알린다. 손으로는 ``release-dd``.
* 2026-10-19 사용자 "altair는 정리하고 vega를 업비트로 옮기도록 하자 · deneb:vega = 90:10" →
  바이낸스에서 업비트로 되돌림(10-15 의 반대). 예약 ``AutoTrading_Vega`` 가 이 실행기를 부른다.
"""

from __future__ import annotations

import json
import sys
import time
from dataclasses import asdict
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import httpx

from auto_trading.config.secrets import load_telegram_credentials, read_env_file
from auto_trading.core.errors import AutoTradingError, ExchangeError, OrderStateUnknownError
from auto_trading.exchange.upbit import UpbitClient, UpbitOrder, upbit_trading_enabled
from auto_trading.notify.channels import NotifyError, OutboxNotifier, TelegramNotifier
from auto_trading.notify.style import RULE, sub
from auto_trading.ops import user_settings
from auto_trading.ops.kill_switch import halted_for
from auto_trading.ops.ledger_file import write_json_atomic
from auto_trading.ops.t2_upbit import (
    COINS,
    DD_STOP,
    MARKETS,
    MIN_KRW,
    STEP,
    books,
    equity,
    nav_step,
    plan,
    reason,
    score_calc,
    score_of,
    score_text,
    signals,
    signs_of,
    stop_reminder,
    targets,
    trend_arrows,
    trend_icons,
)

REPO = Path(__file__).resolve().parent.parent
DATA = REPO / "data" / "upbit_t2"
STATE = DATA / "state.json"
REPORTS = REPO / "reports" / "upbit_t2"
HALT = REPO / "data" / "HALT"
DAY_MS = 86_400_000
FEE = Decimal("0.0005")
"""업비트 원화 마켓 수수료 0.05%."""
LEVEL = 2
"""호가창 몇 번째 칸(0 부터) 가격으로 IOC 를 낼지."""
CASH_USE = Decimal("0.995")
"""살 때 원화의 99.5% 까지만 쓴다 — 수수료 · 가격 움직임 여유."""
TAG = "🌌 [Vega · T/v2 업비트]"
"""2026-10-09 사용자: 업비트 T/v2 실거래 이름 = **Vega**. 내부 파일 · 예약 이름은 그대로."""


def _client() -> UpbitClient:
    """[쓰임] 업비트 창구 — ``.env`` 의 ``UPBIT_TRADING_ENABLED=true`` 일 때만 주문이 열린다."""
    env = read_env_file(REPO / ".env")
    return UpbitClient(
        env["UPBIT_API_KEY"],
        env["UPBIT_AOU_SECRET"],
        trading_enabled=upbit_trading_enabled(env),
    )


def _notify(text: str) -> None:
    """[쓰임] 알림 — 기록 파일에 남기고 텔레그램으로도 (실패해도 실행은 계속)."""
    REPORTS.mkdir(parents=True, exist_ok=True)
    OutboxNotifier(REPORTS / "outbox.log").send(text)
    credentials = load_telegram_credentials(REPO / ".env")
    if credentials is None:
        return
    try:
        TelegramNotifier(credentials).send(text)
    except NotifyError as exc:
        print(f"텔레그램 실패: {exc}")


def _load() -> dict[str, Any]:
    """[쓰임] Vega 장부 (없으면 빈 장부)."""
    if STATE.exists():
        return dict(json.loads(STATE.read_text(encoding="utf-8")))
    return {"units": "0", "peak": "0", "stopped": False, "history": [], "orders": []}


def _save(state: dict[str, Any]) -> None:
    write_json_atomic(STATE, state, default=str)


def _snapshot(client: UpbitClient) -> tuple[Decimal, dict[str, Decimal], dict[str, Decimal]]:
    """[쓰임] 지금 (원화, 코인 보유, 코인 현재가)."""
    balances = client.balances()
    tickers = client.tickers(list(MARKETS.values()))
    return (
        balances.get("KRW", Decimal(0)),
        {coin: balances.get(coin, Decimal(0)) for coin in COINS},
        {coin: tickers[MARKETS[coin]] for coin in COINS},
    )


def _decision(now_ms: int) -> tuple[int, str]:
    """[쓰임] 판단 기준 = 방금 마감된 일봉 끝(ms)과 그 날짜(UTC)."""
    decision_ms = now_ms - now_ms % DAY_MS - 1
    return decision_ms, datetime.fromtimestamp(decision_ms / 1000, UTC).strftime("%Y-%m-%d")


def trend_ready(state: dict[str, Any], btc_weight: Decimal | None) -> bool:
    """[쓰임] 낙폭 정지를 풀 때인가 — 오늘 BTC 목표가 0 이면 '꺼짐 봄' 을 적고,
    꺼짐을 본 뒤 0 보다 커지면 True.
    [이유] 낙폭 바닥 근처에서 바로 다시 사지 않고, 추세가 한 번 꺼졌다 다시 켜질 때 풀려고
           (옛 바이낸스판과 같은 규칙, 사용자 2026-10-18)."""
    if not state.get("stopped") or btc_weight is None:
        return False
    if btc_weight <= 0:
        state["halt_off_seen"] = True
        return False
    return bool(state.get("halt_off_seen"))


def _plan_today(client: UpbitClient, state: dict[str, Any], now_ms: int) -> dict[str, Any]:
    """[쓰임] 오늘 판단 전부 — 잔고 · 단위 가치(입출금 걸러 냄) · 목표 비중 · 정지 · 주문 의도.
    [이유] 장부는 바꾸지 않는다(정지 풀기 '꺼짐 봄' 표시만) — ``plan`` 명령도 같은 함수를 쓴다."""
    decision_ms, day = _decision(now_ms)
    krw, holdings, prices = _snapshot(client)
    expected = state.get("expected") or {}
    nav = nav_step(
        units=Decimal(state["units"]),
        peak=Decimal(state["peak"]),
        expected_krw=Decimal(str(expected.get("KRW", krw))),
        expected={coin: Decimal(str(expected.get(coin, holdings[coin]))) for coin in COINS},
        krw=krw,
        holdings=holdings,
        prices=prices,
    )
    daily = {coin: client.closed_daily(MARKETS[coin], decision_ms) for coin in COINS}
    weights = targets(daily, decision_ms)
    released = trend_ready(state, weights.get("BTC"))
    newly_deep = Decimal(state["units"]) > 0 and nav.dd >= DD_STOP
    stopped = not released and (bool(state.get("stopped")) or newly_deep)
    account_value = equity(krw, holdings, prices)
    intents = plan(weights, holdings, prices, account_value, allow_buy=not stopped)
    return {
        "day": day,
        "krw": krw,
        "hold": holdings,
        "prices": prices,
        "equity": account_value,
        "nav": nav,
        "stopped": stopped,
        "released": released,
        "weights": weights,
        "signals": signals(daily, decision_ms),
        "intents": intents,
    }


def kst_day(decision_day: str) -> str:
    """[쓰임] 결정일(UTC 마감 일봉) → 그 결정을 실행한 한국 날짜(다음 날 09:05)."""
    from datetime import date, timedelta

    return (date.fromisoformat(decision_day) + timedelta(days=1)).isoformat()


def _weights_text(weights: dict[str, Decimal | None]) -> str:
    """[쓰임] 'BTC 30.0% · ETH ? · SOL 12.5%' (판단 불가는 ?)."""
    parts = []
    for coin, weight in weights.items():
        parts.append(f"{coin} {'?' if weight is None else f'{weight:.1%}'}")
    return " · ".join(parts)


def plan_cmd() -> int:
    """[쓰임] 계획만 출력 — 주문 · 장부 · 알림 없음 (첫날 확인용)."""
    client = _client()
    try:
        today = _plan_today(client, _load(), int(time.time() * 1000))
    finally:
        client.close()
    print(f"[계획만 — 주문 없음] 결정일 {today['day']}")
    print(f"원화 {today['krw']:,.0f} · 보유 {today['hold']} · 평가액 {today['equity']:,.0f} 원")
    print(f"목표 비중: {_weights_text(today['weights'])}")
    print(f"낙폭 {today['nav'].dd:.1%} · 매수 정지 {today['stopped']}")
    for intent in today["intents"]:
        print(
            f"  {intent.side.value} {intent.symbol} {intent.quantity}"
            f" (≈ {intent.quantity * today['prices'][intent.symbol]:,.0f} 원)"
        )
    if not today["intents"]:
        print("  주문 없음 (밴드 안 · 최소 주문 5,000 원 미만)")
    return 0


def _send(
    client: UpbitClient, coin: str, side: str, qty: Decimal, identifier: str
) -> tuple[UpbitOrder | None, str]:
    """[쓰임] 지정가 IOC 한 건 → (주문 결과, 알림 줄).
    [이유] 같은 ``identifier`` 가 이미 있으면 다시 보내지 않는다 —
           결과를 모르는 주문을 두 번 내지 않게.
           보낸 뒤 0.5 초씩 최대 10 번 결과를 확인한다."""
    done = client.get_order(identifier)
    if done is None:
        asks, bids = client.orderbook(MARKETS[coin])
        levels = asks if side == "bid" else bids
        price = levels[min(LEVEL, len(levels) - 1)]
        try:
            client.place_limit_ioc(MARKETS[coin], side, qty, price, identifier)
        except OrderStateUnknownError as exc:
            return None, f"⚠ {coin} 주문 결과 모름 — 다시 보내지 않는다: {exc}"
        except ExchangeError as exc:
            return None, f"❌ {coin} 주문 거절 — {exc}"
        for _ in range(10):
            time.sleep(0.5)
            done = client.get_order(identifier)
            if done is not None and done.state in ("done", "cancel"):
                break
    if done is None:
        return None, f"⚠ {coin} 주문 확인 안 됨 — 내일 잔고로 확인"
    word = "매수" if side == "bid" else "매도"
    if done.executed_volume <= 0:
        return done, f"⏸ {coin} {word} 체결 없음 (IOC) — 내일 다시 판단"
    return done, (
        f"🛒 {coin} {word} {done.executed_volume:g} · {done.funds:,.0f} 원"
        f" (수수료 {done.paid_fee:,.0f})"
    )


def _record_flows(state: dict[str, Any], today: dict[str, Any], lines: list[str]) -> None:
    """[쓰임] 입금 · 출금을 장부 ``deposits`` 에 적는다 (첫 실행이면 그때 평가액 = 첫 입금)."""
    nav = today["nav"]
    deposits = state.setdefault("deposits", [])
    if Decimal(state["units"]) <= 0 and not deposits:
        deposits.append([today["day"], str(today["equity"])])  # 첫 실행: 그때 평가액 = 첫 입금
    elif nav.flow and abs(nav.flow) >= 1:
        deposits.append([today["day"], str(nav.flow)])
        word = "입금" if nav.flow > 0 else "출금"
        lines.append(f"💰 {word} 감지 {nav.flow:+,.0f} 원 → 총 입금에 반영")


def _update_stop(state: dict[str, Any], today: dict[str, Any], lines: list[str]) -> None:
    """[쓰임] 낙폭 정지 풀림 · 걸림 · 30 일마다 다시 알림을 장부에 반영하고 알림 줄을 더한다."""
    nav = today["nav"]
    if today["released"]:
        lines.append(
            "▶ 계좌 낙폭 정지 스스로 풀림 — BTC 추세가 꺼졌다 다시 켜짐, 고점을 지금 단위 가치로"
        )
        state.setdefault("releases", []).append(
            [datetime.now(UTC).isoformat(timespec="seconds"), "BTC 추세 다시 켜짐 (자동)"]
        )
        for key in ("stopped_since", "stop_reminded", "halt_off_seen"):
            state.pop(key, None)
    if today["stopped"] and not state.get("stopped"):
        lines.append(
            f"🛑 계좌 낙폭 {nav.dd:.1%} ≥ 30% — 새 매수 멈춤"
            " (BTC 추세가 꺼졌다 다시 켜지면 스스로 풀림)"
        )
        state["stopped_since"], state["stop_reminded"] = today["day"], []
        btc_weight = today["weights"].get("BTC")
        state["halt_off_seen"] = btc_weight is not None and btc_weight <= 0
    state.update(
        units=str(nav.units),
        peak=str(nav.nav if today["released"] else nav.peak),
        stopped=today["stopped"],
        last_nav=str(nav.nav),
    )
    if today["stopped"]:
        since = state.setdefault("stopped_since", today["day"])
        due_days = stop_reminder(since, today["day"], state.setdefault("stop_reminded", []))
        if due_days:
            state["stop_reminded"].append(due_days)
            lines.extend(stop_remind_lines(due_days))


def _record_order(
    state: dict[str, Any], identifier: str, line: str, order: UpbitOrder | None
) -> None:
    """[쓰임] 장부 ``orders`` 에 한 줄 (주문 결과가 있으면 그 칸들까지)."""
    details = {} if order is None else asdict(order)
    state["orders"].append({"id": identifier, "line": line, **details})


def run_cmd() -> int:
    """[쓰임] 하루 실행 (09:05) — 입출금 · 정지 반영 → 팔 것 → 살 것 → 비중 설명 · 손익 알림.
    [이유] 같은 날 두 번 실행해도 장부를 건드리지 않는다 (아래 '이미 실행함').
           주문 · 입출금 · 정지가 있던 날만 알린다."""
    # 2026-10-15 바이낸스로 옮기며 걸었던 CRUISE_UPBIT 잠금은 10-19 업비트로 되돌리며 뺐다.
    now_ms = int(time.time() * 1000)
    state = _load()
    halted = halted_for(HALT, "vega")  # 전체 정지 또는 Vega 정지(전부 정리 뒤)
    client = _client()
    lines: list[str] = []
    fill_lines: list[str] = []
    adjust: list[str] = []
    try:
        today = _plan_today(client, state, now_ms)
        nav = today["nav"]
        if state.get("last_decision") == today["day"]:
            # 같은 날 다시 실행: 장부를 건드리지 않는다. 예전엔 여기서 입출금을 기록하고
            # 단위를 늘린 뒤 '있어야 할 잔고'(expected)는 그대로 둬서, 다음 날 같은 차액을
            # 또 입금으로 셌다(10-08 · 09 소액 569 원이 두 번 → 총 입금 301,139 원,
            # 사용자 2026-10-12 발견).
            print(f"{today['day']} 이미 실행함")
            return 0
        # 1 단계: 입출금 · 낙폭 정지 반영
        _record_flows(state, today, lines)
        _update_stop(state, today, lines)
        if halted:
            lines.append(f"⏸ 긴급 정지 중 — 주문 안 함 ({halted})")
            intents = []
        else:
            intents = today["intents"]
        # 2 단계: 팔 것 먼저
        fills: dict[str, Decimal] = {}
        for intent in [item for item in intents if item.side.value == "SELL"]:
            identifier = f"t2u-{today['day']}-{intent.symbol}-S"
            order, line = _send(client, intent.symbol, "ask", intent.quantity, identifier)
            fill_lines.append(line)
            _record_order(state, identifier, line, order)
            if order is not None and order.executed_volume > 0:
                fills[intent.symbol] = -order.funds
        # 3 단계: 살 것 — 판 뒤의 원화 안에서
        buys = [item for item in intents if item.side.value == "BUY"]
        if buys:
            krw, _, prices = _snapshot(client)
            cash = krw * CASH_USE
            for intent in buys:
                qty = min(intent.quantity, (cash / (prices[intent.symbol] * (1 + FEE * 4))))
                qty = (qty / STEP).to_integral_value(rounding="ROUND_DOWN") * STEP
                if qty * prices[intent.symbol] < MIN_KRW:
                    lines.append(f"⏸ {intent.symbol} 매수 건너뜀 — 원화 부족 · 5,000 원 미만")
                    continue
                identifier = f"t2u-{today['day']}-{intent.symbol}-B"
                order, line = _send(client, intent.symbol, "bid", qty, identifier)
                fill_lines.append(line)
                _record_order(state, identifier, line, order)
                if order is not None:
                    cash -= order.funds + order.paid_fee
                    if order.executed_volume > 0:
                        fills[intent.symbol] = order.funds
        # 4 단계: 비중 설명 · 장부 마감 ('있어야 할' 잔고 = 지금 잔고)
        adjust = adjust_lines(state, today, fills, nav.flow)
        krw, holdings, prices = _snapshot(client)
        account_value = equity(krw, holdings, prices)
        state["expected"] = {"KRW": str(krw), **{coin: str(qty) for coin, qty in holdings.items()}}
        state["last_decision"] = today["day"]
        state["history"].append([today["day"], str(nav.nav), str(account_value), str(nav.dd)])
        _save(state)
    except AutoTradingError as exc:
        text = f"⚠ {TAG} 실패\n{RULE}\n{exc}"
        _notify(text)
        print(text)
        return 1
    finally:
        client.close()
    # 5 단계: 보고 · 알림
    report = {
        "day": today["day"],
        "weights": {
            coin: None if weight is None else str(weight)
            for coin, weight in today["weights"].items()
        },
        "equity": str(account_value),
        "nav": str(nav.nav),
        "dd": str(nav.dd),
        "lines": lines,
    }
    REPORTS.mkdir(parents=True, exist_ok=True)
    (REPORTS / f"{today['day']}.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    text = "\n".join(
        [
            f"{TAG} · {kst_day(today['day'])}",
            RULE,
            *lines,
            *(["", "📋 오늘 비중 조절", *adjust] if adjust else []),
            *(["", "🛒 체결", *[sub(line) for line in fill_lines]] if fill_lines else []),
            "",
            *pnl_lines(state, krw, holdings, prices, account_value, nav.dd, avg_prices(client)),
        ]
    )
    print(text)
    # 주문 · 입출금 · 정지가 있던 날만 알린다. 설정에서 매매 알림을 꺼도 정지 · 경고는 보낸다.
    risky = any("정지" in line or "⚠" in line for line in lines)
    if (lines or fill_lines) and (user_settings.get(REPO, "alerts.vega_trade") or risky):
        _notify(text)
    return 0


def _signal_record(signal: Any) -> dict[str, Any]:
    """[쓰임] 마지막으로 사고판 날의 판단 근거 (장부 ``last_trade_signals`` 한 칸)."""
    return {
        "ups": str(signal.ups),
        "vol": str(signal.vol),
        "raw": str(signal.raw),
        "weight": str(signal.weight),
        "parts": [str(value) for value in signal.parts],
    }


def _previous_score(previous: dict[str, Any] | None) -> Decimal | None:
    """[쓰임] 지난번 기록의 점수 (옛 기록은 수익률이 없어 '상승 개수' 로 셈)."""
    if previous is None:
        return None
    if previous.get("parts"):
        signs = []
        for value in previous["parts"]:
            number = Decimal(value)
            signs.append(1 if number > 0 else -1 if number < 0 else 0)
        return score_of(signs)
    ups = int(previous.get("ups", "0"))
    return Decimal(ups - (3 - ups)) / 3


def adjust_lines(
    state: dict[str, Any], today: dict[str, Any], fills: dict[str, Decimal], flow: Decimal
) -> list[str]:
    """[쓰임] '📋 오늘 비중 조절' — 코인마다 '지금 비중 → 목표 · 산 · 판 금액' + 추세 세 개 +
    점수 계산.
    [이유] 사용자 2026-10-12 시안 B (C+ 는 너무 길어 되돌림). 점수가 그대로인데 매매했으면
           (변동성 · 합 100 % · 입출금 · 가격 이동) 그 까닭 한 줄을 더한다.
           점수 변화는 그 코인을 **마지막으로 사고판 날**의 판단과 비교한다
           (밴드 때문에 며칠 쌓인 변화)."""
    last_signals = state.setdefault("last_trade_signals", {})
    account_value = today["equity"]
    lines: list[str] = []
    for coin in COINS:
        signal = today["signals"].get(coin)
        current_weight = Decimal(0)
        if account_value > 0:
            current_weight = today["hold"][coin] * today["prices"][coin] / account_value
        if signal is None:
            continue
        score = score_of(signs_of(signal))
        if coin in fills:
            previous = last_signals.get(coin)
            icon, why = reason(coin, signal, previous, current_weight, flow=flow)
            word = "매수" if fills[coin] > 0 else "매도"
            full = " (전량)" if signal.weight == 0 else ""
            lines.append(
                f"{icon} {coin} {current_weight:.1%} → {signal.weight:.1%} · {word}{full}"
                f" {abs(fills[coin]):,.0f}원"
            )
            lines.append(sub(f"추세: {trend_icons(signal)}"))
            previous_score = _previous_score(previous)
            if previous_score is not None and previous_score == score:
                lines.append(sub(why))
            lines.append(sub(score_calc(signal, previous_score)))
            last_signals[coin] = _signal_record(signal)
        else:
            lines.append(
                f"➖ {coin} {current_weight:.1%} 그대로 · 추세 {trend_arrows(signal)}"
                f" 점수 {score_text(score)}"
                f" · 목표와 차이 {abs(signal.weight - current_weight):.1%}p"
            )
        last_signals.setdefault(coin, _signal_record(signal))
    if lines:
        lines.append(sub("목표와 5%p 넘게 벌어질 때만 사고팔아요"))
    return lines


def avg_prices(client: Any) -> dict[str, Decimal] | None:
    """[쓰임] 업비트 앱의 코인별 매수 평균가. 조회 못 하면 None (그때는 주문 기록 원가)."""
    get_accounts = getattr(client, "accounts", None)
    if get_accounts is None:
        return None
    try:
        accounts = get_accounts()
    except (AutoTradingError, httpx.HTTPError):
        return None
    return {coin: accounts[coin][1] for coin in COINS if coin in accounts}


def pnl_lines(
    state: dict[str, Any],
    krw: Decimal,
    holdings: dict[str, Decimal],
    prices: dict[str, Decimal],
    account_value: Decimal,
    dd: Decimal,
    average: dict[str, Decimal] | None = None,
) -> list[str]:
    """[쓰임] '💼 총 입금 → 평가 · 손익' + 코인별 평가 손익 + 원화 · 낙폭 (2026-10-09 사용자 요청).
    [이유] ``average`` (업비트 매수 평균가)가 있으면 코인별 원가 = 보유 × 평균가 — 업비트 앱과 같은
           숫자 (2026-10-20: 정리 기능으로 판 매도가 주문 기록에 없어 옛 원가가 남던 문제)."""
    paid = sum((Decimal(str(row[1])) for row in state.get("deposits", [])), Decimal(0))
    coin_books = books(state.get("orders", []))
    coin_lines = []
    for coin in COINS:
        value = holdings[coin] * prices[coin]
        if average and average.get(coin):
            cost = holdings[coin] * average[coin]
        else:
            cost = coin_books[coin].cost
        if value <= 0 and (cost <= 0 or average is not None):
            continue  # 업비트 평균가를 쓰면 보유가 없을 때 원가도 없다
        gain = value - cost
        percent = f" ({gain / cost:+.1%})" if cost > 0 else ""
        coin_lines.append(f"{coin} {value:,.0f} 원 · 원가 {cost:,.0f} · {gain:+,.0f}{percent}")
    total = account_value - paid
    percent = f" ({total / paid:+.1%})" if paid > 0 else ""
    return [
        f"💼 총 입금 {paid:,.0f} 원 → 평가 {account_value:,.0f} 원 · 손익 {total:+,.0f}{percent}",
        *[sub(line) for line in coin_lines],
        sub(f"원화 {krw:,.0f} 원 · 낙폭 {dd:.1%}"),
    ]


def _trades_since(state: dict[str, Any], start_day: str) -> list[dict[str, Any]]:
    """[쓰임] ``start_day`` 뒤 체결이 있던 주문 (주문 번호 ``t2u-<날짜>-…`` 의 날짜로 고른다)."""
    trades = []
    for order in state.get("orders", []):
        order_day = str(order.get("id", ""))[4:14]
        filled = Decimal(str(order.get("executed_volume") or "0")) > 0
        if order_day >= start_day and filled:
            trades.append(order)
    return trades


def weekly_cmd() -> int:
    """[쓰임] 토요일 주간 요약 (2026-10-09 사용자 "주간 요약 같은 건 토요일에"). 주문 없음.
    [이유] 이번 주 수익률은 입출금을 뺀 단위 가치로 잰다 (입금을 수익으로 세지 않게)."""
    from datetime import date, timedelta

    state = _load()
    client = _client()
    try:
        now_ms = int(time.time() * 1000)
        decision_ms, day = _decision(now_ms)
        krw, holdings, prices = _snapshot(client)
        daily = {coin: client.closed_daily(MARKETS[coin], decision_ms) for coin in COINS}
        coin_signals = signals(daily, decision_ms)
        average = avg_prices(client)
    finally:
        client.close()
    # 1 단계: 이번 주 단위 가치 · 입금 · 매매
    account_value = equity(krw, holdings, prices)
    end = date.fromisoformat(day)
    start = end - timedelta(days=6)
    label_start = date.fromisoformat(kst_day(start.isoformat()))
    label_end = date.fromisoformat(kst_day(day))
    history = state.get("history", [])
    units = Decimal(str(state.get("units") or "0"))
    nav_now = account_value / units if units > 0 else Decimal(1000)
    before = [row for row in history if row[0] < start.isoformat()]
    nav_then = Decimal(str(before[-1][1])) if before else Decimal(1000)
    week_return = nav_now / nav_then - 1
    deposited = sum(
        (Decimal(str(row[1])) for row in state.get("deposits", []) if row[0] >= start.isoformat()),
        Decimal(0),
    )
    trades = _trades_since(state, start.isoformat())
    trade_text = " · ".join(
        f"{kst_day(str(order['id'])[4:14])[5:].replace('-', '/')} {str(order['id']).split('-')[-2]}"
        f" {'매수' if order.get('side') == 'bid' else '매도'}"
        for order in trades
    )
    # 2 단계: 코인별 줄
    rows = []
    for coin in COINS:
        signal = coin_signals.get(coin)
        if signal is None:
            continue
        held_weight = (
            holdings[coin] * prices[coin] / account_value if account_value > 0 else Decimal(0)
        )
        score = score_of(signs_of(signal))
        icon = "🟢" if score > 0 else "🔴"
        rows.append(
            sub(
                f"{icon} {coin} 주간 {signal.week:+.1%} · 추세 {trend_arrows(signal)}"
                f" 점수 {score_text(score)} · 목표 {signal.weight:.1%} · 보유 {held_weight:.1%}"
            )
        )
    peak = Decimal(str(state.get("peak") or "0"))
    dd = max(Decimal(0), 1 - nav_now / peak) if peak > 0 else Decimal(0)
    text = "\n".join(
        [
            f"📅 [Vega 주간 요약] {label_start:%m/%d} ~ {label_end:%m/%d}",
            RULE,
            f"📈 이번 주 수익률 {week_return:+.1%}"
            f" (입출금 뺀 단위 가치 {nav_then:,.1f} → {nav_now:,.1f})",
            f"💰 이번 주 입금 {deposited:+,.0f} 원",
            f"🛒 이번 주 매매 {len(trades)}건" + (f": {trade_text}" if trades else ""),
            kimp_line(prices["BTC"]),
            "",
            "코인별 (주간 가격 · 추세 20·60·120일 · 목표 · 보유)",
            *rows,
            "",
            *pnl_lines(state, krw, holdings, prices, account_value, dd, average),
        ]
    )
    print(text)
    if user_settings.get(REPO, "alerts.vega_weekly"):
        _notify(text)
    return 0


def status_cmd() -> int:
    """[쓰임] 장부 요약 + 지금 잔고로 손익 (조회만)."""
    state = _load()
    stop_since = ""
    if state.get("stopped") and "stopped_since" in state:
        stop_since = f" (시작 {state['stopped_since']})"
    print(
        f"마지막 실행 {state.get('last_decision')} · 단위 가치 {state.get('last_nav')}"
        f" · 고점 {state.get('peak')}"
        f" · 매수 정지 {state.get('stopped')}" + stop_since
    )
    for row in state.get("history", [])[-10:]:
        print("  ", row)
    client = _client()
    try:
        krw, holdings, prices = _snapshot(client)
        average = avg_prices(client)
    finally:
        client.close()
    account_value = equity(krw, holdings, prices)
    peak = Decimal(str(state.get("peak") or "0"))
    units = Decimal(str(state.get("units") or "0"))
    dd = 1 - (account_value / units) / peak if units > 0 and peak > 0 else Decimal(0)
    print(
        "\n".join(
            pnl_lines(state, krw, holdings, prices, account_value, max(dd, Decimal(0)), average)
        )
    )
    return 0


def _get_json(url: str) -> Any:
    """[쓰임] 공개 시세 JSON (야후는 User-Agent 가 없으면 429 를 준다)."""
    with httpx.Client(timeout=10.0, headers={"User-Agent": "Mozilla/5.0"}) as http:
        response = http.get(url)
        response.raise_for_status()
        return response.json()


def kimp_line(upbit_btc: Decimal, get: Any = _get_json) -> str:
    """[쓰임] 주간 요약 김프 한 줄 (문서 200, 사용자 2026-10-10).
    김프 = 업비트 BTC ÷ (바이낸스 BTCUSDT × 야후 원/달러) − 1.
    [이유] 공개 시세가 실패해도 요약은 보낸다 ('확인 실패' 한 줄로)."""
    try:
        btc_usd = Decimal(
            str(get("https://api.binance.com/api/v3/ticker/price?symbol=BTCUSDT")["price"])
        )
        chart = get("https://query1.finance.yahoo.com/v8/finance/chart/KRW=X?range=1d&interval=1d")
        usd_krw = Decimal(str(chart["chart"]["result"][0]["meta"]["regularMarketPrice"]))
        premium = upbit_btc / (btc_usd * usd_krw) - 1
    except Exception as exc:  # 공개 시세 실패는 요약을 막지 않는다
        return f"🇰🇷 김프 확인 실패 ({type(exc).__name__})"
    return f"🇰🇷 김프 BTC {premium:+.1%} (평소 +1% 안팎 · 과열 때 +5% 넘음 — 문서 200)"


def stop_remind_lines(days: int) -> list[str]:
    """[쓰임] 정지 N 일째 다시 알림 (문서 199)."""
    return [
        f"⏰ 새 매수 정지 {days}일째 — BTC 추세가 다시 켜지면 스스로 풀려요",
        sub("문서 199: 풀기만 하면 언제 풀든 10 년 차이 1% 안, 안 풀면 −15 ~ −53%"),
        sub('손으로 풀기: python -m scripts.vega_upbit release-dd "사유"'),
    ]


def release_cmd(reason_text: str) -> int:
    """[쓰임] 낙폭 정지를 손으로 푼다 — 고점을 지금 단위 가치로 다시 잡고, 풀어 준 까닭을 남긴다."""
    state = _load()
    state["stopped"] = False
    state.pop("stopped_since", None)
    state.pop("stop_reminded", None)
    state["peak"] = state.get("last_nav", state.get("peak", "0"))
    state.pop("halt_off_seen", None)
    state.setdefault("releases", []).append([datetime.now(UTC).isoformat(), reason_text])
    _save(state)
    print(f"낙폭 정지 해제: {reason_text}")
    return 0


def main(argv: list[str]) -> int:
    """[쓰임] 명령 이름 → 함수 (예약이 ``run`` · ``weekly`` 로 부른다)."""
    if argv[:1] == ["release-dd"] and len(argv) == 2:
        return release_cmd(argv[1])
    commands = {"plan": plan_cmd, "run": run_cmd, "status": status_cmd, "weekly": weekly_cmd}
    if len(argv) != 1 or argv[0] not in commands:
        raise SystemExit(
            "usage: python -m scripts.vega_upbit plan|run|status|weekly|release-dd 사유"
        )
    return commands[argv[0]]()


if __name__ == "__main__":
    for _stream in (sys.stdout, sys.stderr):
        _reconfigure = getattr(_stream, "reconfigure", None)
        if _reconfigure is not None:
            _reconfigure(encoding="utf-8", errors="replace")
    raise SystemExit(main(sys.argv[1:]))
