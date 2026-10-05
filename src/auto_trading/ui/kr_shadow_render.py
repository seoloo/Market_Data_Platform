"""국장 주도주·테마 그림자 기록 화면 (docs/research/143) — 저장된 보고서만 읽는다. 주문 없음.

출처: ``reports/kr_shadow/<날짜>.json`` · ``latest.json``
(매 거래일 20:20 KST, ``scripts.kr_shadow daily``).
같은 날 세 판단(가격 주도주 · 수급 주도주 · AI 뉴스 판단)을 나란히 보여 주고, 두 규칙의 Paper 를
같은 종목군 고르게 들기(EW)와 비교한다. 모든 동적 문자열은 이스케이프한다.
"""

from __future__ import annotations

from html import escape
from pathlib import Path
from typing import Any

from auto_trading.ui.charts import Series, line_chart
from auto_trading.ui.lt_render import _ms, _pct, _read

__all__ = ["kr_shadow_page", "load_history"]

_REASON = {"trend_off": "추세 꺾임", "stop_35": "고점 −35%", "delisted": "상장폐지"}
_ROOM = {"up": "더 오를 여지 있음", "neutral": "중립", "down": "내릴 위험"}
HISTORY_DAYS = 15


def load_history(root: Path) -> list[dict[str, Any]]:
    """날짜 이름 보고서들(오래된 순). 읽지 못한 파일은 건너뛴다."""
    out = []
    for path in sorted((root / "reports" / "kr_shadow").glob("20*.json")):
        rep = _read(path)
        if rep is not None:
            out.append(rep)
    return out


def _won(x: Any) -> str:
    try:
        return f"{float(x):,.0f}원"
    except (TypeError, ValueError):
        return "—"


def _leader_table(title: str, rows: list[dict[str, Any]], foot: str) -> str:
    body = (
        "".join(
            f"<tr><td>{i}</td><td>{escape(str(x.get('name')))}"
            f" <span class='mute'>{escape(str(x.get('code')))}</span></td>"
            f"<td>{escape(str(x.get('sector', '')))}</td>"
            f"<td class='num'>{float(x.get('score') or 0):.3f}</td>"
            f"<td>{'있음' if x.get('trend') else '없음'}</td></tr>"
            for i, x in enumerate(rows, 1)
        )
        or "<tr><td colspan=5 class='mute'>없음</td></tr>"
    )
    return (
        f'<div class="panel"><h2>{escape(title)}</h2><table><tr><th>#</th><th>종목</th>'
        f"<th>섹터</th><th class='num'>점수</th><th>추세</th></tr>{body}</table>"
        f"<div class='foot'>{foot}</div></div>"
    )


def _sectors(title: str, rows: list[dict[str, Any]]) -> str:
    body = (
        "".join(
            f"<tr><td>{i}</td><td>{escape(str(x.get('sector')))}</td>"
            f"<td class='num'>{float(x.get('median') or 0):.3f}</td>"
            f"<td class='num'>{int(x.get('n') or 0)}</td></tr>"
            for i, x in enumerate(rows[:6], 1)
        )
        or "<tr><td colspan=4 class='mute'>없음</td></tr>"
    )
    return (
        f'<div class="panel"><h2>{escape(title)}</h2><table><tr><th>#</th><th>섹터</th>'
        f"<th class='num'>중앙값</th><th class='num'>종목 수</th></tr>{body}</table></div>"
    )


