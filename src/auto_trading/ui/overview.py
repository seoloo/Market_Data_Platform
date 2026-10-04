"""운영 홈의 요약 조각. 저장 보고서만 읽으며 계좌·주문을 호출하지 않는다."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from html import escape
from math import isfinite

from auto_trading.ui.data import DashboardData
from auto_trading.ui.qndx_render import _read
from auto_trading.ui.running import TRACKS, load_running

_KST = timezone(timedelta(hours=9))
_SYMBOLS = ("QNDX", "BTQ", "WQTM", "QS")
_STATUS = {
    "BUY": "1주 살 차례",
    "WAIT": "적립 중",
    "CAP": "이번 달 한도",
    "PAUSE": "QS 추세 꺼짐",
    "FAILED": "조회 실패",
}


def _stock_cards(data: DashboardData) -> tuple[str, int]:
    cards: list[str] = []
    recorded = 0
    today = datetime.fromtimestamp(data.now_ms / 1000, _KST).strftime("%Y-%m-%d")
    for symbol in _SYMBOLS:
        paths = (
            sorted(
                p
                for p in (data.root / "reports" / "toss" / symbol).glob("20*.json")
                if p.stem <= today
            )
            if data.root
            else []
        )
        report = _read(paths[-1]) if paths else None
        label = "기록 읽기 실패" if paths else "첫 실행 대기"
        progress = '<div class="foot">진행률은 첫 저장 보고서 이후 표시됩니다.</div>'
        if report:
            recorded += 1
            label = _STATUS.get(str(report.get("status")), "상태 미확인")
            advice = report.get("advice")
            if isinstance(advice, dict):
                try:
                    accrued = float(advice.get("accrued", -1))
                    price = float(advice.get("price", 0))
                except (TypeError, ValueError, OverflowError):
                    accrued, price = -1.0, 0.0
                if isfinite(accrued) and isfinite(price) and accrued >= 0 and price > 0:
                    pct = min(100.0, accrued / price * 100)
                    progress = (
                        '<div class="progress" role="progressbar" '
                        f'aria-label="{symbol} 다음 1주 적립" '
                        f'aria-valuemin="0" aria-valuemax="100" aria-valuenow="{pct:.1f}">'
                        f'<span style="width:{pct:.1f}%"></span></div>'
                        f'<div class="foot">다음 1주 적립 {pct:.0f}% · '
                        f"{escape(paths[-1].stem)}</div>"
                    )
        cards.append(
            f'<a class="stock-mini" href="/toss#{symbol.lower()}"><div class="row">'
            f'<b>{symbol}</b><span class="tag">적립</span></div>'
            f"<p>{escape(label)}</p>{progress}</a>"
        )
    return "".join(cards), recorded


def _upcoming(data: DashboardData) -> list[tuple[datetime, str]]:
    """문서화된 예정 시각. 스케줄러 등록/실행 상태를 보증하지 않는다."""
    now = datetime.fromtimestamp(data.now_ms / 1000, _KST)
    upcoming: list[tuple[datetime, str]] = []
    for track in TRACKS:
        clock = track.schedule[:5]
        if len(clock) != 5 or clock[2] != ":" or not clock.replace(":", "").isdigit():
            continue
        hour, minute = map(int, clock.split(":"))
        at = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if at <= now:
            at += timedelta(days=1)
        first = datetime.strptime(track.first, "%Y-%m-%d").replace(tzinfo=_KST)
        at = max(at, first.replace(hour=hour, minute=minute))
        while track.weekdays_only and at.weekday() >= 5:
            at += timedelta(days=1)
        if track.last and at.strftime("%Y-%m-%d") > track.last:
            continue
        upcoming.append((at, track.name))
    return sorted(upcoming, key=lambda item: item[0])


def overview_summary(data: DashboardData) -> str:
    _, recorded = _stock_cards(data)
    upcoming = _upcoming(data)
    next_clock = upcoming[0][0].strftime("%m/%d %H:%M") if upcoming else "예정 없음"
    last = data.watch[0] if data.watch else None
    watch = f"마지막 점검 {last.date}" if last else "점검 기록 없음"
    count = len(data.ledgers)
    names = " · ".join(escape(lg.name) for lg in data.ledgers) or "아직 원장 없음"
    return (
        '<div class="summary-cards">'
        f'<a class="card summary" href="/compare"><div class="eyebrow">코인 / PAPER</div>'
        f'<div class="big">{count}개 원장<span class="tag">가상 체결</span></div>'
        f'<div class="sub">{names}</div></a>'
        '<a class="card summary" href="/toss"><div class="eyebrow">해외 주식 / 적립</div>'
        '<div class="big">토스증권<span class="tag">저장 기록</span></div>'
        f'<div class="sub">3개 적립 종목 · {recorded}/3 보고서</div></a>'
        '<a class="card summary" href="/running"><div class="eyebrow">시스템 / 다음 예정</div>'
        f'<div class="big">{next_clock}<span class="tag">KST</span></div>'
        f'<div class="sub">{escape(watch)} · 실행 보증 아님</div></a></div>'
    )


def overview_stocks(data: DashboardData) -> str:
    cards, _ = _stock_cards(data)
    return (
        '<section class="panel"><div class="row"><h2>해외 주식 적립</h2>'
        '<a class="text-link" href="/toss">전체 보기 →</a></div>'
        f'<div class="stock-minis">{cards}</div>'
        '<div class="quick-links"><a href="/toss#qs">QS 추세 전환 →</a>'
        '<a href="/toss#fx">환전 안내 →</a></div>'
        '<div class="foot">마지막 실행 당시 기록 · 평가 손익은 표시하지 않습니다.</div></section>'
    )


def overview_attention(data: DashboardData) -> str:
    items: list[str] = []
    if data.demo.halt:
        items.append(f'<a href="/demo">긴급 정지<span>{escape(data.demo.halt)}</span></a>')
    if data.errors:
        items.append(f'<a href="/risk">자료 읽기 실패<span>{len(data.errors)}개 항목</span></a>')
    if data.root:
        rows, _ = load_running(data.root, data)
        for row in rows:
            if row.level in {"warn", "bad"}:
                items.append(
                    f'<a href="/running">{escape(row.track.name)}'
                    f"<span>{escape(row.status)}</span></a>"
                )
    if not items:
        items.append(
            '<a href="/running">실행 기록 확인<span>경고 미검출 · 실시간 상태 아님</span></a>'
        )
    upcoming = _upcoming(data)
    timeline = (
        "".join(
            f"<li><time>{at.strftime('%m/%d %H:%M')}</time><b>{escape(name)}</b></li>"
            for at, name in upcoming[:4]
        )
        or '<li class="mute">예정 항목 없음</li>'
    )
    return (
        '<section class="panel"><div class="row"><h2>확인할 일</h2>'
        f'<span class="tag">{len(items)}개</span></div>'
        f'<div class="attention">{"".join(items[:5])}</div>'
        '<a class="text-link" href="/running">실행 현황 전체 보기 →</a></section>'
        '<section class="panel"><h2>다가오는 일정</h2>'
        f'<ol class="timeline">{timeline}</ol>'
        '<div class="foot">KST · 설정된 예정 시각이며 실제 실행은 기록으로 확인합니다.</div>'
        '<a class="text-link" href="/running">일정 · 점검 보기 →</a></section>'
    )
