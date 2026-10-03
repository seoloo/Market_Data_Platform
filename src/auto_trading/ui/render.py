"""대시보드 화면 HTML (순수 함수). 모든 동적 문자열은 이스케이프한다.

구성은 사용자가 고른 초안을 따른다: 왼쪽 메뉴, 실행 현황, 상단 카드, 전략별 자산 변화(100 기준),
시스템 상태, 테스트넷 검증 체크리스트, 포지션·보호 상태, 최근 이벤트.
**보여 주기만 한다** — 버튼·폼·쓰기 요청이 없다.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from html import escape
from pathlib import Path

from auto_trading.ui.charts import Series, line_chart
from auto_trading.ui.data import DashboardData, DrillView, LedgerView, Point, WatchView
from auto_trading.ui.demo_render import demo_card, demo_page
from auto_trading.ui.lt_render import lt_paper_page
from auto_trading.ui.qndx_render import accumulate_page
from auto_trading.ui.qs_render import qs_paper_page
from auto_trading.ui.running import running_page

__all__ = ["PAGES", "render_page"]

PAGES: tuple[tuple[str, str, str], ...] = (
    ("overview", "/", "운영 개요"),
    ("running", "/running", "실행 현황"),
    ("compare", "/compare", "전략 비교"),
    ("positions", "/positions", "포지션 · 주문"),
    ("testnet", "/testnet", "테스트넷 검증"),
    ("risk", "/risk", "위험 · 알림"),
    ("demo", "/demo", "데모 · 실거래 준비"),
    ("overseas", "/overseas", "해외 주식"),
    ("lt_paper", "/lt-paper", "주도주 Paper"),
    ("qs_paper", "/qs-paper", "QS Paper"),
    ("research", "/research", "연구 기록"),
)
_STOCK_PAGES = frozenset({"overseas", "lt_paper", "qs_paper"})
_PAPER_PAGES = frozenset({"lt_paper", "qs_paper"})
_KST = timedelta(hours=9)
_DAY_MS = 86_400_000
_COLORS = {
    "t2": "var(--t2)",
    "o1": "var(--o1)",
    "o2": "var(--o2)",
    "o3": "var(--o3)",
    "hold": "var(--hold)",
}
PHASE7_FROM = datetime(2026, 9, 25, tzinfo=UTC)
PHASE7_DAYS = 14
_STOP_LINE, _WARN_LINE, _HALT_LINE = 0.25, 0.20, 0.30

_CSS = (Path(__file__).with_name("dashboard.css")).read_text(encoding="utf-8")


def _kst(ms: int, fmt: str = "%Y-%m-%d %H:%M") -> str:
    return (datetime.fromtimestamp(ms / 1000, tz=UTC) + _KST).strftime(fmt)


def _date(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, tz=UTC).strftime("%Y-%m-%d")


def _pct(x: float | None, digits: int = 2) -> str:
    if x is None:
        return "—"
    cls = "pos" if x > 0 else ("neg" if x < 0 else "mute")
    return f'<span class="{cls}">{x * 100:+.{digits}f}%</span>'


def _money2(x: float | None) -> str:
    return "—" if x is None else f"{x:,.2f}"


def _yield_note(data: DashboardData) -> str:
    if not data.cash_yield:
        return ""
    return (
        f'<div class="foot">현금 이자 가정: 쉬는 현금을 USDT 유동형 예치에 둔다고 보고 계산한 값 '
        f"({escape(data.cash_yield)}, config/cash_yield.yaml). 선물 원장은 선물 명목의 절반을 "
        "증거금으로 남긴다. 원장이 아니며 실제 예치는 없다.</div>"
    )


def _money(x: float | None) -> str:
    return "" if x is None else f"{x:,.0f}"


def _dot(level: str) -> str:
    cls = {"ok": "ok-bg", "warn": "warn-bg", "bad": "bad-bg"}.get(level, "mute-bg")
    return f'<span class="dot {cls}"></span>'


def _layout(data: DashboardData, page: str, body: str) -> str:
    main_nav = "".join(
        f'<a href="{href}" class="{"on" if key == page else ""}">{escape(label)}</a>'
        for key, href, label in PAGES
        if key not in _STOCK_PAGES
    )
    nav = (
        f'<div class="nav-group"><div class="nav-heading">자동매매</div>{main_nav}</div>'
        '<div class="nav-group nav-stock"><div class="nav-heading">해외 주식</div>'
        f'<a href="/overseas" class="{"on" if page == "overseas" else ""}">'
        "적립 현황</a>"
        f'<a href="/lt-paper" class="{"on" if page == "lt_paper" else ""}">'
        "주도주 Paper</a>"
        f'<a href="/qs-paper" class="{"on" if page == "qs_paper" else ""}">'
        "QS Paper</a></div>"
    )
    title = next(label for key, _, label in PAGES if key == page)
    errors = (
        '<div class="errors">읽기 실패: ' + " · ".join(escape(e) for e in data.errors) + "</div>"
        if data.errors
        else ""
    )
    trading_badge = (
        '<span class="badge">저장된 적립 기록</span>'
        if page == "overseas"
        else '<span class="badge">저장된 Paper 기록 · 주문 없음</span>'
        if page in _PAPER_PAGES
        else '<span class="badge red">실거래 비활성</span>'
    )
    if page in _PAPER_PAGES:
        foot = (
            "60초마다 저장된 Paper 보고서를 다시 읽는다 · "
            "이 화면은 KIS 계좌를 조회하거나 주문하지 않는다."
        )
    elif page == "overseas":
        foot = (
            "60초마다 저장된 적립 보고서를 다시 읽는다 · "
            "이 화면은 KIS 계좌를 실시간 조회하거나 주문하지 않는다."
        )
    else:
        foot = (
            "60초마다 새로 읽는다 · 데이터: Paper 원장(가상 체결)·테스트넷 기록·"
            "데모(가짜 돈)·일일 점검 보고. PAPER·TESTNET·DEMO 자산은 합산하지 않는다."
        )
    return (
        '<!doctype html><html lang="ko"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        '<meta http-equiv="refresh" content="60">'
        f"<title>{escape(title)} · Auto Trading</title><style>{_CSS}</style></head><body>"
        f'<div class="layout"><nav><div class="logo">AUTO <b>TRADING</b></div>{nav}</nav><main>'
        f'<header><h1>{escape(title)}</h1><span class="date">{_kst(data.now_ms)} KST</span>'
        f'<span class="badge">읽기 전용 · 다시 실행 버튼 1개</span>{trading_badge}</header>'
        f"{errors}{body}"
        f'<div class="foot">{foot}</div></main></div></body></html>'
    )


# ---------------------------------------------------------------- 공용 조각


def _indexed(points: Sequence[Point], since_ms: int) -> tuple[tuple[int, float], ...]:
    chosen = [p for p in points if p.day_ms >= since_ms]
    if not chosen:
        return ()
    base = chosen[0].equity
    return tuple((p.day_ms, 100 * p.equity / base) for p in chosen)


def _chart_panel(data: DashboardData) -> str:
    last = max((p.day_ms for lg in data.ledgers for p in lg.points), default=data.now_ms)
    ranges = (
        ("r1", "1주", last - 6 * _DAY_MS),
        ("r2", "1개월", last - 29 * _DAY_MS),
        ("r3", "전체", 0),
    )
    lines = [(lg.name, _COLORS.get(lg.key, "var(--text)"), lg.points) for lg in data.ledgers]
    lines.append(("현물 50/50 보유", _COLORS["hold"], data.benchmark))
    charts = "".join(
        f'<div class="rng {rid}">'
        + line_chart([Series(n, c, _indexed(p, since)) for n, c, p in lines])
        + "</div>"
        for rid, _, since in ranges
    )
    inputs = "".join(
        f'<input type="radio" name="rng" id="{rid}"{" checked" if rid == "r3" else ""}>'
        for rid, _, _ in ranges
    )
    labels = "".join(f'<label for="{rid}">{label}</label>' for rid, label, _ in ranges)
    legend = "".join(
        f'<span><span class="dot" style="background:{c}"></span>{escape(n)}</span>'
        for n, c, _ in lines
    )
    return (
        f'<div class="panel"><div class="ranges">{inputs}'
        f'<div class="row"><h2>전략별 자산 변화</h2><div>{labels}</div></div>'
        f'<div class="legend">각 원장 시작값 100 기준 · {legend}</div>'
        f'<div class="charts">{charts}</div></div></div>'
    )


def _drill_status(data: DashboardData) -> tuple[str, str]:
    """(한 줄 요약, 상태 수준)."""
    daily = [d for d in data.drills if d.kind == "drill"]
    if not daily:
        return "매일 훈련 기록 없음", "mute"
    last = daily[-1]
    since = int(PHASE7_FROM.timestamp() * 1000)
    period = {_date(d.at_ms): d.ok for d in daily if d.at_ms >= since}
    text = f"최근 {_kst(last.at_ms, '%m-%d %H:%M')} {last.passed}/{len(last.checks)}"
    if data.now_ms >= since:
        text += f" · {PHASE7_DAYS}일 중 실행 {len(period)} · 통과 {sum(period.values())}"
    else:
        text += " · 운영 기간(09-25~) 전"
    return text, "ok" if last.ok else "warn"


def _ledger_card(lg: LedgerView) -> str:
    flags = []
    if lg.liquidations:
        flags.append('<span class="neg">청산 발생</span>')
    if lg.halted:
        flags.append('<span class="neg">계좌 정지</span>')
    if lg.stop_hits:
        flags.append(f'<span class="neg">비상 스톱 {lg.stop_hits}회</span>')
    extra = " · ".join(flags) or f"{escape(lg.status)} · {len(lg.points)}일"
    return (
        f'<div class="card" style="border-left-color:{_COLORS.get(lg.key, "var(--line)")}">'
        f'<h3>{escape(lg.name)}<span class="tag">PAPER</span></h3>'
        f'<div class="sub">{escape(lg.subtitle)} · 가상 자산</div>'
        f'<div class="big">{lg.equity:,.2f} <span class="sub">USDT</span></div>'
        f"<div>{_pct(lg.change)} "
        f'<span class="mute">시작 대비 · 노출 {lg.exposure:.2f}배</span></div>'
        f'<div class="sub">{extra}</div>{_yield_line(lg)}</div>'
    )


def _yield_line(lg: LedgerView) -> str:
    if not lg.with_yield:
        return ""
    gain = lg.with_yield[-1].equity - lg.equity
    return (
        f'<div class="sub">현금 이자 가정 시 {lg.with_yield[-1].equity:,.2f} '
        f'<span class="pos">({gain:+,.2f})</span></div>'
    )


def _stream_checklist(data: DashboardData) -> str:
    """최근 스트림·스톱 훈련에서 항목별 통과 여부."""
    items = (
        ("체결 알림", "stream", "진입 체결 이벤트"),
        ("스톱 등록 알림", "stream", "스톱 NEW 이벤트"),
        ("외부 취소 반영", "stream", "CANCELED"),
        ("모르는 주문 감지", "stream", "UNTRACKED"),
        ("연결 복구", "stream", "재연결"),
        ("스톱 발동", "stop", "스톱 기록 FILLED"),
        ("최종 정리", "stream", "평탄"),
    )
    rows = []
    for label, kind, key in items:
        runs = [d for d in data.drills if d.kind == kind]
        state, when = "mute", "기록 없음"
        if runs:
            last = runs[-1]
            hit = [ok for name, ok, _ in last.checks if key in name]
            if hit:
                state = "ok" if all(hit) else "bad"
                when = _kst(last.at_ms, "%m-%d")
        text = {"ok": "확인", "bad": "실패", "mute": "없음"}[state]
        rows.append(
            f"<tr><td>{label}</td><td>{_dot(state)}{text}</td><td class='mute'>{when}</td></tr>"
        )
    return (
        '<div class="panel"><h2>테스트넷 검증</h2><table>'
        + "".join(rows)
        + '</table><div class="foot">수익 검증이 아닌 주문 안전성 검증 · 가상 자금</div></div>'
    )


def _system_panel(data: DashboardData) -> str:
    watch = data.watch[0] if data.watch else None
    if watch is None:
        check = (_dot("mute") + "기록 없음", "")
    else:
        level = {0: "ok", 1: "warn"}.get(watch.exit_code or 0, "bad")
        text = {"ok": "이상 없음", "warn": "경고", "bad": "조치 필요"}[level]
        check = (_dot(level) + text, watch.date)
    drill_text, drill_level = _drill_status(data)
    last_rec = max((lg.points[-1].day_ms for lg in data.ledgers if lg.points), default=None)
    rows = (
        ("일일 점검", check[0], check[1]),
        (
            "원장 기록",
            _dot("ok" if last_rec else "mute") + (_date(last_rec) if last_rec else "없음"),
            "마지막 결정일",
        ),
        ("테스트넷 매일 훈련", _dot(drill_level) + escape(drill_text), ""),
        ("WebSocket", _dot("mute") + "훈련에서만 사용", "운영 실행기 미연결"),
        ("실거래 주문", _dot("bad") + "비활성", "주문 경로 없음 (Phase 8 전)"),
    )
    body = "".join(
        f"<tr><td>{a}</td><td>{b}</td><td class='mute'>{c}</td></tr>" for a, b, c in rows
    )
    return f'<div class="panel"><h2>시스템 상태</h2><table>{body}</table></div>'


def _positions_table(data: DashboardData) -> str:
    rows = [
        f"<tr><td>{escape(h.ledger)}<span class='tag'>PAPER</span></td><td>{escape(h.symbol)}</td>"
        f"<td>{escape(h.venue)} 롱</td><td class='num'>{h.qty:,.5f}</td>"
        f"<td class='num'>{h.notional:,.2f}</td><td class='num'>{h.weight * 100:.1f}%</td>"
        f"<td>{escape(h.protection)}</td></tr>"
        for lg in data.ledgers
        for h in lg.holdings
    ]
    if not rows:
        rows = ["<tr><td colspan=7 class='mute'>보유 없음 (현금)</td></tr>"]
    return (
        '<div class="panel"><h2>포지션 · 보호 상태</h2><table><tr><th>원장</th><th>종목</th>'
        "<th>방향</th><th class='num'>수량</th><th class='num'>명목(USDT)</th>"
        "<th class='num'>자본 대비</th><th>보호</th></tr>" + "".join(rows) + "</table>"
        '<div class="foot">가상 체결 · 실제 주문 없음. '
        "테스트넷 훈련 포지션은 매번 정리되어 표시하지 않는다.</div></div>"
    )


def _events(data: DashboardData, limit: int = 10) -> str:
    events: list[tuple[int, str, str]] = []
    for lg in data.ledgers:
        for p in lg.points[-3:]:
            events.append(
                (p.day_ms + _DAY_MS, "원장", f"{lg.name} {_date(p.day_ms)} 기록 · {p.equity:,.2f}")
            )
    for d in data.drills[-6:]:
        kind = {"drill": "매일 훈련", "stop": "스톱 발동 훈련", "stream": "WS 훈련"}[d.kind]
        events.append((d.at_ms, "테스트넷", f"{kind} {d.outcome} {d.passed}/{len(d.checks)}"))
    for w in data.watch[:3]:
        for level, ledger, message in w.findings:
            if level != "INFO":
                events.append((0, level, f"[{w.date}] {ledger}: {message}"))
    events.sort(key=lambda e: e[0], reverse=True)
    rows = "".join(
        f"<tr><td class='mute'>{_kst(t, '%m-%d %H:%M') if t else ''}</td>"
        f"<td><span class='tag'>{escape(tag)}</span></td><td>{escape(text)}</td></tr>"
        for t, tag, text in events[:limit]
    )
    return f'<div class="panel"><h2>최근 이벤트</h2><table>{rows}</table></div>'


# ---------------------------------------------------------------- 화면


def _overview(data: DashboardData) -> str:
    days = min((len(lg.points) for lg in data.ledgers), default=0)
    banner = (
        f'<div class="banner">초기 데이터 — 원장 기록 {days}일. '
        "곡선과 수익률은 아직 의미가 작다.</div>"
        if days < 7
        else ""
    )
    drill_text, _ = _drill_status(data)
    last_stream = next((d for d in reversed(data.drills) if d.kind == "stream"), None)
    phase7 = (
        '<div class="card" style="border-left-color:var(--accent)">'
        '<h3>Phase 7<span class="tag">TESTNET</span></h3>'
        '<div class="sub">가상 자금 · 주문 안전성</div>'
        f'<div class="big" style="font-size:16px">{escape(drill_text)}</div>'
        + (
            f'<div class="sub">WS 훈련 {escape(last_stream.outcome)} '
            f"{last_stream.passed}/{len(last_stream.checks)}</div>"
            if last_stream
            else ""
        )
        + "</div>"
    )
    note = (
        '<div class="card"><div class="sub">'
        "각 전략은 독립 원장으로 운영하며 자산을 합산하지 않는다."
        "<br>PAPER 자산과 TESTNET 자산은 절대 합산하지 않는다.</div></div>"
    )
    cards = (
        "".join(_ledger_card(lg) for lg in data.ledgers)
        + phase7
        + demo_card(data.demo, data.now_ms)
        + note
    )
    return (
        f'{banner}<div class="cards">{cards}</div>'
        f'<div class="grid2"><div>{_chart_panel(data)}{_positions_table(data)}{_events(data)}</div>'
        f"<div>{_system_panel(data)}{_stream_checklist(data)}</div></div>"
    )


def _compare(data: DashboardData) -> str:
    bt = (
        "".join(
            f"<tr><td>{escape(r.name)}</td><td class='num'>{r.cagr * 100:.1f}%</td>"
            f"<td class='num'>{r.mdd * 100:.1f}%</td><td class='num'>{r.sharpe:.2f}</td>"
            f"<td class='num'>{_money(r.final_equity)}</td></tr>"
            for r in data.backtests
        )
        or "<tr><td colspan=5 class='mute'>판정 결과 파일 없음</td></tr>"
    )
    by_key = {lg.key: lg for lg in data.ledgers}
    fw = "".join(
        f"<tr><td>{escape(lg.name)}</td><td class='num'>{len(lg.points)}</td>"
        f"<td class='num'>{lg.equity:,.2f}</td><td class='num'>{_pct(lg.change)}</td>"
        f"<td class='num'>{lg.drawdown * 100:.1f}%</td>"
        f"<td class='num'>{_money2(lg.with_yield[-1].equity if lg.with_yield else None)}</td></tr>"
        for lg in data.ledgers
    )
    if data.benchmark:
        b = data.benchmark
        fw += (
            f"<tr><td>현물 50/50 보유</td><td class='num'>{len(b)}</td>"
            f"<td class='num'>{b[-1].equity:,.2f}</td>"
            f"<td class='num'>{_pct(b[-1].equity / b[0].equity - 1)}</td>"
            "<td class='num'>—</td><td class='num'>—</td></tr>"
        )
    insurance = ""
    if "o1" in by_key and "o2" in by_key:
        gap = by_key["o2"].equity - by_key["o1"].equity
        insurance = (
            f'<div class="foot">보험 비용(O/v2 − O/v1) 지금까지 {gap:+,.2f} USDT · '
            f"비상 스톱 {by_key['o2'].stop_hits}회</div>"
        )
    return (
        f"{_chart_panel(data)}"
        '<div class="panel"><h2>전향 관찰 (Paper, 처음 보는 데이터)</h2><table><tr><th>원장</th>'
        "<th class='num'>일수</th><th class='num'>가상 자산</th><th class='num'>시작 대비</th>"
        f"<th class='num'>고점 대비</th><th class='num'>현금 이자 가정</th></tr>{fw}</table>"
        f"{insurance}{_yield_note(data)}</div>"
        '<div class="panel"><h2>백테스트 (2020-03 ~ 2026-08, 1회 판정 · 이미 본 구간)</h2><table>'
        "<tr><th>전략</th><th class='num'>연 수익률</th><th class='num'>최대 낙폭</th>"
        f"<th class='num'>샤프</th><th class='num'>최종 (시작 10,000)</th></tr>{bt}</table>"
        '<div class="foot">백테스트는 이미 본 과거 구간이다. 판단은 전향 관찰로 한다.</div></div>'
    )


def _positions(data: DashboardData) -> str:
    rows = "".join(
        f"<tr><td>{escape(lg.name)}</td><td>{escape(lg.status)}</td>"
        f"<td class='num'>{lg.exposure:.2f}배</td><td class='num'>{lg.trades_today}</td>"
        f"<td>{escape(lg.note)}</td></tr>"
        for lg in data.ledgers
    )
    return (
        f"{_positions_table(data)}"
        '<div class="panel"><h2>원장별 오늘</h2><table><tr><th>원장</th><th>상태</th>'
        f"<th class='num'>노출</th><th class='num'>체결</th><th>메모</th></tr>{rows}</table>"
        '<div class="foot">실거래 주문 경로는 없다. 체결은 모두 가상(Paper)이다.</div></div>'
    )


def _drill_details(d: DrillView) -> str:
    checks = "".join(
        f"<tr><td>{_dot('ok' if ok else 'bad')}{escape(name)}</td>"
        f"<td class='mute'>{escape(detail)}</td></tr>"
        for name, ok, detail in d.checks
    )
    obs = " · ".join(f"{escape(str(k))}={escape(str(v))}" for k, v in d.observations.items())
    kind = {"drill": "매일 훈련", "stop": "스톱 발동", "stream": "WS 스트림"}[d.kind]
    return (
        f"<details><summary>{_kst(d.at_ms)} · {kind} · {escape(d.outcome)} "
        f"{d.passed}/{len(d.checks)}</summary><table>{checks}</table>"
        f'<div class="foot">{obs}</div></details>'
    )


def _testnet(data: DashboardData) -> str:
    since = int(PHASE7_FROM.timestamp() * 1000)
    daily = {}
    for d in data.drills:
        if d.kind == "drill" and d.at_ms >= since:
            daily[_date(d.at_ms)] = d.ok
    cells = []
    for k in range(PHASE7_DAYS):
        day = PHASE7_FROM + timedelta(days=k)
        key = day.strftime("%Y-%m-%d")
        state = "ok" if daily.get(key) else ("bad" if key in daily else "mute")
        label = {"ok": "통과", "bad": "실패", "mute": "—"}[state]
        cells.append(f"<div>{day.strftime('%m-%d')}<br>{_dot(state)}{label}</div>")
    runs = (
        "".join(_drill_details(d) for d in reversed(data.drills)) or "<p class='mute'>기록 없음</p>"
    )
    return (
        '<div class="panel"><h2>Phase 7 매일 훈련 (2026-09-25 ~ 10-08, 14일)</h2>'
        f'<div class="cal">{"".join(cells)}</div>'
        '<div class="foot">기준(26 §2): 14일 모두 실행 · 실패는 전부 원인 설명 · '
        "코드 결함 실패 0 · 매일 평탄 마무리</div></div>"
        f"{_stream_checklist(data)}"
        f'<div class="panel"><h2>훈련 기록 (새것 먼저)</h2>{runs}</div>'
    )


def _watch_row(w: WatchView) -> str:
    level = {0: "ok", 1: "warn"}.get(w.exit_code or 0, "bad")
    text = {"ok": "이상 없음", "warn": "경고", "bad": "조치 필요"}[level]
    alerts = " · ".join(f"{lv} {lg}: {msg}" for lv, lg, msg in w.findings if lv != "INFO")
    return (
        f"<tr><td>{escape(w.date)}</td><td>{_dot(level)}{text}</td>"
        f"<td>{escape(alerts or '—')}</td></tr>"
    )


def _risk(data: DashboardData) -> str:
    rows = []
    for lg in data.ledgers:
        dd = lg.drawdown
        level = "bad" if dd >= _STOP_LINE else ("warn" if dd >= _WARN_LINE else "ok")
        rows.append(
            f"<tr><td>{escape(lg.name)}</td><td class='num'>{_dot(level)}{dd * 100:.1f}%</td>"
            f"<td class='num'>{_WARN_LINE:.0%} / {_STOP_LINE:.0%}</td>"
            f"<td class='num'>{lg.liquidations}</td><td>{'정지' if lg.halted else '정상'}</td>"
            f"<td class='num'>{lg.stop_hits if lg.key in {'o2', 'o3'} else '—'}</td></tr>"
        )
    history = "".join(_watch_row(w) for w in data.watch) or (
        "<tr><td colspan=3 class='mute'>점검 기록 없음</td></tr>"
    )
    return (
        '<div class="panel"><h2>원장별 위험 한도</h2><table><tr><th>원장</th>'
        "<th class='num'>고점 대비</th><th class='num'>경고 / 중단</th><th class='num'>청산</th>"
        f"<th>계좌 정지(−30%)</th><th class='num'>비상 스톱</th></tr>{''.join(rows)}</table>"
        '<div class="foot">중단선 25%(16 §3·25 §3·28 §3) · 경고 20% · 청산·정지는 즉시 중단 · '
        "일 손실 2% 날은 노출 증가 금지(O/v1·O/v2)</div></div>"
        '<div class="panel"><h2>일일 점검 기록 (텔레그램과 같은 내용)</h2><table><tr><th>날짜</th>'
        f"<th>결과</th><th>경고·조치</th></tr>{history}</table></div>"
    )


def _research(data: DashboardData) -> str:
    color = {"PASS": "ok", "REJECTED": "bad"}
    rows = (
        "".join(
            f"<tr><td>{escape(r.title)}</td>"
            f"<td>{_dot(color.get(r.verdict, 'warn'))}{escape(r.verdict)}</td>"
            f"<td class='mute nowrap'>{escape(r.date)}</td>"
            f"<td>{escape(r.about or r.summary)}"
            + (
                f"<details><summary>판정 근거</summary>"
                f"<div class='mute'>{escape(r.summary)}</div></details>"
                if r.about and r.summary
                else ""
            )
            + "</td></tr>"
            for r in reversed(data.research)
        )
        or "<tr><td colspan=4 class='mute'>기록 없음</td></tr>"
    )
    return (
        '<div class="panel"><h2>연구 판정 (RESEARCH_HISTORY)</h2>'
        "<table><tr><th>연구</th><th>판정</th>"
        f"<th>날짜</th><th>무엇을 연구했나 — 결과</th></tr>{rows}</table>"
        '<div class="foot">기각된 연구는 파라미터만 바꿔 다시 하지 않는다. '
        "자세한 내용은 docs/current/RESEARCH_HISTORY.md</div></div>"
    )


_RENDER = {
    "overview": _overview,
    "compare": _compare,
    "positions": _positions,
    "testnet": _testnet,
    "risk": _risk,
    "demo": lambda data: demo_page(data.demo, data.now_ms),
    "running": running_page,
    "overseas": accumulate_page,
    "lt_paper": lt_paper_page,
    "qs_paper": qs_paper_page,
    "research": _research,
}


def render_page(data: DashboardData, page: str) -> str:
    if page not in _RENDER:
        raise KeyError(page)
    return _layout(data, page, _RENDER[page](data))
