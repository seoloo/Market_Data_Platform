"""🧯 '전부 정리' 실행기 — 사용자가 텔레그램 · 오르빗에서 누를 때만 돈다 (사용자 2026-10-11).

언제 실행: 누를 때 ``start`` · 텔레그램 봇이 2 분마다 ``resume`` (끝날 때까지 이어 감).
읽고 쓰는 것: 업비트 · 토스 (· 옛 바이낸스) API → 작업 기록 ``data/liquidate/jobs.json`` ·
              그룹 정지 ``data/HALT_<그룹>`` · Deneb 장부 ``data/toss/DENEB.json`` (판 몫 빼기) ·
              💹 개별주 장부(감시 넘기기) · 알림 기록 ``reports/liquidate/outbox.log``.

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
from auto_trading.exchange.upbit import UpbitClient, upbit_trading_enabled
from auto_trading.notify.channels import NotifyError, OutboxNotifier, TelegramNotifier
from auto_trading.ops import sirius_live as sirius_core
from auto_trading.ops.deneb_live import SYMBOLS as DENEB_SYMBOLS
from auto_trading.ops.deneb_live import DenebOrder, DenebState
from auto_trading.ops.kill_switch import (
    GROUPS,
    group_halted,
    group_path,
    legacy_path,
)
from auto_trading.ops.ledger_file import write_json_atomic
from auto_trading.ops.liquidate import (
    SCOPES,
    VENUE_NAMES,
    Item,
    Job,
    Store,
    acc_symbol_of,
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
FINISHED = ("done", "failed")
"""팔 것 하나의 끝난 상태."""


def _now_ms() -> int:
    """[쓰임] 지금 시각(ms) — 시험이 바꿔 끼운다."""
    return int(time.time() * 1000)


def _kst(ms: int) -> datetime:
    return datetime.fromtimestamp(ms / 1000, UTC) + KST


def _upbit() -> UpbitClient:
    """[쓰임] 업비트 창구 (``UPBIT_TRADING_ENABLED=true`` 일 때만 주문 열림)."""
    env = read_env_file(REPO / ".env")
    return UpbitClient(
        env["UPBIT_API_KEY"],
        env["UPBIT_AOU_SECRET"],
        trading_enabled=upbit_trading_enabled(env),
    )


def _binance() -> BinanceSpotLive | None:
    """[쓰임] 옛 Vega 바이낸스 거래 키(2026-10-15 ~ 10-19).

    키가 없으면 None — 바이낸스 몫은 없다고 본다."""
    try:
        credentials, enabled = load_live_spot(REPO / ".env")
    except ConfigError:
        return None
    return BinanceSpotLive(credentials, trading_enabled=enabled)


ALTAIR_STATE = REPO / "data" / "altair_live" / "state.json"
ALTAIR_SYMBOLS = {"BTC": "BTCUSDC", "ETH": "ETHUSDC"}
"""옛 Altair 실거래 종목 (은퇴한 retired/scripts/altair_live 와 같음)."""


def _futures() -> BinanceUsdmLive | None:
    """[쓰임] 옛 Altair 선물 키. 없으면 None — Altair 몫은 없다고 본다."""
    try:
        credentials, enabled = load_live_futures(REPO / ".env")
    except ConfigError:
        return None
    return BinanceUsdmLive(credentials, trading_enabled=enabled)


def _altair_positions(client: BinanceUsdmLive) -> dict[str, Decimal]:
    """[쓰임] 바이낸스 선물 포지션 {코인: 부호 수량} (0 인 것은 뺀다)."""
    account = client.account()
    positions = {}
    for coin, symbol in ALTAIR_SYMBOLS.items():
        if symbol in account.positions and account.positions[symbol].quantity != 0:
            positions[coin] = account.positions[symbol].quantity
    return positions


def _altair_forget(coin: str) -> None:
    """[쓰임] 정리한 코인의 Altair 숏 · 스톱 · 메이커 기록을 지운다.
    [이유] Altair 실행기가 사라진 포지션을 '스톱 발동' 으로 잘못 읽지 않게 (지금은 은퇴)."""
    try:
        state = json.loads(ALTAIR_STATE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return
    state.get("shorts", {}).pop(coin, None)
    state.get("stops", {}).pop(coin, None)
    state.get("maker", {}).pop(coin, None)  # 걸어 둔 메이커 주문 기록 (문서 269)
    if state.get("maker") == {}:
        state.pop("maker")
    write_json_atomic(ALTAIR_STATE, state)


SIZE_STATE = REPO / "data" / "size_guard" / "state.json"
"""💹 개별주 장부 (scripts/size_guard)."""


def _stock_tickets() -> list[dict[str, Any]]:
    """[쓰임] 💹 개별주 장부의 티켓 목록 (없거나 깨지면 빈 목록)."""
    try:
        data = json.loads(SIZE_STATE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    return [ticket for ticket in data.get("tickets") or [] if isinstance(ticket, dict)]


def _stock_qty() -> dict[str, Decimal]:
    """[쓰임] 개별주 장부가 들고 있는 수량 (보유 · 손절 매도 중) = 산 수량 − 판 수량."""
    held: dict[str, Decimal] = {}
    for ticket in _stock_tickets():
        if ticket.get("status") not in ("holding", "selling"):
            continue
        sold = sum(float(sell.get("filled", 0)) for sell in ticket.get("sells") or [])
        qty = Decimal(str(float(ticket.get("filled", 0)) - sold))
        if qty > 0:
            symbol = str(ticket["symbol"]).upper()
            held[symbol] = held.get(symbol, Decimal(0)) + qty
    return held


def _stock_symbols() -> set[str]:
    """[쓰임] 개별주 장부가 살아 있는(예약 · 매수 중 · 보유 · 매도 중) 종목들."""
    return {
        str(ticket["symbol"]).upper()
        for ticket in _stock_tickets()
        if ticket.get("status") in ("reserved", "buying", "holding", "selling")
    }


def _handover_stock(symbols: set[str], reason: str) -> list[str]:
    """[쓰임] 정리로 넘긴 개별주: 예약 · 매수 중은 취소, 보유 · 손절 중은 감시 해제.
    [이유] 손절 감시와 정리가 같은 주식을 겹쳐 팔지 않게."""
    if not symbols:
        return []
    from scripts import size_guard

    lines: list[str] = []
    try:
        lock = size_guard._Lock().__enter__()
    except SystemExit:
        return ["개별주 장부가 잠겨 있어 손절 감시를 못 풀었어요 — 정리 매도는 그대로 해요"]
    try:
        tickets = size_guard.load_tickets()
        for ticket in tickets:
            if ticket.symbol.upper() not in symbols or not ticket.active:
                continue
            new_status = "canceled" if ticket.status in ("reserved", "buying") else "released"
            ticket.status, ticket.note, ticket.closed_ms = new_status, reason, _now_ms()
            what = "예약 취소" if new_status == "canceled" else "손절 감시 끝"
            lines.append(f"개별주 {ticket.symbol} {what}")
        size_guard.save_tickets(tickets)
    finally:
        lock.__exit__(None, None, None)
    return lines


def _reader() -> TossReader:
    return TossReader(load_toss_credentials(REPO / ".env"), TossTokenCache(TOKEN))


def _trader(holdings: dict[str, TossPosition], symbols: set[str]) -> TossTrader:
    """[쓰임] 정리용 토스 창구 — 지금 보유 종목만 팔 수 있고, 수량은 보유 이하,
    지정가는 현재가 2 배 이하.
    [이유] 정리 중 실수로 다른 종목을 팔거나 엉뚱한 가격이 나가지 않게."""
    allowed = set(holdings) | symbols
    us_prices = [position.last for symbol, position in holdings.items() if not is_kr_symbol(symbol)]
    kr_prices = [position.last for symbol, position in holdings.items() if is_kr_symbol(symbol)]
    return TossTrader(
        load_toss_credentials(REPO / ".env"),
        TossTokenCache(TOKEN),
        allowed_symbols=frozenset(allowed),
        max_qty={symbol: max(1, int(position.qty)) for symbol, position in holdings.items()},
        sell_symbols=frozenset(holdings),
        max_limit_usd=max(us_prices, default=Decimal(1)) * 2,
        max_limit_krw=max(kr_prices, default=Decimal(1)) * 2,
    )


def _deneb_qty() -> dict[str, Decimal]:
    """[쓰임] Deneb 장부 보유 {종목: 수량} (0 인 것은 뺀다)."""
    try:
        data = json.loads(DENEB_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return {
        symbol: Decimal(str(qty))
        for symbol, qty in (data.get("qty") or {}).items()
        if float(qty) > 0
    }


def _acc_qty(holdings: dict[str, Decimal]) -> dict[str, Decimal]:
    """[쓰임] 토스 적립 장부가 산 몫 {종목: 수량}.
    미국 = 보유 − 적립 시작 전 수량(``baseline_qty``), 국내 = 장부의 체결 매수 합.
    [이유] 장부가 없는 종목은 0 — 사람이 직접 산 것(수동 보유)으로 보고 팔지 않는다."""
    acc: dict[str, Decimal] = {}
    for path in sorted(TOSS_DATA.glob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(data, dict):
            continue
        if path.name.startswith("KR_"):
            symbol = path.stem[3:]
            bought = sum(
                (Decimal(str(buy.get("qty", 0))) for buy in data.get("buys") or []), Decimal(0)
            )
            if bought > 0:
                acc[symbol] = bought
        elif "baseline_qty" in data and data.get("symbol"):
            symbol = str(data["symbol"]).upper()
            baseline = Decimal(str(data["baseline_qty"]))
            acc[symbol] = max(Decimal(0), holdings.get(symbol, Decimal(0)) - baseline)
    return acc


def _notify(text: str) -> None:
    """[쓰임] 알림 — 기록 파일에 남기고 텔레그램으로도 (실패해도 계속)."""
    REPORTS.mkdir(parents=True, exist_ok=True)
    OutboxNotifier(REPORTS / "outbox.log").send(text)
    credentials = load_telegram_credentials(REPO / ".env")
    if credentials is None:
        return
    bot = TelegramNotifier(credentials)
    try:
        bot.send(text)
    except NotifyError as exc:
        print(f"텔레그램 실패: {exc}")
    finally:
        bot.close()


# ------------------------------------------------------------------ 계획


def _upbit_balances_prices(notes: list[str]) -> tuple[dict[str, Decimal], dict[str, Decimal]]:
    """[쓰임] 업비트 (잔고, Vega 코인 현재가). 조회 실패는 메모로."""
    balances: dict[str, Decimal] = {}
    prices: dict[str, Decimal] = {}
    client = _upbit()
    try:
        balances = client.balances()
        for coin, market in MARKETS.items():
            price = client.tickers([market]).get(market)
            if price is not None:
                prices[coin] = price
    except AutoTradingError as exc:
        notes.append(f"업비트 조회 실패: {exc}")
    finally:
        client.close()
    return balances, prices


def _binance_balances(
    notes: list[str],
) -> tuple[dict[str, Decimal], dict[str, Decimal], dict[str, Decimal]]:
    """[쓰임] 옛 바이낸스 현물 (잔고, 가격, 최소 주문). 키가 없으면 모두 빈 값."""
    balances: dict[str, Decimal] = {}
    prices: dict[str, Decimal] = {}
    minimums: dict[str, Decimal] = {}
    client = _binance()
    if client is None:
        return balances, prices, minimums
    try:
        balances = client.balances()
        for coin in COINS:
            if balances.get(coin, Decimal(0)) > 0:
                prices[coin] = client.last_price(f"{coin}USDT")
                minimums[coin] = client.filters(f"{coin}USDT").min_notional
    except AutoTradingError as exc:
        notes.append(f"바이낸스 조회 실패: {exc}")
    finally:
        client.close()
    return balances, prices, minimums


def plan(scope: str) -> tuple[list[Item], list[str]]:
    """[쓰임] 범위 → (팔 것, 메모). 조회만 한다 (주문 없음).
    [이유] 조회가 한 곳 실패해도 나머지는 계획한다 — 실패는 메모로 보여 준다."""
    groups = groups_of(scope)
    notes: list[str] = []
    # 1 단계: 코인 (업비트 · 옛 바이낸스 현물)
    upbit_balances: dict[str, Decimal] = {}
    upbit_prices: dict[str, Decimal] = {}
    if "vega" in groups:
        upbit_balances, upbit_prices = _upbit_balances_prices(notes)
    binance_balances: dict[str, Decimal] = {}
    binance_prices: dict[str, Decimal] = {}
    binance_min: dict[str, Decimal] = {}
    if "vega" in groups:
        binance_balances, binance_prices, binance_min = _binance_balances(notes)
    # 2 단계: 옛 Altair 선물
    futures_positions: dict[str, Decimal] = {}
    if "altair" in groups:
        futures = _futures()
        if futures is None:
            notes.append("Altair 선물 키 없음 — 바이낸스 선물 포지션은 확인 못 함")
        else:
            try:
                futures_positions = _altair_positions(futures)
            except AutoTradingError as exc:
                notes.append(f"바이낸스 선물 조회 실패: {exc}")
            finally:
                futures.close()
    # 3 단계: 토스 보유 · 팔 수 있는 수량
    holdings: dict[str, Decimal] = {}
    sellable: dict[str, Decimal] = {}
    only_symbol = symbol_of(scope)
    only_acc = acc_symbol_of(scope)
    toss_needed = "deneb" in groups or "acc" in groups or "stock" in groups
    if toss_needed or only_symbol or only_acc:
        reader = _reader()
        try:
            held = reader.holdings()
            holdings = {symbol: position.qty for symbol, position in held.items()}
            sellable = {symbol: reader.sellable(symbol) for symbol in held}
        except AutoTradingError as exc:
            notes.append(f"토스 조회 실패: {exc}")
        finally:
            reader.close()
    deneb_qty = {symbol: qty for symbol, qty in _deneb_qty().items() if symbol in DENEB_SYMBOLS}
    items, more = build_items(
        groups,
        upbit_balances=upbit_balances,
        upbit_prices=upbit_prices,
        vega_coins=COINS,
        toss_holdings=holdings,
        toss_sellable=sellable,
        deneb_qty=deneb_qty,
        acc_qty=_acc_qty(holdings),
        is_kr=is_kr_symbol,
        binance_balances=binance_balances,
        binance_prices=binance_prices,
        binance_min=binance_min,
        altair_positions=futures_positions,
        stock_qty=_stock_qty(),
        only_symbol=only_symbol,
        only_acc=only_acc,
    )
    return items, notes + more


def preview_text(scope: str = "all") -> str:
    """[쓰임] 누르기 전 미리 보기 글 — 그룹마다 팔 것 · 메모 · 누르면 무엇을 하는지 (주문 없음)."""
    items, notes = plan(scope)
    lines = [f"🧯 전부 정리 미리 보기 · {scope_label(scope)} (아직 아무것도 안 팔았어요)"]
    if symbol_of(scope) or acc_symbol_of(scope):
        lines.append(
            ", ".join(f"{VENUE_NAMES[item.venue]} {item.symbol} {item.want:f}주" for item in items)
            or "팔 것 없음"
        )
    for group in groups_of(scope):
        if group == "sirius":  # 아래 줄로 따로 (Sirius 실행기가 소수점으로 판다)
            continue
        rows = [item for item in items if group in item.groups]
        listed = ", ".join(
            f"{VENUE_NAMES[item.venue]} {item.symbol} {item.want:f}" for item in rows
        )
        lines.append(f"[{GROUPS[group]}] " + (listed or "팔 것 없음"))
    if "sirius" in groups_of(scope):
        lines.append(_sirius_preview())
    lines += [f"· {note}" for note in notes]
    if acc_symbol_of(scope):
        lines.append(
            "누르면: ① 그 종목 자동 적립 멈춤(적립 목록에서 빼기 — 다시 하려면 '적립' → 추가) "
            "② 그 종목 토스 미체결 매수 취소 ③ 적립 몫만 지정가로 매도 (토스는 정규장에서)"
        )
        return "\n".join(lines)
    if symbol_of(scope):
        lines.append(
            "누르면: ① 그 종목 토스 미체결 매수 취소 ② 지정가로 매도 (그룹 정지 없음 · "
            "토스는 정규장에서 — 닫혀 있으면 열린 뒤). 💹 개별주 손절 감시는 정리로 넘겨요"
        )
        return "\n".join(lines)
    lines.append(
        "누르면: ① 그 그룹 새 매수 정지 ② 토스 미체결 매수 취소 ③ 지정가로 매도 "
        "(업비트 · 바이낸스는 바로, 토스는 정규장에서 — 닫혀 있으면 열린 뒤)"
        + (" · Sirius 는 소수점 시장가" if "sirius" in groups_of(scope) else "")
    )
    return "\n".join(lines)


def _sirius_preview() -> str:
    """[쓰임] Sirius 몫 미리 보기 한 줄 (Sirius 실행기가 소수점 시장가로 판다)."""
    try:
        state = json.loads((REPO / "data" / "sirius" / "state.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return "[Sirius] 장부 없음"
    held = sirius_core.holdings(state)
    if not held:
        return "[Sirius] 팔 것 없음 (보유 없음)"
    rows = ", ".join(f"{symbol} {qty:.6g}" for symbol, (qty, _cost) in sorted(held.items()))
    return f"[Sirius] 토스 미국 소수점 {rows} — 정규장(마감 1 시간 전까지)에서 소수점 시장가로"


# ------------------------------------------------------------------ 장 시간


def _us_open(reader: TossReader, now_ms: int) -> bool:
    """[쓰임] 미국 정규장이 지금 열려 있나 — 시작 3 분 뒤 ~ 끝 10 분 전만 (호가가 안정된 때)."""
    ny_day = datetime.fromtimestamp(now_ms / 1000, NY).strftime("%Y-%m-%d")
    session = reader.us_regular_session(ny_day)
    if session is None:
        return False
    start_ms = datetime.fromisoformat(session[0]).timestamp() * 1000
    end_ms = datetime.fromisoformat(session[1]).timestamp() * 1000
    return start_ms + 3 * 60_000 <= now_ms < end_ms - 10 * 60_000


def _kr_open(reader: TossReader, now_ms: int) -> bool:
    """[쓰임] 국내 정규장이 지금 열려 있나 — 09:03 ~ 15:15 이고 개장일."""
    kst_now = _kst(now_ms)
    if not "09:03" <= kst_now.strftime("%H:%M") < "15:15":
        return False
    return reader.kr_open_today(kst_now.strftime("%Y-%m-%d"))


# ------------------------------------------------------------------ 실행


def _book_deneb(symbol: str, qty: Decimal, usd: Decimal, item: Any = None) -> None:
    """[쓰임] 팔린 Deneb 몫을 Deneb 장부(DENEB.json)에서 뺀다 — 수량 ↓ · 달러 현금 ↑.
    [이유] 매도도 장부 주문 목록에 ``kind="liquidate"`` · 체결 · ``booked`` 로 남긴다(2026-10-16 —
           없으면 오르빗에 산 기록만 보이고, 양도세 계산이 이 매도를 Deneb 몫으로 못 셌다).
           이미 적은 주문 번호는 다시 적지 않는다."""
    deneb = DenebState.from_dict(json.loads(DENEB_FILE.read_text(encoding="utf-8")))
    deneb.qty[symbol] = max(0.0, deneb.qty.get(symbol, 0.0) - float(qty))
    deneb.cash_usd += float(usd)
    orders = item.orders if item is not None else []
    closed_orders = [order for order in orders if order.closed and order.filled > 0]
    sold = sum((order.filled for order in closed_orders), Decimal(0))
    known_ids = {order.client_id for order in deneb.orders}
    for order in closed_orders:
        if order.client_id in known_ids or sold <= 0:
            continue
        share = float(qty / sold)
        deneb.orders.append(
            DenebOrder(
                date=_kst(order.placed_ms).strftime("%Y-%m-%d"),
                symbol=symbol,
                side="SELL",
                qty=int(order.filled),
                limit=float(order.price),
                client_id=order.client_id,
                order_id=order.order_id,
                status="FILLED",
                filled_qty=float(order.filled) * share,
                filled_usd=float(order.amount) * share,
                commission=float(order.fee) * share,
                kind="liquidate",
                booked=True,
            )
        )
    write_json_atomic(DENEB_FILE, deneb.to_dict(), newline_at_end=True)


def _cancel_buys(trader: TossTrader, job: Job) -> list[str]:
    """[쓰임] 정리 범위의 토스 미체결 **매수** 를 취소한다.
    [이유] 오늘 밤 장 마감 매수가 판 것을 다시 사지 않게."""
    groups = groups_of(job.scope)
    log = []
    only = symbol_of(job.scope) or acc_symbol_of(job.scope)
    for order in trader.open_orders():
        is_deneb = order.symbol in DENEB_SYMBOLS
        in_scope = (
            ("deneb" in groups and is_deneb)
            or ("acc" in groups and not is_deneb)
            or ("stock" in groups and order.symbol in _stock_symbols())
            or (only is not None and order.symbol == only)
        )
        if order.side != "BUY" or not in_scope or order.status.startswith("PENDING_CANCEL"):
            continue
        result = trader.cancel(order.order_id)
        tag = f"{order.symbol} {order.qty - order.filled_qty:f}주"
        if result.accepted:
            job.buys_canceled.append(tag)
            log.append(f"토스 미체결 매수 취소 {tag}")
        else:
            log.append(f"토스 매수 취소 실패 {tag} ({result.code})")
    return log


def _step_futures(job: Job, futures_items: list[Item]) -> list[str]:
    """[쓰임] 옛 Altair 선물 포지션 닫기 → 닫히면 그 종목 비상 스톱 지우기 · 장부 기록 정리."""
    log: list[str] = []
    client = _futures()
    if client is None:
        return ["Altair 선물 키 없음 — 바이낸스 앱에서 선물 포지션을 직접 닫아 주세요"]
    if not client.trading_enabled:
        client.close()
        return [
            "BINANCE_FUTURES_LIVE_TRADING_ENABLED 꺼짐 — 선물 포지션은 바이낸스 앱에서 직접"
            " 닫아 주세요 (또는 .env 에서 켜면 다음 확인 때 닫아요)"
        ]
    try:
        held = _altair_positions(client)
        for item in futures_items:
            symbol = ALTAIR_SYMBOLS[item.symbol]
            try:
                # 걸어 둔 메이커 주문(문서 269)이 정리 뒤 체결돼 다시 사지 않게 먼저 취소
                for open_order in client.open_orders(symbol):
                    client.cancel_order(symbol, open_order.client_order_id)
                    log.append(
                        f"{item.symbol} 걸어 둔 주문 취소"
                        f" ({open_order.side} {open_order.orig_qty:g})"
                    )
                filters = client.filters(symbol)
                log += run_futures_item(
                    item,
                    client,
                    job.job_id,
                    _now_ms,
                    time.sleep,
                    held=abs(held.get(item.symbol, Decimal(0))),
                    step=filters.step_size,
                    tick=filters.tick_size,
                )
            except AutoTradingError as exc:
                item.status = "working"  # 코인은 24 시간 — 다음 확인 때 다시
                log.append(f"{item.symbol} 바이낸스 선물 오류: {exc} — 다음 확인 때 다시")
                continue
            if item.status == "done":
                for stop in client.open_stops(symbol):
                    try:
                        client.cancel_stop(symbol, stop.client_order_id)
                    except AutoTradingError as exc:
                        log.append(f"{item.symbol} 스톱 취소 실패: {exc}")
                _altair_forget(item.symbol)
                log.append(f"{item.symbol} 선물 비상 스톱 지움")
    finally:
        client.close()
    return log


def _open_items(job: Job, venues: tuple[str, ...]) -> list[Item]:
    """[쓰임] 그곳(들)에서 아직 안 끝난 팔 것."""
    return [item for item in job.items if item.venue in venues and item.status not in FINISHED]


def _step_upbit(job: Job, items: list[Item]) -> list[str]:
    """[쓰임] 업비트 팔 것들 한 번 진행 (끝날 때까지 몇 회 IOC)."""
    log: list[str] = []
    client = _upbit()
    try:
        for item in items:
            try:
                log += run_upbit_item(item, client, job.job_id, _now_ms, time.sleep)
            except AutoTradingError as exc:
                item.status = "working"  # 업비트는 24 시간 — '장 열리면' 이 아니다
                log.append(f"{item.symbol} 업비트 오류: {exc} — 다음 확인 때 다시")
    finally:
        client.close()
    return log


def _step_binance(job: Job, items: list[Item]) -> list[str]:
    """[쓰임] 옛 바이낸스 현물 팔 것들 한 번 진행."""
    client = _binance()
    if client is None:
        return ["바이낸스 거래 키 없음 — Vega 바이낸스 몫은 앱에서 직접 팔아 주세요"]
    log: list[str] = []
    try:
        for item in items:
            try:
                filters = client.filters(f"{item.symbol}USDT")
                log += run_binance_item(
                    item,
                    client,
                    job.job_id,
                    _now_ms,
                    time.sleep,
                    step=filters.step_size,
                    min_notional=filters.min_notional,
                )
            except AutoTradingError as exc:
                item.status = "working"  # 코인은 24 시간 — 다음 확인 때 다시
                log.append(f"{item.symbol} 바이낸스 오류: {exc} — 다음 확인 때 다시")
    finally:
        client.close()
    return log


def _step_toss(job: Job, items: list[Item], now: int) -> list[str]:
    """[쓰임] 토스 한 번 진행 — 범위의 미체결 매수 취소 → 종목마다 상태 확인 · (장 열리면) 주문
    → 팔린 Deneb 몫은 Deneb 장부에서 뺀다."""
    log: list[str] = []
    reader = _reader()
    try:
        held = reader.holdings()
        trader = _trader(held, {item.symbol for item in items})
        try:
            log += _cancel_buys(trader, job)
            has_us = any(item.venue == "toss_us" for item in items)
            has_kr = any(item.venue == "toss_kr" for item in items)
            us_open = _us_open(reader, now) if has_us else False
            kr_open = _kr_open(reader, now) if has_kr else False
            for item in items:
                log += run_toss_item(
                    item,
                    trader,
                    job.job_id,
                    _now_ms(),
                    session_open=us_open if item.venue == "toss_us" else kr_open,
                )
                take, usd = deneb_to_book(item)
                if take > 0:
                    _book_deneb(item.symbol, take, usd, item)
                    item.deneb_booked += take
                    log.append(f"Deneb 장부: {item.symbol} {take:f}주 빼고 ${usd:,.2f} 현금")
        finally:
            trader.close()
    except AutoTradingError as exc:
        log.append(f"토스 오류: {exc} — 다음 확인 때 다시")
    finally:
        reader.close()
    return log


def step(job: Job) -> list[str]:
    """[쓰임] 정리 한 번 진행 — 업비트 · 옛 바이낸스는 끝날 때까지 몇 회, 선물 닫기,
    토스는 상태 확인 · (장 열려 있으면) 주문. 진행 기록 줄을 돌려주고 작업 ``log`` 에도 남긴다."""
    now = _now_ms()
    log: list[str] = []
    upbit_items = _open_items(job, ("upbit",))
    if upbit_items:
        log += _step_upbit(job, upbit_items)
    binance_items = _open_items(job, ("binance",))
    if binance_items:
        log += _step_binance(job, binance_items)
    futures_items = _open_items(job, ("binance_fut",))
    if futures_items:
        log += _step_futures(job, futures_items)
    toss_items = _open_items(job, ("toss_us", "toss_kr"))
    groups = groups_of(job.scope)
    # 팔 것이 없어도 첫 번에는 매수 취소를 한 번 한다
    first_toss_step = not job.log and (
        "deneb" in groups
        or "acc" in groups
        or "stock" in groups
        or symbol_of(job.scope) is not None
        or acc_symbol_of(job.scope) is not None
    )
    if toss_items or first_toss_step:
        log += _step_toss(job, toss_items, now)
    job.done = all(item.status in FINISHED for item in job.items)
    stamp = _kst(now).strftime("%m-%d %H:%M")
    job.log += [f"{stamp} {line}" for line in log]
    return log


def _unique_job_id(base: str) -> str:
    """[쓰임] 작업 번호 — 보통은 시작 시각 ``YYYYMMDDTHHMMSS``,
    같은 번호가 이미 있으면 ``-2`` · ``-3`` …
    [이유] 같은 초에 두 범위를 시작하면 번호가 같아져 뒤 작업이 앞 작업 기록을 덮어썼다
           (앞 작업 매도가 이어지지 않음, 2026-10-21 발견 · 사용자 '고쳐줘')."""
    taken = {job.job_id for job in STORE.jobs()}
    candidate = base
    number = 2
    while candidate in taken:
        candidate = f"{base}-{number}"
        number += 1
    return candidate


def start(scope: str, source: str) -> str:
    """[쓰임] 정리 시작 — 그룹 정지 → 계획 → (Sirius · 적립 한 종목 · 개별주 넘기기) → 작업 기록 →
    첫 진행 → 알림. 작업 요약 글을 돌려준다.
    [이유] 작업은 주문 전에 기록에 남긴다 — 도중에 멈춰도 다음 ``resume`` 이 이어 가게."""
    if scope not in SCOPES and not symbol_of(scope) and not acc_symbol_of(scope):
        raise SystemExit(f"범위는 {', '.join(SCOPES)} · sym:종목 · acc:종목 중 하나")
    with STORE.running() as ok:
        if not ok:
            return "다른 정리가 실행 중이에요 — 잠시 뒤 결과를 확인해 주세요"
        now = _now_ms()
        job_id = _unique_job_id(_kst(now).strftime("%Y%m%dT%H%M%S"))
        reason = f"전부 정리({scope_label(scope)}) {_kst(now):%Y-%m-%d %H:%M} · {source}"
        # 1 단계: 새 매수부터 막는다
        set_group_halts(HALT, groups_of(scope), reason)
        items, notes = plan(scope)
        groups = groups_of(scope)
        # 2 단계: 다른 실행기에 넘길 것 (Sirius · 적립 한 종목 · 개별주 감시)
        if "sirius" in groups:  # Sirius 소수점 몫은 Sirius 실행기가 정규장에서 판다
            sirius_core.request_liquidation(REPO, reason, now)
            notes += _sirius_liquidate()
        acc_symbol = acc_symbol_of(scope)
        if acc_symbol:  # 자동 적립 한 종목: 그 종목 적립을 멈춘다(다시 사지 않게)
            notes.append(_stop_acc(acc_symbol, source))
        if symbol_of(scope):
            handover = {symbol_of(scope) or ""}
        elif "stock" in groups:
            handover = _stock_symbols()
        else:
            handover = set()
        notes += _handover_stock(handover, reason)
        # 3 단계: 기록 → 첫 진행 → 알림
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


def _sirius_liquidate() -> list[str]:
    """[쓰임] Sirius 정리 요청이 있으면 한 걸음 (정규장 밖이면 대기 줄). 실패는 줄로."""
    if sirius_core.liquidation_request(REPO) is None:
        return []
    from scripts import sirius_live

    try:
        return sirius_live.liquidate_step()
    except AutoTradingError as exc:
        return [f"⚠ Sirius 정리 실패 — 다음 확인 때 ({exc})"]


def _sirius_resume() -> None:
    """[쓰임] 봇이 2 분마다: Sirius 정리 진행. 새 줄이 바뀌었을 때만 알린다."""
    request = sirius_core.liquidation_request(REPO)
    if request is None:
        return
    lines = _sirius_liquidate()
    note = "\n".join(lines)
    still_requested = sirius_core.liquidation_request(REPO) is not None
    if lines and note != request.get("last_note") and still_requested:
        request["last_note"] = note
        (REPO / sirius_core.LIQUIDATE).write_text(json.dumps(request, ensure_ascii=False), "utf-8")
        _notify(f"🧯 Sirius 정리 진행\n{note}")
    elif lines and any("정리 끝" in line for line in lines):
        _notify(f"🧯 Sirius\n{note}")


def _stop_acc(symbol: str, source: str) -> str:
    """[쓰임] 그 종목을 자동 적립 목록에서 뺀다(설정 화면 '빼기' 와 같음). 보유는 정리가 판다."""
    from datetime import UTC, datetime

    from auto_trading.ops import user_settings

    current = user_settings.load(REPO)
    hit = None
    for item in user_settings.acc_items(current):
        if item["sym"] == symbol:
            hit = item
            break
    if hit is None:
        return f"{symbol} 는 적립 목록에 없음 (이미 뺐거나 적립 종목 아님)"
    new = user_settings.remove_item(current, hit["key"])
    user_settings.apply(
        REPO, user_settings.diff(current, new), datetime.now(UTC), by=f"정리 · {source}", new=new
    )
    return f"{symbol} 자동 적립 멈춤 (목록에서 뺌 — 다시 하려면 '적립' → 추가)"


def resume() -> list[str]:
    """[쓰임] 진행 중 정리를 이어 한다 (봇이 2 분마다). 끝나면 마지막 보고 한 번.
    [이유] 같은 진행 내용(예: 같은 오류)은 2 분마다 다시 보내지 않는다 (``last_note``)."""
    _sirius_resume()
    texts: list[str] = []
    if not STORE.active():
        return texts
    with STORE.running() as ok:
        if not ok:
            return texts
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
            texts.append(job_text(job))
    return texts


def halts() -> dict[str, str | None]:
    """[쓰임] 그룹마다 정지 사유 (정지 아니면 None)."""
    return {group: group_halted(HALT, group) for group in GROUPS}


def unhalt(group: str, source: str) -> str:
    """[쓰임] 그룹 정지 풀기 (옛 이름 정지 파일도 지운다). Sirius 면 정리 요청도 취소."""
    paths = [group_path(HALT, group), legacy_path(HALT, group)]
    found = [path for path in paths if path is not None and path.exists()]
    if not found:
        return f"{GROUPS[group]} 는 정지 중이 아니에요"
    for path in found:
        path.unlink()
    if group == "sirius" and sirius_core.clear_liquidation(REPO):
        _notify("✨ Sirius 정리 요청도 취소 — 남은 보유는 그대로")
    text = f"▶ {GROUPS[group]} 정지 풀림 ({source}) — 다음 실행부터 그 전략 규칙대로 다시 사요"
    if HALT.exists():
        text += " · 전체 비상 정지는 아직 켜져 있어요"
    _notify(text)
    return text


def status() -> str:
    """[쓰임] 그룹 정지 상태 + 마지막 작업 요약."""
    jobs = STORE.jobs()
    lines = [
        f"{GROUPS[group]}: {'⛔ ' + reason if reason else '정상'}"
        for group, reason in halts().items()
    ]
    if jobs:
        lines += ["", job_text(jobs[-1])]
    return "\n".join(lines)


def main(argv: list[str]) -> int:
    """[쓰임] 명령 이름 → 함수 (봇은 ``resume`` 을 부른다)."""
    if argv[:1] == ["preview"]:
        print(preview_text(argv[1] if len(argv) > 1 else "all"))
    elif argv[:1] == ["start"] and len(argv) == 3:
        print(start(argv[1], argv[2]))
    elif argv == ["resume"]:
        for text in resume():
            print(text)
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
