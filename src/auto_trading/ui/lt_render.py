"""주도주 추세 Paper 화면 (docs/research/115) — 저장된 보고서·상태 파일만 읽는다. 주문 없음.

출처: ``reports/lt_paper/latest.json`` (매일 21:10 KST 화~토) · ``data/lt_paper/state.json``.
모든 동적 문자열은 이스케이프한다.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from html import escape
from pathlib import Path
from typing import Any

from auto_trading.ui.charts import Series, line_chart

__all__ = ["lt_paper_page"]

_REASON = {"trend_off": "추세 꺾임", "stop_35": "고점 −35%"}


def _read(path: Path) -> dict[str, Any] | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _pct(x: Any) -> str:
    try:
        v = float(x)
    except (TypeError, ValueError):
        return "—"
    cls = "pos" if v > 0 else ("neg" if v < 0 else "mute")
    return f'<span class="{cls}">{v * 100:+.2f}%</span>'


def _usd(x: Any) -> str:
    try:
        return f"${float(x):,.2f}"
    except (TypeError, ValueError):
        return "—"


def _ms(day: str) -> int | None:
    try:
        return int(datetime.fromisoformat(day).replace(tzinfo=UTC).timestamp() * 1000)
    except ValueError:
        return None


_NQ_COLORS = {"NQ": "var(--c1)", "SN20": "var(--c3)", "SN20C": "var(--c5)"}
"""NQ 계열 그래프 · 카드 왼쪽 선 색 (같게)."""
_NQ_BOOKS = (
    ("NQ", "nq_paper", "NQ · 나스닥 100 상위 10 (각 10%)"),
    ("SN20", "sn20_paper", "SN20 · S&P 10 + 나스닥 10 (각 5%)"),
    ("SN20C", "sn20c_paper", "SN20-C · SN20 + 10% 상한 덜어내기"),
)


def _nq_panel(root: Path) -> str:
    """NQ · SN20 · SN20-C Paper — 모멘텀 순위 상위 종목, 판단일(첫 거래일 · 14 일 뒤) 교체."""
    cards, rows = "", ""
    for key, folder, label in _NQ_BOOKS:
        r = _read(root / "reports" / folder / "latest.json")
        if r is None:
            continue
        held = ", ".join(escape(str(x.get("symbol"))) for x in r.get("positions") or []) or "현금"
        cards += (
            f'<div class="card" style="border-left-color:{_NQ_COLORS[key]}">'
            f'<h3>{_usd(r.get("equity_usd"))}</h3><div class="sub">'
            f"{escape(label)} · {_pct(r.get('return_since_start'))} · 같은 기간 QQQ "
            f"{_pct(r.get('qqq_since_start'))}</div></div>"
        )
        rows += (
            f"<tr><th>{escape(key)}</th><td>기준 {escape(str(r.get('date')))} · 다음 판단 "
            f"{escape(str(r.get('next_rebalance')))} · 보유 {held}</td></tr>"
        )
    if not cards:
        return ""
    chart = line_chart(nq_series(root), fmt=lambda v: f"${v:,.0f}", show_legend=True)
    return (
        '<div class="panel"><h2>나스닥 · S&amp;P 상위 종목 Paper (가상 1,000 USD 씩)</h2>'
        '<p class="mute">모멘텀 순위 상위 종목을 판단일(그달 첫 거래일 · 14 일 뒤)에 바꾸고, '
        "T/v2 추세가 꺼진 종목은 다음 날 판다. 현재 명단을 쓰므로 앞으로의 기록만 판단 근거다. "
        "실제 주문은 없다.</p>"
        f'<div class="cards">{cards}</div>{chart}<table>{rows}</table></div>'
    )


def nq_series(root: Path) -> list[Series]:
    """NQ · SN20 · SN20-C 날마다 평가액 (``reports/<장부>/<날짜>.json`` 의 equity_usd)."""
    out = []
    for key, folder, label in _NQ_BOOKS:
        pts = []
        for f in sorted((root / "reports" / folder).glob("20??-??-??.json")):
            r = _read(f)
            t = _ms(f.stem)
            if r and t is not None and r.get("equity_usd") is not None:
                pts.append((t, float(r["equity_usd"])))
        if pts:  # 기록이 없는 장부는 범례에도 넣지 않는다
            out.append(Series(label, _NQ_COLORS[key], tuple(pts), tag=key))
    return out


def lt_paper_page(data: Any) -> str:
    root = getattr(data, "root", None) or Path()
    latest = _read(root / "reports" / "lt_paper" / "latest.json")
    state = _read(root / "data" / "lt_paper" / "state.json") or {}
    intro = (
        '<div class="panel"><h2>주도주 추세 보유 · Paper (가상 1,000 USD)</h2>'
        '<p class="mute">문서 113 규칙을 앞으로의 자료로 매일 기록한다 — 주도 섹터 상위 3 안의 '
        "주도주 상위 10 을 그달 첫 거래일에 사고(추세 있음 · S&amp;P 500 이 200 일선 위), 매달 "
        "목표 비중까지 추매, 종목 추세가 꺾이거나 산 뒤 고점에서 −35% 이면 판다. "
        "<b>실제 주문은 없다.</b></p></div>"
    )
    if latest is None:
        return (
            intro
            + '<div class="panel"><p class="mute">아직 기록이 없다.</p></div>'
            + _nq_panel(root)
        )
    equity = latest.get("equity_usd")
    cards = (
        f'<div class="card"><h3>{_usd(equity)}</h3><div class="sub">평가액 · 시작 '
        f"{_usd(latest.get('start_usd'))} · {_pct(latest.get('return_since_start'))}</div></div>"
        f'<div class="card"><h3>{len(latest.get("positions") or [])} 종목</h3>'
        f'<div class="sub">보유 · 현금 {_usd(latest.get("cash_usd"))}</div></div>'
        f'<div class="card"><h3>{"위" if latest.get("spy_above_200") else "아래"}</h3>'
        '<div class="sub">S&amp;P 500 vs 200 일선 (아래면 새로 안 산다)</div></div>'
        f'<div class="card"><h3>{float(latest.get("breadth_above_50ma") or 0):.0%}</h3>'
        '<div class="sub">50 일선 위 종목 비율 (시장 폭)</div></div>'
    )
    head = (
        f'<div class="panel"><div class="sub">기준 {escape(str(latest.get("date")))} 종가 · '
        f"주도주 계산 {escape(str(latest.get('leaders_as_of')))} 종가</div>"
        f'<div class="cards">{cards}</div></div>'
    )
    points = []
    for d, v in state.get("equity") or []:
        t = _ms(str(d))
        if t is not None:
            points.append((t, float(v)))
    chart = (
        '<div class="panel"><h2>평가액 (USD)</h2>'
        + line_chart([Series("LT Paper", "var(--t2)", tuple(points))])
        + "</div>"
    )
    pos_rows = (
        "".join(
            f"<tr><td>{escape(str(p.get('symbol')))}</td>"
            f"<td class='nowrap'>{escape(str(p.get('entry')))}</td>"
            f"<td class='num'>{_usd(p.get('cost_usd'))}</td>"
            f"<td class='num'>{_usd(p.get('value_usd'))}</td>"
            f"<td class='num'>{_pct(p.get('return'))}</td>"
            f"<td class='num'>{_pct(p.get('from_peak'))}</td>"
            f"<td class='num'>{float(p.get('weight') or 0):.0%}</td></tr>"
            for p in latest.get("positions") or []
        )
        or "<tr><td colspan=7 class='mute'>보유 없음</td></tr>"
    )
    pending = latest.get("pending_sell_next_session") or {}
    pend = (
        " · ".join(
            f"{escape(str(s))} ({escape(_REASON.get(str(r), str(r)))})" for s, r in pending.items()
        )
        or "없음"
    )
    positions = (
        '<div class="panel"><h2>보유 종목</h2><table><tr><th>종목</th><th>산 날</th>'
        "<th class='num'>들인 돈</th><th class='num'>평가</th><th class='num'>수익</th>"
        f"<th class='num'>고점 대비</th><th class='num'>비중</th></tr>{pos_rows}</table>"
        f"<div class='foot'>다음 거래일 종가에 팔 종목: {pend}</div></div>"
    )
    lead_rows = (
        "".join(
            f"<tr><td>{i}</td><td>{escape(str(x.get('symbol')))}</td>"
            f"<td class='num'>{float(x.get('score') or 0):.3f}</td>"
            f"<td>{'있음' if x.get('trend_up') else '없음'}</td></tr>"
            for i, x in enumerate(latest.get("leaders") or [], 1)
        )
        or "<tr><td colspan=4 class='mute'>주도주 없음</td></tr>"
    )
    sectors = " · ".join(escape(str(s)) for s in latest.get("leading_sectors") or []) or "—"
    trades = list(reversed(state.get("trades") or []))[:20]
    trade_rows = (
        "".join(
            f"<tr><td>{escape(str(t.get('symbol')))}</td>"
            f"<td class='nowrap'>{escape(str(t.get('entry')))}</td>"
            f"<td class='nowrap'>{escape(str(t.get('exit')))}</td>"
            f"<td class='num'>{_pct(t.get('ret'))}</td>"
            f"<td>{escape(_REASON.get(str(t.get('reason')), str(t.get('reason'))))}</td></tr>"
            for t in trades
        )
        or "<tr><td colspan=5 class='mute'>아직 판 종목이 없다</td></tr>"
    )
    tables = (
        '<div class="grid2"><div class="panel"><h2>오늘의 주도주</h2>'
        f"<div class='sub'>주도 섹터: {sectors}</div><table><tr><th>#</th><th>종목</th>"
        f"<th class='num'>주도 점수</th><th>추세</th></tr>{lead_rows}</table>"
        "<div class='foot'>주도 점수 = 1·3·6 달 수익 순위 평균 (S&amp;P 500 안). "
        "매수는 그달 첫 거래일에만.</div></div>"
        '<div class="panel"><h2>판 기록 (최근 20)</h2><table><tr><th>종목</th><th>산 날</th>'
        f"<th>판 날</th><th class='num'>수익</th><th>이유</th></tr>{trade_rows}</table></div></div>"
    )
    news = latest.get("news_shadow") or {}
    news_line = (
        f"뉴스 {int(news.get('items') or 0)} 건 · 출처 {int(news.get('sources_ok') or 0)} 개 성공"
        + (
            " · 실패 " + ", ".join(escape(str(s)) for s in news.get("sources_failed") or [])
            if news.get("sources_failed")
            else ""
        )
        + f" · 모은 시각 {escape(str(news.get('fetched_at', '—')))} · AI 판단: "
        + ("기록됨" if news.get("ai_judgement") else "아직 없음 (API 키 필요)")
        if news
        else "뉴스 기록 없음"
    )
    news_panel = (
        '<div class="panel"><h2>뉴스 기록 (무료 RSS)</h2>'
        f"<div class='sub'>{news_line}</div>"
        "<div class='foot'>제목·요약만 모은다. AI 판단은 그날 모은 뉴스만 쓴다.</div></div>"
    )
    judge = news.get("ai_judgement") if isinstance(news, dict) else None
    ai_panel = ""
    if isinstance(judge, dict):
        room = {"up": "더 오를 여지 있음", "neutral": "중립", "down": "내릴 위험"}.get(
            str(judge.get("market_room")), str(judge.get("market_room"))
        )
        vs = judge.get("vs_mechanical") or {}

        def names(key: str) -> str:
            return " · ".join(escape(str(s)) for s in vs.get(key) or []) or "—"

        ai_panel = (
            f'<div class="panel"><h2>AI 판단 ({escape(str(judge.get("model")))})</h2>'
            f"<div class='sub'>증시 여지: <b>{escape(room)}</b> · 확신 "
            f"{float(judge.get('confidence') or 0):.0%} · 주도 섹터: "
            + " · ".join(escape(str(s)) for s in judge.get("leading_sectors") or [])
            + "</div>"
            f"<table><tr><th>기계 규칙과 AI 둘 다</th><td>{names('both')}</td></tr>"
            f"<tr><th>기계 규칙만</th><td>{names('only_mechanical')}</td></tr>"
            f"<tr><th>AI 만</th><td>{names('only_ai')}</td></tr></table>"
            f"<p class='mute'>{escape(str(judge.get('summary', '')))}</p>"
            "<div class='foot'>AI 는 그날 모은 뉴스 제목만 보고 판단한다 (기계 규칙 결과는 안 줌). "
            "기록·채점용이며 매매에 쓰지 않는다.</div></div>"
        )
    return intro + head + chart + positions + tables + news_panel + ai_panel + _nq_panel(root)
