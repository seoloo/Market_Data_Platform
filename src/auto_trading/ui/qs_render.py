"""QS Paper 화면 (docs/research/116) — 저장된 보고서·상태 파일만 읽는다. 주문 없음.

출처: ``reports/qs_paper/latest.json`` (매일 21:10 KST 화~토) · ``data/qs_paper/state.json``.
모든 동적 문자열은 이스케이프한다.
"""

from __future__ import annotations

from html import escape
from pathlib import Path
from typing import Any

from auto_trading.ui.charts import Series, line_chart
from auto_trading.ui.lt_render import _ms, _pct, _read, _usd

__all__ = ["qs_paper_page"]

_BOOKS = (
    ("QS", "QS (멈춤 → TQQQ 전환)", "var(--t2)"),
    ("A", "A (매달 QQQ, 지금 방식)", "var(--hold)"),
    ("AG", "AG-US (1,000 USD 한 번, TQQQ/현금)", "var(--o2)"),
)
_SIDE = {"BUY_TQQQ": "TQQQ 매수", "SELL_TQQQ": "TQQQ 매도"}


def qs_paper_page(data: Any) -> str:
    root = getattr(data, "root", None) or Path()
    latest = _read(root / "reports" / "qs_paper" / "latest.json")
    state = _read(root / "data" / "qs_paper" / "state.json") or {}
    intro = (
        '<div class="panel"><h2>QQQ 적립 · TQQQ 전환 · Paper (가상)</h2>'
        '<p class="mute">문서 110 QS 규칙을 앞으로의 자료로 매일 기록한다 — 매달 1,000 USD 를 넣어 '
        "QQQ 추세(20·60·120 거래일 수익 부호 평균)가 켜져 있으면 QQQ 를 사고, 꺼지면 현금으로 "
        "모았다가 다시 켜지는 날 모은 현금 전부로 TQQQ 를 산다. 추세가 꺾이면 TQQQ 만 판다. "
        "비교로 매달 QQQ 만 사는 A 와, 1,000 USD 한 번으로 TQQQ/현금을 오가는 AG-US 를 "
        "함께 적는다. "
        "<b>실제 주문은 없다.</b></p></div>"
    )
    if latest is None:
        return intro + '<div class="panel"><p class="mute">아직 기록이 없다.</p></div>'
    nxt = latest.get("trend_for_next_session") or {}
    on = bool(nxt.get("on"))
    books = latest.get("books") or {}
    cards = (
        f'<div class="card"><h3>{"켜짐" if on else "꺼짐"}</h3><div class="sub">QQQ 추세 '
        f"({escape(str(nxt.get('as_of')))} 종가) · 20 일 {_pct(nxt.get('r20'))} · 60 일 "
        f"{_pct(nxt.get('r60'))} · 120 일 {_pct(nxt.get('r120'))}</div></div>"
    )
    for key, label, _ in _BOOKS:
        b = books.get(key) or {}
        cards += (
            f'<div class="card"><h3>{_usd(b.get("value_usd"))}</h3><div class="sub">'
            f"{escape(label)} · 넣은 돈 {_usd(b.get('invested_usd'))} · "
            f"{_pct(b.get('return_on_invested'))}</div></div>"
        )
    head = (
        f'<div class="panel"><div class="sub">기준 {escape(str(latest.get("date")))} 종가 · '
        f"QQQ {_usd(latest.get('qqq_close'))} · TQQQ {_usd(latest.get('tqqq_close'))}</div>"
        f'<div class="cards">{cards}</div></div>'
    )
    series = []
    for key, label, color in _BOOKS:
        b = (state.get("books") or {}).get(key) or {}
        points = []
        for d, v in b.get("equity") or []:
            t = _ms(str(d))
            if t is not None:
                points.append((t, float(v)))
        series.append(Series(label, color, tuple(points)))
    chart = '<div class="panel"><h2>평가액 (USD)</h2>' + line_chart(series) + "</div>"
    rows = ""
    for key, label, _ in _BOOKS:
        b = books.get(key) or {}
        today = " · ".join(escape(str(x)) for x in b.get("today") or []) or "—"
        rows += (
            f"<tr><td>{escape(label)}</td><td class='num'>{_usd(b.get('qqq_usd'))}</td>"
            f"<td class='num'>{_usd(b.get('tqqq_usd'))}</td>"
            f"<td class='num'>{_usd(b.get('cash_usd'))}</td>"
            f"<td class='num'>{_usd(b.get('tax_paid_usd'))}</td>"
            f"<td>{today}</td>"
            f"<td>{escape(str(b.get('next_session_if_trend_holds', '—')))}</td></tr>"
        )
    holdings = (
        '<div class="panel"><h2>장부별 보유</h2><table><tr><th>장부</th>'
        "<th class='num'>QQQ</th><th class='num'>TQQQ</th><th class='num'>현금</th>"
        "<th class='num'>낸 세금</th><th>오늘 한 일</th><th>다음 거래일 할 일</th></tr>"
        f"{rows}</table><div class='foot'>오늘 종가로 계산한 추세는 다음 거래일 종가에 실행한다. "
        "QS 와 A 는 추세가 켜져 있는 동안 같다 — 추세가 꺼지는 날부터 갈린다.</div></div>"
    )
    trade_rows = ""
    for key, _, _ in _BOOKS:
        b = (state.get("books") or {}).get(key) or {}
        for t in reversed(b.get("trades") or []):
            trade_rows += (
                f"<tr><td>{escape(key)}</td><td class='nowrap'>{escape(str(t.get('date')))}</td>"
                f"<td>{escape(_SIDE.get(str(t.get('side')), str(t.get('side'))))}</td>"
                f"<td class='num'>{_usd(t.get('usd'))}</td>"
                f"<td class='num'>{_pct(t.get('ret')) if 'ret' in t else '—'}</td></tr>"
            )
    trades = (
        '<div class="panel"><h2>TQQQ 매매 기록</h2><table><tr><th>장부</th><th>날짜</th>'
        "<th>매매</th><th class='num'>금액</th><th class='num'>수익</th></tr>"
        + (trade_rows or "<tr><td colspan=5 class='mute'>아직 TQQQ 매매가 없다</td></tr>")
        + "</table></div>"
    )
    return intro + head + chart + holdings + trades
