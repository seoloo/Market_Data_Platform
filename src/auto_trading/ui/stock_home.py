"""주식 중심 홈. 저장 파일만 읽고 실거래 성과를 추정하거나 합산하지 않는다."""

from __future__ import annotations

from datetime import UTC, datetime
from html import escape
from math import isfinite
from typing import Any

from auto_trading.ui.charts import Series, line_chart
from auto_trading.ui.data import DashboardData
from auto_trading.ui.qndx_render import _read

_BOOKS = {
    "LD_M3": ("Deneb 비교 · 변동성", "var(--c1)"),
    "LD_M2": ("Deneb 비교 · 브레이크", "var(--c2)"),
    "LD_M0": ("TQQQ·SCHD 기준", "var(--c8)"),
    "NBG": ("나스닥·채권·금", "var(--c3)"),
    "MIX": ("나스닥·추세 ETF", "var(--c4)"),
    "BASE": ("QNDX 기준", "var(--c6)"),
    "GBG": ("Deneb·채권·금", "var(--c5)"),
    "NBG_ISA": ("나스닥·채권·금 ISA (원화 ÷ 1,000)", "var(--c7)"),
}
"""선마다 다른 색(dashboard.css 의 --c1 ~ --c8)."""
_KRW_SCALE = {"NBG_ISA": 0.001}
"""원화 장부(100 만 원)를 USD 장부(1,000)와 같은 그래프에 놓을 때 나누는 값."""
_DAY_MS = 86_400_000


