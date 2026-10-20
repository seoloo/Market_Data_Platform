"""🇰🇷 국내 주식 매일 적립 실행기 (사용자 2026-10-10 "국내 주식도 포함").

언제 실행: 평일 12:10 KST ``AutoTrading_Toss_KR`` (``run``).
읽고 쓰는 것: 설정(추가한 국내 종목 ``x.KR.<코드>``) · 토스 API →
              장부 ``data/toss/KR_<코드>.json`` · 알림 기록 ``reports/toss/outbox.log``.

사용 예::

    python -m scripts.toss_kr run      # 어제 주문 체결 확인 → 살 차례면 1 주 지정가 (예약)
    python -m scripts.toss_kr plan     # 주문 없이 오늘 계획만 화면에

* 토스 API 에 국내 장 마감 지정가가 없어 **당일(DAY) 지정가 = 현재가 +0.5% 호가 올림**,
  하루 1 건 · 1 주. 남은 수량은 장 마감에 저절로 취소되고,
  체결은 다음 실행에서 주문 상세로 확인한다.
* 정지 파일(``data/HALT`` · ``HALT_acc``) · ``TOSS_TRADING_ENABLED`` 가 꺼져 있으면 주문하지 않는다.
* 주문 · 체결 · 실패 · 원화 부족만 투자 봇으로 알린다. 계산은 ``ops/kr_accumulate.py``.
"""

from __future__ import annotations

import json
import sys
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

from auto_trading.config.secrets import load_telegram_credentials, load_toss_credentials
from auto_trading.core.errors import AutoTradingError, OrderStateUnknownError
from auto_trading.exchange.toss import TossApiError, TossTokenCache, TossTrader
from auto_trading.notify.channels import NotifyError, OutboxNotifier, TelegramNotifier
from auto_trading.notify.style import RULE, sub
from auto_trading.ops import user_settings
from auto_trading.ops.kill_switch import halted_for
from auto_trading.ops.kr_accumulate import MAX_ORDER_QTY, KrOrder, KrState, advise, settle
from auto_trading.ops.ledger_file import write_json_atomic

REPO = Path(__file__).resolve().parent.parent
DATA = REPO / "data" / "toss"
REPORTS = REPO / "reports" / "toss"
HALT = REPO / "data" / "HALT"
TOKEN = REPO / "data" / "toss_token.json"
MAX_LIMIT_KRW = Decimal(2_000_000)
"""1 주 지정가 상한 — 하루 3 만 원 적립에 200 만 원 넘는 주식은 맞지 않는다."""
TAG = "🇰🇷 [국내 적립 · 토스]"
ORDER_HOURS = ("09:05", "15:10")
"""이 시각(KST) 사이에만 주문한다 — 늦게 켜진 PC 가 장 밖에 내지 않게."""


def _kst_now() -> datetime:
    """[쓰임] 지금 한국 시각 (시험이 바꿔 끼운다)."""
    return datetime.now(UTC) + timedelta(hours=9)


def _items() -> list[dict[str, Any]]:
    """[쓰임] 설정 화면에서 추가한 국내 종목들 (뺀 것도 포함 — 지난 주문 체결 확인용)."""
    extras = user_settings.load(REPO).get("_extras") or []
    return [dict(item) for item in extras if item.get("mkt") == "KR"]


def _path(symbol: str) -> Path:
    return DATA / f"KR_{symbol}.json"


def _load(item: dict[str, Any], today: str) -> KrState:
    """[쓰임] 종목 장부 (없거나 깨졌으면 새 장부 — 적립은 추가한 다음 날부터 쌓인다)."""
    try:
        return KrState.from_dict(json.loads(_path(str(item["sym"])).read_text(encoding="utf-8")))
    except (OSError, ValueError, KeyError):
        etf = str(item.get("kind")) in ("ETF", "ETN", "FOREIGN_ETF")
        return KrState(
            str(item["sym"]),
            str(item.get("name") or item["sym"]),
            etf,
            str(item.get("added") or today),  # 적립은 추가한 다음 날부터 쌓인다
            market=str(item.get("market") or ""),
        )


def _save(state: KrState) -> None:
    write_json_atomic(_path(state.symbol), state.to_dict())


def _trader(symbols: set[str]) -> TossTrader:
    """[쓰임] 토스 주문 창구 — 켜진 국내 종목만, 한 번 :data:`MAX_ORDER_QTY` 주까지, 사기만,
    지정가 200 만 원 상한."""
    return TossTrader(
        load_toss_credentials(REPO / ".env"),
        TossTokenCache(TOKEN),
        allowed_symbols=frozenset(symbols),
        max_qty=dict.fromkeys(symbols, MAX_ORDER_QTY),
        sell_symbols=frozenset(),
        max_limit_krw=MAX_LIMIT_KRW,
    )


def _notify(text: str) -> None:
    """[쓰임] 알림 — 기록 파일에 남기고 텔레그램으로도 (실패해도 실행은 계속)."""
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


