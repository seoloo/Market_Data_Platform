"""AG Paper 화면 (docs/research/117) — 저장된 보고서만 읽는다. 주문 없음.

출처: ``reports/ag_paper/latest.json`` (매일 21:17 KST), AGS 는 ``reports/ags_paper/latest.json``
(같은 작업 안, 문서 232 §8). 모든 동적 문자열은 이스케이프한다.
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


_AGS_LINES = (
    ("AGS1", "AGS1 · AG-CR + 숏 1 배", "var(--warn)"),
    ("AGS3", "AGS3 · AG-CR + 숏 3 배", "var(--bad)"),
    ("AG4S1", "AG4S1 · AG-CR4 + 숏 1 배", "var(--c7)"),
    ("AG4S2", "Altair(AG4S2) · AG-CR4 + 숏 2 배", "var(--c5)"),
)
"""롱 원장과 같은 눈금(그 원장 첫날 = 1,000)으로 그래프에 겹친다.

시작 전날 롱 원장 값에서 갈라진다."""
_SKIP = {
    "LONG_TARGET_POSITIVE": "롱이 켜져 있어 안 함",
    "OWN_HALT": "숏 차단 중",
    "COOLDOWN": "쉬는 중",
    "DAILY_LOSS": "하루 손실 2%",
    "NO_ROOM": "남은 증거금 없음",
}
_REASON = {"STOP": "손절", "48H": "48 시간", "LONG_PRIORITY": "롱 복귀"}


def _ags_panel(root: Path) -> str:
    """AGS1 · AGS3 · AG4S1 · AG4S2 — AG-CR · AG-CR4 Paper 롱 + 232 숏 관찰 장부.

    시작 = 1,000 으로 맞춰 보인다.
    """
    r = _read(root / "reports" / "ags_paper" / "latest.json")
    head = (
        '<div class="panel"><h2>AGS · AG-CR + 숏 (관찰 장부)</h2>'
        '<p class="mute">AG-CR(코인 3 배) · AG-CR4(4 배) Paper 를 롱으로 그대로 쓰고, 롱이 꺼진 '
        "코인에만 4 시간봉 RSI7 꺾임 숏을 붙인다(문서 232 · 234 · 235). AGS1 = AG-CR + 숏 1 배, "
        "AGS3 = AG-CR + 숏 3 배, AG4S1 · AG4S2 = AG-CR4 + 숏 1 · 2 배. "
        "숏 손익은 롱 평가액에 더한다. "
        "실제 주문은 없다.</p>"
    )
    if r is None:
        return head + '<p class="mute">아직 기록이 없다.</p></div>'
    ag = r.get("ag_cr") or {}
    target = ag.get("long_target_now") or {}
    on = [escape(str(k).replace("USDT", "")) for k, w in target.items() if float(w or 0) > 0]
    ag4 = r.get("ag_cr4") or {}
    cards = (
        f'<div class="card" style="border-left-color:var(--o2)">'
        f'<h3>{_pct(ag.get("return_since_start"))}</h3><div class="sub">'
        "AG-CR (숏 없음) · AGS1 · AGS3 와 같은 시작부터</div></div>"
    )
    if ag4:
        cards += (
            f'<div class="card" style="border-left-color:var(--o3)">'
            f'<h3>{_pct(ag4.get("return_since_start"))}</h3><div class="sub">'
            "AG-CR4 (숏 없음) · AG4S1 · AG4S2 와 같은 시작부터</div></div>"
        )
    rows = ""
    for name, b in (r.get("books") or {}).items():
        start, eq = b.get("start_equity"), b.get("equity")
        scaled = 1000 * float(eq) / float(start) if start and eq else None
        halt = b.get("own_halted_at")
        state = f"숏 차단 {escape(str(halt))}" if halt else "숏 가능"
        color = next((c for k, _, c in _AGS_LINES if k == name), "var(--line2)")
        cards += (
            f'<div class="card" style="border-left-color:{color}">'
            f"<h3>{_usd(scaled) if scaled else '시작 전'}</h3>"
            f'<div class="sub">{escape(str(name))} · '
            f"{escape(str(b.get('long') or 'AG-CR'))} + 숏 "
            f"{float(b.get('short_leverage') or 1):.0f} 배"
            f" · {_pct(b.get('return_since_start'))}"
            f" · 숏 {int(b.get('short_trades') or 0)} 건 손익 {_usd(b.get('short_realized'))}"
            f" · {state}</div></div>"
        )
        opened = " · ".join(
            f"{escape(str(o.get('symbol')).replace('USDT', ''))} {escape(str(o.get('entry_at')))} "
            f"진입 {float(o.get('entry') or 0):,.2f} 손절 {float(o.get('stop') or 0):,.2f}"
            for o in b.get("open_shorts") or []
        )
        trades = " · ".join(
            f"{escape(str(t.get('symbol')).replace('USDT', ''))} {escape(str(t.get('exit_at')))} "
            f"{escape(_REASON.get(str(t.get('reason')), str(t.get('reason'))))} "
            f"{_usd(t.get('net'))}"
            for t in (b.get("trades") or [])[-5:]
        )
        signals = " · ".join(
            f"{escape(str(e.get('symbol')).replace('USDT', ''))} {escape(str(e.get('at')))} "
            + escape(_SKIP.get(str(e.get("skipped")), "진입") if e.get("skipped") else "진입")
            for e in (b.get("signals") or [])[-5:]
        )
        rows += (
            f"<tr><th>{escape(str(name))}</th><td>열린 숏 {opened or '없음'}"
            f"<br>최근 청산 {trades or '—'}<br>최근 신호 {signals or '—'}</td></tr>"
        )
    note = (
        f"지금 AG-CR 롱이 켜진 코인: {', '.join(on) or '없음'} — 켜진 코인에는 숏을 내지 않는다."
        if target
        else ""
    )
    return (
        head + f'<div class="sub">기준 {escape(str(r.get("as_of_utc")))} UTC · 시작 '
        f"{escape(str(r.get('start_utc')))} UTC</div>"
        f'<div class="cards">{cards}</div><table>{rows}</table>'
        f"<div class='foot'>{note}</div></div>"
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
        return (
            intro
            + '<div class="panel"><p class="mute">아직 기록이 없다.</p></div>'
            + _ags_panel(root)
        )
    books = latest.get("books") or {}
    cards = ""
    for key, label, color in _BOOKS:  # 카드 왼쪽 띠 = 그래프 선 색
        b = books.get(key) or {}
        stopped = b.get("stopped")
        state = (
            f"정리됨 {escape(str(stopped))}" if stopped else f"고점 대비 {_pct(b.get('from_peak'))}"
        )
        cards += (
            f'<div class="card" style="border-left-color:{color}">'
            f'<h3>{_usd(b.get("value_usd"))}</h3><div class="sub">'
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
    ags = _read(root / "reports" / "ags_paper" / "latest.json") or {}
    for key, label, color in _AGS_LINES:
        raw = ((ags.get("books") or {}).get(key) or {}).get("path") or {}
        pts = [(t, float(v)) for d, v in sorted(raw.items()) if (t := _ms(str(d))) is not None]
        if len(pts) >= 2:
            series.append(Series(label, color, tuple(pts)))
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
    return intro + head + chart + detail + _ags_panel(root)
