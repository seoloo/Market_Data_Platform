"""AG Paper 화면 (docs/research/117) — 저장된 보고서만 읽는다. 주문 없음.

출처: ``reports/ag_paper/latest.json`` (매일 21:17 KST). 모든 동적 문자열은 이스케이프한다.
"""

from __future__ import annotations

from html import escape
from pathlib import Path
from typing import Any

from auto_trading.ui.charts import Series, line_chart
from auto_trading.ui.lt_render import _ms, _pct, _read, _usd

__all__ = ["ag_paper_page"]

_STATUS = {
    "OK": "정상 체결",
    "NO_PRIOR_DECISION": "체결 없음 (첫날 또는 빠진 날 뒤 — 목표만 새로 정함)",
    "MISSED": "빠진 날 (보유만 계산)",
}

_BOOKS = (
    ("AG-US", "AG-US · 미국 (TQQQ / 현금)", "var(--t2)"),
    ("AG-CR", "AG-CR · 코인 3 배 (BTC·ETH)", "var(--o2)"),
    ("AG-MIX", "AG-MIX · 반반, 해마다 재조정", "var(--hold)"),
    ("AG-CR4", "AG-CR4 · 코인 4 배", "var(--o3)"),
    ("AG-MIX4", "AG-MIX4 · 반반 (코인 4 배)", "var(--o1)"),
)


def _crypto_row(name: str, cr: dict[str, Any]) -> str:
    k = float(cr.get("multiple") or 3)
    weights = cr.get("next_target_weights") or {}
    target = (
        " · ".join(
            f"{escape(str(s).replace('USDT', ''))} {float(w) * k:.0%}" for s, w in weights.items()
        )
        or "현금"
    )
    status = escape(_STATUS.get(str(cr.get("status")), str(cr.get("status"))))
    return (
        f"<tr><th>{escape(name)}</th><td>노출 {float(cr.get('exposure') or 0):.2f} 배 · "
        f"다음 목표({k:.0f} 배 반영) {target} · {status}"
        f"{' · 선물 청산' if cr.get('liquidations') else ''}"
        f"{' · 비상 스톱' if cr.get('stop_hits') else ''}</td></tr>"
    )


def ag_paper_page(data: Any) -> str:
    root = getattr(data, "root", None) or Path()
    latest = _read(root / "reports" / "ag_paper" / "latest.json")
    intro = (
        '<div class="panel"><h2>소액 공격형 · AG Paper (가상 1,000 USD 씩)</h2>'
        '<p class="mute">문서 112 계좌들을 앞으로의 자료로 매일 기록한다. AG-US 는 QQQ 추세가 '
        "켜지면 TQQQ 전액·꺼지면 현금(QS Paper 의 AG 장부), AG-CR 은 코인 T/v2 신호에 노출 3 배"
        "(선물 비상 스톱 −20%), AG-MIX 는 500·500 으로 시작해 해마다 반반으로 다시 맞춘다. "
        "AG-CR4·AG-MIX4 는 코인 쪽 배율만 4 배로 올린 비교 장부다. "
        "<b>처음 돈의 30%(300 USD) 이하가 되면 정리하고 멈춘다.</b> 실제 주문은 없다.</p></div>"
    )
    if latest is None:
        return intro + '<div class="panel"><p class="mute">아직 기록이 없다.</p></div>'
    books = latest.get("books") or {}
    cards = ""
    for key, label, _ in _BOOKS:
        b = books.get(key) or {}
        stopped = b.get("stopped")
        state = (
            f"정리됨 {escape(str(stopped))}" if stopped else f"고점 대비 {_pct(b.get('from_peak'))}"
        )
        cards += (
            f'<div class="card"><h3>{_usd(b.get("value_usd"))}</h3><div class="sub">'
            f"{escape(label)} · {_pct(b.get('return'))} · {state} · 최저 "
            f"{_pct(b.get('worst_vs_start'))}</div></div>"
        )
    head = (
        f'<div class="panel"><div class="sub">기준 {escape(str(latest.get("date")))} · 코인은 '
        "09:00 KST 마감 일봉, 미국은 전날 종가</div>"
        f'<div class="cards">{cards}</div></div>'
    )
    series = []
    for key, label, color in _BOOKS:
        points = []
        for d, v in sorted(((latest.get("paths") or {}).get(key) or {}).items()):
            t = _ms(str(d))
            if t is not None:
                points.append((t, float(v)))
        series.append(Series(label, color, tuple(points)))
    chart = '<div class="panel"><h2>평가액 (USD)</h2>' + line_chart(series) + "</div>"
    us = books.get("AG-US") or {}
    trades = (
        " · ".join(
            f"{escape(str(t.get('date')))} "
            + ("매수" if t.get("side") == "BUY_TQQQ" else "매도")
            + f" {_usd(t.get('usd'))}"
            for t in us.get("trades") or []
        )
        or "—"
    )
    detail = (
        '<div class="panel"><h2>지금 들고 있는 것</h2><table>'
        f"<tr><th>AG-US</th><td>TQQQ {float(us.get('tqqq_shares') or 0):.4f} 주 · 현금 "
        f"{_usd(us.get('cash_usd'))} · 매매 {trades}</td></tr>"
        + "".join(_crypto_row(name, books.get(name) or {}) for name in ("AG-CR", "AG-CR4"))
        + "</table>"
        "<div class='foot'>AG-MIX 는 두 계좌 값으로 계산한다(따로 매매하지 않음). "
        "AG-US 는 QS Paper(21:15) 뒤에 읽는다.</div></div>"
    )
    return intro + head + chart + detail
