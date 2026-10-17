"""💹 월간 미장 주도주 추천 · 매도 신호 — 매일 07:30 KST (예약 ``AutoTrading_Stock_Reco``).

사용 예::

    python -m scripts.stock_reco daily          # 일봉 갱신 → 매도 신호 → 월말이면 추천 (텔레그램)
    python -m scripts.stock_reco preview        # 지금 종가로 카드 글만 (안 보냄, 주문 없음)

* 종목: S&P 500(LT Paper 일봉, ``data/lt_paper/bars``) + 나스닥 100 에만 있는 15 종목
  (``data/stock_reco/bars``).
  일봉은 한투 조회 전용(LT Paper 와 같은 ``fetch_merge``). LT Paper 장부 · 종목군은 건드리지 않는다.
* 월말 = 마지막 종가의 다음 미국 정규장(토스 달력)이 다른 달. 그달 한 번만 보낸다(``state.json``).
* 카드: 상위 3 개 [🛒 매수] + [📋 전부 보기] [이번 달 건너뛰기]. 버튼은 5 일 유효, 누르면 💹 예약
  (:func:`scripts.size_guard.reserve` — 그때 가격으로 다시 확인 · 정규장 시작 직후 지정가).
* 매도 신호: 💹 보통 장부(PEAD 아님) 보유 종목 — :func:`auto_trading.ops.stock_reco.sell_signal`.
  [🧹 다음 장에서 매도] 를 누르면 그 장부를 ``selling`` 으로(💹 tick 이 지정가로 판다),
  [계속 보유] 면 같은
  이유로는 다시 묻지 않는다. 자동으로 팔지 않는다(사용자 2026-10-17 시안 — 버튼).
"""

from __future__ import annotations

import hashlib
import json
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

REPO = Path(__file__).resolve().parent.parent
if str(REPO / "src") not in sys.path:
    sys.path.insert(0, str(REPO / "src"))

from auto_trading.config.secrets import load_telegram_credentials  # noqa: E402
from auto_trading.notify.channels import (  # noqa: E402
    NotifyError,
    OutboxNotifier,
    TelegramNotifier,
)
from auto_trading.ops import user_settings  # noqa: E402
from auto_trading.ops.size_order import PEAD  # noqa: E402
from auto_trading.ops.stock_reco import (  # noqa: E402
    Reco,
    is_month_end,
    reco_text,
    recommend,
    sell_signal,
    sell_text,
)

ROOT = REPO / "data" / "stock_reco"
BARS = ROOT / "bars"
STATE = ROOT / "state.json"
OFFERS = ROOT / "offers.json"
OUTBOX = REPO / "reports" / "stock_reco" / "outbox.log"
NY = ZoneInfo("America/New_York")
KST = ZoneInfo("Asia/Seoul")
OFFER_DAYS = 5
SHOW = 3
EXTRAS = {
    "ASML": "Information Technology",
    "MSTR": "Information Technology",
    "ALNY": "Health Care",
    "MELI": "Consumer Discretionary",
    "NBIS": "Information Technology",
    "SHOP": "Information Technology",
    "CCEP": "Consumer Staples",
    "PDD": "Consumer Discretionary",
    "RKLB": "Industrials",
    "ARM": "Information Technology",
    "TRI": "Industrials",
    "FER": "Industrials",
    "ALAB": "Information Technology",
    "CRWV": "Information Technology",
    "SPCX": "Industrials",
}
"""나스닥 100(2026-10-17 nasdaq.com 목록) 중 S&P 500 일봉에 없는 종목 → GICS 에 맞춘 섹터.
모두 나스닥 상장."""
EXTRA_SINCE = "2024-01-01"
EXCHANGES = ("NAS", "NYS", "AMS")


# ------------------------------------------------------------------ 상태 · 버튼


def _read(path: Path) -> dict[str, Any]:
    try:
        d = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return d if isinstance(d, dict) else {}