def _ai_panel(ai: dict[str, Any] | None, news: dict[str, Any] | None) -> str:
    news_line = (
        f"뉴스 {int(news.get('items') or 0)} 건 · 모은 시각 {escape(str(news.get('fetched_at')))}"
        + (
            " · 실패 " + ", ".join(escape(str(s)) for s in news.get("sources_failed") or [])
            if news.get("sources_failed")
            else ""
        )
        if news
        else "뉴스 기록 없음"
    )
    if not ai:
        return (
            '<div class="panel"><h2>AI 판단</h2>'
            f"<div class='sub'>{news_line}</div><p class='mute'>아직 없음</p></div>"
        )
    leaders = " · ".join(f"{escape(str(x.get('name')))}" for x in ai.get("leaders") or []) or "—"

    def names(key: str) -> str:
        return str(len(ai.get(key) or []))

    return (
        f'<div class="panel"><h2>③ AI 판단 ({escape(str(ai.get("model")))})</h2>'
        f"<div class='sub'>{news_line}</div>"
        f"<table><tr><th>KOSPI 여지</th><td><b>"
        f"{escape(_ROOM.get(str(ai.get('market_room')), str(ai.get('market_room'))))}</b> · "
        f"확신 {float(ai.get('confidence') or 0):.0%}</td></tr>"
        "<tr><th>주도 테마</th><td>"
        + " · ".join(escape(str(t)) for t in ai.get("themes") or [])
        + f"</td></tr><tr><th>주도주</th><td>{leaders}</td></tr>"
        f"<tr><th>기계 목록과 겹침</th><td>가격 {names('overlap_price')} · "
        f"수급 {names('overlap_flow')}</td></tr></table>"
        f"<p class='mute'>{escape(str(ai.get('summary', '')))}</p>"
        "<div class='foot'>AI 에게는 그날 모은 한국어 뉴스 제목과 종목군 이름만 준다 "
        "(기계 목록·가격 지표는 안 줌). 기록·채점용이며 매매에 쓰지 않는다.</div></div>"
    )


def _paper_panel(paper: dict[str, Any] | None, history: list[dict[str, Any]]) -> str:
    if not paper:
        return (
            '<div class="panel"><h2>Paper (가상 1,000 만 원)</h2>'
            "<p class='mute'>2026-10-06 부터 굴린다 — 첫 매수는 10-30 월말 신호 → 11-02 종가.</p>"
            "</div>"
        )
    lines: dict[str, list[tuple[int, float]]] = {"price": [], "flow": [], "ew": []}
    for rep in history:
        p = rep.get("paper") or {}
        t = _ms(str(rep.get("date")))
        if not p or t is None:
            continue
        for key, val in (
            ("price", (p.get("price_rule") or {}).get("value_krw")),
            ("flow", (p.get("flow_rule") or {}).get("value_krw")),
            ("ew", p.get("equal_weight_krw")),
        ):
            if val is not None:
                lines[key].append((t, float(val)))
    chart = line_chart(
        [
            Series("① 가격 규칙", "var(--t2)", tuple(lines["price"])),
            Series("② 수급 규칙", "var(--o2)", tuple(lines["flow"])),
            Series("고르게 들기", "var(--hold)", tuple(lines["ew"])),
        ]
    )

    def holds(rule: dict[str, Any]) -> str:
        rows = (
            "".join(
                f"<tr><td>{escape(str(h.get('name')))}</td>"
                f"<td class='nowrap'>{escape(str(h.get('entry')))}</td>"
                f"<td class='num'>{_pct(h.get('return'))}</td></tr>"
                for h in rule.get("positions") or []
            )
            or "<tr><td colspan=3 class='mute'>보유 없음</td></tr>"
        )
        sells = (
            " · ".join(
                f"{escape(str(c))} ({escape(_REASON.get(str(r), str(r)))})"
                for c, r in (rule.get("sell_next_close") or {}).items()
            )
            or "없음"
        )
        return (
            f"<table><tr><th>종목</th><th>산 날</th><th class='num'>수익</th></tr>{rows}</table>"
            f"<div class='foot'>다음 거래일 종가에 팔 종목: {sells}</div>"
        )

    price = paper.get("price_rule") or {}
    flow = paper.get("flow_rule") or {}
    return (
        '<div class="panel"><h2>Paper (가상 1,000 만 원, '
        f"{escape(str(paper.get('start')))} 부터)</h2>"
        f"<div class='sub'>① 가격 규칙 {_won(price.get('value_krw'))} · ② 수급 규칙 "
        f"{_won(flow.get('value_krw'))} · 고르게 들기 {_won(paper.get('equal_weight_krw'))}</div>"
        f"{chart}</div>"
        '<div class="grid2"><div class="panel"><h2>① 가격 규칙 보유</h2>'
        f"{holds(price)}</div>"
        f'<div class="panel"><h2>② 수급 규칙 보유</h2>{holds(flow)}</div></div>'
    )


