"""국장 주도주·테마 그림자 기록 화면 (docs/research/143) — 저장된 보고서만 읽는다. 주문 없음.

출처: ``reports/kr_shadow/<날짜>.json`` · ``latest.json``
(매 거래일 20:20 KST, ``scripts.kr_shadow daily``).
같은 날 세 판단(가격 주도주 · 수급 주도주 · AI 뉴스 판단)을 나란히 보여 주고, 두 규칙의 Paper 를
같은 종목군 고르게 들기(EW)와 비교한다. 모든 동적 문자열은 이스케이프한다.
"""

from __future__ import annotations

import csv
from html import escape
from pathlib import Path
from typing import Any

from auto_trading.ui.charts import Series, line_chart
from auto_trading.ui.lt_render import _ms, _pct, _read

__all__ = ["follow_lines", "kospi_200", "kr_shadow_page", "load_history"]

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


_FOLLOW = (
    ("price", "① 가격 주도주", "var(--t2)"),
    ("flow", "② 수급 주도주", "var(--o2)"),
    ("ai", "③ AI 판단", "var(--o3)"),
    ("foreign", "④ 외국인 수급", "var(--o1)"),
)


def _closes(path: Path) -> dict[str, float]:
    """``data/kr_shadow`` 일봉 CSV(날짜 YYYYMMDD, 종가, …) → {YYYY-MM-DD: 종가}. 못 읽으면 빈 값."""
    out: dict[str, float] = {}
    try:
        with path.open(encoding="utf-8") as f:
            for row in csv.reader(f):
                if len(row) >= 2 and row[0].isdigit() and len(row[0]) == 8:
                    out[f"{row[0][:4]}-{row[0][4:6]}-{row[0][6:]}"] = float(row[1])
    except (OSError, ValueError):
        return {}
    return out


def _codes(rep: dict[str, Any], key: str) -> list[str]:
    if key == "ai":
        ai = rep.get("ai")
        rows = (ai.get("leaders") or []) if isinstance(ai, dict) else []
    else:
        rows = rep.get(f"{key}_leaders") or []
    return [str(r.get("code")) for r in rows if isinstance(r, dict) and r.get("code")]


def follow_lines(root: Path, history: list[dict[str, Any]]) -> dict[str, list[tuple[str, float]]]:
    """매일 **전날 저녁 목록**을 고르게 든다고 칠 때의 지수(첫 기록일 = 100, 비용 없음).

    다음 거래일 수익 = 전날 목록 종목들의 (오늘 종가 ÷ 전날 종가 − 1) 평균. 종가가 없는 종목은 뺀다.
    KOSPI 도 같은 날짜로 함께. 사용자 2026-10-15 '국장 주도주 같은 경우도 그래프를 만들어줘'.
    """
    base = root / "data" / "kr_shadow"
    days = [str(r.get("date")) for r in history if r.get("date")]
    cache: dict[str, dict[str, float]] = {}

    def close(code: str, day: str) -> float | None:
        if code not in cache:
            cache[code] = _closes(base / "prices" / f"{code}.csv")
        return cache[code].get(day)

    kospi = _closes(base / "kospi.csv")
    out: dict[str, list[tuple[str, float]]] = {k: [] for k, _, _ in _FOLLOW}
    out["kospi"] = []
    if len(days) < 2:
        return out
    level = dict.fromkeys(out, 100.0)
    for k in out:
        out[k].append((days[0], 100.0))
    for prev, rep, day in zip(days, history, days[1:], strict=False):
        for key, _, _ in _FOLLOW:
            rets = []
            for code in _codes(rep, key):
                a, b = close(code, prev), close(code, day)
                if a and b:
                    rets.append(b / a - 1)
            if rets:
                level[key] *= 1 + sum(rets) / len(rets)
                out[key].append((day, level[key]))
        a, b = kospi.get(prev), kospi.get(day)
        if a and b:
            level["kospi"] *= b / a
            out["kospi"].append((day, level["kospi"]))
    return out