def _write(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(path)


def _now_ms() -> int:
    return int(time.time() * 1000)


def new_offer(kind: str, now_ms: int, **data: Any) -> str:
    offers = _read(OFFERS)
    nonce = hashlib.sha256(f"{kind}:{data}:{now_ms}:{len(offers)}".encode()).hexdigest()[:10]
    offers[nonce] = {"kind": kind, "at": now_ms, "until": now_ms + OFFER_DAYS * 86_400_000, **data}
    _write(OFFERS, offers)
    return nonce


def take_offer(nonce: str, kind: str, now_ms: int) -> dict[str, Any] | None:
    """유효하면 한 번 쓰고(used) 돌려준다. 매수 버튼은 같은 카드의 다른 종목에 영향 없음."""
    offers = _read(OFFERS)
    o: dict[str, Any] | None = offers.get(nonce)
    if o is None or o.get("kind") != kind or o.get("used") or now_ms > int(o.get("until", 0)):
        return None
    o["used"] = True
    _write(OFFERS, offers)
    return o


# ------------------------------------------------------------------ 일봉


def _broker() -> Any:
    from auto_trading.config.secrets import load_kis_credentials
    from auto_trading.exchange.kis import KisOverseasReadOnly, KisTokenCache

    return KisOverseasReadOnly(
        load_kis_credentials(REPO / ".env"), KisTokenCache(REPO / "data" / "kis_token.json")
    )


def update_bars(extra: list[str] | None = None) -> dict[str, Any]:
    """S&P 500(LT Paper 일봉) + 나스닥 100 추가 15 + ``extra``(💹 보유 종목)를 갱신."""
    from auto_trading.ops.us_daily_bars import last_settled_day
    from scripts import lt_paper

    lt = lt_paper.update()
    last_ok = last_settled_day()
    BARS.mkdir(parents=True, exist_ok=True)
    meta = _read(ROOT / "exchanges.json")
    lt_syms = set(json.loads(lt_paper.META.read_text(encoding="utf-8"))["exchanges"])
    want = list(EXTRAS) + [s for s in extra or [] if s not in lt_syms and s not in EXTRAS]
    failed: list[str] = []
    broker = _broker()
    try:
        for sym in want:
            tries = [meta[sym]] if sym in meta else list(EXCHANGES)
            for ex in tries:
                try:
                    m = lt_paper.fetch_merge(
                        broker, sym, ex, BARS / f"{sym}.csv", last_ok, since_if_empty=EXTRA_SINCE
                    )
                except Exception:
                    continue
                if m.rows:
                    meta[sym] = ex
                    break
            else:
                failed.append(sym)
    finally:
        broker.close()
    _write(ROOT / "exchanges.json", meta)
    return {"lt": lt, "extra_failed": failed}


def panel() -> tuple[list[str], dict[str, list[float | None]], list[float | None], dict[str, str]]:
    """LT Paper 판 + 추가 종목(같은 날짜 줄에 맞춤)."""
    from scripts import lt_paper

    days, closes, spy, sectors = lt_paper._panel()
    at = {d: i for i, d in enumerate(days)}
    secs = dict(sectors)
    for p in sorted(BARS.glob("*.csv")):
        rows = lt_paper._read(p)
        xs: list[float | None] = [None] * len(days)
        for r in rows:
            i = at.get(r["date"])
            if i is not None and r.get("close"):
                xs[i] = float(r["close"])
        if any(v is not None for v in xs):
            closes[p.stem] = xs
            secs.setdefault(p.stem, EXTRAS.get(p.stem, "Unknown"))
    return days, closes, spy, secs


# ------------------------------------------------------------------ 카드 재료


def _next_session(reader: Any, day: str) -> str:
    d = date.fromisoformat(day) + timedelta(days=1)
    for _ in range(10):
        if reader.checked_regular_session(d.isoformat(), kr=False) is not None:
            return d.isoformat()
        d += timedelta(days=1)
    raise ValueError("다음 미국 정규장 미확인")


def _sec_lines(
    syms: list[str], days: list[str], closes: dict[str, list[float | None]]
) -> dict[str, list[str]]:
    """시가총액(유통 시총 × 그 뒤 가격) · 최근 분기 매출 · 영업이익 1 년 비교 — 참고 정보.
    실패하면 비움."""
    import httpx
    from scripts.fetch_sec_facts_261 import extract
    from scripts.run_leader_quality_261 import Fundamentals, _days

    from auto_trading.config.secrets import read_env_file

    email = read_env_file(REPO / ".env").get("SEC_CONTACT_EMAIL", "").strip()
    if not email:
        return {}
    folder = ROOT / "sec"
    folder.mkdir(parents=True, exist_ok=True)
    hdr = {"User-Agent": f"AutoTrading stock reco {email}", "Accept-Encoding": "gzip, deflate"}
    try:
        with httpx.Client(timeout=30.0, headers=hdr) as c:
            cmap = c.get("https://www.sec.gov/files/company_tickers.json").json()
            cik = {v["ticker"].upper(): int(v["cik_str"]) for v in cmap.values()}
            for s in syms:
                key = s.replace(".", "-").upper()
                if key not in cik:
                    continue
                time.sleep(0.15)
                r = c.get(f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik[key]:010d}.json")
                if r.status_code == 200:
                    (folder / f"{s}.json").write_text(
                        json.dumps(extract(r.json())), encoding="utf-8"
                    )
    except (httpx.HTTPError, ValueError, KeyError):
        return {}
    f = Fundamentals(folder)
    day = days[-1]
    out: dict[str, list[str]] = {}
    for s in syms:
        parts: list[str] = []
        cap = f.cap(s, day, closes[s], days) if s in closes else None
        if cap:
            parts.append(f"시총 약 ${cap / 1e8:,.0f}억")
        for label, qs in (("매출", f.rev.get(s, [])), ("영업익", f.op.get(s, []))):
            known = [q for q in qs if q[0] <= day]
            if not known:
                continue
            last = max(known, key=lambda q: q[1])
            prev = [q for q in known if 350 <= _days(q[1], last[1]) <= 380]
            if not prev or _days(last[1], day) > 200:
                continue
            a, b = last[2], prev[0][2]
            if b > 0 and a > 0:
                parts.append(f"{label} {a / b - 1:+.0%}")
            elif b <= 0 < a:
                parts.append(f"{label} 흑자 전환")
            elif a < 0 and b < 0:
                parts.append(f"{label} 손실 {'줄어듦' if a > b else '늘어남'}")
            else:
                parts.append(f"{label} 적자 전환")
        if parts:
            out[s] = [" · ".join(parts)]
    return out


def _advice_lines(reader: Any, syms: list[str]) -> dict[str, list[str]]:
    """💹 지금 규칙으로 수량 · 손절 (추천 기록에 남기지 않는 내부 함수)."""
    from scripts import size_guard as sg

    out: dict[str, list[str]] = {}
    now = _now_ms()
    for s in syms:
        try:
            a = sg._advise(reader, s, s, now)
        except Exception:
            continue
        if a.ok:
            loss = (a.price - a.stop) * a.qty * a.fx
            out[s] = [
                f"💹 {a.weight:.0%} ≈ {a.qty}주 (${a.price * a.qty:,.0f}) · 손절 ${a.stop:,.2f}"
                f" (−3%, 약 −{loss / 10_000:,.1f}만 원)"
            ]
        else:
            out[s] = ["⚠ 지금 💹 규칙에 걸림: " + " · ".join(a.blocked)[:80]]
    return out


def _send(text: str, buttons: list[list[dict[str, str]]] | None = None) -> None:
    OutboxNotifier(OUTBOX).send(text)
    creds = load_telegram_credentials(REPO / ".env")
    if creds is None:
        return
    try:
        TelegramNotifier(creds).send_message(text, buttons=buttons)
    except NotifyError as exc:
        print(f"텔레그램 실패: {exc}")


def buy_buttons(reco: Reco, start: int, stop: int, now_ms: int) -> list[list[dict[str, str]]]:
    row: list[dict[str, str]] = []
    rows: list[list[dict[str, str]]] = []
    for p in reco.picks[start:stop]:
        if not p.trend:
            continue
        n = new_offer("buy", now_ms, sym=p.symbol, month=reco.day[:7])
        row.append({"text": f"🛒 {p.symbol}", "callback_data": f"rcb:{n}:{p.symbol}"})
        if len(row) == 3:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    return rows


# ------------------------------------------------------------------ 하루


def _ticket_entry_day(t: Any) -> str:
    ms = min((o.placed_ms for o in t.buys), default=t.created_ms)
    return datetime.fromtimestamp(ms / 1000, NY).date().isoformat()


def sell_checks(
    days: list[str], closes: dict[str, list[float | None]], state: dict[str, Any], *, send: bool
) -> list[str]:
    from scripts import size_guard as sg

    seen: dict[str, str] = state.setdefault("signals", {})
    out: list[str] = []
    fx = 1_400.0
    try:
        reader = sg._reader()
        try:
            fx = float(reader.usd_krw())
        finally:
            reader.close()
    except Exception:
        pass
    for t in sg.load_tickets():
        if t.status != "holding" or t.kind == PEAD or t.kr or t.symbol not in closes:
            continue
        xs = closes[t.symbol]
        start = _ticket_entry_day(t)
        i0 = next((i for i, d in enumerate(days) if d >= start), None)
        last = len(days) - 1
        while last >= 0 and xs[last] is None:
            last -= 1
        if i0 is None or last < i0:
            continue
        sig = sell_signal(xs, i0, last)
        if sig is None or seen.get(t.id) == sig[0]:
            continue
        reason, peak = sig
        px = xs[last]
        assert px is not None
        text = sell_text(t.name, t.filled - t.sold, t.avg, px, peak, reason, fx)
        out.append(text)
        if send:
            n = new_offer("sell", _now_ms(), tid=t.id, reason=reason)
            _send(
                text,
                [
                    [
                        {"text": "🧹 다음 장에서 매도", "callback_data": f"rsl:{n}:{t.id}"},
                        {"text": "계속 보유", "callback_data": f"rsk:{n}:{t.id}"},
                    ]
                ],
            )
        seen[t.id] = reason
    return out


def build_card(
    reco: Reco, days: list[str], closes: dict[str, list[float | None]], *, live: bool
) -> tuple[str, dict[str, list[str]]]:
    from scripts import size_guard as sg

    detail: dict[str, list[str]] = {}
    top = [p.symbol for p in reco.picks]
    if live and reco.market_ok:
        for s, xs in _sec_lines(top, days, closes).items():
            detail.setdefault(s, []).extend(xs)
        try:
            reader = sg._reader()
            try:
                for s, xs in _advice_lines(reader, top[:SHOW]).items():
                    detail.setdefault(s, []).extend(xs)
            finally:
                reader.close()
        except Exception as exc:
            print(f"💹 수량 계산 건너뜀: {type(exc).__name__}")
    held = [
        f"{t.symbol}"
        for t in sg.load_tickets()
        if t.status == "holding" and t.kind != PEAD and not t.kr
    ]
    prev = _read(STATE).get("last_picks") or []
    dropped = [s for s in prev if s not in top and s in held]
    month = f"{int(date.fromisoformat(reco.day).month) % 12 + 1}월"
    names: dict[str, str] = {}
    for p in reco.picks:
        try:
            names[p.symbol] = sg.resolve(p.symbol)[1]
        except SystemExit:
            continue
    text = reco_text(reco, month, detail=detail, names=names, held=held, dropped=dropped, show=SHOW)
    return text, detail


def daily(*, send: bool = True) -> dict[str, Any]:
    from scripts import size_guard as sg

    if not user_settings.get(REPO, "stock.reco"):
        return {"skipped": "설정 stock.reco 꺼짐"}
    held = sorted(
        {
            t.symbol
            for t in sg.load_tickets()
            if t.status == "holding" and t.kind != PEAD and not t.kr
        }
    )
    upd = update_bars(held)
    days, closes, spy, sectors = panel()
    state = _read(STATE)
    out: dict[str, Any] = {"update": upd, "last_bar": days[-1]}
    out["sell"] = sell_checks(days, closes, state, send=send)
    reader = sg._reader()
    try:
        nxt = _next_session(reader, days[-1])
    finally:
        reader.close()
    month = days[-1][:7]
    if is_month_end(days[-1], nxt) and state.get("sent_month") != month:
        universe = {s: closes[s] for s in closes if s in sectors and s != "SPY"}
        reco = recommend(days, universe, sectors, spy, len(days) - 1)
        text, _detail = build_card(reco, days, closes, live=send)
        out["reco"] = text
        if send:
            rows = buy_buttons(reco, 0, SHOW, _now_ms()) if reco.market_ok else []
            if reco.market_ok and len(reco.picks) > SHOW:
                n = new_offer("more", _now_ms(), month=month)
                rows.append(
                    [
                        {
                            "text": f"📋 {SHOW + 1} ~ {len(reco.picks)}위 보기",
                            "callback_data": f"rcm:{n}:x",
                        },
                        {"text": "이번 달 건너뛰기", "callback_data": f"rcs:{n}:x"},
                    ]
                )
            _send(text, rows or None)
            state["sent_month"] = month
            state["last_picks"] = [p.symbol for p in reco.picks]
            state["last_reco"] = {
                "day": reco.day,
                "market_ok": reco.market_ok,
                "sectors": reco.sectors,
                "picks": [p.symbol for p in reco.picks],
            }
    if send:
        _write(STATE, state)
    return out


# ------------------------------------------------------------------ 버튼 (텔레그램 봇이 부름)


def on_buy(nonce: str, sym: str) -> str:
    from scripts import size_guard as sg

    if take_offer(nonce, "buy", _now_ms()) is None:
        return "⌛ 지난 버튼이에요 (5 일 지남 · 이미 누름)"
    return sg.reserve(sym)


def on_more(nonce: str) -> str:
    """4 ~ 10 위 글 + 버튼을 보낸다."""
    o = take_offer(nonce, "more", _now_ms())
    last = _read(STATE).get("last_reco") or {}
    if o is None or not last:
        return "⌛ 지난 버튼이에요"
    days, closes, spy, sectors = panel()
    t = next((i for i, d in enumerate(days) if d == last["day"]), len(days) - 1)
    universe = {s: closes[s] for s in closes if s in sectors and s != "SPY"}
    reco = recommend(days, universe, sectors, spy, t)
    lines = [f"📋 [미장 주도주 · {last['day'][:7]}] {SHOW + 1} ~ {len(reco.picks)}위"]
    for i, p in enumerate(reco.picks[SHOW:], start=SHOW + 1):
        trend = "" if p.trend else " · ⚠ 추세 아님"
        lines.append(
            f"{i}. {p.symbol} · 1달 {p.rets[0]:+.0%} · 3달 {p.rets[1]:+.0%}"
            f" · 6달 {p.rets[2]:+.0%}{trend}"
        )
    _send("\n".join(lines), buy_buttons(reco, SHOW, len(reco.picks), _now_ms()) or None)
    return ""


def on_skip(nonce: str) -> str:
    return (
        "이번 달은 건너뛰었어요" if take_offer(nonce, "more", _now_ms()) else "⌛ 지난 버튼이에요"
    )


def on_sell(nonce: str, tid: str) -> str:
    from scripts import size_guard as sg

    o = take_offer(nonce, "sell", _now_ms())
    if o is None:
        return "⌛ 지난 버튼이에요"
    with sg._Lock():
        tickets = sg.load_tickets()
        for t in tickets:
            if t.id == tid and t.status == "holding":
                why = "추세 꺾임" if o.get("reason") == "trend" else "고점 −35%"
                t.status, t.note = "selling", f"매도 신호 — {why}"
                sg.save_tickets(tickets)
                return f"🧹 {t.name} 다음 정규장에서 지정가로 팔아요 ({why})"
    return "이미 팔렸거나 장부에 없어요"


def on_keep(nonce: str) -> str:
    return (
        "계속 들고 있을게요 — 같은 이유로는 다시 묻지 않아요"
        if take_offer(nonce, "sell", _now_ms())
        else "⌛ 지난 버튼이에요"
    )


def main(argv: list[str]) -> int:
    cmd = argv[1] if len(argv) > 1 else ""
    if cmd == "daily":
        out = daily()
    elif cmd == "preview":
        days, closes, spy, sectors = panel()
        universe = {s: closes[s] for s in closes if s in sectors and s != "SPY"}
        reco = recommend(days, universe, sectors, spy, len(days) - 1)
        out = {"text": build_card(reco, days, closes, live=False)[0]}
    else:
        print(__doc__)
        return 2
    sys.stdout.write(json.dumps(out, ensure_ascii=False, indent=1) + "\n")
    return 0


if __name__ == "__main__":
    for _stream in (sys.stdout, sys.stderr):
        _reconfigure = getattr(_stream, "reconfigure", None)
        if _reconfigure is not None:
            _reconfigure(encoding="utf-8", errors="replace")
    raise SystemExit(main(sys.argv))
