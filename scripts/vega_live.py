"""Vega — T/v2 바이낸스 **실거래** (BTC · ETH · SOL, USDT). 하루 1 회.

    python -m scripts.vega_live plan     # 계획만 — 주문 · 기록 · 알림 없음 (첫날 확인)
    python -m scripts.vega_live run      # 실행 (예약 매일 09:05 KST, 일봉 마감 5 분 뒤)
    python -m scripts.vega_live status
    python -m scripts.vega_live release-dd "사유"   # 계좌 낙폭 30% 정지 해제 (자동 해제 없음)

* 사용자 (2026-10-15): "vega는 그냥 바이낸스로 옮기면 돼" — 업비트 Vega
  (:mod:`scripts.vega_upbit`)와 **같은 규칙**(:mod:`auto_trading.ops.t2_upbit`: T/v2 그대로 ·
  세 종목 합 100% 넘으면 비율대로 줄임 · 밴드 5%p · 계좌 낙폭 30% 면 새 매수 정지)을
  바이낸스 현물에서 USDT 로 돌린다.
* 주문은 **지정가 IOC 만**. 가격은 호가창 세 번째 칸(매수 = 매도 호가 3, 매도 = 매수 호가 3).
  매도 먼저, 매수는 가진 USDT 안에서. 현물 지갑의 **USDT · BTC · ETH · SOL 전체**를
  이 전략 돈으로 본다(따로 들고 있을 코인은 Funding 지갑에 둔다). 그 밖의 코인은 건드리지 않는다.
* 입출금은 단위 가치(NAV)로 걸러 낸다 — 아무 때나 USDT 를 넣으면 다음 실행이 입금으로 알아챈다.
* 긴급 정지 ``data/HALT`` · ``data/HALT_vega`` 면 주문하지 않는다.
* 상태 ``data/vega/state.json`` · 보고 ``reports/vega/<날짜>.json`` · 알림 텔레그램.
"""

from __future__ import annotations

import json
import sqlite3
import sys
import time
from datetime import UTC, date, datetime, timedelta
from decimal import ROUND_DOWN, Decimal
from pathlib import Path
from typing import Any

from auto_trading.config.secrets import load_binance_credentials, load_telegram_credentials
from auto_trading.core.errors import (
    AutoTradingError,
    ConfigError,
    ExchangeError,
    OrderStateUnknownError,
)
from auto_trading.exchange.binance_spot_live import BinanceSpotLive, load_live_spot
from auto_trading.exchange.spot import SpotFilters, SpotOrderResult
from auto_trading.exchange.wallet import TransferDirection
from auto_trading.execution.wallet_transfers import WalletTransferManager
from auto_trading.notify.channels import NotifyError, OutboxNotifier, TelegramNotifier
from auto_trading.notify.style import RULE, sub
from auto_trading.ops import user_settings
from auto_trading.ops.altair_split import altair_share, move_amount
from auto_trading.ops.kill_switch import halted_for
from auto_trading.ops.t2_upbit import (
    COINS,
    DD_STOP,
    equity,
    nav_step,
    plan,
    signals,
    stop_reminder,
    targets,
)
from auto_trading.storage.repositories.wallet_transfers import WalletTransferRepository

REPO = Path(__file__).resolve().parent.parent
DATA = REPO / "data" / "vega"
STATE = DATA / "state.json"
REPORTS = REPO / "reports" / "vega"
HALT = REPO / "data" / "HALT"
DAY_MS = 86_400_000
CASH = "USDT"
SYMBOLS = {c: f"{c}USDT" for c in COINS}
FEE = Decimal("0.001")
"""바이낸스 현물 기본 수수료(테이커 0.1%) — 매수 가능 수량을 넉넉히 잡을 때만 쓴다."""
LEVEL = 2
TAG = "🌌 [Vega · T/v2 바이낸스]"
_FINAL = {"FILLED", "CANCELED", "EXPIRED", "REJECTED", "EXPIRED_IN_MATCH"}


