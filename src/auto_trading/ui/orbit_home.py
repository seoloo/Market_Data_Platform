"""사용자 승인 오르빗 실거래 화면. 장부·공개 시세를 표시만 한다."""

from __future__ import annotations

from html import escape
from pathlib import Path

from auto_trading.ui.live_home import Book, Quotes, _priced, _won
from auto_trading.ui.orbit_icons import icon, symbol_icon

_CSS = Path(__file__).with_name("orbit.css").read_text(encoding="utf-8")
_NAMES = {
    "BTC": "비트코인",
    "ETH": "이더리움",
    "SOL": "솔라나",
    "TQQQ": "ProShares UltraPro QQQ",
    "SCHD": "Schwab US Dividend Equity ETF",
    "BTQ": "BTQ",
    "WQTM": "WQTM",
    "QS": "QuantumScape",
}


def _popup(target: str, title: str, content: str) -> str:
    return (
        f'<section popover="auto" class="orbit-popover" id="{target}" role="dialog" '
        f'aria-labelledby="{target}-title"><header><h2 id="{target}-title">{title}</h2>'
        f'<button type="button" class="orbit-close" popovertarget="{target}" '
        f'popovertargetaction="hide" aria-label="{title} 닫기" autofocus>{icon("x")}</button>'
        f'</header><div class="orbit-popover-body">{content}</div></section>'
    )


def _guide() -> str:
    return (
        '<button type="button" class="orbit-guide" popovertarget="orbit-guide" '
        'aria-label="화면 읽는 법">'
        f"{icon('help')}<span>화면 읽는 법</span></button>"
        + _popup(
            "orbit-guide",
            "화면 읽는 법",
            "<p>내 자산은 실거래 장부의 현금과 보유 평가액입니다. "
            "기준 금액은 총 입금 + 적립 매입원가입니다. "
            "평가 차액은 세금 전이며 확정 수익이 아닙니다.</p>"
            "<p>시세가 없으면 원가로 셈 합니다. 환율 미확인 시에도 추정 평가액입니다. "
            "주문 접수 ≠ 체결 완료. 장부만으로 프로그램의 현재 가동 여부를 확정하지 않습니다.</p>"
            "<p>Glide는 미국 주식, Cruise는 BTC·ETH·SOL, 자동 적립은 주식 모으기입니다. "
            "이 화면은 읽기 전용입니다. 앱을 닫아도 매매 프로그램은 종료되지 않습니다.</p>"
            "<p>자산 그래프는 검증된 실거래 평가 이력만 표시합니다. 입금은 수익이 아닙니다.</p>",
        )
    )


def layout(body: str, theme: str) -> str:
    themes = "".join(
        f'<a href="/live?theme={key}" aria-label="{label} 모드" '
        f'aria-current="{"true" if theme == key else "false"}">{label}</a>'
        for key, label in (("light", "라이트"), ("dark", "다크"))
    )
    return (
        f'<!doctype html><html lang="ko" data-theme="{theme}" data-page="live">'
        '<head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        '<meta http-equiv="refresh" content="60"><title>오르빗 · 내 자산</title>'
        '<link rel="icon" type="image/png" href="/assets/obi.png?v=3">'
        '<link rel="icon" type="image/x-icon" href="/favicon.ico?v=3">'
        f'<style>{_CSS}</style></head><body><div class="topbar">'
        '<a class="logo" href="/live"><img src="/assets/orbit-obi.png" '
        'alt="오비, 네온 궤도 속 우주 고양이" width="44" height="44"><b>오르빗</b></a>'
        '<nav class="orbit-nav" aria-label="주 메뉴">'
        '<a class="on" href="/live" aria-current="page" aria-label="홈">'
        f"{icon('home')}<span>홈</span></a>"
        f'<a href="#orbit-assets" aria-label="내 자산">{icon("pie")}<span>내 자산</span></a>'
        '<button type="button" class="orbit-accounts" popovertarget="orbit-accounts" '
        'aria-label="연결 계좌">'
        f"{icon('wallet')}<span>연결 계좌</span></button>"
        + _popup(
            "orbit-accounts",
            "연결 계좌",
            "<p>Glide · 자동 적립: 토스<br>Cruise: 업비트</p>"
            "<p>저장된 장부 기준입니다. 실제 API 연결·잔고를 새로 조회하지 않습니다. "
            "계좌번호·API 키는 표시하지 않습니다.</p>",
        )
        + '<button type="button" class="orbit-settings" popovertarget="orbit-settings" '
        'aria-label="설정">'
        f"{icon('gear')}<span>설정</span></button>"
        + _popup(
            "orbit-settings",
            "화면 설정",
            f'<div class="orbit-themes">{themes}</div>'
            "<p>표시 테마만 바꿉니다. 매매 설정은 변경하지 않습니다.</p>",
        )
        + f'{_guide()}</nav><div class="orbit-tools">'
        '<button type="button" popovertarget="orbit-status" aria-label="확인할 사항">'
        f"{icon('bell')}</button>"
        f'<a href="/live?theme={"dark" if theme == "light" else "light"}" '
        f'aria-label="{"다크" if theme == "light" else "라이트"} 모드로 전환" '
        f'title="{"다크" if theme == "light" else "라이트"} 모드로 전환">'
        f"{icon('moon' if theme == 'light' else 'sun')}</a>"
        f'</div></div><main class="orbit-main">{body}</main></body></html>'
    )


