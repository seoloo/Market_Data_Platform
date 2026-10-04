"""토스 적립 화면 (docs/research/89 · 118 §7) — 보고서·상태 파일만 읽는다.

출처: ``reports/toss/<종목>/<KST 날짜>.json`` (월~금 밤 9 시) · ``data/toss/<종목>.json``.
한국투자 적립 화면은 2026-10-04 사용자 요청으로 뺐다(토스로 옮김).
평가 손익·수익률은 **일부러 보여 주지 않는다** — 사용자 요청("계좌를 보면 팔고 싶어서").
모든 동적 문자열은 이스케이프한다.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from html import escape
from pathlib import Path
from typing import Any

__all__ = ["toss_page"]

_KST = timedelta(hours=9)

_STATUS = {
    "BUY": ("1주 살 차례", "var(--ok)"),
    "WAIT": ("적립 중", "var(--o2)"),
    "CAP": ("이번 달 한도", "var(--warn)"),
    "PAUSE": ("QS 멈춤 (추세 꺼짐)", "var(--warn)"),
    "FAILED": ("조회 실패", "var(--bad)"),
}
_ORDER = {
    "SENT": "접수",
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


def _section(root: Path, folder: Path, state_dir: Path | None = None) -> str:
    symbol = folder.name
    paths = sorted(folder.glob("20*.json"))
    if not paths:
        state0 = _read((state_dir or root / "data" / "accumulate") / f"{symbol}.json") or {}
        started = (
            f" 적립 시작 {escape(str(state0.get('start_date')))} — 첫 밤 실행(월~금 21:00 KST)"
            " 뒤 적립금·진행률이 표시된다."
            if state0.get("start_date")
            else ""
        )
        return (
            f'<section class="stock-section" id="{symbol.lower()}">'
            f'<div class="panel"><h2>{escape(symbol)}</h2>'
            f'<p class="mute">아직 기록이 없다.{started}</p>'
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
    state = _read((state_dir or root / "data" / "accumulate") / f"{symbol}.json") or {}
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
        f"<th>주문</th></tr>{''.join(hist)}</table></div></div>"
    )
    return (
        f'<section class="stock-section" id="{symbol.lower()}"><h2>{escape(symbol)}</h2>'
        f'{lead}<div class="panel"><div class="cards">{cards}</div>{bar}</div>{tables}'
        "</section>"
    )


_TOSS_SYMBOLS = ("QNDX", "BTQ", "WQTM")


def _toss_qs(base: Path) -> str:
    paths = sorted((base / "QS").glob("20*.json"))
    r = _read(paths[-1]) if paths else None
    if not r:
        return (
            '<div class="panel" id="qs"><h2>QS — QQQ 추세</h2><p class="mute">첫 밤 실행 전이다 '
            "(월~금 21:00 KST).</p></div>"
        )
    p = r.get("parts") or {}

    def pct(k: str) -> str:
        try:
            return f"{float(p[k]):+.1%}"
        except (KeyError, TypeError, ValueError):
            return "—"

    on = bool(r.get("trend_on"))
    want = {"NONE": "할 일 없음", "BUY": "TQQQ 살 차례", "SELL": "TQQQ 팔 차례"}.get(
        str(r.get("want")), str(r.get("want"))
    )
    color = "var(--ok)" if on else "var(--warn)"
    cards = (
        f'<div class="card" style="border-left-color:{color}"><h3>추세 {"켜짐" if on else "꺼짐"}'
        f'</h3><div class="sub">{escape(str(r.get("as_of")))} 종가 · 20일 {pct("r20")} · 60일 '
        f"{pct('r60')} · 120일 {pct('r120')}</div></div>"
        f'<div class="card"><h3>{escape(want)}</h3><div class="sub">QNDX '
        f"{'멈춤' if r.get('qndx_paused') else '평소대로 적립'}</div></div>"
        f'<div class="card"><h3>{_qty(r.get("strategy_tqqq_qty"))}</h3>'
        f'<div class="sub">QS 가 산 TQQQ · 순지출 {_money(r.get("net_krw"), "KRW")}</div></div>'
    )
    line = escape(str(r.get("order_line") or ""))
    return (
        '<div class="panel" id="qs"><h2>QS — QQQ 추세 · TQQQ 전환</h2>'
        f'<div class="cards">{cards}</div>'
        f"<div class='foot'>마지막 실행 {escape(_observed(r.get('at_ms'), r.get('date')))}"
        f"{' · ' + line if line else ''}</div></div>"
    )


def _toss_fx(base: Path) -> str:
    paths = sorted(base.glob("fx-*.json"))
    r = _read(paths[-1]) if paths else None
    if not r:
        return (
            '<div class="panel" id="fx"><h2>환전 안내</h2>'
            '<p class="mute">아직 환전 점검 기록이 없습니다.</p></div>'
        )
    short = float(r.get("short_usd") or 0)
    needs = " · ".join(
        f"{escape(str(n.get('symbol')))} {int(n.get('qty') or 0)}주" for n in r.get("needs") or []
    )
    msg = (
        f"${short:,.0f} 환전 필요 (약 {short * float(r.get('fx') or 0):,.0f}원)"
        if short > 0
        else "환전 필요 없음"
    )
    return (
        '<div class="panel" id="fx"><h2>환전 안내</h2>'
        f'<div class="sub">{escape(str(r.get("at")))} 확인 · '
        f"오늘 밤 매수 {needs or '없음'} · 달러 ${float(r.get('usd') or 0):,.2f} → "
        f"<b>{escape(msg)}</b></div><div class='foot'>평일 09·12·15시에 확인해 모자라면 "
        "텔레그램으로 알린다. 15:30 까지 환전하면 95% 우대.</div></div>"
    )


def toss_page(data: Any) -> str:
    """토스 적립 + QS (문서 118 §7) — ``reports/toss/`` · ``data/toss/`` 만 읽는다."""
    root = getattr(data, "root", None) or Path()
    base = root / "reports" / "toss"
    state_dir = root / "data" / "toss"
    jump = "".join(f'<a href="#{s.lower()}">{s}</a>' for s in _TOSS_SYMBOLS)
    intro = (
        '<div class="panel"><h2>토스증권 · 적립 현황</h2>'
        f'<div class="stock-jump">{jump}</div>'
        '<p class="mute">QNDX 매일 1만 원 · BTQ 매일 1달러 · WQTM 매일 4달러를 적립해 1주 값이 '
        "모이면 장 마감 지정가로 1주 산다. QQQ 추세가 꺼지면 QNDX 를 멈추고, 다시 켜지면 쌓인 "
        "적립금으로 TQQQ 를 산다(QS). 저장된 마지막 보고서이며 실시간 계좌 화면이 아니다. "
        "매수·매도 버튼은 없다.</p></div>"
    )
    sections = "".join(_section(root, base / s, state_dir) for s in _TOSS_SYMBOLS)
    foot = (
        "<div class='foot'>월~금 밤 9시(KST) 한 번 판단한다. 체결은 다음 실행에서 토스 주문 "
        "조회·잔고로 확인한다.</div>"
    )
    return intro + _toss_qs(base) + _toss_fx(base) + sections + foot