def _client(*, preview: bool = False) -> BinanceSpotLive:
    """거래 키로 연결. ``preview`` 면 거래 키가 아직 없을 때 읽기 전용 키로 대신한다(주문 꺼짐)."""
    try:
        creds, enabled = load_live_spot(REPO / ".env")
    except ConfigError:
        if not preview:
            raise
        return BinanceSpotLive(load_binance_credentials(REPO / ".env"), trading_enabled=False)
    return BinanceSpotLive(creds, trading_enabled=enabled and not preview)


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
    if STATE.exists():
        return dict(json.loads(STATE.read_text(encoding="utf-8")))
    return {"units": "0", "peak": "0", "stopped": False, "history": [], "orders": []}


def _save(st: dict[str, Any]) -> None:
    DATA.mkdir(parents=True, exist_ok=True)
    tmp = STATE.with_suffix(".tmp")
    tmp.write_text(json.dumps(st, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    tmp.replace(STATE)


def _usd(x: Decimal) -> str:
    return f"{x:,.2f} USDT"


def _snapshot(c: BinanceSpotLive) -> tuple[Decimal, dict[str, Decimal], dict[str, Decimal]]:
    bal = c.balances()
    return (
        bal.get(CASH, Decimal(0)),
        {k: bal.get(k, Decimal(0)) for k in COINS},
        {k: c.last_price(SYMBOLS[k]) for k in COINS},
    )


def _decision(now_ms: int) -> tuple[int, str]:
    d = now_ms - now_ms % DAY_MS - 1
    return d, datetime.fromtimestamp(d / 1000, UTC).strftime("%Y-%m-%d")


def kst_day(decision_day: str) -> str:
    return (date.fromisoformat(decision_day) + timedelta(days=1)).isoformat()


def _plan_today(
    c: BinanceSpotLive, st: dict[str, Any], now_ms: int
) -> tuple[dict[str, Any], dict[str, SpotFilters]]:
    dms, day = _decision(now_ms)
    cash, hold, prices = _snapshot(c)
    exp = st.get("expected") or {}
    ns = nav_step(
        units=Decimal(st["units"]),
        peak=Decimal(st["peak"]),
        expected_krw=Decimal(str(exp.get(CASH, cash))),
        expected={k: Decimal(str(exp.get(k, hold[k]))) for k in COINS},
        krw=cash,
        holdings=hold,
        prices=prices,
    )
    stopped = bool(st.get("stopped")) or (Decimal(st["units"]) > 0 and ns.dd >= DD_STOP)
    daily = {k: c.closed_daily(SYMBOLS[k], dms) for k in COINS}
    w = targets(daily, dms)
    filters = {k: c.filters(SYMBOLS[k]) for k in COINS}
    eq = equity(cash, hold, prices)
    intents = plan(
        w,
        hold,
        prices,
        eq,
        allow_buy=not stopped,
        step={k: f.step_size for k, f in filters.items()},
        min_notional={k: f.min_notional for k, f in filters.items()},
    )
    return {
        "day": day,
        "cash": cash,
        "hold": hold,
        "prices": prices,
        "equity": eq,
        "nav": ns,
        "stopped": stopped,
        "weights": w,
        "signals": signals(daily, dms),
        "intents": intents,
    }, filters


def _fmt_w(w: dict[str, Decimal | None]) -> str:
    return " · ".join(f"{k} {'?' if v is None else f'{v:.1%}'}" for k, v in w.items())


def plan_cmd() -> int:
    c = _client(preview=True)
    try:
        p, _f = _plan_today(c, _load(), int(time.time() * 1000))
    finally:
        c.close()
    print(f"[계획만 — 주문 없음] 결정일 {p['day']}")
    print(f"USDT {p['cash']:,.2f} · 보유 {p['hold']} · 평가액 {_usd(p['equity'])}")
    print(f"목표 비중: {_fmt_w(p['weights'])}")
    print(f"낙폭 {p['nav'].dd:.1%} · 매수 정지 {p['stopped']}")
    for i in p["intents"]:
        value = _usd(i.quantity * p["prices"][i.symbol])
        print(f"  {i.side.value} {i.symbol} {i.quantity} (≈ {value})")
    if not p["intents"]:
        print("  주문 없음 (밴드 안 · 최소 주문 금액 미만)")
    return 0


def _wait(c: BinanceSpotLive, symbol: str, ident: str) -> SpotOrderResult | None:
    done = c.get_order(symbol, ident)
    for _ in range(10):
        if done is not None and done.status.value in _FINAL:
            break
        time.sleep(0.5)
        done = c.get_order(symbol, ident)
    return done


def _fee_usdt(c: BinanceSpotLive, coin: str, o: SpotOrderResult) -> Decimal:
    """체결 수수료를 USDT 로 (코인으로 냈으면 체결가로 환산, BNB 등은 0 으로 두고 기록만)."""
    try:
        fills = c.fills(SYMBOLS[coin], o.exchange_order_id)
    except AutoTradingError:
        return o.quote_qty * FEE
    total = Decimal(0)
    for f in fills:
        if f.commission_asset == CASH:
            total += f.commission
        elif f.commission_asset == coin:
            total += f.commission * f.price
    return total if fills else o.quote_qty * FEE


def _send(
    c: BinanceSpotLive, coin: str, side: str, qty: Decimal, ident: str
) -> tuple[SpotOrderResult | None, Decimal, str]:
    """지정가 IOC 한 건 → (결과, 수수료 USDT, 알림 줄).

    같은 주문 번호가 이미 있으면 다시 보내지 않는다."""
    symbol = SYMBOLS[coin]
    done = c.get_order(symbol, ident)
    if done is None:
        bids, asks = c.order_book(symbol, limit=5)
        levels = asks if side == "BUY" else bids
        price = levels[min(LEVEL, len(levels) - 1)][0]
        try:
            c.place_limit_ioc(symbol, side, qty, price, ident)
        except OrderStateUnknownError as exc:
            return None, Decimal(0), f"⚠ {coin} 주문 결과 모름 — 다시 보내지 않는다: {exc}"
        except ExchangeError as exc:
            return None, Decimal(0), f"❌ {coin} 주문 거절 — {exc}"
        done = _wait(c, symbol, ident)
    if done is None:
        return None, Decimal(0), f"⚠ {coin} 주문 확인 안 됨 — 내일 잔고로 확인"
    word = "매수" if side == "BUY" else "매도"
    if done.executed_qty <= 0:
        return done, Decimal(0), f"⏸ {coin} {word} 체결 없음 (IOC) — 내일 다시 판단"
    fee = _fee_usdt(c, coin, done)
    return (
        done,
        fee,
        (f"🛒 {coin} {word} {done.executed_qty:g} · {_usd(done.quote_qty)} (수수료 {fee:,.4f})"),
    )


def _record(
    st: dict[str, Any], ident: str, line: str, o: SpotOrderResult | None, fee: Decimal
) -> None:
    row: dict[str, Any] = {"id": ident, "line": line}
    if o is not None:
        row.update(
            side=o.side,
            status=o.status.value,
            executed_qty=str(o.executed_qty),
            quote_qty=str(o.quote_qty),
            fee_usdt=str(fee),
            exchange_order_id=o.exchange_order_id,
        )
    st["orders"].append(row)


def _split(c: BinanceSpotLive, st: dict[str, Any], now_ms: int) -> list[str]:
    """Altair 자동 분배 (사용자 2026-10-15): 새 입금에서 선물 몫이 모자라면 현물 → 선물로 옮긴다.

    판단은 :func:`auto_trading.ops.altair_split.move_amount`. 이체는 문서 53 규약(선기록 ·
    결과 모르면 재전송 안 함, :class:`WalletTransferManager`). 실패해도 Vega 실행은 계속한다.
    """
    dms, day = _decision(now_ms)
    cash, hold, prices = _snapshot(c)
    eq = equity(cash, hold, prices)
    exp = st.get("expected")
    if exp:
        before = Decimal(exp.get(CASH, "0")) + sum(
            (Decimal(exp.get(k, "0")) * prices[k] for k in COINS), Decimal(0)
        )
        new_money = eq - before
    else:
        new_money = eq
    fut = c.futures_balance()
    # 오르빗 Altair 카드가 읽는 기록 (화면은 계좌를 조회하지 않는다)
    altair = st.setdefault("altair", {"transfers": []})
    altair.update(futures_usdt=str(fut), checked=day)
    amount = move_amount(spot_equity=eq, futures_balance=fut, new_money=new_money, free_usdt=cash)
    if amount <= 0:
        return []
    with sqlite3.connect(REPO / "data" / "trading.db") as conn:
        manager = WalletTransferManager(
            c,
            WalletTransferRepository(conn),
            mode="live",
            strategy_id="altair-split",
            strategy_tag="altair-split",
            clock=lambda: int(time.time() * 1000),
        )
        manager.recover()
        if manager.blocked():
            return ["⚠ Altair 이체 결과를 아직 모르는 건이 있어 이번엔 옮기지 않음 (확인 필요)"]
        rec = manager.transfer(
            decision_ms=dms, direction=TransferDirection.SPOT_TO_FUTURES, amount=amount
        )
    share = f"{altair_share():.0%}"
    if rec.state == "CONFIRMED":
        altair["transfers"].append([day, str(amount)])
        altair["futures_usdt"] = str(fut + amount)
        return [
            f"☄️ Altair 몫 {_usd(amount)} 현물 → 선물 이체",
            sub(f"목표 선물 {share} · 선물 지갑 {_usd(fut + amount)}"),
        ]
    if rec.state == "REJECTED":
        return [
            f"⚠ Altair 이체 거부 — {rec.error}",
            sub("키에 '내부 이체(Universal Transfer)' 권한이 필요해요"),
        ]
    return [f"⚠ Altair 이체 결과 모름 ({_usd(amount)}) — 다음 실행 때 이력으로 확인해요"]


def run_cmd() -> int:
    now_ms = int(time.time() * 1000)
    st = _load()
    halted = halted_for(HALT, "vega")
    c = _client()
    lines: list[str] = []
    fill_lines: list[str] = []
    try:
        if st.get("last_decision") == _decision(now_ms)[1]:
            print(f"{_decision(now_ms)[1]} 이미 실행함")
            return 0
        if not halted and c.trading_enabled:
            try:
                lines += _split(c, st, now_ms)
            except (AutoTradingError, sqlite3.Error) as exc:  # 분배 실패는 Vega 를 멈추지 않는다
                lines.append(f"⚠ Altair 자동 분배 실패: {exc}")
        p, filters = _plan_today(c, st, now_ms)
        ns = p["nav"]
        deposits = st.setdefault("deposits", [])
        if Decimal(st["units"]) <= 0 and not deposits:
            deposits.append([p["day"], str(p["equity"])])
        elif ns.flow and abs(ns.flow) >= Decimal("0.5"):
            deposits.append([p["day"], str(ns.flow)])
            word = "입금" if ns.flow > 0 else "출금"
            lines.append(f"💰 {word} 감지 {ns.flow:+,.2f} USDT → 총 입금에 반영")
        if p["stopped"] and not st.get("stopped"):
            lines.append(f"🛑 계좌 낙폭 {ns.dd:.1%} ≥ 30% — 새 매수 멈춤 (해제는 사람이)")
            st["stopped_since"], st["stop_reminded"] = p["day"], []
        st.update(
            units=str(ns.units), peak=str(ns.peak), stopped=p["stopped"], last_nav=str(ns.nav)
        )
        if p["stopped"]:
            since = st.setdefault("stopped_since", p["day"])
            due = stop_reminder(since, p["day"], st.setdefault("stop_reminded", []))
            if due:
                st["stop_reminded"].append(due)
                lines.append(f"🔔 매수 정지 {due} 일째 — 풀려면 release-dd (문서 199)")
        intents = [] if halted else p["intents"]
        if halted:
            lines.append(f"⏸ 긴급 정지 중 — 주문 안 함 ({halted})")
        for i in [x for x in intents if x.side.value == "SELL"]:
            ident = f"t2b-{p['day']}-{i.symbol}-S"
            o, fee, line = _send(c, i.symbol, "SELL", i.quantity, ident)
            fill_lines.append(line)
            _record(st, ident, line, o, fee)
        buys = [x for x in intents if x.side.value == "BUY"]
        if buys:
            cash, _h, prices = _snapshot(c)
            budget = cash * Decimal("0.995")
            for i in buys:
                f = filters[i.symbol]
                qty = min(i.quantity, budget / (prices[i.symbol] * (1 + FEE * 4)))
                qty = (qty / f.step_size).to_integral_value(rounding=ROUND_DOWN) * f.step_size
                if qty < f.min_qty or qty * prices[i.symbol] < f.min_notional:
                    lines.append(f"⏸ {i.symbol} 매수 건너뜀 — USDT 부족 · 최소 주문 금액 미만")
                    continue
                ident = f"t2b-{p['day']}-{i.symbol}-B"
                o, fee, line = _send(c, i.symbol, "BUY", qty, ident)
                fill_lines.append(line)
                _record(st, ident, line, o, fee)
                if o is not None:
                    budget -= o.quote_qty + fee
        cash, hold, prices = _snapshot(c)
        eq = equity(cash, hold, prices)
        st["expected"] = {CASH: str(cash), **{k: str(v) for k, v in hold.items()}}
        st["last_decision"] = p["day"]
        st["history"].append([p["day"], str(ns.nav), str(eq), str(ns.dd)])
        _save(st)
    except AutoTradingError as exc:
        text = f"⚠ {TAG} 실패\n{RULE}\n{exc}"
        _notify(text)
        print(text)
        return 1
    finally:
        c.close()
    invested = sum((Decimal(str(d[1])) for d in st.get("deposits", [])), Decimal(0))
    report = {
        "day": p["day"],
        "weights": {k: None if v is None else str(v) for k, v in p["weights"].items()},
        "equity": str(eq),
        "nav": str(ns.nav),
        "dd": str(ns.dd),
        "lines": lines,
        "fills": fill_lines,
    }
    REPORTS.mkdir(parents=True, exist_ok=True)
    (REPORTS / f"{p['day']}.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    hold_txt = [
        sub(f"{k} {hold[k]:g} · {_usd(hold[k] * prices[k])} (목표 {_fmt_w({k: p['weights'][k]})})")
        for k in COINS
    ]
    pnl = eq - invested
    text = "\n".join(
        [
            f"{TAG} · {kst_day(p['day'])}",
            RULE,
            *lines,
            *(["", "🛒 체결", *[sub(x) for x in fill_lines]] if fill_lines else []),
            "",
            f"💼 총 입금 {_usd(invested)} → 평가 {_usd(eq)} · 손익 {pnl:+,.2f}"
            + (f" ({pnl / invested:+.1%})" if invested > 0 else ""),
            *hold_txt,
            sub(f"USDT {cash:,.2f} · 낙폭 {ns.dd:.1%}"),
        ]
    )
    print(text)
    risky = any("정지" in x or "⚠" in x for x in lines)
    if (lines or fill_lines) and (user_settings.get(REPO, "alerts.vega_trade") or risky):
        _notify(text)
    return 0


def status_cmd() -> int:
    st = _load()
    print(
        f"마지막 실행 {st.get('last_decision')} · 단위 가치 {st.get('last_nav')}"
        f" · 고점 {st.get('peak')} · 매수 정지 {st.get('stopped')}"
    )
    for row in st.get("history", [])[-7:]:
        print("  ", row)
    return 0


def release_cmd(reason: str) -> int:
    st = _load()
    st.update(stopped=False, peak=st.get("last_nav", st.get("peak", "0")))
    st.setdefault("releases", []).append([datetime.now(UTC).isoformat(timespec="seconds"), reason])
    st.pop("stopped_since", None)
    st.pop("stop_reminded", None)
    _save(st)
    _notify(f"{TAG}\n{RULE}\n✅ 계좌 낙폭 정지 해제 — {reason}")
    return 0


def main(argv: list[str]) -> int:
    if argv == ["plan"]:
        return plan_cmd()
    if argv == ["run"]:
        return run_cmd()
    if argv == ["status"]:
        return status_cmd()
    if len(argv) == 2 and argv[0] == "release-dd":
        return release_cmd(argv[1])
    raise SystemExit("usage: python -m scripts.vega_live plan | run | status | release-dd 사유")


if __name__ == "__main__":
    for _stream in (sys.stdout, sys.stderr):
        _reconfigure = getattr(_stream, "reconfigure", None)
        if _reconfigure is not None:
            _reconfigure(encoding="utf-8", errors="replace")
    raise SystemExit(main(sys.argv[1:]))
