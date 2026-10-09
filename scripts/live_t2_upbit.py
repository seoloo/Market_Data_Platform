"""T/v2 업비트 **실거래** — BTC · ETH · SOL 원화 (2026-10-09 사용자 결정). 하루 1 회.

사용 예::

    python -m scripts.live_t2_upbit plan    # 계획만 — 주문 · 기록 · 알림 없음 (첫날 확인)
    python -m scripts.live_t2_upbit run     # 실행 (예약 매일 09:05 KST, 일봉 마감 5 분 뒤)
    python -m scripts.live_t2_upbit status
    python -m scripts.live_t2_upbit release-dd "사유"  # 계좌 낙폭 30% 정지 해제 (자동 해제 없음)

* 사용자 (2026-10-09): "그냥 지금 paper 돌고 있어도 실거래 진행하는 것이 맞는 것 같아", 업비트,
  BTC + ETH + SOL, 목표 합이 100% 넘으면 비율대로 줄임, 적립식으로 30 만 원부터.
* 규칙 · 계산은 :mod:`auto_trading.ops.t2_upbit` (T/v2 그대로 + 세 종목 축소). 결정 = 방금 마감된
  일봉(00:00 UTC = 09:00 KST), 그 자리에서 체결한다.
* 주문은 **지정가 IOC 만**(사용자 규칙: 시장가 금지). 가격은 호가창 세 번째 칸(매수 = 매도 호가 3,
  매도 = 매수 호가 3) — 바로 체결되되 멀리 미끄러지지 않게. 남은 수량은 다음 날 밴드로 다시 본다.
* 매도 먼저, 매수는 가진 원화 안에서. 원화 계좌 전체를 이 전략의 현금으로 본다
  (다른 용도의 원화는 업비트에 두지 않는다). BTC · ETH · SOL 외 코인은 건드리지 않는다.
* 긴급 정지 ``data/HALT`` 면 주문하지 않는다. 계좌 낙폭(NAV) 30% 면 새 매수를 멈춘다.
* 상태 ``data/upbit_t2/state.json`` · 보고 ``reports/upbit_t2/<날짜>.json`` · 알림 텔레그램.
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

from auto_trading.config.secrets import load_telegram_credentials, read_env_file
from auto_trading.core.errors import AutoTradingError, ExchangeError, OrderStateUnknownError
from auto_trading.exchange.upbit import UpbitClient, UpbitOrder
from auto_trading.notify.channels import NotifyError, OutboxNotifier, TelegramNotifier
from auto_trading.notify.style import RULE, sub
from auto_trading.ops.kill_switch import halted_reason
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
    targets,
)

REPO = Path(__file__).resolve().parent.parent
DATA = REPO / "data" / "upbit_t2"
STATE = DATA / "state.json"
REPORTS = REPO / "reports" / "upbit_t2"
HALT = REPO / "data" / "HALT"
DAY_MS = 86_400_000
FEE = Decimal("0.0005")
LEVEL = 2
"""호가창 몇 번째 칸(0 부터) 가격으로 IOC 를 낼지."""
TAG = "📊 [T/v2 업비트 실거래]"


def _client() -> UpbitClient:
    env = read_env_file(REPO / ".env")
    return UpbitClient(env["UPBIT_API_KEY"], env["UPBIT_AOU_SECRET"])


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


def _snapshot(c: UpbitClient) -> tuple[Decimal, dict[str, Decimal], dict[str, Decimal]]:
    bal = c.balances()
    px = c.tickers(list(MARKETS.values()))
    return (
        bal.get("KRW", Decimal(0)),
        {k: bal.get(k, Decimal(0)) for k in COINS},
        {k: px[MARKETS[k]] for k in COINS},
    )


def _decision(now_ms: int) -> tuple[int, str]:
    d = now_ms - now_ms % DAY_MS - 1
    return d, datetime.fromtimestamp(d / 1000, UTC).strftime("%Y-%m-%d")


def _plan_today(c: UpbitClient, st: dict[str, Any], now_ms: int) -> dict[str, Any]:
    dms, day = _decision(now_ms)
    krw, hold, prices = _snapshot(c)
    exp = st.get("expected") or {}
    ns = nav_step(
        units=Decimal(st["units"]),
        peak=Decimal(st["peak"]),
        expected_krw=Decimal(str(exp.get("KRW", krw))),
        expected={k: Decimal(str(exp.get(k, hold[k]))) for k in COINS},
        krw=krw,
        holdings=hold,
        prices=prices,
    )
    stopped = bool(st.get("stopped")) or (Decimal(st["units"]) > 0 and ns.dd >= DD_STOP)
    daily = {k: c.closed_daily(MARKETS[k], dms) for k in COINS}
    w = targets(daily, dms)
    eq = equity(krw, hold, prices)
    intents = plan(w, hold, prices, eq, allow_buy=not stopped)
    return {
        "day": day,
        "krw": krw,
        "hold": hold,
        "prices": prices,
        "equity": eq,
        "nav": ns,
        "stopped": stopped,
        "weights": w,
        "intents": intents,
    }


def _fmt_w(w: dict[str, Decimal | None]) -> str:
    return " · ".join(f"{k} {'?' if v is None else f'{v:.1%}'}" for k, v in w.items())


def plan_cmd() -> int:
    c = _client()
    try:
        p = _plan_today(c, _load(), int(time.time() * 1000))
    finally:
        c.close()
    print(f"[계획만 — 주문 없음] 결정일 {p['day']}")
    print(f"원화 {p['krw']:,.0f} · 보유 {p['hold']} · 평가액 {p['equity']:,.0f} 원")
    print(f"목표 비중: {_fmt_w(p['weights'])}")
    print(f"낙폭 {p['nav'].dd:.1%} · 매수 정지 {p['stopped']}")
    for i in p["intents"]:
        print(
            f"  {i.side.value} {i.symbol} {i.quantity}"
            f" (≈ {i.quantity * p['prices'][i.symbol]:,.0f} 원)"
        )
    if not p["intents"]:
        print("  주문 없음 (밴드 안 · 최소 주문 5,000 원 미만)")
    return 0


def _send(
    c: UpbitClient, coin: str, side: str, qty: Decimal, ident: str
) -> tuple[UpbitOrder | None, str]:
    """지정가 IOC 한 건. 같은 identifier 가 이미 있으면 다시 보내지 않는다."""
    done = c.get_order(ident)
    if done is None:
        asks, bids = c.orderbook(MARKETS[coin])
        levels = asks if side == "bid" else bids
        price = levels[min(LEVEL, len(levels) - 1)]
        try:
            c.place_limit_ioc(MARKETS[coin], side, qty, price, ident)
        except OrderStateUnknownError as exc:
            return None, f"⚠ {coin} 주문 결과 모름 — 다시 보내지 않는다: {exc}"
        except ExchangeError as exc:
            return None, f"❌ {coin} 주문 거절 — {exc}"
        for _ in range(10):
            time.sleep(0.5)
            done = c.get_order(ident)
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


def run_cmd() -> int:
    now_ms = int(time.time() * 1000)
    st = _load()
    halted = halted_reason(HALT)
    c = _client()
    lines: list[str] = []
    try:
        p = _plan_today(c, st, now_ms)
        ns = p["nav"]
        deposits = st.setdefault("deposits", [])
        if Decimal(st["units"]) <= 0 and not deposits:
            deposits.append([p["day"], str(p["equity"])])  # 첫 실행: 그때 평가액 = 첫 입금
        elif ns.flow and abs(ns.flow) >= 1:
            deposits.append([p["day"], str(ns.flow)])
            word = "입금" if ns.flow > 0 else "출금"
            lines.append(f"💰 {word} 감지 {ns.flow:+,.0f} 원 → 총 입금에 반영")
        if p["stopped"] and not st.get("stopped"):
            lines.append(f"🛑 계좌 낙폭 {ns.dd:.1%} ≥ 30% — 새 매수 멈춤 (해제는 사람이)")
        st.update(
            units=str(ns.units), peak=str(ns.peak), stopped=p["stopped"], last_nav=str(ns.nav)
        )
        if st.get("last_decision") == p["day"]:
            _save(st)
            print(f"{p['day']} 이미 실행함")
            return 0
        if halted:
            lines.append(f"⏸ 긴급 정지 중 — 주문 안 함 ({halted})")
            intents = []
        else:
            intents = p["intents"]
        for i in [x for x in intents if x.side.value == "SELL"]:
            ident = f"t2u-{p['day']}-{i.symbol}-S"
            o, line = _send(c, i.symbol, "ask", i.quantity, ident)
            lines.append(line)
            st["orders"].append({"id": ident, "line": line, **({} if o is None else asdict(o))})
        buys = [x for x in intents if x.side.value == "BUY"]
        if buys:
            krw, _, prices = _snapshot(c)
            cash = krw * Decimal("0.995")
            for i in buys:
                qty = min(i.quantity, (cash / (prices[i.symbol] * (1 + FEE * 4))))
                qty = (qty / STEP).to_integral_value(rounding="ROUND_DOWN") * STEP
                if qty * prices[i.symbol] < MIN_KRW:
                    lines.append(f"⏸ {i.symbol} 매수 건너뜀 — 원화 부족 · 5,000 원 미만")
                    continue
                ident = f"t2u-{p['day']}-{i.symbol}-B"
                o, line = _send(c, i.symbol, "bid", qty, ident)
                lines.append(line)
                st["orders"].append({"id": ident, "line": line, **({} if o is None else asdict(o))})
                if o is not None:
                    cash -= o.funds + o.paid_fee
        krw, hold, prices = _snapshot(c)
        eq = equity(krw, hold, prices)
        st["expected"] = {"KRW": str(krw), **{k: str(v) for k, v in hold.items()}}
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
    report = {
        "day": p["day"],
        "weights": {k: None if v is None else str(v) for k, v in p["weights"].items()},
        "equity": str(eq),
        "nav": str(ns.nav),
        "dd": str(ns.dd),
        "lines": lines,
    }
    REPORTS.mkdir(parents=True, exist_ok=True)
    (REPORTS / f"{p['day']}.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    text = "\n".join(
        [
            f"{TAG} · {p['day']}",
            RULE,
            f"목표 {_fmt_w(p['weights'])}",
            *lines,
            "",
            *pnl_lines(st, krw, hold, prices, eq, ns.dd),
        ]
    )
    print(text)
    if lines:  # 주문 · 입출금 · 정지가 있던 날만 알린다
        _notify(text)
    return 0


def pnl_lines(
    st: dict[str, Any],
    krw: Decimal,
    hold: dict[str, Decimal],
    prices: dict[str, Decimal],
    eq: Decimal,
    dd: Decimal,
) -> list[str]:
    """총 입금 · 코인별 평가 손익 · 총 손익 (2026-10-09 사용자 요청)."""
    paid = sum((Decimal(str(d[1])) for d in st.get("deposits", [])), Decimal(0))
    bk = books(st.get("orders", []))
    out = []
    for k in COINS:
        value = hold[k] * prices[k]
        b = bk[k]
        if value <= 0 and b.cost <= 0:
            continue
        gain = value - b.cost
        pct = f" ({gain / b.cost:+.1%})" if b.cost > 0 else ""
        out.append(f"{k} {value:,.0f} 원 · 원가 {b.cost:,.0f} · {gain:+,.0f}{pct}")
    total = eq - paid
    pct = f" ({total / paid:+.1%})" if paid > 0 else ""
    return [
        f"💼 총 입금 {paid:,.0f} 원 → 평가 {eq:,.0f} 원 · 손익 {total:+,.0f}{pct}",
        *[sub(x) for x in out],
        sub(f"원화 {krw:,.0f} 원 · 낙폭 {dd:.1%}"),
    ]


def status_cmd() -> int:
    st = _load()
    print(
        f"마지막 실행 {st.get('last_decision')} · 단위 가치 {st.get('last_nav')}"
        f" · 고점 {st.get('peak')}"
        f" · 매수 정지 {st.get('stopped')}"
    )
    for row in st.get("history", [])[-10:]:
        print("  ", row)
    c = _client()
    try:
        krw, hold, prices = _snapshot(c)
    finally:
        c.close()
    eq = equity(krw, hold, prices)
    peak = Decimal(str(st.get("peak") or "0"))
    units = Decimal(str(st.get("units") or "0"))
    dd = 1 - (eq / units) / peak if units > 0 and peak > 0 else Decimal(0)
    print("\n".join(pnl_lines(st, krw, hold, prices, eq, max(dd, Decimal(0)))))
    return 0


def release_cmd(reason: str) -> int:
    st = _load()
    st["stopped"] = False
    st["peak"] = st.get("last_nav", st.get("peak", "0"))
    st.setdefault("releases", []).append([datetime.now(UTC).isoformat(), reason])
    _save(st)
    print(f"낙폭 정지 해제: {reason}")
    return 0


def main(argv: list[str]) -> int:
    if argv[:1] == ["release-dd"] and len(argv) == 2:
        return release_cmd(argv[1])
    cmds = {"plan": plan_cmd, "run": run_cmd, "status": status_cmd}
    if len(argv) != 1 or argv[0] not in cmds:
        raise SystemExit("usage: python -m scripts.live_t2_upbit plan|run|status|release-dd 사유")
    return cmds[argv[0]]()


if __name__ == "__main__":
    for _stream in (sys.stdout, sys.stderr):
        _reconfigure = getattr(_stream, "reconfigure", None)
        if _reconfigure is not None:
            _reconfigure(encoding="utf-8", errors="replace")
    raise SystemExit(main(sys.argv[1:]))
