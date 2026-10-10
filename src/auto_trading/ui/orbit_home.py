"""사용자 승인 오르빗 실거래 화면. 장부·공개 시세를 표시만 한다.

2026-10-10 보완 (Codex 제안 · 사용자 "일단 작업해봐"):
금액 숨기기 · 장부/시세 갱신 시각과 '추정' 표시 · 부분 새로고침(`data-live` 칸만) · 하루 평가
기록 그래프 · 종목 상세 · 거래 내역 필터 · 연결 계좌 상태 · 처음 쓰는 사람 안내.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import UTC, date, datetime, timedelta
from html import escape
from pathlib import Path
from typing import Any

from auto_trading.ui.live_home import Book, Quotes, Trade, _priced
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
_KST = timedelta(hours=9)
_HIDDEN = "•••••"
STALE_HOURS = {"cruise": 30, "accumulate": 30, "glide": 24 * 35}
"""장부가 이 시간보다 오래되면 '오래된 기록' (Cruise · 적립은 매일, Glide 는 매달 돈다)."""
PERIODS = (
    ("w1", "1주", 7),
    ("m1", "1개월", 31),
    ("m3", "3개월", 92),
    ("m6", "6개월", 183),
    ("y1", "1년", 366),
)
_KIND_WORD = {"buy": "매수", "sell": "매도", "deposit": "입금"}


# 오비 움직임 (사용자 2026-10-10 "오비 텔레그램 스티커처럼 움직이게" → 2번: 둥실 대신 눈 깜빡임 등).
# 오비는 배경 그림 안에 그려져 있어 떼어 움직일 수 없다 → 같은 크기(1536 × 1024)의 투명 SVG 층을
# 그림 위에 겹쳐 눈꺼풀 · 반짝이 · 궤도 빛 · 잠꼬대(다크)만 움직인다. 위치는 원본 그림에서 잰 값.
# 움직임 줄이기를 켠 PC 에서는 멈춘다 (orbit.css @media prefers-reduced-motion).
_EYES = ((978, 442, 38, 44), (1108, 450, 41, 42))
"""(중심 x, 중심 y, 가로 반지름, 세로 반지름) — 청록 눈 테두리까지 덮는 크기."""
_TWINKLES = (
    (868, 300, 0.0),
    (1292, 252, 1.2),
    (1255, 540, 2.0),
    (1004, 618, 0.6),
    (1405, 468, 2.7),
    (822, 470, 1.6),
    (640, 182, 3.1),
)
"""(x, y, 시작 지연 초) — 오비 · 고리 둘레의 하늘 쪽 (배경과 대비가 나는 곳)."""
_SLEEPY = ((978, 452), (1111, 462))
"""다크(잠자는 오비) 그림의 감은 눈 중심 — 밝은 그림보다 조금 아래."""
_FACE_DARK = "#071a52"


def _star(x: int, y: int, r: int) -> str:
    return (
        f"M{x} {y - r}Q{x} {y} {x + r} {y}Q{x} {y} {x} {y + r}"
        f"Q{x} {y} {x - r} {y}Q{x} {y} {x} {y - r}Z"
    )


def _sleepy_eye(x: int, y: int, i: int) -> str:
    """반쯤 뜬 졸린 눈: 감은 선을 얼굴색으로 덮고, 위 절반을 눈꺼풀로 가린 청록 눈."""
    return (
        f'<ellipse cx="{x}" cy="{y}" rx="44" ry="26" fill="{_FACE_DARK}"/>'
        f'<clipPath id="obi-eye{i}"><ellipse cx="{x}" cy="{y}" rx="33" ry="30"/></clipPath>'
        f'<g clip-path="url(#obi-eye{i})">'
        f'<ellipse cx="{x}" cy="{y}" rx="33" ry="30" fill="#36dbe8"/>'
        f'<ellipse cx="{x}" cy="{y + 5}" rx="23" ry="23" fill="#08133a"/>'
        f'<circle cx="{x + 8}" cy="{y + 1}" r="6" fill="#ffffff"/>'
        f'<rect x="{x - 40}" y="{y - 40}" width="80" height="38" fill="{_FACE_DARK}"/></g>'
        f'<path d="M{x - 34} {y - 1}Q{x} {y - 9} {x + 34} {y - 1}" fill="none" '
        'stroke="#3fe6f0" stroke-width="7" stroke-linecap="round"/>'
    )


HERO_FX = (
    '<svg class="orbit-hero-fx" viewBox="0 0 1536 1024" aria-hidden="true" focusable="false">'
    '<defs><filter id="obi-glow" x="-20%" y="-50%" width="140%" height="200%">'
    '<feGaussianBlur stdDeviation="14"/></filter>'
    '<filter id="obi-spark" x="-100%" y="-100%" width="300%" height="300%">'
    '<feGaussianBlur stdDeviation="5" result="b"/><feMerge><feMergeNode in="b"/>'
    '<feMergeNode in="SourceGraphic"/></feMerge></filter>'
    # 고리 앞쪽(아래 절반)만 — 뒤쪽 빛이 오비 얼굴 위로 지나가지 않게
    '<clipPath id="obi-front"><rect x="700" y="548" width="800" height="200" '
    'transform="rotate(-11 1075 556)"/></clipPath></defs>'
    '<g class="fx-glow" clip-path="url(#obi-front)">'
    '<ellipse class="fx-ring" cx="1075" cy="556" rx="300" ry="78" '
    'transform="rotate(-11 1075 556)" filter="url(#obi-glow)"/>'
    '<ellipse class="fx-flow" cx="1075" cy="556" rx="300" ry="78" '
    'transform="rotate(-11 1075 556)" filter="url(#obi-spark)"/></g>'
    + "".join(
        f'<path class="fx-twinkle" style="animation-delay:{d}s" d="{_star(x, y, 26)}" '
        'filter="url(#obi-spark)"/>'
        for x, y, d in _TWINKLES
    )
    + '<g class="fx-awake">'
    + "".join(
        f'<ellipse class="fx-lid" cx="{x}" cy="{y}" rx="{rx}" ry="{ry}"/>'
        f'<path class="fx-shut" d="M{x - rx + 8} {y + 4}'
        f'Q{x} {y + ry // 2 + 6} {x + rx - 8} {y + 4}"/>'
        for x, y, rx, ry in _EYES
    )
    + "</g>"
    '<g class="fx-sleep"><g class="fx-zzz">'
    '<text class="fx-z" x="1248" y="420">z</text>'
    '<text class="fx-z" x="1282" y="388" style="animation-delay:1.1s">z</text>'
    '<text class="fx-z" x="1320" y="350" style="animation-delay:2.2s">Z</text></g>'
    '<g class="fx-sleepy">'
    + "".join(_sleepy_eye(x, y, i) for i, (x, y) in enumerate(_SLEEPY))
    + "</g></g></svg>"
)


def _won(x: float, hide: bool = False) -> str:
    return _HIDDEN if hide else f"{x:,.0f}"


def _kst(ms: int) -> str:
    return (datetime.fromtimestamp(ms / 1000, UTC) + _KST).strftime("%m/%d %H:%M")


def _stale(book: Book, now_ms: int) -> bool:
    if not book.updated_ms:
        return False
    return now_ms - book.updated_ms > STALE_HOURS.get(book.key, 30) * 3_600_000


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
            "<p>시세가 없으면 원가로 셈 하고 <em class='orbit-est'>추정</em>을 붙입니다. "
            "주문 접수 ≠ 체결 완료. 장부만으로 프로그램의 현재 가동 여부를 확정하지 않습니다.</p>"
            "<p>‘장부 업데이트’는 매매 프로그램이 장부를 마지막으로 저장한 시각, "
            "‘시세 업데이트’는 이 화면이 공개 시세를 받은 시각입니다. "
            "화면이 열려 있다고 매매가 정상이라는 뜻은 아닙니다.</p>"
            "<p>Glide는 미국 주식, Cruise는 BTC·ETH·SOL, 자동 적립은 주식 모으기입니다. "
            "이 화면은 읽기 전용입니다. 앱을 닫아도 매매 프로그램은 종료되지 않습니다.</p>"
            "<p>자산 그래프는 하루 한 번 남긴 실거래 평가 기록만 그립니다. "
            "기록이 없는 과거는 채우지 않습니다. 입금은 수익이 아닙니다.</p>",
        )
    )


def layout(body: str, theme: str, active: str = "live") -> str:
    """``active``: 'live'(실거래 홈) · 'settings'(설정, 2026-10-10 — 스크립트 없음)."""
    # 앱의 선택 테마를 따른다. OS 테마나 CSS 필터로 낮 그림을 어둡게 만들지 않는다.
    if theme == "dark":
        body = body.replace(
            '<img class="orbit-hero-cat" src="/assets/orbit-hero-light.png"',
            '<img class="orbit-hero-cat" src="/assets/orbit-hero-dark-sleep.png"',
            1,
        )
    # 테마 고르기는 설정 화면(/settings)과 위쪽 해 · 달 버튼으로 옮겼다 (2026-10-10).
    return (
        f'<!doctype html><html lang="ko" data-theme="{theme}" data-page="{active}">'
        '<head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        # 스크립트가 꺼진 브라우저만 60 초마다 새로 고친다 (설정 화면은 입력이 지워져 뺌)
        + (
            '<noscript><meta http-equiv="refresh" content="60"></noscript>'
            if active == "live"
            else ""
        )
        + f"<title>오르빗 · {'설정' if active == 'settings' else '내 자산'}</title>"
        '<link rel="icon" type="image/png" href="/assets/obi.png?v=3">'
        '<link rel="icon" type="image/x-icon" href="/favicon.ico?v=3">'
        # 휴대폰 '홈 화면에 추가' (2026-10-11)
        '<link rel="manifest" href="/manifest.webmanifest">'
        '<link rel="apple-touch-icon" href="/assets/apple-touch-icon.png">'
        '<meta name="apple-mobile-web-app-capable" content="yes">'
        '<meta name="mobile-web-app-capable" content="yes">'
        '<meta name="apple-mobile-web-app-title" content="오르빗">'
        '<meta name="theme-color" content="#0c1630">'
        f"<style>{_CSS}</style>"
        + ('<script src="/assets/orbit-live.js" defer></script>' if active == "live" else "")
        + '</head><body><div class="topbar">'
        '<a class="logo" href="/live"><img src="/assets/orbit-obi.png" '
        'alt="오비, 네온 궤도 속 우주 고양이" width="44" height="44"><b>오르빗</b></a>'
        '<nav class="orbit-nav" aria-label="주 메뉴">'
        + (
            '<a class="on" href="/live" aria-current="page" aria-label="홈">'
            if active == "live"
            else '<a href="/live" aria-label="홈">'
        )
        + f"{icon('home')}<span>홈</span></a>"
        f'<a href="/live#orbit-assets" aria-label="내 자산">{icon("pie")}<span>내 자산</span></a>'
        + (
            '<button type="button" class="orbit-accounts" popovertarget="orbit-accounts" '
            f'aria-label="연결 계좌">{icon("wallet")}<span>연결 계좌</span></button>'
            if active == "live"
            else ""
        )
        + f'<a class="orbit-settings{" on" if active == "settings" else ""}" href="/settings" '
        f'aria-label="설정"{" aria-current=page" if active == "settings" else ""}>'
        f"{icon('gear')}<span>설정</span></a>"
        + f'{_guide()}</nav><div class="orbit-tools">'
        + (
            '<button type="button" popovertarget="orbit-status" aria-label="확인할 사항">'
            f"{icon('bell')}</button>"
            if active == "live"
            else ""
        )
        + '<a class="orbit-theme-toggle" '
        f'href="/{active}?theme={"dark" if theme == "light" else "light"}" '
        f'aria-label="{"다크" if theme == "light" else "라이트"} 모드로 전환" '
        f'title="{"다크" if theme == "light" else "라이트"} 모드로 전환">'
        # 해 · 달 둘 다 넣고 테마에 따라 하나만 보인다 — 스크립트가 다시 불러오지 않고 바로 바꾼다
        f'<span class="ico-to-dark">{icon("moon")}</span>'
        f'<span class="ico-to-light">{icon("sun")}</span></a>'
        f'</div></div><main class="orbit-main">{body}</main></body></html>'
    )


def _change(value: float, base: float, hide: bool = False) -> str:
    diff = value - base
    if hide:
        return f'<span class="mute"><b>{_HIDDEN}원</b><small>{_HIDDEN}</small></span>'
    cls = "pos" if diff > 0 else "neg" if diff < 0 else "mute"
    pct = f"<small>{diff / base:+.2%}</small>" if base > 0 else ""
    return f'<span class="{cls}"><b>{diff:+,.0f}원</b>{pct}</span>'


def _est(on: bool) -> str:
    return '<em class="orbit-est" title="시세·환율이 없어 원가로 셈">추정</em>' if on else ""


# ---------------------------------------------------------------- 그래프 (기록만)


def _nav_points(
    rows: Sequence[Mapping[str, Any]], key: str | None = None
) -> list[tuple[str, float, float, float]]:
    """기록 → (날짜, 평가액, 기준 금액, 투자 성과 지수 1.0 시작). 입금 · 출금은 수익에서 뺀다."""
    out: list[tuple[str, float, float, float]] = []
    nav, prev_v, prev_d = 1.0, 0.0, 0.0
    for r in rows:
        src = r if key is None else (r.get("books") or {}).get(key)
        if not isinstance(src, Mapping) or src.get("priced") is False:
            continue  # 시세 없이 원가로 셈한 날(추정)은 그래프에 쓰지 않는다
        try:
            v, dep = float(src["value"]), float(src["deposits"])
        except (KeyError, TypeError, ValueError):
            continue
        if out and prev_v > 0:
            nav *= (v - (dep - prev_d)) / prev_v
        out.append((str(r["date"]), v, dep, nav))
        prev_v, prev_d = v, dep
    return out


def _svg_line(
    points: Sequence[tuple[str, float]],
    *,
    base: Sequence[float] | None = None,
    label: str,
    fmt: str,
    hide_axis: bool,
) -> str:
    w, h, left, right, top, bottom = 620, 170, 62, 14, 12, 26
    vals = [v for _, v in points] + list(base or [])
    lo, hi = min(vals), max(vals)
    pad = (hi - lo) * 0.12 or abs(hi) * 0.02 or 1.0
    lo, hi = lo - pad, hi + pad
    n = len(points)

    def px(i: int) -> float:
        return left + (i / (n - 1)) * (w - left - right)

    def py(v: float) -> float:
        return top + (hi - v) / (hi - lo) * (h - top - bottom)

    parts = [
        f'<svg class="orbit-plot-svg" viewBox="0 0 {w} {h}" role="img" '
        f'aria-label="{escape(label)}">'
    ]
    for k in range(3):
        v = lo + (hi - lo) * k / 2
        y = py(v)
        txt = "•••" if hide_axis else fmt.format(v)
        parts.append(
            f'<line x1="{left}" x2="{w - right}" y1="{y:.1f}" y2="{y:.1f}" class="grid"/>'
            f'<text x="{left - 6}" y="{y + 4:.1f}" class="axis" text-anchor="end">{txt}</text>'
        )
    for i, anchor in ((0, "start"), (n - 1, "end")):
        parts.append(
            f'<text x="{px(i):.1f}" y="{h - 7}" class="axis" text-anchor="{anchor}">'
            f"{escape(points[i][0][5:].replace('-', '/'))}</text>"
        )
    if base:
        pts = " ".join(f"{px(i):.1f},{py(v):.1f}" for i, v in enumerate(base))
        parts.append(
            f'<polyline points="{pts}" class="orbit-plot-base"><title>기준 금액</title></polyline>'
        )
    pts = " ".join(f"{px(i):.1f},{py(v):.1f}" for i, (_, v) in enumerate(points))
    parts.append(
        f'<polyline points="{pts}" class="orbit-plot-line">'
        f"<title>{escape(label)}</title></polyline>"
    )
    parts.append("</svg>")
    return "".join(parts)


def _history_panel(history: Sequence[Mapping[str, Any]], hide: bool) -> str:
    pts = _nav_points(history)
    views = (("asset", "자산 변화"), ("perf", "투자 성과"))
    head = (
        '<div class="orbit-panel-heading">'
        f"<h2>{icon('chart')}자산 변화</h2>"
        '<div class="orbit-views" role="group" aria-label="그래프 종류">'
        + "".join(
            f'<label><input type="radio" name="orbit-view" value="{k}" '
            f"{'checked' if k == 'asset' else ''}><span>{t}</span></label>"
            for k, t in views
        )
        + '</div><div class="orbit-periods" role="group" aria-label="자산 변화 표시 기간">'
        + "".join(
            f'<label><input type="radio" name="orbit-range" value="{code}" '
            f"{'checked' if code == 'm1' else ''}><span>{word}</span></label>"
            for code, word, _ in PERIODS
        )
        + "</div></div>"
    )
    if len(pts) < 2:
        first = f"{pts[0][0]} 부터 기록 중 · " if pts else ""
        body = (
            '<div class="orbit-chart-empty"><span>평가 기록 대기</span>'
            f"<p>{first}하루 한 번 남기는 실거래 평가 기록이 2일 이상 쌓이면 "
            "그래프가 그려집니다.</p></div>"
        )
    else:
        last = date.fromisoformat(pts[-1][0])
        plots = []
        for code, word, days in PERIODS:
            sub = [p for p in pts if date.fromisoformat(p[0]) >= last - timedelta(days=days)]
            for view, title in views:
                cls = f"orbit-plot p-{code} v-{view}"
                if len(sub) < 2:
                    plots.append(
                        f'<div class="{cls} orbit-chart-empty"><span>{word} 기록 '
                        f"{len(sub)}일</span></div>"
                    )
                    continue
                if view == "asset":
                    svg = _svg_line(
                        [(d, v) for d, v, _, _ in sub],
                        base=[b for _, _, b, _ in sub],
                        label=f"{word} {title}",
                        fmt="{:,.0f}",
                        hide_axis=hide,
                    )
                    cap = (
                        f"평가액 {_won(sub[0][1], hide)} → {_won(sub[-1][1], hide)}원"
                        " · 점선은 기준 금액(입금 포함)"
                    )
                else:
                    start = sub[0][3]
                    perf = [(d, (n / start - 1) * 100) for d, _, _, n in sub]
                    svg = _svg_line(perf, label=f"{word} {title}", fmt="{:+.1f}%", hide_axis=False)
                    cap = f"입출금 뺀 수익률 {perf[-1][1]:+.2f}%"
                plots.append(f'<div class="{cls}">{svg}<p class="orbit-plot-cap">{cap}</p></div>')
        body = f'<div class="orbit-plots">{"".join(plots)}</div>'
    return (
        '<section class="orbit-panel orbit-history">'
        + head
        + f'<div data-live="history">{body}</div>'
        '<p class="orbit-chart-note">실거래 장부만 표시 · 하루 한 번 남긴 평가 기록만 · '
        "기록 없는 과거는 채우지 않음 · 자산 변화 = 입금으로 늘어난 금액 포함 · "
        "투자 성과 = 입출금 영향 뺀 수익률</p></section>"
    )


def _spark(history: Sequence[Mapping[str, Any]], key: str) -> str:
    pts = _nav_points(history, key)[-30:]
    if len(pts) < 2:
        return '<div class="orbit-spark-empty" aria-label="평가 기록 대기">평가 기록 대기</div>'
    perf = [n for _, _, _, n in pts]
    lo, hi = min(perf), max(perf)
    span = (hi - lo) or 1.0
    xy = " ".join(
        f"{i / (len(perf) - 1) * 90 + 1:.1f},{32 - (v - lo) / span * 28:.1f}"
        for i, v in enumerate(perf)
    )
    cls = "pos" if perf[-1] >= perf[0] else "neg"
    return (
        f'<svg class="orbit-spark {cls}" viewBox="0 0 92 34" role="img" '
        f'aria-label="최근 {len(perf)}일 투자 성과"><polyline points="{xy}"/></svg>'
    )


# ---------------------------------------------------------------- 카드 · 종목 상세


def _holding_popup(book: Book, j: int, quotes: Quotes, hide: bool) -> tuple[str, str]:
    h = book.holdings[j]
    pid = f"orbit-h-{book.key}-{j}"
    unit = "주" if book.key != "cruise" else ""
    avg = h.cost_krw / h.qty if h.qty > 0 else 0.0
    rows = [
        ("보유 수량", _HIDDEN if hide else f"{h.qty:,.8g}{unit}"),
        ("평균 매입단가", f"{avg:,.0f}원" if avg else "—"),
    ]
    if h.value_krw is not None and h.qty > 0:
        rows.append(("현재 가격", f"{h.value_krw / h.qty:,.0f}원"))
        usd = quotes.usd.get(h.name)
        if usd is not None:
            rows.append(("현재 가격 (달러)", f"${usd:,.2f}"))
        rows.append(("평가금액", f"{_won(h.value_krw, hide)}원"))
        rows.append(("평가손익", _change(h.value_krw, h.cost_krw, hide)))
    else:
        rows.append(("현재 가격", "시세 미확인"))
        rows.append(("평가금액", f"{_won(h.cost_krw, hide)}원 {_est(True)}"))
    rows.append(("가격 기준", _kst(quotes.at_ms) if quotes.at_ms else "시세 없음"))
    if h.note:
        rows.append(("메모", escape(h.note)))
    table = "".join(f"<dt>{k}</dt><dd>{v}</dd>" for k, v in rows)
    body = (
        f'<dl class="orbit-detail-list" data-live="{pid}">{table}</dl>'
        "<p class='orbit-detail-foot'>장부 기준 · 세금 전 · 주문 기능 없음</p>"
    )
    return pid, _popup(pid, f"{escape(h.name)} 보유 상세", body)


def _card(
    book: Book,
    quotes: Quotes,
    index: int,
    *,
    recorded: bool = True,
    hide: bool = False,
    now_ms: int = 0,
    history: Sequence[Mapping[str, Any]] = (),
    show_detail: bool = False,
    head_extra: str = "",
    pad_rows: int = 0,
) -> str:
    key = book.key if book.key in {"glide", "cruise", "accumulate"} else "other"
    category, subtitle, glyph = {
        "glide": ("미국 주식", "Glide", "chart"),
        "cruise": ("코인", "Cruise", "coins"),
        "accumulate": ("자동 적립", "자동 적립", "leaf"),
    }.get(key, ("투자 장부", book.title, "wallet"))
    priced = _priced(book, quotes)
    change = (
        _change(book.value_krw, book.deposits_krw, hide)
        if priced
        else ('<span class="mute">시세 확인 후 표시</span>')
    )
    if not recorded:
        change = '<span class="mute">장부 기록 대기</span>'
    amount = _won(book.value_krw, hide) + "<span>원</span>" + _est(not priced) if recorded else "—"
    stamp = ""
    if recorded and book.updated_ms:
        old = _stale(book, now_ms)
        stamp = (
            f'<p class="orbit-card-stamp{" orbit-stale" if old else ""}">'
            f"장부 업데이트 {_kst(book.updated_ms)}{' · 오래된 기록' if old else ''}</p>"
        )
    rows, pops = [], []
    for j, holding in enumerate(book.holdings):
        symbol = escape(holding.name)
        coin_class = holding.name.lower() if holding.name in _NAMES else "other"
        company = escape(_NAMES.get(holding.name, holding.name))
        value = holding.value_krw if holding.value_krw is not None else holding.cost_krw
        row_pl = (
            _change(holding.value_krw, holding.cost_krw, hide)
            if holding.value_krw is not None
            else ('<span class="mute">시세 미확인</span>')
        )
        pid, pop = _holding_popup(book, j, quotes, hide)
        pops.append(pop)
        rows.append(
            f'<li><button type="button" class="orbit-holding-btn" popovertarget="{pid}" '
            f'aria-label="{symbol} 보유 상세 열기"><div class="orbit-holding-name">'
            f'<span class="orbit-coin coin-{coin_class}">{symbol_icon(holding.name)}</span>'
            f"<div><b>{symbol}</b><small>{company}</small></div>"
            '</div><div class="orbit-holding-value">'
            f"<b>{_won(value, hide)}원{_est(holding.value_krw is None)}</b>"
            f"<small>{row_pl}</small></div></button></li>"
        )
    return (
        f'<section id="live-book-{index}" class="orbit-card orbit-{key}">'
        '<div class="orbit-card-heading">'
        f'<span class="orbit-category-icon">{icon(glyph)}</span>'
        f"<div><h2>{category}</h2><span>{escape(subtitle)}</span></div>"
        f"{head_extra}</div>"
        f'<div class="orbit-card-summary" data-live="card-{key}-summary"><div>'
        f"<strong>{amount}</strong><p>{change}</p>{stamp}</div>"
        f"{_spark(history, book.key)}</div>"
        f'<div class="orbit-row-label"><span>보유 종목 ({len(book.holdings)}개)</span>'
        "<span>평가금액</span></div>"
        f'<ul class="orbit-holdings" data-live="card-{key}-holdings">'
        f"{''.join(rows) if rows else '<li>아직 보유 기록이 없습니다.</li>'}"
        # 세 카드 줄을 맞추려고, 종목이 적은 카드는 보이지 않는 빈 줄로 채운다
        + '<li class="orbit-pad" aria-hidden="true"></li>' * max(0, pad_rows - max(len(rows), 1))
        + "</ul>"
        + "".join(pops)
        # 상세는 화면 위 '상세 보기' 버튼 하나로 셋을 함께 연다 (카드 바닥에 붙음)
        + f'<div class="orbit-dt orbit-book-detail" data-live="card-{key}-detail"'
        + ("" if show_detail else " hidden")
        + ">"
        + _detail(book, quotes, hide=hide, priced=priced, now_ms=now_ms, pad_rows=pad_rows)
        + "</div></section>"
    )


# ---------------------------------------------------------------- 보유 상세 (타일 · 구성 · 표)

_COLORS = {
    "BTC": "#EF9F27",
    "ETH": "#7F77DD",
    "SOL": "#1D9E75",
    "TQQQ": "#378ADD",
    "SCHD": "#1D9E75",
}
_SPARE = ("#D4537E", "#378ADD", "#BA7517", "#5DCAA5", "#7F77DD")


def _color(sym: str, i: int) -> str:
    return _COLORS.get(sym, _SPARE[i % len(_SPARE)])


def _tile(label: str, value: str, sub: str = "", cls: str = "") -> str:
    # '−15%면' 처럼 % 뒤에 붙은 말은 떼지 않는다 (브라우저는 % 뒤를 줄바꿈 자리로 본다)
    sub = sub.replace("%면", "%\u2060면")
    return (
        f'<div class="orbit-dt-tile"><span>{label}</span>'
        f"<b{f' class={chr(34)}{cls}{chr(34)}' if cls else ''}>{value}</b>"
        + (f"<small>{sub}</small>" if sub else "")
        + "</div>"
    )


def _tiles(book: Book, hide: bool, now_ms: int) -> tuple[str, str]:
    """(타일 4 칸, 아래 왼쪽 일정 칩). 장부 값만 쓰고 없으면 '—'."""
    info = book.info
    cash = f"{_won(book.cash_krw, hide)}원"
    if book.key == "cruise":
        dd = info.get("dd")
        stopped = bool(info.get("stopped"))
        tiles = [
            _tile("현금", cash, "남은 돈"),
            _tile(
                "사는 중인 코인", f"{info.get('buying', 0)} / {info.get('coins', 0)}개", cls="ok"
            ),
            _tile("최고점 기준", "—" if dd is None else f"−{dd:.1%}", "−30%면 매수 멈춤"),
            _tile("매수", "멈춤" if stopped else "하는 중", cls="warn" if stopped else "ok"),
        ]
        last = str(info.get("last_decision") or "")
        chip = "매일 09:05 판단" + (f" · 마지막 {last[5:].replace('-', '/')}" if last else "")
        return "".join(tiles), chip
    if book.key == "glide":
        month = str(info.get("month") or "")
        amount = info.get("month_amount")
        lev = info.get("lev_target")
        stage = int(info.get("brake_stage") or 0)
        brake, bcls = {0: ("안 걸림", "ok"), 1: ("절반 팖", "warn"), 2: ("전부 팖", "warn")}.get(
            stage, ("확인 필요", "warn")
        )
        usd = info.get("cash_usd")
        reserve = float(info.get("reserve_usd") or 0.0)

        def dollars(x: float) -> str:
            return _HIDDEN if hide else f"${x:,.2f}"

        cash_sub = "" if usd is None else f"남은 달러 {dollars(usd)}"
        if reserve > 0:  # 현금 = 남은 달러 + 브레이크 보관 달러
            cash_sub = f"보관 {dollars(reserve)} 포함"
        tiles = [
            _tile("현금", cash, cash_sub),
            _tile(
                "이번 달 납입",
                "—" if amount is None else f"{_won(amount, hide)}원",
                f"{int(month[5:])}월 첫 거래일" if len(month) >= 7 else "",
            ),
            _tile("TQQQ 목표 비중", "—" if lev is None else f"{lev:.0%}"),
            _tile(
                "급락 브레이크",
                brake,
                f"판 돈 {dollars(reserve)} 보관" if reserve > 0 else "고점 −15%면 절반 팖",
                bcls,
            ),
        ]
        now = datetime.fromtimestamp(now_ms / 1000, UTC) + _KST
        nxt = now.month % 12 + 1
        return "".join(tiles), f"다음 매수 {nxt}월 첫 거래일"
    # 자동 적립 · 그 밖의 장부
    last = str(info.get("last_buy") or "")
    tiles = [
        _tile("넣은 원가", f"{_won(book.deposits_krw, hide)}원", "적립 매입원가"),
        _tile("적립 종목", f"{len(book.holdings)}개"),
        _tile("현금", cash),
        _tile("마지막 적립", last[5:].replace("-", "/") if last else "—"),
    ]
    return "".join(tiles), "매일 적립 · 토스 주식 모으기"


def _detail(
    book: Book, quotes: Quotes, *, hide: bool, priced: bool, now_ms: int, pad_rows: int = 0
) -> str:
    tiles, chip = _tiles(book, hide, now_ms)
    total = book.value_krw
    parts, legend, rows = [], [], []
    for i, h in enumerate(book.holdings):
        value = h.value_krw if h.value_krw is not None else h.cost_krw
        w = value / total if total > 0 else 0.0
        color = _color(h.name, i)
        parts.append(f'<span style="flex:{w:.4f} 0 0;background:{color}"></span>')
        legend.append(
            f'<span><i class="orbit-dt-dot" style="background:{color}"></i>'
            f"{escape(h.name)} {w:.0%}</span>"
        )
        usd_now = quotes.usd.get(h.name)
        if h.avg_usd is not None or usd_now is not None:
            avg = "—" if h.avg_usd is None else f"${h.avg_usd:,.2f}"
            now = "—" if usd_now is None else f"${usd_now:,.2f}"
        else:
            avg = f"{h.cost_krw / h.qty:,.0f}" if h.qty > 0 and h.cost_krw > 0 else "—"
            now = f"{h.value_krw / h.qty:,.0f}" if h.qty > 0 and h.value_krw is not None else "—"
        unit = "" if book.key == "cruise" else "주"
        qty = _HIDDEN if hide else f"{h.qty:,.8g}{unit}"
        if h.target is not None:
            off = abs(w - h.target) >= 0.10
            gauge = (
                f'<span class="orbit-dt-gauge"><i style="width:{min(w, 1) * 100:.0f}%;'
                f'background:{color}"></i><u style="left:{min(h.target, 1) * 100:.0f}%"></u></span>'
                f"<em{' class=' + chr(34) + 'warn' + chr(34) if off else ''}>"
                f"{w * 100:.0f} / {h.target * 100:.0f}%</em>"
            )
        else:
            gauge = (
                f'<span class="orbit-dt-gauge"><i style="width:{min(w, 1) * 100:.0f}%;'
                f'background:{color}"></i></span><em>{w * 100:.0f}%</em>'
            )
        rows.append(
            f"<tr><td>{escape(h.name)}</td><td>{qty}</td><td>{avg}</td><td>{now}</td>"
            f'<td><div class="orbit-dt-wt">{gauge}</div></td></tr>'
        )
    cash_w = book.cash_krw / total if total > 0 else 0.0
    if book.cash_krw > 0:
        parts.append(f'<span style="flex:{cash_w:.4f} 0 0;background:var(--dt-cash)"></span>')
        legend.append(
            '<span><i class="orbit-dt-dot" style="background:var(--dt-cash)"></i>'
            f"현금 {cash_w:.0%}</span>"
        )
    has_target = any(h.target is not None for h in book.holdings)
    table = (
        (
            '<p class="orbit-dt-sec">종목별'
            + (" (막대 = 지금 · 세로선 = 목표)" if has_target else " (막대 = 지금 비중)")
            + "</p>"
            '<table class="orbit-dt-table"><colgroup><col style="width:14%"><col style="width:20%">'
            '<col style="width:19%"><col style="width:19%"><col style="width:28%"></colgroup>'
            "<tr><th>종목</th><th>수량</th><th>평균 단가</th><th>현재가</th>"
            f"<th>{'비중 / 목표' if has_target else '비중'}</th></tr>{''.join(rows)}"
            + '<tr class="orbit-pad" aria-hidden="true"><td colspan="5">&nbsp;</td></tr>'
            * max(0, pad_rows - len(rows))
            + "</table>"
        )
        if rows
        else ""
    )
    bar = (
        (
            '<p class="orbit-dt-sec">지금 구성</p>'
            f'<div class="orbit-dt-bar">{"".join(parts)}</div>'
            f'<div class="orbit-dt-legend">{"".join(legend)}</div>'
        )
        if parts
        else ""
    )
    foot = f"장부 기준 · 세금 전 · {'평가액' if priced else '추정 평가액'}"
    return (
        f'<div class="orbit-dt-tiles">{tiles}</div>'
        + bar
        + table
        + f'<div class="orbit-dt-bottom"><span class="orbit-dt-chip">{escape(chip)}</span>'
        f'<span class="orbit-dt-foot">{foot}</span></div>'
    )


# ---------------------------------------------------------------- 거래 내역


def _trade_rows(trades: Sequence[Trade], hide: bool) -> str:
    out = []
    for t in trades:
        sym = t.symbol if t.symbol in _NAMES else ""
        cls = sym.lower() if sym else "other"
        word = _KIND_WORD.get(t.kind, t.kind)
        usd = t.currency == "USD"
        cur, won = ("$", "") if usd else ("", "원")
        digits = 2 if usd else 0

        def money(x: float, d: int, *, cur: str = cur, won: str = won) -> str:
            return _HIDDEN if hide else f"{cur}{x:,.{d}f}{won}"

        if t.kind == "deposit":
            title = f"{word} {money(t.amount, 0)}"
            info = [t.note] if t.note else []
        else:
            qty = _HIDDEN if hide else f"{t.qty:,.8g}{'주' if usd else ''}"
            title = f"{escape(t.symbol)} {word} {qty}"
            info = []
            if t.price:
                head = "체결가" if t.status == "체결" else "가격"
                info.append(f"{head} {cur}{t.price:,.{digits}f}{won}")
            if t.amount:
                info.append(f"금액 {money(t.amount, digits)}")
            if t.fee:
                small = not usd and t.fee < 1 and not hide
                info.append(
                    "수수료 1원 미만" if small else f"수수료 {money(t.fee, 2 if usd else 0)}"
                )
            if t.note:
                info.append(t.note)
        if t.status == "체결":
            state_cls = "done"
        elif t.kind == "deposit":
            state_cls = "deposit"
        elif "대기" in t.status:
            state_cls = "wait"
        else:
            state_cls = "other"
        sub = escape(t.date) + (" · " + escape(" · ".join(info)) if info else "")
        out.append(
            f'<li class="kind-{escape(t.kind)}">'
            f'<span class="orbit-coin coin-{cls}">{symbol_icon(sym)}</span>'
            f"<div><b>{title}</b><small>{sub}</small></div>"
            f'<span class="orbit-trade-state state-{state_cls}">{escape(t.status)}</span></li>'
        )
    return "".join(out)


def _trades_panel(trades: Sequence[Trade], hide: bool) -> str:
    filt = (
        '<div class="orbit-trade-filter" role="group" aria-label="거래 종류">'
        + "".join(
            f'<label><input type="radio" name="orbit-trade-filter" id="tf-{k}" value="{k}" '
            f"{'checked' if k == 'all' else ''}><span>{w}</span></label>"
            for k, w in (("all", "전체"), ("buy", "매수"), ("sell", "매도"), ("deposit", "입금"))
        )
        + "</div>"
    )
    rows = _trade_rows(trades, hide) or "<li>아직 거래 기록이 없습니다.</li>"
    # 필터에 해당 기록이 없으면 빈 칸 대신 한 줄 (전체에서는 숨김)
    rows += "".join(
        f'<li class="kind-{k} orbit-trade-empty">{w} 기록이 아직 없습니다.</li>'
        for k, w in (("buy", "매수"), ("sell", "매도"), ("deposit", "입금"))
        if trades and not any(t.kind == k for t in trades)
    )
    recent = _trade_rows(trades[:5], hide) or "<li>아직 거래·입금 기록이 없습니다.</li>"
    return (
        '<section class="orbit-panel orbit-recent" id="orbit-recent">'
        '<div class="orbit-panel-heading">'
        f"<h2>{icon('log')}거래 내역 <small>(최근 5건)</small></h2>"
        '<button type="button" class="orbit-all-trades" popovertarget="orbit-all-trades">'
        "전체 보기 ›</button>"
        + _popup(
            "orbit-all-trades",
            "거래 내역",
            filt + f'<ul class="orbit-trades-list" data-live="trades-all">{rows}</ul>',
        )
        + "</div>"
        f'<ul data-live="trades-recent">{recent}</ul></section>'
    )


# ---------------------------------------------------------------- 연결 계좌 · 처음 안내


def _accounts(books: Sequence[Book], now_ms: int) -> str:
    by_key = {b.key: b for b in books}
    rows = []
    for key, name, venue in (
        ("glide", "Glide", "토스증권 · 미국 주식"),
        ("cruise", "Cruise", "업비트 · 코인"),
        ("accumulate", "자동 적립", "토스증권 · 주식 모으기"),
    ):
        b = by_key.get(key)
        if b is None:
            state, cls = "연결 미확인 · 장부 없음", "other"
        elif not b.updated_ms:
            state, cls = "저장 장부만 표시 · 저장 시각 모름", "other"
        elif _stale(b, now_ms):
            state, cls = f"오래된 장부 · {_kst(b.updated_ms)} — 매매 프로그램 확인 필요", "wait"
        else:
            state, cls = f"마지막 조회 성공 · {_kst(b.updated_ms)}", "done"
        rows.append(
            f"<li><div><b>{name}</b><small>{venue}</small></div>"
            f'<span class="orbit-trade-state state-{cls}">{escape(state)}</span></li>'
        )
    return _popup(
        "orbit-accounts",
        "연결 계좌",
        f'<ul class="orbit-accounts-list" data-live="accounts">{"".join(rows)}</ul>'
        "<p>‘마지막 조회 성공’은 매매 프로그램이 계좌를 조회한 뒤 장부를 저장한 시각입니다. "
        "이 화면은 계좌를 새로 조회하지 않고 저장 장부만 표시합니다. "
        "계좌번호·API 키는 표시하지 않습니다.</p>",
    )


def _onboard() -> str:
    return (
        '<section class="orbit-onboard" aria-labelledby="orbit-onboard-title">'
        '<h2 id="orbit-onboard-title">처음 오셨나요? 세 가지만 보면 됩니다</h2><ol>'
        "<li><b>화면 설명</b><span>내 자산 = 장부의 현금 + 보유 평가액. "
        "기준 금액은 넣은 돈입니다. 눈 아이콘으로 금액을 가릴 수 있습니다.</span></li>"
        "<li><b>계좌 연결</b><span>Glide · 자동 적립은 토스증권, Cruise 는 업비트입니다. "
        "연결은 매매 프로그램이 하고, 이 화면은 저장 장부만 읽습니다.</span></li>"
        "<li><b>기록 확인</b><span>장부 업데이트 시각과 거래 내역으로 프로그램이 일했는지 "
        "봅니다. 오래된 장부는 빨갛게 표시됩니다.</span></li></ol>"
        '<p class="orbit-onboard-key">이 화면(앱)을 닫아도 매매 프로그램은 멈추지 않습니다 — '
        "예약 작업으로 따로 돕니다.</p>"
        '<p class="orbit-onboard-actions"><a href="/live?onboard=done">확인했어요</a>'
        '<button type="button" popovertarget="orbit-guide">자세히 보기</button></p></section>'
    )


# ---------------------------------------------------------------- 화면


def render_live(
    books: list[Book],
    quotes: Quotes,
    now_ms: int,
    *,
    hide: bool = False,
    onboarded: bool = True,
    history: Sequence[Mapping[str, Any]] | None = None,
    show_detail: bool = False,
) -> str:
    """승인한 화면의 요소만 표시. 예시 수익/이력/연결 상태를 만들어 넣지 않는다."""
    hist = list(history or [])
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
    warnings.extend(
        f"{b.title} · 장부가 오래됨 ({_kst(b.updated_ms)})" for b in books if _stale(b, now_ms)
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
        amount = _won(value, hide) + "<small>원</small>" + _est(not complete)
        base_text = _won(base, hide) + "원"
        change = (
            _change(value, base, hide) if complete else '<span class="mute">시세 확인 필요</span>'
        )
    caution = "확정 수익 아님" if complete else "추정치 포함 · 확정 수익 아님"
    ledger_ms = max((b.updated_ms for b in books), default=0)
    stale_n = sum(_stale(b, now_ms) for b in books)
    stamp = (
        f"장부 업데이트 {_kst(ledger_ms) if ledger_ms else '모름'}"
        f" · 시세 업데이트 {_kst(quotes.at_ms) if quotes.at_ms else '없음'}"
        + (f" · 오래된 장부 {stale_n}개" if stale_n else "")
    )
    word = "보이기" if hide else "숨기기"
    eye = (
        f'<a class="orbit-eye{" on" if hide else ""}" '
        f'href="/live?amounts={"show" if hide else "hide"}" '
        f'aria-label="금액 {word}" title="금액 {word}">{icon("eye")}<span>{word}</span></a>'
    )
    hero = (
        '<section class="orbit-hero">'
        '<div class="orbit-hero-art" aria-hidden="true">'
        '<img class="orbit-hero-cat" src="/assets/orbit-hero-light.png" alt="">'
        f"{HERO_FX}</div>"
        '<div class="orbit-greeting"><span>안녕하세요,</span>'
        '<h1>오늘도 오비와 함께 <span aria-hidden="true">🪐</span></h1>'
        "<p>작은 투자가 만드는 더 나은 오늘, 자동으로 차곡차곡</p></div>"
        '<section class="orbit-networth" id="orbit-assets" aria-labelledby="live-total-title">'
        f'<div><h2 id="live-total-title">내 자산 {eye}</h2>'
        f'<strong data-live="total" class="orbit-total">{amount}</strong></div>'
        f'<div><span>기준 금액</span><b data-live="base">{base_text}</b></div>'
        f'<div><span>평가 차액</span><strong data-live="change">{change}</strong>'
        f'<small class="orbit-caution">ⓘ {caution}'
        "</small></div>"
        f'<p class="orbit-updated{" orbit-stale" if stale_n else ""}" data-live="updated">'
        f"{stamp}</p></section>"
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
        f'<div data-live="status"><p>{escape(title)}</p><p>{escape(detail)}</p></div>'
        "<p>주문 접수 ≠ 체결 완료. 프로그램의 현재 가동 여부를 확정하지 않습니다.</p>",
    )
    # 체결 대기 줄만 숨긴다. 시세/정지/장부 결측 경고는 계속 읽을 수 있어야 한다.
    if not pending_count and (warnings or not books):
        status += f'<p class="orbit-data-note" data-live="data-note">{escape(detail)}</p>'
    by_key = {b.key: b for b in books}
    pad = max((len(b.holdings) for b in books), default=1)
    toggle = (
        f'<a class="orbit-detail-toggle{" on" if show_detail else ""}" '
        f'href="/live?detail={"close" if show_detail else "open"}" '
        f'aria-expanded="{str(show_detail).lower()}">'
        f"{'상세 닫기' if show_detail else '상세 보기'}"
        f'<span aria-hidden="true">{"⌃" if show_detail else "⌄"}</span></a>'
    )
    cards = "".join(
        _card(
            by_key.get(k, Book(k, k, "", 0, 0, ())),
            quotes,
            i,
            recorded=k in by_key,
            hide=hide,
            now_ms=now_ms,
            history=hist,
            show_detail=show_detail,
            pad_rows=pad,
        )
        for i, k in enumerate(("glide", "cruise", "accumulate"))
    )
    trades = sorted((t for b in books for t in b.trades), key=lambda t: t.date, reverse=True)
    return (
        ("" if onboarded else _onboard())
        + hero
        + alert
        + status
        + _accounts(books, now_ms)
        + f'<div class="orbit-cards-tools">{toggle}</div>'
        + f'<div class="orbit-cards">{cards}</div>'
        + '<div class="orbit-bottom">'
        + _history_panel(hist, hide)
        + _trades_panel(trades, hide)
        + "</div>"
    )
