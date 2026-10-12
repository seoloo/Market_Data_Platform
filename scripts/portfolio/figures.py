"""포트폴리오 보고서 — SVG 그림. 숫자는 카드와 연구 기록에서 가져온다."""

from __future__ import annotations

from collections import Counter
from datetime import date
from html import escape

from scripts.portfolio.cards_base import FAMILIES, HORIZONS, MARKETS, VERDICTS, Card

INK = "#1b2333"
SUB = "#5b6475"
LINE = "#d9dde5"
SOFT = "#f3f5f8"
ACC = "#2350a8"
FONT = "font-family=\"'Noto Sans KR','Malgun Gothic',sans-serif\""
ORDER = ("pass", "hist", "design", "unclear", "nodata", "reject")


def _t(
    x: float,
    y: float,
    s: str,
    size: float = 11,
    fill: str = INK,
    anchor: str = "start",
    weight: int = 400,
) -> str:
    return (
        f'<text x="{x:.1f}" y="{y:.1f}" font-size="{size}" fill="{fill}" text-anchor="{anchor}" '
        f'font-weight="{weight}">{escape(s)}</text>'
    )


def _svg(w: int, h: int, body: str) -> str:
    return f'<svg viewBox="0 0 {w} {h}" width="100%" xmlns="http://www.w3.org/2000/svg" {FONT}>{body}</svg>'


def _box(
    x: float,
    y: float,
    w: float,
    h: float,
    title: str,
    sub: str = "",
    fill: str = SOFT,
    stroke: str = LINE,
    tc: str = INK,
) -> str:
    out = f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="6" fill="{fill}" stroke="{stroke}"/>'
    cy = y + h / 2 + (-3 if sub else 4)
    out += _t(x + w / 2, cy, title, 12, tc, "middle", 700)
    if sub:
        out += _t(x + w / 2, cy + 16, sub, 9.5, SUB if tc == INK else tc, "middle")
    return out


def _arrow(x1: float, y1: float, x2: float, y2: float, color: str = SUB) -> str:
    return (
        f'<line x1="{x1}" y1="{y1}" x2="{x2}" y2="{y2}" stroke="{color}" stroke-width="1.4" '
        f'marker-end="url(#ah)"/>'
    )


DEFS = (
    f'<defs><marker id="ah" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" '
    f'orient="auto"><path d="M0,0 L10,5 L0,10 z" fill="{SUB}"/></marker></defs>'
)


def system_map() -> str:
    """그림: 전체 구성 — 자료에서 운영까지 단계 관문."""
    w, h = 760, 300
    b = DEFS
    stages = [
        ("자료", "시세 · 체결 · 공시 · 수급"),
        ("연구", "사전 등록 · 1회 판정"),
        ("Paper", "가상 장부 · 매일 재계산"),
        ("그림자 · 데모", "주문 0건 · 실측 비용"),
        ("운영 후보", "사용자 승인 관문"),
    ]
    bw, gap = 128, 30
    x0 = (w - (bw * 5 + gap * 4)) / 2
    for i, (a, s) in enumerate(stages):
        x = x0 + i * (bw + gap)
        fill = "#e8eefb" if i == 2 else SOFT
        b += _box(x, 40, bw, 62, a, s, fill)
        if i < 4:
            b += _arrow(x + bw + 2, 71, x + bw + gap - 2, 71)
    # 관문 문구
    gates = ["검정력 · 중복 감사", "통과만", "전향 기간", "위험 한도 · 승인"]
    for i, g in enumerate(gates):
        x = x0 + i * (bw + gap) + bw + gap / 2
        b += _t(x, 122, g, 9, ACC, "middle")
    # 아래: 공통 기반
    base = [
        ("일일 점검", "누락 · 낙폭 · 재계산 대조"),
        ("알림", "텔레그램 · 브리핑"),
        ("오르빗", "읽기 전용 대시보드"),
        ("백업", "다른 디스크 · 해시 목록"),
    ]
    bw2 = 160
    x1 = (w - (bw2 * 4 + 20 * 3)) / 2
    b += f'<rect x="{x1 - 14}" y="160" width="{bw2 * 4 + 60 + 28}" height="118" rx="8" fill="none" stroke="{LINE}" stroke-dasharray="4 4"/>'
    b += _t(x1 - 4, 178, "운영 기반 (모든 단계 공통)", 10, SUB)
    for i, (a, s) in enumerate(base):
        b += _box(x1 + i * (bw2 + 20), 194, bw2, 62, a, s)
    return _svg(w, h, b)