def kospi_200(
    root: Path, days: int = 250
) -> tuple[list[tuple[str, float]], list[tuple[str, float]]]:
    """최근 ``days`` 거래일 KOSPI 종가와 200 일 이동평균 (``data/kr_shadow/kospi.csv``)."""
    closes = sorted(_closes(root / "data" / "kr_shadow" / "kospi.csv").items())
    out_c: list[tuple[str, float]] = []
    out_m: list[tuple[str, float]] = []
    run = 0.0
    for i, (d, c) in enumerate(closes):
        run += c
        if i >= 200:
            run -= closes[i - 200][1]
        if i >= len(closes) - days:
            out_c.append((d, c))
            if i >= 199:
                out_m.append((d, run / 200))
    return out_c, out_m


def _kospi_panel(root: Path) -> str:
    closes, ma = kospi_200(root)
    if not closes or not ma:
        return ""
    last, line = closes[-1][1], ma[-1][1]
    gap = last / line - 1
    chart = line_chart(
        [
            Series(
                "KOSPI",
                "var(--o1)",
                tuple((t, v) for d, v in closes if (t := _ms(d)) is not None),
            ),
            Series(
                "200 일선",
                "var(--hold)",
                tuple((t, v) for d, v in ma if (t := _ms(d)) is not None),
            ),
        ]
    )
    state = "위 — 새로 살 수 있음" if gap > 0 else "아래 — 새로 사지 않음"
    return (
        '<div class="panel"><h2>KOSPI 와 200 일선 (최근 1 년)</h2>'
        f"<div class='sub'>{escape(closes[-1][0])} 종가 {last:,.2f} · 200 일선 {line:,.2f} · "
        f"{_pct(gap)} · {state}</div>{chart}"
        "<div class='foot'>Paper 는 판단일(15 일 무렵 · 월말)에 KOSPI 가 200 일선 위일 때만 "
        "새로 산다.</div>"
        "</div>"
    )


