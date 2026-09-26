"""해외 주식 적립 화면 (docs/research/89) — 보고서·상태 파일만 읽는다.

출처: ``reports/accumulate/<종목>/<KST 날짜>.json`` (매일 밤 9 시)
· ``data/accumulate/<종목>.json``.
평가 손익·수익률은 **일부러 보여 주지 않는다** — 사용자 요청("계좌를 보면 팔고 싶어서").
모든 동적 문자열은 이스케이프한다.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from html import escape
from pathlib import Path
from typing import Any

__all__ = ["accumulate_page"]

_SYMBOLS = ("QNDX", "BTQ")
_KST = timedelta(hours=9)

_STATUS = {
    "BUY": ("1주 살 차례", "var(--ok)"),
    "WAIT": ("적립 중", "var(--o2)"),
    "CAP": ("이번 달 한도", "var(--warn)"),
    "FAILED": ("조회 실패", "var(--bad)"),
}
_ORDER = {
    "SENT": "예약 매수 접수",
    "UNKNOWN": "결과 모름",
    "REJECTED": "거절",
    "FILLED": "체결",
    "EXPIRED": "만료",
}


def _read(path: Path) -> dict[str, Any] | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _money(x: Any, currency: str) -> str:
    try:
        v = float(x)
    except (TypeError, ValueError):
        return "—"
    return f"{v:,.0f}원" if currency == "KRW" else f"${v:,.2f}"


def _qty(x: Any) -> str:
    try:
        return f"{float(x):g}주"
    except (TypeError, ValueError):
        return "—"


def _observed(at_ms: Any, day: Any) -> str:
    try:
        return (datetime.fromtimestamp(float(at_ms) / 1000, UTC) + _KST).strftime(
            "%Y-%m-%d %H:%M KST"
        )
    except (TypeError, ValueError, OverflowError, OSError):
        return str(day or "시간 미기록")


def _section(root: Path, folder: Path) -> str:
    symbol = folder.name
    paths = sorted(folder.glob("20*.json"))
    if not paths:
        return (
            f'<section class="stock-section" id="{symbol.lower()}">'
            f'<div class="panel"><h2>{escape(symbol)}</h2>'
            '<p class="mute">아직 기록이 없다. 마지막 적립 실행 후 이곳에 표시된다.</p>'
            "</div></section>"
        )
    latest = _read(paths[-1]) or {}
    status = str(latest.get("status", "?"))
    label, color = _STATUS.get(status, (status, "var(--mute)"))
    a = latest.get("advice") or {}
    cur = str(a.get("currency") or "KRW")
    accrued, price = float(a.get("accrued") or 0), float(a.get("price") or 0)
    pct = max(0.0, min(accrued / price, 1.0)) if price > 0 else 0.0
    lead = (
        f"<div class='errors'>{escape(str(latest.get('error', '')))}</div>"
        if status == "FAILED"
        else ""
    )
    days = a.get("days_to_go")
    next_line = (
        f"지정가 {float(a.get('limit_usd') or 0):.2f} USD 이하로 1주"
        if status == "BUY"
        else (f"약 {days}일 뒤" if days is not None else "—")
    )
    next_line += " · 마지막 실행 당시 자동 매수 " + (
        "켜짐" if latest.get("auto_buy") else "꺼짐 (알림 전용)"
    )
    observed = escape(_observed(latest.get("at_ms"), latest.get("date")))
    fx = f" × {float(a.get('fx_rate') or 0):,.2f}원" if cur == "KRW" else ""
    cards = (
        f'<div class="card" style="border-left-color:{color}"><h3>{escape(symbol)} · '
        f"{escape(label)}</h3>"
        f'<div class="sub">마지막 적립 실행 {observed}'
        f" · {escape(next_line)}</div></div>"
        f'<div class="card"><h3>{_money(accrued, cur)}</h3>'
        '<div class="sub">쌓인 적립금</div></div>'
        f'<div class="card"><h3>{_money(price, cur)}</h3><div class="sub">1주 가격 '
        f"{float(a.get('last_usd') or 0):.2f} USD{fx} · 수수료 포함</div></div>"
        f'<div class="card"><h3>{_money(a.get("month_spent"), cur)}</h3>'
        f'<div class="sub">이번 달 매수 / 한도 {_money(a.get("monthly_cap"), cur)}</div></div>'
        f'<div class="card"><h3>{_qty(latest.get("holding_qty"))}</h3>'
        '<div class="sub">마지막 적립 실행 때 확인한 계좌 보유</div></div>'
    )
    bar = (
        '<div style="background:var(--mute-bg,#8883);border-radius:6px;height:14px;'
        'overflow:hidden;margin:4px 0 2px">'
        f'<div style="width:{pct * 100:.1f}%;height:100%;background:{color}"></div></div>'
        f"<div class='foot'>다음 1주까지 {pct:.0%} — {_money(accrued, cur)} / "
        f"{_money(price, cur)}</div>"
    )
    state = _read(root / "data" / "accumulate" / f"{symbol}.json") or {}
    buys = state.get("buys") or []
    buy_rows = (
        "".join(
            f"<tr><td class='nowrap'>{escape(str(b.get('date')))}</td>"
            f"<td class='num'>{float(b.get('qty') or 0):g}</td>"
            f"<td class='num'>{float(b.get('cost_usd') or 0):,.2f}</td>"
            f"<td class='num'>{_money(b.get('cost', b.get('cost_krw')), cur)}</td></tr>"
            for b in reversed(buys)
        )
        or "<tr><td colspan=4 class='mute'>아직 산 기록이 없다</td></tr>"
    )
    orders = {str(o.get("date")): o for o in state.get("orders") or []}
    hist = []
    for p in reversed(paths[-30:]):
        r = _read(p) or {}
        ra = r.get("advice") or {}
        day = str(r.get("date", p.stem))
        name = _STATUS.get(str(r.get("status")), (str(r.get("status")), ""))[0]
        o = orders.get(day)
        order = (
            f"{_ORDER.get(str(o.get('status')), str(o.get('status')))}"
            f" · {float(o.get('limit_usd') or 0):.2f} USD"
            if o
            else "—"
        )
        hist.append(
            f"<tr><td class='nowrap'>{escape(day)}</td><td>{escape(name)}</td>"
            f"<td class='num'>{_money(ra.get('accrued', ra.get('accrued_krw')), cur)}</td>"
            f"<td class='num'>{_money(ra.get('price', ra.get('price_krw')), cur)}</td>"
            f"<td>{escape(order)}</td></tr>"
        )
    tables = (
        '<div class="grid2"><div class="panel"><h2>매수 기록 (잔고에서 확인)</h2><table>'
        "<tr><th>확인한 날</th><th class='num'>수량</th><th class='num'>USD</th>"
        "<th class='num'>적립금에서 뺀 돈</th></tr>"
        f"{buy_rows}</table>"
        f"<div class='foot'>적립 시작 {escape(str(state.get('start_date', '—')))} · 시작 때 이미 "
        f"있던 {float(state.get('baseline_qty') or 0):g}주는 적립에 넣지 않는다.</div></div>"
        '<div class="panel"><h2>매일 기록 (최근 30일)</h2><table>'
        "<tr><th>날짜</th><th>상태</th><th class='num'>적립금</th><th class='num'>1주 가격</th>"
        f"<th>예약 매수</th></tr>{''.join(hist)}</table></div></div>"
    )
    return (
        f'<section class="stock-section" id="{symbol.lower()}"><h2>{escape(symbol)}</h2>'
        f'{lead}<div class="panel"><div class="cards">{cards}</div>{bar}</div>{tables}'
        "</section>"
    )


def accumulate_page(data: Any) -> str:
    root = getattr(data, "root", None) or Path()
    base = root / "reports" / "accumulate"
    sections = "".join(_section(root, base / symbol) for symbol in _SYMBOLS)
    intro = (
        '<div class="panel"><h2>해외 주식 · 적립 현황</h2>'
        '<div class="stock-jump"><a href="#qndx">QNDX</a><a href="#btq">BTQ</a></div>'
        '<p class="mute">QNDX는 원화, BTQ는 달러로 적립한다. 아래 값은 저장된 마지막 '
        "보고서이며 실시간 계좌 화면이 아니다. 계좌를 바꿨다면 다음 적립 실행 전 기록은 "
        "이전 계좌의 값일 수 있다. 매수·매도 버튼은 없다.</p></div>"
    )
    day = sorted(base.glob("20*.json"))
    tele = _read(day[-1]) if day else None
    note = (
        f"마지막 텔레그램: {escape(str(tele.get('date')))} "
        + ("보냄" if tele.get("notified") else "안 보냄(시험 실행)")
        if tele
        else ""
    )
    foot = (
        "<div class='foot'>매일 밤 9시(KST) 한 번 확인한다. 자동 매수가 켜져 있으면 살 차례인 날 "
        "예약 매수 1주(현재가 +3% 지정가, 당일 유효)를 낸다 — 매도는 하지 않는다. "
        f"체결은 다음 날 잔고로 확인한다. {note}</div>"
    )
    return intro + sections + foot
