"""T/v2 업비트 **실거래** — BTC · ETH · SOL 원화 (2026-10-09 사용자 결정). 하루 1 회.

사용 예::

    python -m scripts.vega_upbit plan    # 계획만 — 주문 · 기록 · 알림 없음 (첫날 확인)
    python -m scripts.vega_upbit run     # 실행 (예약 매일 09:05 KST, 일봉 마감 5 분 뒤)
    python -m scripts.vega_upbit status
    python -m scripts.vega_upbit release-dd "사유"  # 계좌 낙폭 30% 정지 해제 (자동 해제 없음)

* 사용자 (2026-10-09): "그냥 지금 paper 돌고 있어도 실거래 진행하는 것이 맞는 것 같아", 업비트,
  BTC + ETH + SOL, 목표 합이 100% 넘으면 비율대로 줄임, 적립식으로 30 만 원부터.
* 규칙 · 계산은 :mod:`auto_trading.ops.t2_upbit` (T/v2 그대로 + 세 종목 축소). 결정 = 방금 마감된
  일봉(00:00 UTC = 09:00 KST), 그 자리에서 체결한다.
* 주문은 **지정가 IOC 만**(사용자 규칙: 시장가 금지). 가격은 호가창 세 번째 칸(매수 = 매도 호가 3,
  매도 = 매수 호가 3) — 바로 체결되되 멀리 미끄러지지 않게. 남은 수량은 다음 날 밴드로 다시 본다.
* 매도 먼저, 매수는 가진 원화 안에서. 원화 계좌 전체를 이 전략의 현금으로 본다
  (다른 용도의 원화는 업비트에 두지 않는다). BTC · ETH · SOL 외 코인은 건드리지 않는다.
* 긴급 정지 ``data/HALT`` 면 주문하지 않는다. 계좌 낙폭(NAV) 30% 면 새 매수를 멈춘다.
  정지는 **BTC 목표 비중이 0(추세 꺼짐)이 된 뒤 다시 0 보다 커지면 스스로 풀고** 고점을 그날
  단위 가치로 다시 잡는다 — 바이낸스판(``scripts.vega_live``, 사용자 2026-10-18)과 같다.
  정지 30 일째부터 30 일마다 텔레그램으로 다시 알린다. 손으로는 ``release-dd``.
* 2026-10-19 사용자 "altair는 정리하고 vega를 업비트로 옮기도록 하자 · deneb:vega = 90:10" →
  바이낸스에서 업비트로 되돌림(10-15 의 반대). 예약 ``AutoTrading_Vega`` 가 이 실행기를 부른다.
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

import httpx

from auto_trading.config.secrets import load_telegram_credentials, read_env_file
from auto_trading.core.errors import AutoTradingError, ExchangeError, OrderStateUnknownError
from auto_trading.exchange.upbit import UpbitClient, UpbitOrder, upbit_trading_enabled
from auto_trading.notify.channels import NotifyError, OutboxNotifier, TelegramNotifier
from auto_trading.notify.style import RULE, sub
from auto_trading.ops import user_settings
from auto_trading.ops.kill_switch import halted_for
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
LEVEL = 2
"""호가창 몇 번째 칸(0 부터) 가격으로 IOC 를 낼지."""
TAG = "🌌 [Vega · T/v2 업비트]"
"""2026-10-09 사용자: 업비트 T/v2 실거래 이름 = **Vega**.