def research_pipeline() -> str:
    """그림: 연구 절차."""
    w, h = 760, 210
    b = DEFS
    steps = [
        ("아이디어", "책 · 논문 · 커뮤니티 · 외부 제안"),
        ("중복 감사", "이미 시험한 질문인가"),
        ("검정력 관문", "자료로 가릴 수 있는가"),
        ("사전 등록", "규칙 · 기준을 커밋"),
        ("1회 판정", "결과는 한 번만 연다"),
        ("기록", "재시도 금지 목록"),
    ]
    bw, gap = 108, 22
    x0 = (w - (bw * 6 + gap * 5)) / 2
    for i, (a, s) in enumerate(steps):
        x = x0 + i * (bw + gap)
        fill = "#e8eefb" if i in (3, 4) else SOFT
        b += f'<rect x="{x}" y="30" width="{bw}" height="74" rx="6" fill="{fill}" stroke="{LINE}"/>'
        b += _t(x + 10, 52, f"{i + 1:02d}", 10, ACC, weight=700)
        b += _t(x + 10, 72, a, 12.5, INK, weight=700)
        words = s.split(" ")
        line, lines = "", []
        for wd in words:
            if len(line + " " + wd) > 11 and line:
                lines.append(line)
                line = wd
            else:
                line = (line + " " + wd).strip()
        lines.append(line)
        for j, ln in enumerate(lines[:2]):
            b += _t(x + 10, 89 + j * 12, ln, 9, SUB)
        if i < 5:
            b += _arrow(x + bw + 2, 67, x + bw + gap - 2, 67)
    # 탈락 경로
    b += _t(x0 + 1 * (bw + gap) + bw / 2, 128, "↓ 이미 답이 있으면 종료", 9.5, SUB, "middle")
    b += _t(x0 + 2 * (bw + gap) + bw / 2, 146, "↓ 표본 부족이면 '자료 부족'", 9.5, SUB, "middle")
    b += _t(x0 + 4 * (bw + gap) + bw / 2, 128, "↓ 기준 미달이면 기각", 9.5, SUB, "middle")
    b += f'<rect x="{x0}" y="164" width="{bw * 6 + gap * 5}" height="30" rx="6" fill="#fff6e6" stroke="#f0d9a8"/>'
    b += _t(
        w / 2,
        184,
        "판정 뒤에는 같은 자료로 숫자만 바꿔 다시 판정하지 않는다 — 새 질문은 새 자료로",
        10.5,
        "#8a5a00",
        "middle",
        700,
    )
    return _svg(w, h, b)


def heatmap(cards: list[Card]) -> str:
    """그림: 시장 × 보유 기간 — 칸마다 시험 수와 판정 구성."""
    w, h = 760, 330
    lw, top, cw, ch = 110, 40, 160, 44
    b = ""
    for j, hz in enumerate(HORIZONS):
        b += _t(lw + j * cw + cw / 2, top - 12, hz, 11, SUB, "middle", 700)
    for i, mk in enumerate(MARKETS):
        y = top + i * ch
        b += _t(lw - 12, y + ch / 2 + 4, mk, 11, INK, "end", 700)
        for j, hz in enumerate(HORIZONS):
            x = lw + j * cw
            cs = [c for c in cards if c.market == mk and c.horizon == hz]
            b += f'<rect x="{x + 2}" y="{y + 2}" width="{cw - 4}" height="{ch - 4}" rx="4" fill="{SOFT}" stroke="{LINE}"/>'
            if not cs:
                b += _t(x + cw / 2, y + ch / 2 + 4, "—", 10, "#b5bbc6", "middle")
                continue
            cnt = Counter(c.verdict for c in cs)
            good = cnt["pass"] + cnt["hist"]
            b += _t(x + 10, y + 19, f"{len(cs)}건", 12, INK, weight=700)
            if good:
                b += _t(
                    x + cw - 10, y + 19, f"통과 · 지지 {good}", 9.5, VERDICTS["pass"][1], "end", 700
                )
            bx, bwid = x + 10, cw - 20
            acc = 0.0
            for v in ORDER:
                if cnt[v]:
                    ww = bwid * cnt[v] / len(cs)
                    b += f'<rect x="{bx + acc:.1f}" y="{y + 27}" width="{ww:.1f}" height="8" fill="{VERDICTS[v][1]}"/>'
                    acc += ww
    # 범례
    lx = lw
    ly = top + len(MARKETS) * ch + 22
    for v in ORDER:
        b += f'<rect x="{lx}" y="{ly - 9}" width="11" height="11" rx="2" fill="{VERDICTS[v][1]}"/>'
        b += _t(lx + 16, ly, VERDICTS[v][0], 10, SUB)
        lx += 92
    return _svg(w, h, b)


