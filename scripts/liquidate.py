"""'전부 정리' 실행기 — 사용자가 텔레그램 · 오르빗에서 누를 때만 돈다 (사용자 2026-10-11).

사용 예::

    python -m scripts.liquidate preview [all|vega|deneb|acc]   # 보기만 (주문 없음)
    python -m scripts.liquidate start <범위> <출처>               # 정지 → 매수 취소 → 지정가 매도
    python -m scripts.liquidate resume                            # 이어 하기 (봇이 2 분마다)
    python -m scripts.liquidate status
    python -m scripts.liquidate unhalt <vega|deneb|acc>         # 그룹 정지 풀기

규칙 · 순서는 :mod:`auto_trading.ops.liquidate`. 시장가는 쓰지 않는다(지정가만).
업비트 키 ``UPBIT_API_KEY`` · ``UPBIT_AOU_SECRET``, 토스는 ``TOSS_*`` (값은 출력하지 않는다).
토스 주문은 ``TOSS_TRADING_ENABLED=true`` 일 때만 나간다(꺼져 있으면 '주문 막힘' 으로 알린다).
"""

from __future__ import annotations

import json
import sys
import time
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from auto_trading.config.secrets import (
    load_telegram_credentials,
    load_toss_credentials,
    read_env_file,
)
from auto_trading.core.errors import AutoTradingError, ConfigError
from auto_trading.exchange.binance_spot_live import BinanceSpotLive, load_live_spot
from auto_trading.exchange.binance_usdm_live import BinanceUsdmLive, load_live_futures
from auto_trading.exchange.toss import (
    TossPosition,
    TossReader,
    TossTokenCache,
    TossTrader,
    is_kr_symbol,
)
from auto_trading.exchange.upbit import UpbitClient
from auto_trading.notify.channels import NotifyError, OutboxNotifier, TelegramNotifier
from auto_trading.ops.deneb_live import SYMBOLS as DENEB_SYMBOLS
from auto_trading.ops.deneb_live import DenebState
from auto_trading.ops.kill_switch import (
    GROUPS,
    group_halted,
    group_path,
    legacy_path,
)
from auto_trading.ops.liquidate import (
    SCOPES,
    Item,
    Job,
    Store,
    build_items,
    deneb_to_book,
    groups_of,
    job_text,
    run_binance_item,
    run_futures_item,
    run_toss_item,
    run_upbit_item,
    scope_label,
    set_group_halts,
    symbol_of,
)
from auto_trading.ops.t2_upbit import COINS, MARKETS

REPO = Path(__file__).resolve().parent.parent
STORE = Store(REPO / "data" / "liquidate")
HALT = REPO / "data" / "HALT"
TOKEN = REPO / "data" / "toss_token.json"
TOSS_DATA = REPO / "data" / "toss"
DENEB_FILE = TOSS_DATA / "DENEB.json"
REPORTS = REPO / "reports" / "liquidate"
KST = timedelta(hours=9)
NY = ZoneInfo("America/New_York")


def _now_ms() -> int:
    return int(time.time() * 1000)


def _kst(ms: int) -> datetime:
    return datetime.fromtimestamp(ms / 1000, UTC) + KST


def _upbit() -> UpbitClient:
    env = read_env_file(REPO / ".env")
    return UpbitClient(env["UPBIT_API_KEY"], env["UPBIT_AOU_SECRET"])


def _binance() -> BinanceSpotLive | None:
    """Vega 바이낸스 거래 키(2026-10-15 이전). 키가 없으면 None — 바이낸스 몫은 없다고 본다."""
    try:
        creds, enabled = load_live_spot(REPO / ".env")
    except ConfigError:
        return None
    return BinanceSpotLive(creds, trading_enabled=enabled)


ALTAIR_STATE = REPO / "data" / "altair_live" / "state.json"
ALTAIR_SYMBOLS = {"BTC": "BTCUSDC", "ETH": "ETHUSDC"}
"""Altair 실거래 종목 (scripts/altair_live · altair_demo.TRADE 와 같음)."""


def _futures() -> BinanceUsdmLive | None:
    """Altair 선물 키. 없으면 None — Altair 몫은 없다고 본다."""
    try:
        creds, enabled = load_live_futures(REPO / ".env")
    except ConfigError:
        return None
    return BinanceUsdmLive(creds, trading_enabled=enabled)