def _number(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return result if isfinite(result) else None


def _history(raw: Any, now_ms: int) -> tuple[tuple[int, float], ...] | None:
    """원장 순서·고유 날짜·양의 유한 평가액을 검사. 불량 행을 잇지 않는다."""
    if not isinstance(raw, list):
        return None
    points: list[tuple[int, float]] = []
    previous = -1
    for row in raw:
        if not isinstance(row, (list, tuple)) or len(row) != 2:
            return None
        try:
            day = datetime.strptime(str(row[0]), "%Y-%m-%d").replace(tzinfo=UTC)
        except ValueError:
            return None
        at_ms = int(day.timestamp() * 1000)
        value = _number(row[1])
        if at_ms > now_ms:
            continue
        if at_ms <= previous or value is None or value <= 0:
            return None
        previous = at_ms
        points.append((at_ms, value))
    return tuple(points)


def deneb_card(data: DashboardData) -> str:
    state = _read(data.root / "data/toss/DENEB.json") if data.root else None
    month = datetime.fromtimestamp(data.now_ms / 1000, UTC).strftime("%Y-%m")
    amounts = state.get("amounts") if state else None
    amount = _number(amounts.get(month)) if isinstance(amounts, dict) else None
    amount = amount if amount is not None and amount >= 0 else 300_000
    orders = state.get("orders") if state else None
    fills = (
        any(
            isinstance(order, dict) and (_number(order.get("filled_qty")) or 0) > 0
            for order in orders
        )
        if isinstance(orders, list)
        else False
    )
    status = "체결 기록 있음 · 평가 이력 별도" if fills else "첫 체결 확인 대기"
    if data.root and (data.root / "data/toss/DENEB.json").exists() and state is None:
        status = "장부 읽기 실패 · 체결 상태 미확인"
    return (
        '<section class="panel deneb-hero"><div class="deneb-main">'
        '<div><div class="eyebrow">STOCKS / 토스</div><h2>Deneb</h2>'
        '<div class="hero-status"><span>실거래 등록 설정</span>'
        f'<span class="pending">{status}</span></div>'
        '<div class="sub">이번 달 설정 납입금</div>'
        f'<strong class="deneb-amount">{amount:,.0f}<small> 원 / 월</small></strong>'
        '<p class="mute">변동성에 따라 비중 조절</p></div>'
        '<div class="deneb-assets"><div><span class="asset-glyph">T</span>'
        "<b>TQQQ</b><small>나스닥 3배 레버리지 ETF</small></div>"
        '<div><span class="asset-glyph">S</span><b>SCHD</b><small>미국 배당주 ETF</small>'
        '</div></div></div><div class="deneb-meta">'
        "<span>월 1회<small>첫 실행일</small></span>"
        "<span>달러 이월<small>남는 재원 보관</small></span>"
        "<span>배당 재투자<small>기존 등록 규칙</small></span></div>"
        '<div class="deneb-pending">브레이크 연결 상태는 실행 현황에서 확인'
        '<a href="/running">운용 기록 보기 →</a></div>'
        '<div class="foot">등록 설정 2026-10-09 기준 · 설정·주문을 변경하지 않습니다.</div>'
        "</section>"
    )


def stock_settings(data: DashboardData) -> str:
    """사용자 결정의 날짜를 명시한다. 설정은 체결/스케줄러 정상 증거가 아니다."""
    rows = (
        ("QNDX", "ETF · 기존 보유 유지", "적립 중단", "등록 설정"),
        ("BTQ", "개별주", "$1 / 일", "등록 설정"),
        ("WQTM", "ETF", "$1 / 일", "10/10부터 적용 예정"),
        ("QS", "퀀텀스케이프 · 개별주", "$1 / 일", "등록 설정"),
    )
    body = "".join(
        f'<tr><td><a href="/toss#{symbol.lower()}"><span class="ticker-tile">'
        f"{symbol[0]}</span><b>{symbol}</b><small>{description}</small></a></td>"
        f'<td>{setting}</td><td><span class="tag">{status}</span></td></tr>'
        for symbol, description, setting, status in rows
    )
    return (
        '<section class="panel stock-settings"><div class="row"><h2>자동 적립 현황</h2>'
        '<a class="text-link" href="/toss">저장 보고서 →</a></div>'
        '<div class="sub">2026-10-09 등록 설정 · 실행·체결 여부는 저장 보고서로 확인</div>'
        f"<table><tr><th>종목</th><th>설정</th><th>상태</th></tr>{body}</table>"
        '<div class="foot">QS 추세 전략은 Paper만 운영합니다. QS 개별주 적립과 다릅니다.</div>'
        "</section>"
    )


def paper_links() -> str:
    return (
        '<section class="panel stock-paper-links"><div class="row"><h2>주식 Paper 비교</h2>'
        '<span class="tag">가상 자금</span></div>'
        '<a href="#stock-performance"><b>Deneb 비교</b>'
        "<small>TQQQ · SCHD / M0 · M2 · M3</small></a>"
        '<a href="#stock-performance"><b>분산 포트폴리오</b><small>나스닥 · 채권 · 금</small></a>'
        '<a href="#stock-performance"><b>추세 분산</b><small>나스닥 · 추세 ETF</small></a>'
        '<div class="foot">실계좌와 별도 원장 · 자료 부족은 수익 0이 아닙니다.</div></section>'
    )


def _paper_series(data: DashboardData) -> tuple[list[Series], list[str]]:
    if data.root is None:
        return [], []
    path = data.root / "data/mf_paper/state.json"
    state = _read(path)
    if state is None:
        return [], ["Paper 장부 읽기 실패"] if path.exists() else []
    books = state.get("books")
    if not isinstance(books, dict):
        return [], ["Paper 장부 형식 미확인"]
    series: list[Series] = []
    errors: list[str] = []
    for key, (name, color) in _BOOKS.items():
        book = books.get(key)
        if book is None:
            continue
        krw = key in _KRW_SCALE and isinstance(book, dict) and book.get("ccy") == "KRW"
        if not isinstance(book, dict) or (book.get("ccy") != "USD" and not krw):
            errors.append(f"{key}: USD 장부 형식 미확인")
            continue
        points = _history(book.get("history"), data.now_ms)
        if points and krw:
            points = tuple((t, v * _KRW_SCALE[key]) for t, v in points)
        if points is None:
            errors.append(f"{key}: 평가 이력 오류")
        elif points:
            series.append(Series(name, color, points))
    return series, errors


def _transformed(series: Series, mode: str, since_ms: int) -> Series:
    points: list[tuple[int, float]] = []
    peak = 0.0
    base = series.points[0][1]
    for at_ms, value in series.points:
        peak = max(peak, value)
        shown = (
            value
            if mode == "equity"
            else ((value / peak - 1) * 100 if mode == "dd" else (value / base - 1) * 100)
        )
        if at_ms >= since_ms:
            points.append((at_ms, shown))
    return Series(series.name, series.color, tuple(points))


def stock_performance(data: DashboardData) -> str:
    series, errors = _paper_series(data)
    choices = (("return", "누적 수익률 (%)"), ("equity", "평가금액 (USD)"), ("dd", "낙폭 (%)"))
    ranges = (
        ("month", "1개월", 30),
        ("quarter", "3개월", 90),
        ("year", "1년", 365),
        ("all", "전체", 0),
    )
    inputs = '<input id="perf-live" type="radio" name="perf-source" checked>'
    inputs += '<input id="perf-paper" type="radio" name="perf-source">'
    inputs += "".join(
        f'<input id="perf-{mode}" type="radio" name="perf-metric"'
        f"{' checked' if mode == 'return' else ''}>"
        for mode, _ in choices
    )
    inputs += "".join(
        f'<input id="perf-{key}" type="radio" name="perf-range"'
        f"{' checked' if key == 'all' else ''}>"
        for key, _, _ in ranges
    )
    sources = '<label for="perf-live">실거래</label><label for="perf-paper">Paper</label>'
    metrics = "".join(f'<label for="perf-{key}">{label}</label>' for key, label in choices)
    periods = "".join(f'<label for="perf-{key}">{label}</label>' for key, label, _ in ranges)
    charts = "".join(
        f'<div class="perf-plot perf-{mode} perf-{key}">'
        + line_chart(
            [_transformed(s, mode, data.now_ms - days * _DAY_MS if days else 0) for s in series],
            height=280,
        )
        + "</div>"
        for mode, _ in choices
        for key, _, days in ranges
    )
    legend = "".join(
        f'<span><i style="background:{s.color}"></i>{escape(s.name)}</span>' for s in series
    )
    failure = '<div class="banner">' + escape(" · ".join(errors)) + "</div>" if errors else ""
    return (
        '<section class="panel performance" id="stock-performance">'
        + inputs
        + '<div class="row"><h2>수익률 추이</h2><span class="tag">저장된 실제 기록만</span></div>'
        + f'<div class="perf-controls"><div>{sources}</div><div>{periods}</div></div>'
        + '<div class="perf-live-content"><div class="chart-empty"><b>실거래 평가 기록 대기</b>'
        + "<p>Deneb 장부에는 입금·주문·보유 기록만 있습니다.<br>"
        + "일별 평가액·입출금 시점·환율 이력이 없어 수익률을 추정하지 않습니다.</p></div>"
        + '<div class="foot">납입금 증가를 수익으로 표시하지 않습니다. '
        + "실거래·Paper·코인 자산을 합산하지 않습니다.</div></div>"
        + f'<div class="perf-paper-content"><div class="perf-metrics">{metrics}</div>{failure}'
        + f'<div class="perf-plots">{charts}</div><div class="perf-legend">{legend}</div>'
        + '<div class="foot">USD Paper · 첫 저장 평가액 기준 누적 수익률 · '
        + "기간 선택은 표시 범위만 변경 · "
        + "낙폭은 전체 기록의 고점 기준 · 원장에 반영된 비용 포함(첫 평가 이전 비용 제외) · "
        + "각 원장은 독립, 합산하지 않음 · 원화 장부 제외<br>"
        + "QNDX 비교 기준은 Paper BASE 원장입니다. QQQ 가격으로 바꾸거나 환율을 추정하지 않습니다. "
        + "초기 기록은 통계적으로 의미가 작습니다.</div></div></section>"
    )