def _settle_past_orders(
    states: dict[str, KrState], trader: TossTrader, today: str, lines: list[str]
) -> bool:
    """[쓰임] 어제까지 낸 주문을 토스 주문 상세로 정리 — 체결분은 장부에 넣는다.
    알릴 문제가 있었으면 True.
    [이유] 결과를 모르는 주문(UNKNOWN)은 다시 보내지 않고 사람에게 확인을 부탁한다."""
    alert = False
    for state in states.values():
        for order in state.orders:
            if order.date >= today or order.status not in ("SENT", "UNKNOWN"):
                continue
            if order.status == "UNKNOWN" or not order.order_id:
                order.status = "EXPIRED"
                lines.append(
                    f"⚠ {state.name} {order.date} 주문 결과 모름 — 토스 앱에서 확인해 주세요"
                )
                alert = True
                continue
            status = trader.order_status(order.order_id)
            filled_krw = float(status.filled_amount or status.filled_qty * (status.avg_price or 0))
            buy = settle(
                order,
                status.status,
                float(status.filled_qty),
                filled_krw,
                float(status.commission or 0),
            )
            if buy:
                state.buys.append(buy)
                lines.append(f"✅ {state.name} {buy['qty']:g}주 체결 · {buy['cost_krw']:,.0f}원")
            elif order.status != "SENT":
                lines.append(
                    f"▫️ {state.name} {order.date} 주문 미체결 ({status.status}) — 적립금은 그대로"
                )
    return alert


def run(*, dry: bool = False) -> int:
    """[쓰임] 하루치 — 지난 주문 정리 → (장 시간 · 개장일이면) 살 차례인 종목마다
    모인 돈만큼(정수 주) 당일 지정가.
    [이유] 원화가 모자라면 주문하지 않고 알린다 (거절보다 먼저 알아채게). ``dry`` 면 계획만 출력."""
    now = _kst_now()
    today = now.strftime("%Y-%m-%d")
    items = _items()
    if not items:
        print("국내 적립 종목 없음 (오르빗 설정에서 추가)")
        return 0
    active = {str(item["sym"]) for item in items if not item.get("removed")}
    trader = _trader(active or {"000000"})
    lines: list[str] = []
    try:
        # 1 단계: 지난 주문 정리
        states = {str(item["sym"]): _load(item, today) for item in items}
        alert = _settle_past_orders(states, trader, today, lines)
        # 2 단계: 주문할 수 있는 때인가 (장 시간 · 개장일 · 켜진 종목)
        in_hours = ORDER_HOURS[0] <= now.strftime("%H:%M") <= ORDER_HOURS[1]
        if not active or not in_hours or not trader.kr_open_today(today):
            for state in states.values():
                _save(state)
            if lines:
                _notify("\n".join([f"{TAG} · {today}", RULE, *lines]))
            print("\n".join(lines) or "주문 시간 밖 · 휴장 · 켜진 종목 없음 중 하나")
            return 0
        # 3 단계: 종목마다 살 차례면 모인 돈으로 살 수 있는 만큼(정수 주)
        prices = {symbol: float(price) for symbol, price in trader.prices(sorted(active)).items()}
        buying_power = float(trader.buying_power("KRW"))
        enabled = trader.credentials.trading_enabled
        halted = halted_for(HALT, "acc")  # 전체 정지 또는 토스 적립 정지
        for item in items:
            symbol = str(item["sym"])
            state = states[symbol]
            if symbol not in active or symbol not in prices:
                continue
            history = user_settings.acc_history(REPO, symbol)
            advice = advise(state, history, today, prices[symbol])
            print(
                f"{state.name}: 쌓인 {advice.accrued:,.0f}원 / 1주 {advice.need:,.0f}원 "
                f"(지정가 {advice.limit:,}원) → {advice.status}"
            )
            if advice.status != "BUY" or dry:
                continue
            if not enabled or halted:
                lines.append(
                    f"⏸ {state.name} 살 차례지만 주문 안 함 — {halted or '자동 주문 꺼짐'}"
                )
                continue
            qty = min(advice.qty, int(buying_power // advice.need))  # 원화가 모자라면 되는 만큼
            if qty < 1:
                lines.append(
                    f"💸 {state.name} 살 차례인데 원화가 모자라요 — "
                    f"필요 {advice.need:,.0f}원 · 있음 {buying_power:,.0f}원"
                )
                alert = True
                continue
            client_id = f"KR{symbol}-{today}"
            order = KrOrder(today, advice.limit, client_id)
            state.orders.append(order)
            try:
                result = trader.order(
                    symbol,
                    "BUY",
                    qty,
                    Decimal(advice.limit),
                    client_order_id=client_id,
                    time_in_force="DAY",
                )
            except OrderStateUnknownError as exc:
                order.status = "UNKNOWN"
                order.message = str(exc)
                lines.append(f"⚠ {state.name} 주문 전송 결과 모름 — 다시 보내지 않아요")
                alert = True
                continue
            if result.accepted:
                order.order_id = result.code  # 성공이면 주문 ID
                buying_power -= advice.need * qty
                lines.append(
                    f"🛒 {state.name} {qty}주 지정가 {advice.limit:,}원 (오늘 장중 체결 기다림)"
                )
            else:
                order.status = "REJECTED"
                order.message = f"{result.code} {result.message}"
                lines.append(f"❌ {state.name} 주문 거절 — {result.message}")
                alert = True
        for state in states.values():
            _save(state)
    except (AutoTradingError, TossApiError) as exc:
        text = f"⚠ {TAG} 실패\n{RULE}\n{exc}"
        _notify(text)
        print(text)
        return 1
    finally:
        trader.close()
    # 4 단계: 알림
    if lines:
        head = f"{TAG} · {today}"
        _notify("\n".join([head, RULE, *lines, *([sub("확인이 필요해요")] if alert else [])]))
        print("\n".join(lines))
    return 0


def main(argv: list[str]) -> int:
    if argv == ["run"]:
        return run()
    if argv == ["plan"]:
        return run(dry=True)
    raise SystemExit("usage: python -m scripts.toss_kr run|plan")


if __name__ == "__main__":
    for _stream in (sys.stdout, sys.stderr):
        _reconfigure = getattr(_stream, "reconfigure", None)
        if _reconfigure is not None:
            _reconfigure(encoding="utf-8", errors="replace")
    raise SystemExit(main(sys.argv[1:]))