def _card(book: Book, quotes: Quotes, index: int, *, recorded: bool = True) -> str:
    key = book.key if book.key in {"glide", "cruise", "accumulate"} else "other"
    category, subtitle, description, glyph = {
        "glide": ("미국 주식", "Glide", "미국 ETF에 자동으로", "chart"),
        "cruise": ("코인", "Cruise", "주요 코인에 자동으로", "coins"),
        "accumulate": ("자동 적립", "자동 적립", "자동으로 차곡차곡", "leaf"),
    }.get(key, ("투자 장부", book.title, "저장된 기록", "wallet"))
    symbols = {"glide": ("TQQQ", "SCHD"), "cruise": ("BTC", "ETH", "SOL")}.get(
        key, tuple(h.name for h in book.holdings)
    )
    pills = "".join(f"<span>{escape(s)}</span>" for s in symbols)
    change = (
        _change(book.value_krw, book.deposits_krw)
        if _priced(book, quotes)
        else ('<span class="mute">시세 확인 후 표시</span>')
    )
    if not recorded:
        change = '<span class="mute">장부 기록 대기</span>'
    amount = _won(book.value_krw) + "<span>원</span>" if recorded else "—"
    rows = []
    for holding in book.holdings:
        symbol = escape(holding.name)
        coin_class = holding.name.lower() if holding.name in _NAMES else "other"
        company = escape(_NAMES.get(holding.name, holding.name))
        value = holding.value_krw if holding.value_krw is not None else holding.cost_krw
        row_pl = (
            _change(holding.value_krw, holding.cost_krw)
            if holding.value_krw is not None
            else ('<span class="mute">시세 미확인</span>')
        )
        rows.append(
            '<li><div class="orbit-holding-name">'
            f'<span class="orbit-coin coin-{coin_class}">{symbol_icon(holding.name)}</span>'
            f"<div><b>{symbol}</b><small>{company}</small></div>"
            '</div><div class="orbit-holding-value">'
            f"<b>{_won(value)}원</b>"
            f"<small>{row_pl}</small></div></li>"
        )
    notes = "".join(f"<li>{escape(n)}</li>" for n in (*book.notes, *book.pending))
    return (
        f'<section id="live-book-{index}" class="orbit-card orbit-{key}">'
        '<div class="orbit-card-heading">'
        f'<span class="orbit-category-icon">{icon(glyph)}</span>'
        f"<div><h2>{category}</h2><span>{escape(subtitle)}</span></div>"
        f'<div class="orbit-card-pills"><small>{description}</small><div>{pills}</div></div></div>'
        '<div class="orbit-card-summary"><div>'
        f"<strong>{amount}</strong><p>{change}</p></div>"
        '<div class="orbit-spark-empty" aria-label="평가 기록 대기">평가 기록 대기</div></div>'
        f'<div class="orbit-row-label"><span>보유 종목 ({len(book.holdings)}개)</span>'
        '<span>평가금액</span></div><ul class="orbit-holdings">'
        f"{''.join(rows) if rows else '<li>아직 보유 기록이 없습니다.</li>'}</ul>"
        '<details class="orbit-book-detail"><summary aria-label="보유 수량과 장부 메모">'
        '<span class="orbit-detail-open">보유 상세 ›</span>'
        '<span class="orbit-detail-close">보유 상세 닫기 ×</span></summary><div>'
        + "".join(
            f"<p>{escape(h.name)} · {h.qty:,.8g} · {escape(h.note)}</p>" for h in book.holdings
        )
        + f"<p>현금 {_won(book.cash_krw)}원</p>"
        + f"<ul>{notes}</ul><p>장부 기준 · 세금 전 · "
        f"{'평가액' if _priced(book, quotes) else '추정 평가액'}</p></div></details></section>"
    )