def _follow_panel(root: Path, history: list[dict[str, Any]]) -> str:
    lines = follow_lines(root, history)
    series = [
        Series(label, color, tuple((t, v) for d, v in lines[key] if (t := _ms(d)) is not None))
        for key, label, color in _FOLLOW
    ]
    series.append(
        Series(
            "KOSPI",
            "var(--hold)",
            tuple((t, v) for d, v in lines["kospi"] if (t := _ms(d)) is not None),
        )
    )
    last = " · ".join(
        f"{label} {lines[key][-1][1] - 100:+.1f}%"
        for key, label in (*((k, lbl) for k, lbl, _ in _FOLLOW), ("kospi", "KOSPI"))
        if lines[key]
    )
    return (
        '<div class="panel"><h2>목록 따라가기 (첫 기록일 = 100)</h2>'
        '<p class="mute">매일 저녁 기록한 목록을 다음 거래일에 고르게 들었다고 칠 때의 움직임. '
        "비용 · 체결 · KOSPI 200 일선 규칙은 넣지 않은 비교용 지수이고 Paper 장부가 아니다.</p>"
        f"<div class='sub'>{escape(last) if last else '기록 2 일째부터 그려진다'}</div>"
        f"{line_chart(series)}</div>"
    )


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
            "<p class='mute'>2026-10-06 부터 굴린다 — 판단은 2 주마다(15 일 무렵 · 월말), "
            "KOSPI 가 200 일선 위일 때만 다음 거래일 종가에 산다.</p>"
            "</div>"
        )
    lines: dict[str, list[tuple[int, float]]] = {"price": [], "flow": [], "foreign": [], "ew": []}
    for rep in history:
        p = rep.get("paper") or {}
        t = _ms(str(rep.get("date")))
        if not p or t is None:
            continue
        for key, val in (
            ("price", (p.get("price_rule") or {}).get("value_krw")),
            ("flow", (p.get("flow_rule") or {}).get("value_krw")),
            ("foreign", (p.get("foreign_rule") or {}).get("value_krw")),
            ("ew", p.get("equal_weight_krw")),
        ):
            if val is not None:
                lines[key].append((t, float(val)))
    chart = line_chart(
        [
            Series("① 가격 규칙", "var(--t2)", tuple(lines["price"])),
            Series("② 수급 규칙", "var(--o2)", tuple(lines["flow"])),
            Series("④ 외국인 규칙", "var(--o1)", tuple(lines["foreign"])),
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
    frgn = paper.get("foreign_rule") or {}
    return (
        '<div class="panel"><h2>Paper (가상 1,000 만 원, '
        f"{escape(str(paper.get('start')))} 부터)</h2>"
        f"<div class='sub'>① 가격 규칙 {_won(price.get('value_krw'))} · ② 수급 규칙 "
        f"{_won(flow.get('value_krw'))} · ④ 외국인 규칙 {_won(frgn.get('value_krw'))} · "
        f"고르게 들기 {_won(paper.get('equal_weight_krw'))}</div>"
        f"{chart}</div>"
        '<div class="grid2"><div class="panel"><h2>① 가격 규칙 보유</h2>'
        f"{holds(price)}</div>"
        f'<div class="panel"><h2>② 수급 규칙 보유</h2>{holds(flow)}</div></div>'
        + (
            f'<div class="panel"><h2>④ 외국인 규칙 보유</h2>{holds(frgn)}</div>'
            if paper.get("foreign_rule") is not None
            else ""
        )
    )


_ACC_LABELS = (
    ("ai", "AI 주도주 적립"),
    ("ai_trend", "AI 주도주 · 추세 위만"),
    ("price", "가격 주도주 적립"),
    ("flow", "수급 주도주 적립"),
    ("ew", "종목군 전체 적립"),
    ("kospi", "KOSPI 지수 적립"),
)


def _acc_panel(acc: dict[str, Any] | None) -> str:
    """문서 147 — 매달 같은 금액을 넣고 팔지 않는 장기 적립 장부."""
    head = '<div class="panel"><h2>장기 적립 장부 (문서 147)</h2>'
    if not acc:
        return (
            head + "<p class='mute'>첫 적립은 2026-10-30 월말 목록 → 11-02 매수. "
            "매달 장부마다 가상 100 만 원을 넣고 팔지 않는다. 판단 2028-10.</p></div>"
        )
    ledgers = acc.get("ledgers") or {}
    rows = "".join(
        f"<tr><td>{label}</td><td>{_won(v.get('contributed'))}</td>"
        f"<td>{_won(v.get('value'))}</td><td>{_pct(v.get('return'))}</td>"
        f"<td>{_pct(v.get('irr'))}</td><td>{int(v.get('holdings') or 0)}</td></tr>"
        for key, label in _ACC_LABELS
        if isinstance(v := ledgers.get(key), dict)
    )
    missing = acc.get("ai_missing_month_ends") or []
    note = (
        f"<div class='foot'>AI 기록이 없던 월말 {len(missing)} 번 — 그달 돈은 현금으로 남아 "
        "다음 달 목록에 함께 쓴다.</div>"
        if missing
        else ""
    )
    return (
        head + f"<div class='sub'>{escape(str(acc.get('start')))} 부터 · 장부마다 매달 "
        f"{_won(acc.get('monthly_krw'))} · 팔지 않음 · 수익률은 넣은 돈 대비, 연 수익은 IRR</div>"
        "<table><tr><th>장부</th><th>넣은 돈</th><th>평가</th><th>수익률</th><th>연 수익(IRR)</th>"
        f"<th>보유 종목</th></tr>{rows}</table>{note}"
        "<div class='foot'>판단 2028-10 (문서 147 §4) — 그 전 숫자는 기록일 뿐 규칙을 바꾸는 "
        "근거로 쓰지 않는다. 실제 주문은 없다.</div></div>"
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
    if latest.get("foreign_leaders") is not None:
        lists += (
            '<div class="grid2">'
            + _leader_table(
                "④ 외국인 순매수 주도주",
                latest.get("foreign_leaders") or [],
                "외국인 순매수 ÷ 거래대금(기관 빼고), 20·60 일 백분위 → 같은 두 단계. "
                "2026-10-15 추가 — 연구로 검증한 적 없는 비교 목록.",
            )
            + _sectors("외국인 섹터 순위", latest.get("foreign_sectors") or [])
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
        + _kospi_panel(root)
        + _follow_panel(root, history)
        + lists
        + _ai_panel(ai, news)
        + sectors
        + _paper_panel(latest.get("paper"), history)
        + _acc_panel(
            latest.get("accumulation") if isinstance(latest.get("accumulation"), dict) else None
        )
        + _history_table(history)
    )