def _altair_positions(c: BinanceUsdmLive) -> dict[str, Decimal]:
    acct = c.account()
    return {
        coin: acct.positions[sym].quantity
        for coin, sym in ALTAIR_SYMBOLS.items()
        if sym in acct.positions and acct.positions[sym].quantity != 0
    }


def _altair_forget(coin: str) -> None:
    """정리한 코인의 Altair 숏 · 스톱 기록을 지운다 — 실행기가 '스톱 발동' 으로 잘못 읽지 않게."""
    try:
        st = json.loads(ALTAIR_STATE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return
    st.get("shorts", {}).pop(coin, None)
    st.get("stops", {}).pop(coin, None)
    tmp = ALTAIR_STATE.with_suffix(".tmp")
    tmp.write_text(json.dumps(st, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(ALTAIR_STATE)


SIZE_STATE = REPO / "data" / "size_guard" / "state.json"
"""💹 개별주 장부 (scripts/size_guard)."""


def _stock_tickets() -> list[dict[str, Any]]:
    try:
        d = json.loads(SIZE_STATE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    return [t for t in d.get("tickets") or [] if isinstance(t, dict)]


def _stock_qty() -> dict[str, Decimal]:
    """개별주 장부가 들고 있는 수량 (보유 · 손절 매도 중) = 산 수량 − 판 수량."""
    out: dict[str, Decimal] = {}
    for t in _stock_tickets():
        if t.get("status") not in ("holding", "selling"):
            continue
        sold = sum(float(o.get("filled", 0)) for o in t.get("sells") or [])
        q = Decimal(str(float(t.get("filled", 0)) - sold))
        if q > 0:
            sym = str(t["symbol"]).upper()
            out[sym] = out.get(sym, Decimal(0)) + q
    return out


def _stock_symbols() -> set[str]:
    return {
        str(t["symbol"]).upper()
        for t in _stock_tickets()
        if t.get("status") in ("reserved", "buying", "holding", "selling")
    }


def _handover_stock(symbols: set[str], reason: str) -> list[str]:
    """정리로 넘긴 개별주: 예약 · 매수 중은 취소, 보유 · 손절 중은 감시 해제.

    손절 감시와 정리가 같은 주식을 겹쳐 팔지 않게."""
    if not symbols:
        return []
    from scripts import size_guard as sg

    out: list[str] = []
    try:
        lock = sg._Lock().__enter__()
    except SystemExit:
        return ["개별주 장부가 잠겨 있어 손절 감시를 못 풀었어요 — 정리 매도는 그대로 해요"]
    try:
        tickets = sg.load_tickets()
        for t in tickets:
            if t.symbol.upper() not in symbols or not t.active:
                continue
            new = "canceled" if t.status in ("reserved", "buying") else "released"
            t.status, t.note, t.closed_ms = new, reason, _now_ms()
            out.append(f"개별주 {t.symbol} {'예약 취소' if new == 'canceled' else '손절 감시 끝'}")
        sg.save_tickets(tickets)
    finally:
        lock.__exit__(None, None, None)
    return out


def _reader() -> TossReader:
    return TossReader(load_toss_credentials(REPO / ".env"), TossTokenCache(TOKEN))


def _trader(holdings: dict[str, TossPosition], symbols: set[str]) -> TossTrader:
    """정리용 — 지금 보유 종목만 팔 수 있고, 수량은 보유 이하, 지정가는 현재가 2 배 이하."""
    syms = set(holdings) | symbols
    us = [p.last for s, p in holdings.items() if not is_kr_symbol(s)]
    kr = [p.last for s, p in holdings.items() if is_kr_symbol(s)]
    return TossTrader(
        load_toss_credentials(REPO / ".env"),
        TossTokenCache(TOKEN),
        allowed_symbols=frozenset(syms),
        max_qty={s: max(1, int(p.qty)) for s, p in holdings.items()},
        sell_symbols=frozenset(holdings),
        max_limit_usd=max(us, default=Decimal(1)) * 2,
        max_limit_krw=max(kr, default=Decimal(1)) * 2,
    )


def _deneb_qty() -> dict[str, Decimal]:
    try:
        d = json.loads(DENEB_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return {s: Decimal(str(q)) for s, q in (d.get("qty") or {}).items() if float(q) > 0}


def _acc_qty(holdings: dict[str, Decimal]) -> dict[str, Decimal]:
    """토스 적립 장부가 산 몫. 미국 = 보유 − 적립 시작 전 수량(``baseline_qty``),
    국내 = 장부의 체결 매수 합. 장부가 없는 종목은 0 (수동 보유)."""
    out: dict[str, Decimal] = {}
    for f in sorted(TOSS_DATA.glob("*.json")):
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(d, dict):
            continue
        if f.name.startswith("KR_"):
            sym = f.stem[3:]
            bought = sum((Decimal(str(b.get("qty", 0))) for b in d.get("buys") or []), Decimal(0))
            if bought > 0:
                out[sym] = bought
        elif "baseline_qty" in d and d.get("symbol"):
            sym = str(d["symbol"]).upper()
            out[sym] = max(
                Decimal(0), holdings.get(sym, Decimal(0)) - Decimal(str(d["baseline_qty"]))
            )
    return out


def _notify(text: str) -> None:
    REPORTS.mkdir(parents=True, exist_ok=True)
    OutboxNotifier(REPORTS / "outbox.log").send(text)
    creds = load_telegram_credentials(REPO / ".env")
    if creds is None:
        return
    bot = TelegramNotifier(creds)
    try:
        bot.send(text)
    except NotifyError as exc:
        print(f"텔레그램 실패: {exc}")
    finally:
        bot.close()


# ------------------------------------------------------------------ 계획


def plan(scope: str) -> tuple[list[Item], list[str]]:
    groups = groups_of(scope)
    notes: list[str] = []
    bal: dict[str, Decimal] = {}
    px: dict[str, Decimal] = {}
    if "vega" in groups:
        c = _upbit()
        try:
            bal = c.balances()
            px = {k: v for k, m in MARKETS.items() if (v := c.tickers([m]).get(m)) is not None}
        except AutoTradingError as exc:
            notes.append(f"업비트 조회 실패: {exc}")
        finally:
            c.close()
    b_bal: dict[str, Decimal] = {}
    b_px: dict[str, Decimal] = {}
    b_min: dict[str, Decimal] = {}
    if "vega" in groups and (b := _binance()) is not None:
        try:
            b_bal = b.balances()
            for k in COINS:
                if b_bal.get(k, Decimal(0)) > 0:
                    b_px[k] = b.last_price(f"{k}USDT")
                    b_min[k] = b.filters(f"{k}USDT").min_notional
        except AutoTradingError as exc:
            notes.append(f"바이낸스 조회 실패: {exc}")
        finally:
            b.close()
    fut: dict[str, Decimal] = {}
    if "altair" in groups:
        f = _futures()
        if f is None:
            notes.append("Altair 선물 키 없음 — 바이낸스 선물 포지션은 확인 못 함")
        else:
            try:
                fut = _altair_positions(f)
            except AutoTradingError as exc:
                notes.append(f"바이낸스 선물 조회 실패: {exc}")
            finally:
                f.close()
    holdings: dict[str, Decimal] = {}
    sellable: dict[str, Decimal] = {}
    only = symbol_of(scope)
    if "deneb" in groups or "acc" in groups or "stock" in groups or only:
        r = _reader()
        try:
            held = r.holdings()
            holdings = {s: p.qty for s, p in held.items()}
            sellable = {s: r.sellable(s) for s in held}
        except AutoTradingError as exc:
            notes.append(f"토스 조회 실패: {exc}")
        finally:
            r.close()
    items, more = build_items(
        groups,
        upbit_balances=bal,
        upbit_prices=px,
        vega_coins=COINS,
        toss_holdings=holdings,
        toss_sellable=sellable,
        deneb_qty={s: q for s, q in _deneb_qty().items() if s in DENEB_SYMBOLS},
        acc_qty=_acc_qty(holdings),
        is_kr=is_kr_symbol,
        binance_balances=b_bal,
        binance_prices=b_px,
        binance_min=b_min,
        altair_positions=fut,
        stock_qty=_stock_qty(),
        only_symbol=only,
    )
    return items, notes + more


def preview_text(scope: str = "all") -> str:
    items, notes = plan(scope)
    names = {
        "upbit": "업비트",
        "binance": "바이낸스",
        "toss_us": "토스 미국",
        "toss_kr": "토스 국내",
        "binance_fut": "바이낸스 선물",
    }
    lines = [f"🧯 전부 정리 미리 보기 · {scope_label(scope)} (아직 아무것도 안 팔았어요)"]
    if symbol_of(scope):
        lines.append(
            ", ".join(f"{names[it.venue]} {it.symbol} {it.want:f}주" for it in items)
            or "팔 것 없음"
        )
    for g in groups_of(scope):
        rows = [it for it in items if g in it.groups]
        lines.append(
            f"[{GROUPS[g]}] "
            + (
                ", ".join(f"{names[it.venue]} {it.symbol} {it.want:f}" for it in rows)
                or "팔 것 없음"
            )
        )
    lines += [f"· {n}" for n in notes]
    if symbol_of(scope):
        lines.append(
            "누르면: ① 그 종목 토스 미체결 매수 취소 ② 지정가로 매도 (그룹 정지 없음 · "
            "토스는 정규장에서 — 닫혀 있으면 열린 뒤). 💹 개별주 손절 감시는 정리로 넘겨요"
        )
        return "\n".join(lines)
    lines.append(
        "누르면: ① 그 그룹 새 매수 정지 ② 토스 미체결 매수 취소 ③ 지정가로 매도 "
        "(업비트 · 바이낸스는 바로, 토스는 정규장에서 — 닫혀 있으면 열린 뒤)"
    )
    return "\n".join(lines)


# ------------------------------------------------------------------ 장 시간


def _us_open(reader: TossReader, now_ms: int) -> bool:
    ny_day = datetime.fromtimestamp(now_ms / 1000, NY).strftime("%Y-%m-%d")
    sess = reader.us_regular_session(ny_day)
    if sess is None:
        return False
    start = datetime.fromisoformat(sess[0]).timestamp() * 1000
    end = datetime.fromisoformat(sess[1]).timestamp() * 1000
    return start + 3 * 60_000 <= now_ms < end - 10 * 60_000


def _kr_open(reader: TossReader, now_ms: int) -> bool:
    k = _kst(now_ms)
    if not "09:03" <= k.strftime("%H:%M") < "15:15":
        return False
    return reader.kr_open_today(k.strftime("%Y-%m-%d"))


# ------------------------------------------------------------------ 실행


def _book_deneb(symbol: str, qty: Decimal, usd: Decimal) -> None:
    """팔린 Deneb 몫을 Deneb 장부(DENEB.json)에서 뺀다 — 수량 ↓ · 달러 현금 ↑."""
    deneb = DenebState.from_dict(json.loads(DENEB_FILE.read_text(encoding="utf-8")))
    deneb.qty[symbol] = max(0.0, deneb.qty.get(symbol, 0.0) - float(qty))
    deneb.cash_usd += float(usd)
    tmp = DENEB_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(deneb.to_dict(), ensure_ascii=False, indent=1) + "\n", "utf-8")
    tmp.replace(DENEB_FILE)


def _cancel_buys(trader: TossTrader, job: Job) -> list[str]:
    """정리 범위의 토스 미체결 **매수** 를 취소한다 (오늘 밤 장 마감 매수가 다시 사지 않게)."""
    groups = groups_of(job.scope)
    log = []
    for o in trader.open_orders():
        deneb = o.symbol in DENEB_SYMBOLS
        only = symbol_of(job.scope)
        mine = (
            ("deneb" in groups and deneb)
            or ("acc" in groups and not deneb)
            or ("stock" in groups and o.symbol in _stock_symbols())
            or (only is not None and o.symbol == only)
        )
        if o.side != "BUY" or not mine or o.status.startswith("PENDING_CANCEL"):
            continue
        res = trader.cancel(o.order_id)
        tag = f"{o.symbol} {o.qty - o.filled_qty:f}주"
        if res.accepted:
            job.buys_canceled.append(tag)
            log.append(f"토스 미체결 매수 취소 {tag}")
        else:
            log.append(f"토스 매수 취소 실패 {tag} ({res.code})")
    return log


def _step_futures(job: Job, futs: list[Item]) -> list[str]:
    """Altair 선물 포지션 닫기 → 닫히면 그 종목 비상 스톱 지우기 · 장부 기록 정리."""
    log: list[str] = []
    f = _futures()
    if f is None:
        return ["Altair 선물 키 없음 — 바이낸스 앱에서 선물 포지션을 직접 닫아 주세요"]
    if not f.trading_enabled:
        f.close()
        return [
            "BINANCE_FUTURES_LIVE_TRADING_ENABLED 꺼짐 — 선물 포지션은 바이낸스 앱에서 직접"
            " 닫아 주세요 (또는 .env 에서 켜면 다음 확인 때 닫아요)"
        ]
    try:
        held = _altair_positions(f)
        for it in futs:
            sym = ALTAIR_SYMBOLS[it.symbol]
            try:
                flt = f.filters(sym)
                log += run_futures_item(
                    it,
                    f,
                    job.job_id,
                    _now_ms,
                    time.sleep,
                    held=abs(held.get(it.symbol, Decimal(0))),
                    step=flt.step_size,
                    tick=flt.tick_size,
                )
            except AutoTradingError as exc:
                it.status = "working"  # 코인은 24 시간 — 다음 확인 때 다시
                log.append(f"{it.symbol} 바이낸스 선물 오류: {exc} — 다음 확인 때 다시")
                continue
            if it.status == "done":
                for stop in f.open_stops(sym):
                    try:
                        f.cancel_stop(sym, stop.client_order_id)
                    except AutoTradingError as exc:
                        log.append(f"{it.symbol} 스톱 취소 실패: {exc}")
                _altair_forget(it.symbol)
                log.append(f"{it.symbol} 선물 비상 스톱 지움")
    finally:
        f.close()
    return log


def step(job: Job) -> list[str]:
    """한 번 진행: 업비트는 끝날 때까지 몇 회, 토스는 상태 확인 · (장 열려 있으면) 주문."""
    now = _now_ms()
    log: list[str] = []
    ups = [it for it in job.items if it.venue == "upbit" and it.status not in ("done", "failed")]
    if ups:
        c = _upbit()
        try:
            for it in ups:
                try:
                    log += run_upbit_item(it, c, job.job_id, _now_ms, time.sleep)
                except AutoTradingError as exc:
                    it.status = "working"  # 업비트는 24 시간 — '장 열리면' 이 아니다
                    log.append(f"{it.symbol} 업비트 오류: {exc} — 다음 확인 때 다시")
        finally:
            c.close()
    bins = [it for it in job.items if it.venue == "binance" and it.status not in ("done", "failed")]
    if bins:
        b = _binance()
        if b is None:
            log.append("바이낸스 거래 키 없음 — Vega 바이낸스 몫은 앱에서 직접 팔아 주세요")
        else:
            try:
                for it in bins:
                    try:
                        f = b.filters(f"{it.symbol}USDT")
                        log += run_binance_item(
                            it,
                            b,
                            job.job_id,
                            _now_ms,
                            time.sleep,
                            step=f.step_size,
                            min_notional=f.min_notional,
                        )
                    except AutoTradingError as exc:
                        it.status = "working"  # 코인은 24 시간 — 다음 확인 때 다시
                        log.append(f"{it.symbol} 바이낸스 오류: {exc} — 다음 확인 때 다시")
            finally:
                b.close()
    futs = [
        it for it in job.items if it.venue == "binance_fut" and it.status not in ("done", "failed")
    ]
    if futs:
        log += _step_futures(job, futs)
    toss = [
        it
        for it in job.items
        if it.venue in ("toss_us", "toss_kr") and it.status not in ("done", "failed")
    ]
    groups = groups_of(job.scope)
    first = not job.log and (
        "deneb" in groups
        or "acc" in groups
        or "stock" in groups
        or symbol_of(job.scope) is not None
    )  # 팔 것이 없어도 매수 취소는 한 번
    if toss or first:
        r = _reader()
        try:
            held = r.holdings()
            trader = _trader(held, {it.symbol for it in toss})
            try:
                log += _cancel_buys(trader, job)
                us = _us_open(r, now) if any(i.venue == "toss_us" for i in toss) else False
                kr = _kr_open(r, now) if any(i.venue == "toss_kr" for i in toss) else False
                for it in toss:
                    log += run_toss_item(
                        it,
                        trader,
                        job.job_id,
                        _now_ms(),
                        session_open=us if it.venue == "toss_us" else kr,
                    )
                    take, usd = deneb_to_book(it)
                    if take > 0:
                        _book_deneb(it.symbol, take, usd)
                        it.deneb_booked += take
                        log.append(f"Deneb 장부: {it.symbol} {take:f}주 빼고 ${usd:,.2f} 현금")
            finally:
                trader.close()
        except AutoTradingError as exc:
            log.append(f"토스 오류: {exc} — 다음 확인 때 다시")
        finally:
            r.close()
    job.done = all(it.status in ("done", "failed") for it in job.items)
    stamp = _kst(now).strftime("%m-%d %H:%M")
    job.log += [f"{stamp} {x}" for x in log]
    return log


def start(scope: str, source: str) -> str:
    if scope not in SCOPES and not symbol_of(scope):
        raise SystemExit(f"범위는 {', '.join(SCOPES)} 또는 sym:종목 중 하나")
    with STORE.running() as ok:
        if not ok:
            return "다른 정리가 실행 중이에요 — 잠시 뒤 결과를 확인해 주세요"
        now = _now_ms()
        job_id = _kst(now).strftime("%Y%m%dT%H%M%S")
        reason = f"전부 정리({scope_label(scope)}) {_kst(now):%Y-%m-%d %H:%M} · {source}"
        set_group_halts(HALT, groups_of(scope), reason)
        items, notes = plan(scope)
        groups = groups_of(scope)
        handover = (
            {symbol_of(scope) or ""}
            if symbol_of(scope)
            else (_stock_symbols() if "stock" in groups else set())
        )
        notes += _handover_stock(handover, reason)
        job = Job(job_id, scope, source, now, items, notes)
        STORE.put(job)  # 주문 전에 남긴다
        step(job)
        STORE.put(job)
        text = job_text(job)
        if job.done:
            job.reported = True
            STORE.put(job)
        _notify(text)
        return text


def resume() -> list[str]:
    """진행 중 정리를 이어 한다. 끝나면 마지막 보고 한 번."""
    out: list[str] = []
    if not STORE.active():
        return out
    with STORE.running() as ok:
        if not ok:
            return out
        for job in STORE.active():
            if not job.done:
                log = step(job)
                STORE.put(job)
                note = "\n".join(log)
                if log and not job.done and note != job.last_note:
                    job.last_note = note
                    STORE.put(job)
                    _notify(f"🧯 정리 진행 · {job.job_id}\n{note}")
            if job.done and not job.reported:
                job.reported = True
                STORE.put(job)
                _notify(job_text(job) + "\n✅ 정리 끝")
            out.append(job_text(job))
    return out


def halts() -> dict[str, str | None]:
    return {g: group_halted(HALT, g) for g in GROUPS}


def unhalt(group: str, source: str) -> str:
    paths = [group_path(HALT, group), legacy_path(HALT, group)]
    found = [p for p in paths if p is not None and p.exists()]
    if not found:
        return f"{GROUPS[group]} 는 정지 중이 아니에요"
    for p in found:
        p.unlink()
    text = f"▶ {GROUPS[group]} 정지 풀림 ({source}) — 다음 실행부터 그 전략 규칙대로 다시 사요" + (
        " · 전체 비상 정지는 아직 켜져 있어요" if HALT.exists() else ""
    )
    _notify(text)
    return text


def status() -> str:
    jobs = STORE.jobs()
    lines = [f"{GROUPS[g]}: {'⛔ ' + r if r else '정상'}" for g, r in halts().items()]
    if jobs:
        lines += ["", job_text(jobs[-1])]
    return "\n".join(lines)


def main(argv: list[str]) -> int:
    if argv[:1] == ["preview"]:
        print(preview_text(argv[1] if len(argv) > 1 else "all"))
    elif argv[:1] == ["start"] and len(argv) == 3:
        print(start(argv[1], argv[2]))
    elif argv == ["resume"]:
        for t in resume():
            print(t)
    elif argv == ["status"]:
        print(status())
    elif argv[:1] == ["unhalt"] and len(argv) == 2 and argv[1] in GROUPS:
        print(unhalt(argv[1], "명령"))
    else:
        raise SystemExit(__doc__)
    return 0


if __name__ == "__main__":
    for _stream in (sys.stdout, sys.stderr):
        _reconfigure = getattr(_stream, "reconfigure", None)
        if _reconfigure is not None:
            _reconfigure(encoding="utf-8", errors="replace")
    raise SystemExit(main(sys.argv[1:]))