def _change(value: float, base: float) -> str:
    diff = value - base
    cls = "pos" if diff > 0 else "neg" if diff < 0 else "mute"
    pct = f"<small>{diff / base:+.2%}</small>" if base > 0 else ""
    return f'<span class="{cls}"><b>{diff:+,.0f}원</b>{pct}</span>'


def _activity_rows(activities: list[tuple[str, str, str]]) -> str:
    out = []
    for date, text, _key in activities:
        sym = next((s for s in _NAMES if s in text), "")
        cls = sym.lower() if sym in _NAMES else "other"
        out.append(
            f'<li><span class="orbit-coin coin-{cls}">{symbol_icon(sym)}</span>'
            f"<div><b>{escape(text)}</b><small>{escape(date)}</small></div>"
            '<span class="orbit-trade-state">'
            f"{'입금 기록' if '입금' in text else '저장 체결'}</span></li>"
        )
    return "".join(out)


def render_live(books: list[Book], quotes: Quotes, now_ms: int) -> str:
    """승인한 화면의 요소만 표시. 예시 수익/이력/연결 상태를 만들어 넣지 않는다."""
    complete = bool(books) and all(_priced(b, quotes) for b in books)
    value = sum(b.value_krw for b in books)
    base = sum(b.deposits_krw for b in books)
    pending_count = sum(len(b.pending) for b in books)
    warnings = list(quotes.errors)
    if books and not complete:
        warnings.append("일부 시세·환율 미확인: 원가로 셈 하는 추정치 포함")
    warnings.extend(
        f"{b.title} · 매수 정지 기록을 확인하세요"
        for b in books
        if any("낙폭 정지 중" in n for n in b.notes)
    )
    warnings.extend(f"{b.title} · {p}" for b in books for p in b.pending)
    title = (
        f"체결 확인 대기 {pending_count}건"
        if pending_count
        else ("표시 기록 확인 필요" if warnings else "표시된 체결 대기 기록 없음")
    )
    detail = (
        " · ".join(warnings)
        if warnings
        else ("저장된 장부 기준입니다. 현재 계좌 연결이나 실제 가동 상태를 보장하지 않습니다.")
    )
    if not books:
        amount = "—"
        base_text = "—"
        change = '<span class="mute">기록 대기</span>'
        detail = (
            "아직 실거래 장부가 없습니다. 기록 없음은 잔고 0원이나 운영 중단을 뜻하지 않습니다."
        )
    else:
        amount = _won(value) + "<small>원</small>"
        base_text = _won(base) + "원"
        change = _change(value, base) if complete else '<span class="mute">시세 확인 필요</span>'
    caution = "확정 수익 아님" if complete else "추정치 포함 · 확정 수익 아님"
    hero = (
        '<section class="orbit-hero">'
        '<div class="orbit-hero-art" aria-hidden="true">'
        '<img class="orbit-hero-cat" src="/assets/orbit-hero-light.png" alt=""></div>'
        '<div class="orbit-greeting"><span>안녕하세요,</span>'
        '<h1>오늘도 오비와 함께 <span aria-hidden="true">🪐</span></h1>'
        "<p>작은 투자가 만드는 더 나은 오늘, 자동으로 차곡차곡</p></div>"
        '<section class="orbit-networth" id="orbit-assets" aria-labelledby="live-total-title">'
        f'<div><h2 id="live-total-title">내 자산 {icon("eye")}</h2>'
        f'<strong class="orbit-total">{amount}</strong></div>'
        f"<div><span>기준 금액</span><b>{base_text}</b></div>"
        f"<div><span>평가 차액</span><strong>{change}</strong>"
        f'<small class="orbit-caution">ⓘ {caution}'
        "</small></div></section>"
        '<button type="button" class="orbit-help-card" popovertarget="orbit-guide">'
        f"<b>{icon('help')} 화면 읽는 법 {icon('arrow')}</b>"
        "<span>이 화면에서<br>무엇을 볼 수 있나요?</span></button></section>"
    )
    alert = (
        (
            '<details class="orbit-alert" id="orbit-attention"><summary>'
            '<span class="orbit-alert-mark">!</span><b>먼저 확인해 주세요 · '
            f'<span>{title}</span></b><span class="orbit-alert-copy">'
            f'{escape(detail)}</span><span class="orbit-chevron">›</span></summary>'
            f"<div>{escape(detail)}<p>주문 접수 ≠ 체결 완료. "
            "프로그램의 현재 가동 여부를 확정하지 않습니다.</p></div></details>"
        )
        if pending_count
        else ""
    )
    status = _popup(
        "orbit-status",
        "확인할 사항",
        f"<p>{escape(title)}</p><p>{escape(detail)}</p>"
        "<p>주문 접수 ≠ 체결 완료. 프로그램의 현재 가동 여부를 확정하지 않습니다.</p>",
    )
    # 체결 대기 줄만 숨긴다. 시세/정지/장부 결측 경고는 계속 읽을 수 있어야 한다.
    if not pending_count and (warnings or not books):
        status += f'<p class="orbit-data-note">{escape(detail)}</p>'
    by_key = {b.key: b for b in books}
    cards = "".join(
        _card(by_key.get(k, Book(k, k, "", 0, 0, ())), quotes, i, recorded=k in by_key)
        for i, k in enumerate(("glide", "cruise", "accumulate"))
    )
    charts = (
        '<section class="orbit-panel orbit-history"><div class="orbit-panel-heading">'
        f'<h2>{icon("chart")}자산 변화</h2><div class="orbit-periods" role="group" '
        'aria-label="자산 변화 표시 기간">'
        + "".join(
            f'<label><input type="radio" name="orbit-range" value="{p}" '
            f"{'checked' if p == '1개월' else ''}><span>{p}</span></label>"
            for p in ("1주", "1개월", "3개월", "6개월", "1년")
        )
        + '</div></div><div class="orbit-chart-empty"><span>평가 기록 대기</span>'
        "<p>전체 실거래의 일별 평가 이력이 없어 아직 그래프를 표시할 수 없습니다.</p>"
        '</div><p class="orbit-chart-note">총 입금 + 적립 매입원가 기준 · '
        "실거래 장부만 표시 · 공개 시세 미확인 시 추정 평가액</p></section>"
    )
    activities = sorted(
        ((date, text, b.key) for b in books for date, text in b.activity),
        key=lambda a: a[0],
        reverse=True,
    )
    activity_rows = _activity_rows(activities[:5])
    recent = (
        '<section class="orbit-panel orbit-recent" id="orbit-recent">'
        '<div class="orbit-panel-heading">'
        f"<h2>{icon('log')}최근 거래 <small>(최근 5건)</small></h2>"
        '<button type="button" class="orbit-all-trades" popovertarget="orbit-all-trades">'
        "전체 보기 ›</button>"
        + _popup(
            "orbit-all-trades",
            "전체 거래 기록",
            '<ul class="orbit-trades-list">'
            f"{_activity_rows(activities) or '<li>아직 거래 기록이 없습니다.</li>'}"
            "</ul>",
        )
        + "</div>"
        f"<ul>{activity_rows or '<li>아직 거래·입금 기록이 없습니다.</li>'}</ul></section>"
    )
    return (
        hero
        + alert
        + status
        + f'<div class="orbit-cards">{cards}</div>'
        + (f'<div class="orbit-bottom">{charts}{recent}</div>')
    )
