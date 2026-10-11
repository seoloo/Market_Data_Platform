"""국내 주식 매일 적립 실행 — 평일 12:10 KST (사용자 2026-10-10 "국내 주식도 포함").

사용 예::

    python -m scripts.toss_kr run      # 어제 주문 체결 확인 → 살 차례면 1 주 지정가 (예약)
    python -m scripts.toss_kr plan     # 주문 없이 오늘 계획만 화면에

* 종목 · 하루 금액은 오르빗 설정 화면에서 추가한 국내 종목(``x.KR.<코드>``)이다.
* 토스 API 에 국내 장 마감 지정가가 없어 **당일(DAY) 지정가 = 현재가 +0.5% 호가 올림**,
  하루 1 건 · 1 주.
  남은 수량은 장 마감에 저절로 취소되고, 체결은 다음 실행에서 주문 상세로 확인한다.
* ``data/HALT`` · ``TOSS_TRADING_ENABLED`` 가 꺼져 있으면 주문하지 않는다. 장부는
  ``data/toss/KR_<코드>.json``. 주문 · 체결 · 실패 · 원화 부족만 투자 봇으로 알린다.
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
from auto_trading.ops.kr_accumulate import KrOrder, KrState, advise, settle

REPO = Path(__file__).resolve().parent.parent
DATA = REPO / "data" / "toss"
REPORTS = REPO / "reports" / "toss"
HALT = REPO / "data" / "HALT"
TOKEN = REPO / "data" / "toss_token.json"
MAX_LIMIT_KRW = Decimal(2_000_000)
"""1 주 지정가 상한 — 하루 3 만 원 적립에 200 만 원 넘는 주식은 맞지 않는다."""
TAG = "🇰🇷 [국내 적립 · 토스]"


def _kst_now() -> datetime:
    return datetime.now(UTC) + timedelta(hours=9)


def _items() -> list[dict[str, Any]]:
    return [dict(x) for x in user_settings.load(REPO).get("_extras") or [] if x.get("mkt") == "KR"]


def _path(sym: str) -> Path:
    return DATA / f"KR_{sym}.json"


def _load(x: dict[str, Any], today: str) -> KrState:
    try:
        return KrState.from_dict(json.loads(_path(str(x["sym"])).read_text(encoding="utf-8")))
    except (OSError, ValueError, KeyError):
        etf = str(x.get("kind")) in ("ETF", "ETN", "FOREIGN_ETF")
        return KrState(
            str(x["sym"]),
            str(x.get("name") or x["sym"]),
            etf,
            str(x.get("added") or today),  # 적립은 추가한 다음 날부터 쌓인다
            market=str(x.get("market") or ""),
        )


def _save(st: KrState) -> None:
    DATA.mkdir(parents=True, exist_ok=True)
    tmp = _path(st.symbol).with_suffix(".tmp")
    tmp.write_text(json.dumps(st.to_dict(), ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(_path(st.symbol))


def _trader(symbols: set[str]) -> TossTrader:
    return TossTrader(
        load_toss_credentials(REPO / ".env"),
        TossTokenCache(TOKEN),
        allowed_symbols=frozenset(symbols),
        max_qty=dict.fromkeys(symbols, 1),
        sell_symbols=frozenset(),
        max_limit_krw=MAX_LIMIT_KRW,
    )


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


def run(*, dry: bool = False) -> int:
    now = _kst_now()
    today = now.strftime("%Y-%m-%d")
    items = _items()
    if not items:
        print("국내 적립 종목 없음 (오르빗 설정에서 추가)")
        return 0
    active = {str(x["sym"]) for x in items if not x.get("removed")}
    trader = _trader(active or {"000000"})
    lines: list[str] = []
    alert = False
    try:
        states = {str(x["sym"]): _load(x, today) for x in items}
        for st in states.values():  # 어제까지 낸 주문 체결 확인
            for o in st.orders:
                if o.date >= today or o.status not in ("SENT", "UNKNOWN"):
                    continue
                if o.status == "UNKNOWN" or not o.order_id:
                    o.status = "EXPIRED"
                    lines.append(f"⚠ {st.name} {o.date} 주문 결과 모름 — 토스 앱에서 확인해 주세요")
                    alert = True
                    continue
                s = trader.order_status(o.order_id)
                buy = settle(
                    o,
                    s.status,
                    float(s.filled_qty),
                    float(s.filled_amount or s.filled_qty * (s.avg_price or 0)),
                    float(s.commission or 0),
                )
                if buy:
                    st.buys.append(buy)
                    lines.append(f"✅ {st.name} {buy['qty']:g}주 체결 · {buy['cost_krw']:,.0f}원")
                elif o.status != "SENT":
                    lines.append(f"▫️ {st.name} {o.date} 주문 미체결 ({s.status}) — 적립금은 그대로")
        in_hours = "09:05" <= now.strftime("%H:%M") <= "15:10"  # 늦게 켜진 PC 가 장 밖에 안 내게
        if not active or not in_hours or not trader.kr_open_today(today):
            for st in states.values():
                _save(st)
            if lines:
                _notify("\n".join([f"{TAG} · {today}", RULE, *lines]))
            print("\n".join(lines) or "주문 시간 밖 · 휴장 · 켜진 종목 없음 중 하나")
            return 0
        prices = {k: float(v) for k, v in trader.prices(sorted(active)).items()}
        power = float(trader.buying_power("KRW"))
        enabled = trader.credentials.trading_enabled
        halted = halted_for(HALT, "acc")  # 전체 정지 또는 토스 적립 정지
        for x in items:
            sym = str(x["sym"])
            st = states[sym]
            if sym not in active or sym not in prices:
                continue
            hist = user_settings.acc_history(REPO, sym)
            adv = advise(st, hist, today, prices[sym])
            plan = (
                f"{st.name}: 쌓인 {adv.accrued:,.0f}원 / 1주 {adv.need:,.0f}원 "
                f"(지정가 {adv.limit:,}원) → {adv.status}"
            )
            print(plan)
            if adv.status != "BUY" or dry:
                continue
            if not enabled or halted:
                lines.append(f"⏸ {st.name} 살 차례지만 주문 안 함 — {halted or '자동 주문 꺼짐'}")
                continue
            if power < adv.need:
                lines.append(
                    f"💸 {st.name} 살 차례인데 원화가 모자라요 — "
                    f"필요 {adv.need:,.0f}원 · 있음 {power:,.0f}원"
                )
                alert = True
                continue
            cid = f"KR{sym}-{today}"
            order = KrOrder(today, adv.limit, cid)
            st.orders.append(order)
            try:
                res = trader.order(
                    sym, "BUY", 1, Decimal(adv.limit), client_order_id=cid, time_in_force="DAY"
                )
            except OrderStateUnknownError as exc:
                order.status, order.message = "UNKNOWN", str(exc)
                lines.append(f"⚠ {st.name} 주문 전송 결과 모름 — 다시 보내지 않아요")
                alert = True
                continue
            if res.accepted:
                order.order_id = res.code  # 성공이면 주문 ID
                power -= adv.need
                lines.append(f"🛒 {st.name} 1주 지정가 {adv.limit:,}원 (오늘 장중 체결 기다림)")
            else:
                order.status, order.message = "REJECTED", f"{res.code} {res.message}"
                lines.append(f"❌ {st.name} 주문 거절 — {res.message}")
                alert = True
        for st in states.values():
            _save(st)
    except (AutoTradingError, TossApiError) as exc:
        text = f"⚠ {TAG} 실패\n{RULE}\n{exc}"
        _notify(text)
        print(text)
        return 1
    finally:
        trader.close()
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