def family_bars(cards: list[Card]) -> str:
    """그림: 부류별 판정 구성."""
    fams = list(FAMILIES)
    w = 760
    rh, top, lw = 28, 14, 210
    h = top + rh * len(fams) + 40
    mx = max(sum(1 for c in cards if c.fam == f) for f in fams)
    bw = w - lw - 60
    b = ""
    for i, f in enumerate(fams):
        y = top + i * rh
        cs = [c for c in cards if c.fam == f]
        cnt = Counter(c.verdict for c in cs)
        b += _t(lw - 10, y + 15, FAMILIES[f], 10.5, INK, "end")
        acc = 0.0
        for v in ORDER:
            if cnt[v]:
                ww = bw * cnt[v] / mx
                b += f'<rect x="{lw + acc:.1f}" y="{y + 4}" width="{ww:.1f}" height="16" fill="{VERDICTS[v][1]}"/>'
                if ww > 16:
                    b += _t(lw + acc + ww / 2, y + 16, str(cnt[v]), 9, "#ffffff", "middle", 700)
                acc += ww
        b += _t(lw + acc + 6, y + 16, f"{len(cs)}", 10, SUB, weight=700)
    lx, ly = lw, top + rh * len(fams) + 22
    for v in ORDER:
        b += f'<rect x="{lx}" y="{ly - 9}" width="11" height="11" rx="2" fill="{VERDICTS[v][1]}"/>'
        b += _t(lx + 16, ly, VERDICTS[v][0], 10, SUB)
        lx += 88
    return _svg(w, h, b)