Deneb 처럼 보이는 이름만 바꾼다. 내부 파일 · 예약 이름은 그대로."""


def _client() -> UpbitClient:
    env = read_env_file(REPO / ".env")
    return UpbitClient(
        env["UPBIT_API_KEY"],
        env["UPBIT_AOU_SECRET"],
        trading_enabled=upbit_trading_enabled(env),
    )


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


def trend_ready(st: dict[str, Any], btc_w: Decimal | None) -> bool:
    """낙폭 정지 중 — 오늘 BTC 목표가 0 이면 꺼짐을 적고, 꺼짐을 본 뒤 0 보다 크면 True(풀 때).

    바이낸스판 :func:`scripts.vega_live.trend_ready` 와 같다."""
    if not st.get("stopped") or btc_w is None:
        return False
    if btc_w <= 0:
        st["halt_off_seen"] = True
        return False
    return bool(st.get("halt_off_seen"))


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
    daily = {k: c.closed_daily(MARKETS[k], dms) for k in COINS}
    w = targets(daily, dms)
    released = trend_ready(st, w.get("BTC"))
    stopped = not released and (
        bool(st.get("stopped")) or (Decimal(st["units"]) > 0 and ns.dd >= DD_STOP)
    )
    sig = signals(daily, dms)
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
        "released": released,
        "weights": w,
        "signals": sig,
        "intents": intents,
    }


def kst_day(decision_day: str) -> str:
    """결정일(UTC 마감 일봉) → 그 결정을 실행한 한국 날짜(다음 날 09:05)."""
    from datetime import date, timedelta

    return (date.fromisoformat(decision_day) + timedelta(days=1)).isoformat()


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
    # 2026-10-15 바이낸스로 옮기며 걸었던 CRUISE_UPBIT 잠금은 10-19 업비트로 되돌리며 뺐다.
    now_ms = int(time.time() * 1000)
    st = _load()
    halted = halted_for(HALT, "vega")  # 전체 정지 또는 Vega 정지(전부 정리 뒤)
    c = _client()
    lines: list[str] = []
    fill_lines: list[str] = []
    adjust: list[str] = []
    try:
        p = _plan_today(c, st, now_ms)
        ns = p["nav"]
        if st.get("last_decision") == p["day"]:
            # 같은 날 다시 실행: 장부를 건드리지 않는다. 예전엔 여기서 입출금을 기록하고
            # 단위를 늘린 뒤 '있어야 할 잔고'(expected)는 그대로 둬서, 다음 날 같은 차액을
            # 또 입금으로 셌다(10-08 · 09 소액 569 원이 두 번 → 총 입금 301,139 원,
            # 사용자 2026-10-12 발견).
            print(f"{p['day']} 이미 실행함")
            return 0
        deposits = st.setdefault("deposits", [])
        if Decimal(st["units"]) <= 0 and not deposits:
            deposits.append([p["day"], str(p["equity"])])  # 첫 실행: 그때 평가액 = 첫 입금
        elif ns.flow and abs(ns.flow) >= 1:
            deposits.append([p["day"], str(ns.flow)])
            word = "입금" if ns.flow > 0 else "출금"
            lines.append(f"💰 {word} 감지 {ns.flow:+,.0f} 원 → 총 입금에 반영")
        if p["released"]:
            lines.append(
                "▶ 계좌 낙폭 정지 스스로 풀림 — BTC 추세가 꺼졌다 다시 켜짐,"
                " 고점을 지금 단위 가치로"
            )
            st.setdefault("releases", []).append(
                [datetime.now(UTC).isoformat(timespec="seconds"), "BTC 추세 다시 켜짐 (자동)"]
            )
            for k in ("stopped_since", "stop_reminded", "halt_off_seen"):
                st.pop(k, None)
        if p["stopped"] and not st.get("stopped"):
            lines.append(
                f"🛑 계좌 낙폭 {ns.dd:.1%} ≥ 30% — 새 매수 멈춤"
                " (BTC 추세가 꺼졌다 다시 켜지면 스스로 풀림)"
            )
            st["stopped_since"], st["stop_reminded"] = p["day"], []
            btc = p["weights"].get("BTC")
            st["halt_off_seen"] = btc is not None and btc <= 0
        st.update(
            units=str(ns.units),
            peak=str(ns.nav if p["released"] else ns.peak),
            stopped=p["stopped"],
            last_nav=str(ns.nav),
        )
        if p["stopped"]:
            since = st.setdefault("stopped_since", p["day"])
            due = stop_reminder(since, p["day"], st.setdefault("stop_reminded", []))
            if due:
                st["stop_reminded"].append(due)
                lines.extend(stop_remind_lines(due))
        if halted:
            lines.append(f"⏸ 긴급 정지 중 — 주문 안 함 ({halted})")
            intents = []
        else:
            intents = p["intents"]
        fills: dict[str, Decimal] = {}
        for i in [x for x in intents if x.side.value == "SELL"]:
            ident = f"t2u-{p['day']}-{i.symbol}-S"
            o, line = _send(c, i.symbol, "ask", i.quantity, ident)
            fill_lines.append(line)
            st["orders"].append({"id": ident, "line": line, **({} if o is None else asdict(o))})
            if o is not None and o.executed_volume > 0:
                fills[i.symbol] = -o.funds
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
                fill_lines.append(line)
                st["orders"].append({"id": ident, "line": line, **({} if o is None else asdict(o))})
                if o is not None:
                    cash -= o.funds + o.paid_fee
                    if o.executed_volume > 0:
                        fills[i.symbol] = o.funds
        adjust = adjust_lines(st, p, fills, ns.flow)
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
            f"{TAG} · {kst_day(p['day'])}",
            RULE,
            *lines,
            *(["", "📋 오늘 비중 조절", *adjust] if adjust else []),
            *(["", "🛒 체결", *[sub(x) for x in fill_lines]] if fill_lines else []),
            "",
            *pnl_lines(st, krw, hold, prices, eq, ns.dd, avg_prices(c)),
        ]
    )
    print(text)
    # 주문 · 입출금 · 정지가 있던 날만 알린다. 설정에서 매매 알림을 꺼도 정지 · 경고는 보낸다.
    risky = any("정지" in x or "⚠" in x for x in lines)
    if (lines or fill_lines) and (user_settings.get(REPO, "alerts.vega_trade") or risky):
        _notify(text)
    return 0


def _sig_row(sg: Any) -> dict[str, Any]:
    return {
        "ups": str(sg.ups),
        "vol": str(sg.vol),
        "raw": str(sg.raw),
        "weight": str(sg.weight),
        "parts": [str(x) for x in sg.parts],
    }


def _prev_score(prev: dict[str, Any] | None) -> Decimal | None:
    if prev is None:
        return None
    if prev.get("parts"):
        return score_of(
            [1 if Decimal(x) > 0 else -1 if Decimal(x) < 0 else 0 for x in prev["parts"]]
        )
    ups = int(prev.get("ups", "0"))
    return Decimal(ups - (3 - ups)) / 3


def adjust_lines(
    st: dict[str, Any], p: dict[str, Any], fills: dict[str, Decimal], flow: Decimal
) -> list[str]:
    """코인마다 '지금 비중 → 목표 · 산 · 판 금액' + 추세 세 개 + 점수 계산.

    사용자 2026-10-12 시안 B (C+ 는 너무 길어 되돌림).

    점수가 그대로인데 매매했으면(변동성 · 합 100 % · 입출금 · 가격 이동) 그 까닭 한 줄을 더한다.
    점수 변화는 그 코인을 **마지막으로 사고판 날**의 판단과 비교한다(밴드 때문에 며칠 쌓인 변화).
    """
    last = st.setdefault("last_trade_signals", {})
    eq = p["equity"]
    out: list[str] = []
    for k in COINS:
        sg = p["signals"].get(k)
        cur = p["hold"][k] * p["prices"][k] / eq if eq > 0 else Decimal(0)
        if sg is None:
            continue
        sc = score_of(signs_of(sg))
        if k in fills:
            prev = last.get(k)
            icon, why = reason(k, sg, prev, cur, flow=flow)
            word = "매수" if fills[k] > 0 else "매도"
            full = " (전량)" if sg.weight == 0 else ""
            out.append(
                f"{icon} {k} {cur:.1%} → {sg.weight:.1%} · {word}{full} {abs(fills[k]):,.0f}원"
            )
            out.append(sub(f"추세: {trend_icons(sg)}"))
            ps = _prev_score(prev)
            if ps is not None and ps == sc:
                out.append(sub(why))
            out.append(sub(score_calc(sg, ps)))
            last[k] = _sig_row(sg)
        else:
            out.append(
                f"➖ {k} {cur:.1%} 그대로 · 추세 {trend_arrows(sg)} 점수 {score_text(sc)}"
                f" · 목표와 차이 {abs(sg.weight - cur):.1%}p"
            )
        last.setdefault(k, _sig_row(sg))
    if out:
        out.append(sub("목표와 5%p 넘게 벌어질 때만 사고팔아요"))
    return out


def avg_prices(c: Any) -> dict[str, Decimal] | None:
    """업비트 앱의 코인별 매수 평균가. 조회 못 하면 None (그때는 주문 기록 원가)."""
    get = getattr(c, "accounts", None)
    if get is None:
        return None
    try:
        acc = get()
    except (AutoTradingError, httpx.HTTPError):
        return None
    return {k: acc[k][1] for k in COINS if k in acc}


def pnl_lines(
    st: dict[str, Any],
    krw: Decimal,
    hold: dict[str, Decimal],
    prices: dict[str, Decimal],
    eq: Decimal,
    dd: Decimal,
    avg: dict[str, Decimal] | None = None,
) -> list[str]:
    """총 입금 · 코인별 평가 손익 · 총 손익 (2026-10-09 사용자 요청).

    ``avg`` (업비트 매수 평균가)가 있으면 코인별 원가 = 보유 × 평균가 — 업비트 앱과 같은 숫자
    (2026-10-20: 정리 기능으로 판 매도가 주문 기록에 없어 옛 원가가 남던 문제)."""
    paid = sum((Decimal(str(d[1])) for d in st.get("deposits", [])), Decimal(0))
    bk = books(st.get("orders", []))
    out = []
    for k in COINS:
        value = hold[k] * prices[k]
        cost = hold[k] * avg[k] if avg and avg.get(k) else bk[k].cost
        if value <= 0 and (cost <= 0 or avg is not None):
            continue  # 업비트 평균가를 쓰면 보유가 없을 때 원가도 없다
        gain = value - cost
        pct = f" ({gain / cost:+.1%})" if cost > 0 else ""
        out.append(f"{k} {value:,.0f} 원 · 원가 {cost:,.0f} · {gain:+,.0f}{pct}")
    total = eq - paid
    pct = f" ({total / paid:+.1%})" if paid > 0 else ""
    return [
        f"💼 총 입금 {paid:,.0f} 원 → 평가 {eq:,.0f} 원 · 손익 {total:+,.0f}{pct}",
        *[sub(x) for x in out],
        sub(f"원화 {krw:,.0f} 원 · 낙폭 {dd:.1%}"),
    ]


def weekly_cmd() -> int:
    """토요일 주간 요약 (2026-10-09 사용자 "주간 요약 같은 건 토요일에"). 주문 없음."""
    from datetime import date, timedelta

    st = _load()
    c = _client()
    try:
        now_ms = int(time.time() * 1000)
        dms, day = _decision(now_ms)
        krw, hold, prices = _snapshot(c)
        sig = signals({k: c.closed_daily(MARKETS[k], dms) for k in COINS}, dms)
        avg = avg_prices(c)
    finally:
        c.close()
    eq = equity(krw, hold, prices)
    end = date.fromisoformat(day)
    start = end - timedelta(days=6)
    label_start = date.fromisoformat(kst_day(start.isoformat()))
    label_end = date.fromisoformat(kst_day(day))
    hist = st.get("history", [])
    units = Decimal(str(st.get("units") or "0"))
    nav_now = eq / units if units > 0 else Decimal(1000)
    before = [r for r in hist if r[0] < start.isoformat()]
    nav_then = Decimal(str(before[-1][1])) if before else Decimal(1000)
    week_ret = nav_now / nav_then - 1
    dep = sum(
        (Decimal(str(d[1])) for d in st.get("deposits", []) if d[0] >= start.isoformat()),
        Decimal(0),
    )
    trades = [
        o
        for o in st.get("orders", [])
        if str(o.get("id", ""))[4:14] >= start.isoformat()
        and Decimal(str(o.get("executed_volume") or "0")) > 0
    ]
    trade_txt = " · ".join(
        f"{kst_day(str(o['id'])[4:14])[5:].replace('-', '/')} {str(o['id']).split('-')[-2]}"
        f" {'매수' if o.get('side') == 'bid' else '매도'}"
        for o in trades
    )
    rows = []
    for k in COINS:
        sg = sig.get(k)
        if sg is None:
            continue
        held_w = hold[k] * prices[k] / eq if eq > 0 else Decimal(0)
        sc = score_of(signs_of(sg))
        icon = "🟢" if sc > 0 else "🔴"
        rows.append(
            sub(
                f"{icon} {k} 주간 {sg.week:+.1%} · 추세 {trend_arrows(sg)} 점수 {score_text(sc)}"
                f" · 목표 {sg.weight:.1%} · 보유 {held_w:.1%}"
            )
        )
    peak = Decimal(str(st.get("peak") or "0"))
    dd = max(Decimal(0), 1 - nav_now / peak) if peak > 0 else Decimal(0)
    text = "\n".join(
        [
            f"📅 [Vega 주간 요약] {label_start:%m/%d} ~ {label_end:%m/%d}",
            RULE,
            f"📈 이번 주 수익률 {week_ret:+.1%}"
            f" (입출금 뺀 단위 가치 {nav_then:,.1f} → {nav_now:,.1f})",
            f"💰 이번 주 입금 {dep:+,.0f} 원",
            f"🛒 이번 주 매매 {len(trades)}건" + (f": {trade_txt}" if trades else ""),
            kimp_line(prices["BTC"]),
            "",
            "코인별 (주간 가격 · 추세 20·60·120일 · 목표 · 보유)",
            *rows,
            "",
            *pnl_lines(st, krw, hold, prices, eq, dd, avg),
        ]
    )
    print(text)
    if user_settings.get(REPO, "alerts.vega_weekly"):
        _notify(text)
    return 0


def status_cmd() -> int:
    st = _load()
    print(
        f"마지막 실행 {st.get('last_decision')} · 단위 가치 {st.get('last_nav')}"
        f" · 고점 {st.get('peak')}"
        f" · 매수 정지 {st.get('stopped')}"
        + (f" (시작 {st['stopped_since']})" if st.get("stopped") and "stopped_since" in st else "")
    )
    for row in st.get("history", [])[-10:]:
        print("  ", row)
    c = _client()
    try:
        krw, hold, prices = _snapshot(c)
        avg = avg_prices(c)
    finally:
        c.close()
    eq = equity(krw, hold, prices)
    peak = Decimal(str(st.get("peak") or "0"))
    units = Decimal(str(st.get("units") or "0"))
    dd = 1 - (eq / units) / peak if units > 0 and peak > 0 else Decimal(0)
    print("\n".join(pnl_lines(st, krw, hold, prices, eq, max(dd, Decimal(0)), avg)))
    return 0


def _get_json(url: str) -> Any:
    # 야후는 User-Agent 가 없으면 429 를 준다
    with httpx.Client(timeout=10.0, headers={"User-Agent": "Mozilla/5.0"}) as h:
        r = h.get(url)
        r.raise_for_status()
        return r.json()


def kimp_line(upbit_btc: Decimal, get: Any = _get_json) -> str:
    """주간 요약 김프 한 줄 (문서 200, 사용자 2026-10-10).

    업비트 BTC ÷ (바이낸스 BTCUSDT × 야후 원/달러) − 1."""
    try:
        usd = Decimal(
            str(get("https://api.binance.com/api/v3/ticker/price?symbol=BTCUSDT")["price"])
        )
        fx = Decimal(
            str(
                get("https://query1.finance.yahoo.com/v8/finance/chart/KRW=X?range=1d&interval=1d")[
                    "chart"
                ]["result"][0]["meta"]["regularMarketPrice"]
            )
        )
        k = upbit_btc / (usd * fx) - 1
    except Exception as exc:  # 공개 시세 실패는 요약을 막지 않는다
        return f"🇰🇷 김프 확인 실패 ({type(exc).__name__})"
    return f"🇰🇷 김프 BTC {k:+.1%} (평소 +1% 안팎 · 과열 때 +5% 넘음 — 문서 200)"


def stop_remind_lines(days: int) -> list[str]:
    """정지 N 일째 다시 알림 (문서 199)."""
    return [
        f"⏰ 새 매수 정지 {days}일째 — BTC 추세가 다시 켜지면 스스로 풀려요",
        sub("문서 199: 풀기만 하면 언제 풀든 10 년 차이 1% 안, 안 풀면 −15 ~ −53%"),
        sub('손으로 풀기: python -m scripts.vega_upbit release-dd "사유"'),
    ]


def release_cmd(reason: str) -> int:
    st = _load()
    st["stopped"] = False
    st.pop("stopped_since", None)
    st.pop("stop_reminded", None)
    st["peak"] = st.get("last_nav", st.get("peak", "0"))
    st.pop("halt_off_seen", None)
    st.setdefault("releases", []).append([datetime.now(UTC).isoformat(), reason])
    _save(st)
    print(f"낙폭 정지 해제: {reason}")
    return 0


def main(argv: list[str]) -> int:
    if argv[:1] == ["release-dd"] and len(argv) == 2:
        return release_cmd(argv[1])
    cmds = {"plan": plan_cmd, "run": run_cmd, "status": status_cmd, "weekly": weekly_cmd}
    if len(argv) != 1 or argv[0] not in cmds:
        raise SystemExit(
            "usage: python -m scripts.vega_upbit plan|run|status|weekly|release-dd 사유"
        )
    return cmds[argv[0]]()


if __name__ == "__main__":
    for _stream in (sys.stdout, sys.stderr):
        _reconfigure = getattr(_stream, "reconfigure", None)
        if _reconfigure is not None:
            _reconfigure(encoding="utf-8", errors="replace")
    raise SystemExit(main(sys.argv[1:]))
