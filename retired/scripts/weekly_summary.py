"""⚠️ 은퇴 — 실행하지 않음 (2026-10-21, retired/README.md).

토요일 09:30 주간 요약 — Vega · Altair (예약 ``AutoTrading_Weekly_Summary``, 조회만 · 주문 없음).

사용 예::

    python -m scripts.weekly_summary send      # 만들어 투자 봇으로 보냄
    python -m scripts.weekly_summary preview   # 글만 (보내지 않음)

* Vega: 장부(``data/vega/state.json``) — 보유 = ``expected``, 평단 = 장부 체결만
  (:func:`auto_trading.ops.weekly_summary.avg_costs`), 지금 가격 = 바이낸스 공개 시세,
  유동형 = Simple Earn 조회. 이번 주 · 시작부터는 단위 가치(nav) 기준이라 입금이 섞이지 않는다.
* Altair: 선물 계정 조회(포지션 · 진입가 · 표시 가격 · 비상 스톱)
  + 장부(``data/altair_live/state.json``).
* 🧭 Polaris(나무): 장부(``data/polaris/state.json``) 체결만 · 나무 시세 · 원화.
  바이낸스 합계와 따로.
* 설정 ``alerts.vega_weekly`` 가 꺼져 있으면 보내지 않는다.
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

REPO = Path(__file__).resolve().parent.parent
if str(REPO / "src") not in sys.path:
    sys.path.insert(0, str(REPO / "src"))

from auto_trading.config.secrets import (  # noqa: E402
    load_binance_credentials,
    load_telegram_credentials,
)
from auto_trading.notify.channels import (  # noqa: E402
    NotifyError,
    OutboxNotifier,
    TelegramNotifier,
)
from auto_trading.ops import user_settings  # noqa: E402
from auto_trading.ops.weekly_summary import (  # noqa: E402
    Holding,
    Leg,
    avg_costs,
    change_since,
    summary_text,
)

KST = ZoneInfo("Asia/Seoul")
VEGA = REPO / "data" / "vega" / "state.json"
VEGA_REPORTS = REPO / "reports" / "vega"
ALTAIR = REPO / "data" / "altair_live" / "state.json"
ALTAIR_OUTBOX = REPO / "reports" / "altair_live" / "outbox.log"
OUTBOX = REPO / "reports" / "weekly_summary" / "outbox.log"
COINS = ("BTC", "ETH", "SOL")


def _read(path: Path) -> dict[str, Any]:
    try:
        d = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return d if isinstance(d, dict) else {}


def vega_block(week_start: str) -> dict[str, Any] | None:
    from auto_trading.exchange.binance_spot_live import BinanceSpotLive

    st = _read(VEGA)
    if not st:
        return None
    exp = {k: float(v) for k, v in (st.get("expected") or {}).items()}
    c = BinanceSpotLive(load_binance_credentials(REPO / ".env"), trading_enabled=False)
    try:
        px = {a: float(c.last_price(f"{a}USDT")) for a in COINS if exp.get(a, 0) > 0}
        earn = float(c.earn_balance("USDT"))
    finally:
        c.close()
    costs = avg_costs(st.get("orders") or [], {a: exp.get(a, 0.0) for a in COINS})
    holds = [
        Holding(a, exp[a], costs[a][1], px[a])
        for a in COINS
        if exp.get(a, 0) > 0 and a in costs and a in px
    ]
    cash = exp.get("USDT", 0.0)
    equity = cash + sum(h.qty * h.price for h in holds)
    units = float(st.get("units") or 0)
    nav = equity / units if units else 0.0
    hist = [(str(h[0]), float(h[1])) for h in st.get("history") or []]
    week_pct, _base = change_since(hist, week_start, nav)
    peak = max([float(st.get("peak") or 0), nav, *(v for _, v in hist)])
    deposits = sum(float(d[1]) for d in st.get("deposits") or [])
    reports = sorted(VEGA_REPORTS.glob("20*.json"))
    weights = _read(reports[-1]).get("weights", {}) if reports else {}
    week_orders = [
        o
        for o in st.get("orders") or []
        if o.get("status") == "FILLED" and str(o.get("id", ""))[4:14] >= week_start
    ]
    return {
        "equity": equity,
        "deposits": deposits,
        "week": units * nav * week_pct / (1 + week_pct) if week_pct > -1 else 0.0,
        "week_pct": week_pct,
        "since_pct": nav / 1000 - 1 if nav else 0.0,
        "dd": 1 - nav / peak if peak else 0.0,
        "holdings": holds,
        "cash": cash,
        "earn": earn,
        "trend": {a: float(weights.get(a, 0) or 0) > 0 for a in COINS},
        "buys": sum(1 for o in week_orders if o.get("side") == "BUY"),
        "sells": sum(1 for o in week_orders if o.get("side") == "SELL"),
    }


def altair_block(week_start: str) -> dict[str, Any] | None:
    from auto_trading.exchange.binance_usdm_live import BinanceUsdmLive, load_live_futures

    creds, enabled = load_live_futures(REPO / ".env")  # 옛 altair_live._client 와 같음

    st = _read(ALTAIR)
    if not st or not st.get("live"):
        return None
    c = BinanceUsdmLive(creds, trading_enabled=enabled)
    try:
        acct = c.account()
        legs: list[Leg] = []
        stops: dict[str, float] = {}
        unreal = 0.0
        for sym, p in acct.positions.items():
            mark = float(c.mark_price(sym))
            g = Leg(
                sym.removesuffix("USDC").removesuffix("USDT"),
                float(p.quantity),
                float(p.entry_price),
                mark,
            )
            legs.append(g)
            unreal += g.pnl
            for s in c.open_stops(sym):
                price = getattr(s, "stop_price", None)
                if price:
                    stops[g.asset] = float(price)
        equity = float(acct.wallet_balance) + unreal
    finally:
        c.close()
    units = float(st.get("units") or 0)
    nav = equity / units if units else 0.0
    hist = [
        (datetime.fromtimestamp(h[0] / 1000, KST).date().isoformat(), float(h[2]))
        for h in st.get("history") or []
    ]
    week_pct, _base = change_since(hist, week_start, nav)
    peak = max([float(st.get("nav_peak") or 1.0), nav])
    long_n = short_n = 0
    try:
        text = ALTAIR_OUTBOX.read_text(encoding="utf-8")
    except OSError:
        text = ""
    for block in text.split("===== ")[1:]:
        if block[:10] >= week_start:
            long_n += block.count("롱 늘림") + block.count("롱 줄임")
            short_n += block.count("숏 진입") + block.count("숏 청산")
    return {
        "equity": equity,
        "start": float(st.get("alloc") or units),
        "week": units * nav * week_pct / (1 + week_pct) if week_pct > -1 else 0.0,
        "week_pct": week_pct,
        "since_pct": nav - 1,
        "dd": 1 - nav / peak if peak else 0.0,
        "legs": legs,
        "stops": stops,
        "long_trades": long_n,
        "short_trades": short_n,
    }


def polaris_block() -> dict[str, Any] | None:
    """🧭 Polaris 장부(나무) — 장부 체결만, 지금 가격은 나무 시세."""
    from scripts import polaris as pol

    st = pol.load()
    if not st.orders:
        return None
    from auto_trading.ops.polaris import ROUNDS, SYMBOLS, holdings_lines

    nh = pol.client()
    try:
        q = pol.quotes(nh)
    finally:
        nh.close()
    fx = q["SPYM"].fx
    prices = {s: x.last for s, x in q.items()}
    return {
        "value_krw": sum(st.qty[s] * prices[s] for s in SYMBOLS) * fx,
        "cost_krw": sum(st.cost_krw.values()),
        "lines": holdings_lines(st, prices, fx),
        "done": st.done_rounds,
        "rounds": ROUNDS,
    }


def build(now: datetime | None = None) -> str:
    now = now or datetime.now(KST)
    end = now.date()
    start = end - timedelta(days=6)
    period = f"{start:%m/%d} ~ {end:%m/%d}"
    todo: list[str] = []
    v: dict[str, Any] | None = None
    a: dict[str, Any] | None = None
    try:
        v = vega_block(start.isoformat())
    except Exception as exc:  # 조회 실패해도 나머지는 보낸다
        todo.append(f"Vega 조회 실패 ({type(exc).__name__})")
    try:
        a = altair_block(start.isoformat())
    except Exception as exc:
        todo.append(f"Altair 조회 실패 ({type(exc).__name__})")
    p: dict[str, Any] | None = None
    try:
        p = polaris_block()
    except Exception as exc:
        todo.append(f"Polaris 조회 실패 ({type(exc).__name__})")
    pst = _read(REPO / "data" / "polaris" / "state.json")
    if pst.get("halted"):
        todo.append(f"Polaris 멈춤 — {str(pst['halted'])[:80]}")
    vs = _read(VEGA)
    if vs.get("stopped"):
        todo.append("Vega 30% 멈춤 — 다시 시작할지 정해 주세요")
    if _read(ALTAIR).get("no_new"):
        todo.append("Altair 새 노출 멈춤 — 다시 시작할지 정해 주세요")
    return summary_text(period=period, vega=v, altair=a, todo=todo, polaris=p)


def send(text: str) -> None:
    OutboxNotifier(OUTBOX).send(text)
    creds = load_telegram_credentials(REPO / ".env")
    if creds is None:
        return
    try:
        TelegramNotifier(creds).send(text)
    except NotifyError as exc:
        print(f"텔레그램 실패: {exc}")


def main(argv: list[str]) -> int:
    cmd = argv[1] if len(argv) > 1 else ""
    if cmd not in ("send", "preview"):
        print(__doc__)
        return 2
    if cmd == "send" and not user_settings.get(REPO, "alerts.vega_weekly"):
        print("설정 alerts.vega_weekly 꺼짐 — 보내지 않음")
        return 0
    text = build()
    if cmd == "send":
        send(text)
    sys.stdout.write(text + "\n")
    return 0


if __name__ == "__main__":
    for _stream in (sys.stdout, sys.stderr):
        _reconfigure = getattr(_stream, "reconfigure", None)
        if _reconfigure is not None:
            _reconfigure(encoding="utf-8", errors="replace")
    raise SystemExit(main(sys.argv))