def timeline(cards: list[Card]) -> str:
    """그림: 연구 연표 — 날짜별 판정 점과 방향 전환."""
    d0, d1 = date(2026, 9, 21), date(2026, 10, 13)
    w, h = 760, 330
    lx, rx, base = 40, 730, 280
    span = (d1 - d0).days

    def sx(d: date) -> float:
        return lx + (rx - lx) * (d - d0).days / span

    b = f'<line x1="{lx}" y1="{base}" x2="{rx}" y2="{base}" stroke="{INK}" stroke-width="1"/>'
    for k in range(0, span + 1, 3):
        d = date.fromordinal(d0.toordinal() + k)
        b += f'<line x1="{sx(d):.1f}" y1="{base}" x2="{sx(d):.1f}" y2="{base + 4}" stroke="{INK}"/>'
        b += _t(sx(d), base + 17, f"{d.month}/{d.day}", 9.5, SUB, "middle")
    byday: dict[date, list[Card]] = {}
    for c in cards:
        byday.setdefault(date.fromisoformat(c.date), []).append(c)
    for d, cs in byday.items():
        cs.sort(key=lambda c: ORDER.index(c.verdict))
        for k, c in enumerate(cs):
            col, row = divmod(k, 12)
            cx = sx(d) + (col - (len(cs) - 1) // 12 / 2) * 9
            b += f'<circle cx="{cx:.1f}" cy="{base - 8 - row * 9:.1f}" r="3.6" fill="{VERDICTS[c.verdict][1]}"/>'
    phases = [
        (
            date(2026, 9, 22),
            date(2026, 9, 26),
            "단기 방향 · 캐리 탐색",
            "추세 보유와 레버리지만 통과",
        ),
        (
            date(2026, 10, 3),
            date(2026, 10, 9),
            "주식 · 지수 · 배분으로 확장",
            "생존 편향 없는 자료 · 다른 시장 재현",
        ),
        (
            date(2026, 10, 11),
            date(2026, 10, 12),
            "후향 파일럿 체계",
            "조건 맞춘 대조군 · 계좌 시험",
        ),
    ]
    for i, (a, z, t1, t2) in enumerate(phases):
        y = 22 + (i % 2) * 44
        x1, x2 = sx(a) - 6, sx(z) + 6
        b += f'<rect x="{x1:.1f}" y="{y}" width="{x2 - x1:.1f}" height="4" rx="2" fill="{ACC}"/>'
        anchor = "end" if i == 2 else "start"
        tx = x2 if i == 2 else x1
        b += _t(tx, y + 18, t1, 11, INK, anchor, 700)
        b += _t(tx, y + 32, t2, 9.5, SUB, anchor)
    return _svg(w, h, b)


def hbar(
    rows: list[tuple[str, float, str]],
    unit: str,
    ref: tuple[float, str] | None = None,
    lo: float | None = None,
    hi: float | None = None,
    label_w: int = 190,
) -> str:
    """가로 막대(음수 허용). rows = (이름, 값, 색)."""
    w = 760
    rh, top = 30, 16
    h = top + rh * len(rows) + 34
    vals = [r[1] for r in rows] + ([ref[0]] if ref else [])
    lo = min(0.0, min(vals)) if lo is None else lo
    hi = max(0.0, max(vals)) if hi is None else hi
    x0, x1 = label_w, w - 70

    def sx(v: float) -> float:
        return x0 + (x1 - x0) * (v - lo) / (hi - lo)

    b = f'<line x1="{sx(0):.1f}" y1="{top - 4}" x2="{sx(0):.1f}" y2="{top + rh * len(rows)}" stroke="{INK}" stroke-width="1"/>'
    for i, (name, v, col) in enumerate(rows):
        y = top + i * rh
        b += _t(x0 - 10, y + 17, name, 10.5, INK, "end")
        xa, xb = sorted((sx(0), sx(v)))
        b += f'<rect x="{xa:.1f}" y="{y + 5}" width="{max(xb - xa, 1):.1f}" height="17" rx="2" fill="{col}"/>'
        lab = (
            f"{v:+.2f}{unit}"
            if unit == "R"
            else (f"{v:.0f}{unit}" if unit == "건" else f"{v:+.1f}{unit}")
        )
        b += _t(
            xb + 5 if v >= 0 else sx(0) + 5,
            y + 18,
            lab,
            10,
            "#c0392b" if v < 0 else INK,
            "start",
            700,
        )
    if ref:
        rv, rl = ref
        b += (
            f'<line x1="{sx(rv):.1f}" y1="{top - 8}" x2="{sx(rv):.1f}" y2="{top + rh * len(rows) + 4}" '
            f'stroke="#c0392b" stroke-width="1.6" stroke-dasharray="5 3"/>'
        )
        b += _t(sx(rv), top + rh * len(rows) + 20, rl, 10, "#c0392b", "middle", 700)
    return _svg(w, h, b)


def range_bars() -> str:
    """그림: D22 과최적화 — 학습 구간 최고 설정 vs 봉인 구간."""
    w, h = 760, 170
    lo, hi = -40.0, 125.0
    x0, x1 = 200, 720

    def sx(v: float) -> float:
        return x0 + (x1 - x0) * (v - lo) / (hi - lo)

    rows = [
        ("학습 구간 (2018 ~ 2022)", 75, 117, ACC),
        ("봉인 구간 (같은 설정)", -32, -20, "#c0392b"),
        ("봉인 구간 그냥 보유", 42, 42, "#8a8f98"),
    ]
    b = f'<line x1="{sx(0):.1f}" y1="12" x2="{sx(0):.1f}" y2="128" stroke="{INK}"/>'
    for k in (-25, 0, 25, 50, 75, 100):
        b += _t(sx(k), 146, f"{k:+d}%" if k else "0", 9.5, SUB, "middle")
        b += f'<line x1="{sx(k):.1f}" y1="128" x2="{sx(k):.1f}" y2="132" stroke="{INK}"/>'
    for i, (n, a, z, col) in enumerate(rows):
        y = 20 + i * 36
        b += _t(x0 - 10, y + 14, n, 10.5, INK, "end")
        if a == z:
            b += f'<rect x="{min(sx(0), sx(a)):.1f}" y="{y + 3}" width="{abs(sx(a) - sx(0)):.1f}" height="16" rx="2" fill="{col}"/>'
            b += _t(sx(a) + 6, y + 15, f"연 {a:+d}%", 10, INK, weight=700)
        else:
            b += f'<rect x="{sx(a):.1f}" y="{y + 3}" width="{sx(z) - sx(a):.1f}" height="16" rx="2" fill="{col}"/>'
            if z > 0:
                b += _t(
                    (sx(a) + sx(z)) / 2,
                    y + 15,
                    f"연 {a:+d} ~ {z:+d}%",
                    10,
                    "#ffffff",
                    "middle",
                    700,
                )
            else:
                b += _t(sx(0) + 6, y + 15, f"연 {a:+d} ~ {z:+d}%", 10, "#c0392b", "start", 700)
    b += _t(
        w / 2,
        164,
        "연 수익률 (지표 27개 · 360개 설정 중 학습 구간 최고를 골라 봉인 구간에서 한 번 시험)",
        9.5,
        SUB,
        "middle",
    )
    return _svg(w, h, b)


def schedule() -> str:
    """그림: 하루 운영 시간표 (Paper · 점검 · 백업)."""
    w, h = 760, 230
    lx, rx = 30, 730
    b = ""
    b += f'<line x1="{lx}" y1="120" x2="{rx}" y2="120" stroke="{INK}"/>'

    def sx(hh: float) -> float:
        return lx + (rx - lx) * (hh - 6) / 18.5

    for hh in range(6, 25, 2):
        b += f'<line x1="{sx(hh):.1f}" y1="120" x2="{sx(hh):.1f}" y2="125" stroke="{INK}"/>'
        b += _t(sx(hh), 139, f"{hh:02d}:00" if hh < 24 else "24:00", 9.5, SUB, "middle")
    events = [
        (7.5, "07:30", "아침 브리핑", "시장 · 장부 요약"),
        (9.0, "09:00", "주간 테마 정리", "토요일 · 뉴스 근거"),
    ]
    for i, (t, hm, a, s) in enumerate(events):
        x = sx(t)
        y = 40 + i * 34
        b += f'<line x1="{x:.1f}" y1="{y + 8}" x2="{x:.1f}" y2="116" stroke="{LINE}"/>'
        b += f'<circle cx="{x:.1f}" cy="120" r="4" fill="{ACC}"/>'
        b += _t(x + 6, y, f"{hm}  {a}", 10.5, INK, weight=700)
        b += _t(x + 6, y + 14, s, 9.5, SUB)
    # 저녁 묶음 확대
    ex0, ex1 = sx(21.0), sx(21.4)
    b += f'<rect x="{ex0 - 3:.1f}" y="112" width="{ex1 - ex0 + 6:.1f}" height="16" rx="3" fill="#e8eefb" stroke="{ACC}"/>'
    b += f'<circle cx="{sx(23.5):.1f}" cy="120" r="4" fill="{ACC}"/>'
    b += _t(sx(23.5), 104, "23:30 백업", 10.5, INK, "middle", 700)
    b += _t(sx(23.5), 90, "다른 디스크", 9.5, SUB, "middle")
    # 확대 상자
    zx, zy, zw = 40, 158, 680
    b += f'<line x1="{ex0:.1f}" y1="128" x2="{zx}" y2="{zy}" stroke="{LINE}"/><line x1="{ex1:.1f}" y1="128" x2="{zx + zw}" y2="{zy}" stroke="{LINE}"/>'
    b += f'<rect x="{zx}" y="{zy}" width="{zw}" height="62" rx="6" fill="{SOFT}" stroke="{LINE}"/>'
    slots = [
        ("21:00", "코인 추세 Paper"),
        ("21:02", "선물 1.5배 Paper"),
        ("21:04", "비상 스톱판"),
        ("21:06", "2배 변형"),
        ("21:08", "테스트넷 훈련"),
        ("21:10", "주도주 Paper"),
        ("21:15", "지수 전환 Paper"),
        ("21:17", "소액 공격형"),
        ("21:20", "일일 점검"),
    ]
    sw = zw / len(slots)
    for i, (hm, a) in enumerate(slots):
        x = zx + i * sw + sw / 2
        b += _t(x, zy + 24, hm, 10, ACC, "middle", 700)
        b += _t(x, zy + 42, a, 9, INK, "middle")
    return _svg(w, h, b)