def _history_table(history: list[dict[str, Any]]) -> str:
    rows = []
    for rep in reversed(history[-HISTORY_DAYS:]):
        ai = rep.get("ai") or {}
        top = ", ".join(escape(str(x.get("name"))) for x in (rep.get("price_leaders") or [])[:3])
        flow = ", ".join(escape(str(x.get("name"))) for x in (rep.get("flow_leaders") or [])[:3])
        themes = " · ".join(escape(str(t)) for t in ai.get("themes") or []) or "—"
        room = escape(_ROOM.get(str(ai.get("market_room")), "—")) if ai else "—"
        rows.append(
            f"<tr><td class='nowrap'>{escape(str(rep.get('date')))}</td>"
            f"<td>{'위' if rep.get('kospi_above_200d') else '아래'}</td>"
            f"<td>{top or '—'}</td><td>{flow or '—'}</td><td>{themes}</td><td>{room}</td></tr>"
        )
    body = "".join(rows) or "<tr><td colspan=6 class='mute'>기록 없음</td></tr>"
    return (
        f'<div class="panel"><h2>날짜별 기록 (최근 {HISTORY_DAYS} 거래일)</h2><table>'
        "<tr><th>날짜</th><th>KOSPI 200 일선</th><th>가격 주도주 상위 3</th>"
        f"<th>수급 주도주 상위 3</th><th>AI 테마</th><th>AI 여지</th></tr>{body}</table>"
        "<div class='foot'>채점: 매달 마지막 거래일의 세 목록 → 다음 1·3 달 초과 수익(같은 종목군 "
        "고르게 들기 대비). 2027-01 첫 보고 · 2027-10 판단"
        "(12 번 중 8 번 이상 1 달 초과 수익 &gt; 0)."
        "</div></div>"
    )


def kr_shadow_page(data: Any) -> str:
    root = getattr(data, "root", None) or Path()
    latest = _read(root / "reports" / "kr_shadow" / "latest.json")
    intro = (
        '<div class="panel"><h2>국장 주도주·테마 그림자 기록</h2>'
        '<p class="mute">문서 143 — 매 거래일 저녁, 유가증권시장 거래대금 상위 200 안에서 '
        "① 가격 주도주(141 규칙) ② 수급 주도주(142 규칙: 외국인 + 기관 순매수) ③ 한국어 뉴스를 본 "
        "AI 판단을 나란히 기록한다. 앞으로의 자료라 사후 지식이 없다. "
        "<b>실제 주문은 없다.</b></p></div>"
    )
    if latest is None:
        return intro + '<div class="panel"><p class="mute">아직 기록이 없다.</p></div>'
    history = load_history(root)
    news = latest.get("news") if isinstance(latest.get("news"), dict) else None
    cards = (
        f'<div class="card"><h3>{escape(str(latest.get("date")))}</h3>'
        f'<div class="sub">기준 종가 · 기록 {len(history)} 거래일</div></div>'
        f'<div class="card"><h3>{"위" if latest.get("kospi_above_200d") else "아래"}</h3>'
        '<div class="sub">KOSPI vs 200 일선 (아래면 새로 안 산다)</div></div>'
        f'<div class="card"><h3>{_pct(latest.get("universe_median_6m"))}</h3>'
        f'<div class="sub">종목군 {int(latest.get("universe") or 0)} · 6 달 수익 중앙값</div></div>'
        f'<div class="card"><h3>{int(latest.get("flow_coverage") or 0)}</h3>'
        '<div class="sub">수급 자료가 있는 종목 (종목군 안)</div></div>'
    )
    head = f'<div class="panel"><div class="cards">{cards}</div></div>'
    lists = (
        '<div class="grid2">'
        + _leader_table(
            "① 가격 주도주",
            latest.get("price_leaders") or [],
            "1·3·6 달 수익 백분위 → 섹터 중앙값 상위 3 → 상위 10 (문서 141).",
        )
        + _leader_table(
            "② 수급 주도주",
            latest.get("flow_leaders") or [],
            "(외국인 + 기관) 순매수 ÷ 거래대금, 20·60 일 백분위 → 같은 두 단계 (문서 142). "
            "종목이 적은 섹터가 위로 오면 10 개보다 적다.",
        )
        + "</div>"
    )
    sectors = (
        '<div class="grid2">'
        + _sectors("가격 섹터 순위", latest.get("price_sectors") or [])
        + _sectors("수급 섹터 순위", latest.get("flow_sectors") or [])
        + "</div>"
    )
    ai = latest.get("ai") if isinstance(latest.get("ai"), dict) else None
    return (
        intro
        + head
        + lists
        + _ai_panel(ai, news)
        + sectors
        + _paper_panel(latest.get("paper"), history)
        + _history_table(history)
    )
