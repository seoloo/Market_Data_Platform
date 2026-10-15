# ruff: noqa: E501 — 그림 문구 · SVG 문자열이 길다
"""README 그림 (SVG) 만들기 — 표 · 그래프를 라이브러리 없이 직접 그린다.

사용 예::

    python -m scripts.readme_charts   # docs/assets/readme/*.svg 를 다시 만든다

* Deneb 곡선은 184 엔진(`run_brake_184.simulate`)으로 그 자리에서 다시 계산한다.
* 막대 숫자는 판정 문서(184 · 190)와 RESEARCH_HISTORY 에서 옮겼다 — 문서가 바뀌면 여기도 고친다.
* 밝은 · 어두운 화면 모두에서 읽히도록 SVG 안의 CSS 가 ``prefers-color-scheme`` 을 따른다.
"""

from __future__ import annotations

import math
import re
from collections.abc import Sequence
from datetime import date
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parent.parent
OUT = REPO / "docs" / "assets" / "readme"
FONT = "-apple-system,'Segoe UI','Noto Sans KR','Malgun Gothic',sans-serif"
STYLE = f"""<style>
.t{{fill:#59636e;font:12px {FONT}}}
.b{{fill:#1f2328;font:600 12px {FONT}}}
.h{{fill:#1f2328;font:700 16px {FONT}}}
.s{{fill:#59636e;font:12.5px {FONT}}}
.g{{stroke:#d1d9e0;stroke-width:1}}
.ax{{stroke:#818b98;stroke-width:1}}
.card{{fill:#f6f8fa;stroke:#d1d9e0}}
@media (prefers-color-scheme:dark){{
.t,.s{{fill:#9198a1}}.b,.h{{fill:#f0f6fc}}.g{{stroke:#3d444d}}.ax{{stroke:#656c76}}
.card{{fill:#151b23;stroke:#3d444d}}}}
</style>"""
GRAY, LIGHT, BLUE, ORANGE = "#8c959f", "#79b8ff", "#2f81f7", "#f0883e"
GREEN, TEAL, RED, PURPLE = "#3fb950", "#1f9e89", "#e5534b", "#a371f7"


def svg(w: int, h: int, body: str, title: str) -> str:
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {w} {h}" width="{w}" height="{h}"'
        f' role="img" aria-label="{title}"><title>{title}</title>{STYLE}{body}</svg>\n'
    )


def text(x: float, y: float, s: str, cls: str = "t", anchor: str = "start", extra: str = "") -> str:
    s = s.replace("&", "&amp;").replace("<", "&lt;")
    return f'<text x="{x:.1f}" y="{y:.1f}" class="{cls}" text-anchor="{anchor}" {extra}>{s}</text>'


def legend(x: float, y: float, items: Sequence[tuple[str, str]], gap: int = 26) -> str:
    out, cx = [], x
    for label, color in items:
        out.append(
            f'<rect x="{cx:.1f}" y="{y - 9:.1f}" width="12" height="12" rx="3" fill="{color}"/>'
        )
        out.append(text(cx + 18, y + 1, label))
        cx += 18 + sum(12.5 if ord(ch) > 0x3000 else 7.2 for ch in label) + gap
    return "".join(out)


def year_frac(d: str) -> float:
    y, m, dd = (int(x) for x in d.split("-"))
    return y + (date(y, m, dd).timetuple().tm_yday - 1) / 365.25


# ------------------------------------------------------------------ 선 그래프


def line_chart(
    series: Sequence[tuple[str, str, Sequence[tuple[str, float]], bool]],
    *,
    title: str,
    sub: str,
    log: bool,
    y_ticks: Sequence[float],
    y_fmt: str,
    w: int = 880,
    h: int = 400,
    notes: Sequence[tuple[str, float, str, str]] = (),
) -> str:
    """series = (이름, 색, [(날짜, 값)], 면 채우기). notes = (날짜, 값, 글, 색)."""
    left, right, top, bottom = 64, 24, 96, 44
    pw, ph = w - left - right, h - top - bottom
    xs = [year_frac(d) for _, _, pts, _ in series for d, _ in pts]
    x0, x1 = min(xs), max(xs)
    ylo, yhi = min(y_ticks), max(y_ticks)

    def fy(v: float) -> float:
        if log:
            return top + ph * (
                1 - (math.log10(v) - math.log10(ylo)) / (math.log10(yhi) - math.log10(ylo))
            )
        return top + ph * (1 - (v - ylo) / (yhi - ylo))

    def fx(d: str) -> float:
        return left + pw * (year_frac(d) - x0) / (x1 - x0)

    body = [text(left, 28, title, "h"), text(left, 50, sub, "s")]
    for v in y_ticks:
        y = fy(v)
        body.append(f'<line x1="{left}" x2="{left + pw}" y1="{y:.1f}" y2="{y:.1f}" class="g"/>')
        body.append(text(left - 8, y + 4, y_fmt.format(v), anchor="end"))
    step = 2 if x1 - x0 <= 16 else 5
    for yr in range(math.ceil(x0), int(x1) + 1):
        if yr % step:
            continue
        x = left + pw * (yr - x0) / (x1 - x0)
        body.append(
            f'<line x1="{x:.1f}" x2="{x:.1f}" y1="{top}" y2="{top + ph}" class="g" opacity=".55"/>'
        )
        body.append(text(x, top + ph + 20, str(yr), anchor="middle"))
    for _name, color, pts, fill in series:
        path = " ".join(f"{fx(d):.1f},{fy(v):.1f}" for d, v in pts)
        if fill:
            base = fy(min(max(ylo, 0.0), yhi) if not log else ylo)
            body.append(
                f'<polygon points="{fx(pts[0][0]):.1f},{base:.1f} {path} {fx(pts[-1][0]):.1f},{base:.1f}"'
                f' fill="{color}" opacity=".22"/>'
            )
        body.append(
            f'<polyline points="{path}" fill="none" stroke="{color}" stroke-width="2.2" stroke-linejoin="round"/>'
        )
    for d, v, label, color in notes:
        x, y = fx(d), fy(v)
        body.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="4.5" fill="{color}"/>')
        body.append(text(x + 8, y + 16, label, "b", extra=f'fill="{color}" style="fill:{color}"'))
    body.append(legend(left, 76, [(n, c) for n, c, _, _ in series]))
    return svg(w, h, "".join(body), title)


# ------------------------------------------------------------------ 막대 그래프


def bar_panel(
    x: float,
    y: float,
    w: float,
    h: float,
    groups: Sequence[tuple[str, Sequence[float]]],
    colors: Sequence[str],
    *,
    title: str,
    vmax: float,
    fmt: str = "{:.1f}%",
    best: str = "max",
) -> str:
    body = [text(x, y, title, "b")]
    top, base = y + 22, y + h - 26
    ph = base - top
    for k in range(5):
        gy = base - ph * k / 4
        body.append(
            f'<line x1="{x}" x2="{x + w}" y1="{gy:.1f}" y2="{gy:.1f}" class="g" opacity=".7"/>'
        )
    gw = w / len(groups)
    n = len(colors)
    bw = min(46, (gw - 30) / n)
    for gi, (label, vals) in enumerate(groups):
        gx = x + gi * gw + (gw - bw * n - 6 * (n - 1)) / 2
        pick = max(vals) if best == "max" else min(vals)
        for k, v in enumerate(vals):
            bh = ph * v / vmax
            bx = gx + k * (bw + 6)
            body.append(
                f'<rect x="{bx:.1f}" y="{base - bh:.1f}" width="{bw:.1f}" height="{bh:.1f}" rx="4" fill="{colors[k]}"/>'
            )
            cls = "b" if v == pick else "t"
            body.append(text(bx + bw / 2, base - bh - 6, fmt.format(v), cls, "middle"))
        body.append(text(x + gi * gw + gw / 2, base + 18, label, anchor="middle"))
    return "".join(body)


def tradeoff() -> str:
    names = [("브레이크 없음", GRAY), ("H (평단·12개월 고점)", LIGHT), ("T (TQQQ 고점) ✔", BLUE)]
    colors = [c for _, c in names]
    body = [
        text(24, 28, "Deneb 브레이크 고르기 — 세금까지 넣은 비교 (문서 184)", "h"),
        text(24, 50, "매달 30만 원 적립 · 양도세 250만 원 공제 22% · 끝에 전부 판다고 가정", "s"),
        legend(24, 78, names),
        bar_panel(
            24,
            104,
            400,
            250,
            [("실제 2012~", (28.3, 27.3, 30.9)), ("합성 1986~", (21.1, 22.0, 20.4))],
            colors,
            title="세후 연수익 (높을수록 좋음)",
            vmax=35,
        ),
        bar_panel(
            456,
            104,
            400,
            250,
            [("실제 2012~", (52.1, 45.3, 31.7)), ("합성 1986~", (81.8, 69.3, 63.7))],
            colors,
            title="최대 낙폭 (낮을수록 좋음)",
            vmax=90,
            best="min",
        ),
    ]
    return svg(880, 370, "".join(body), "Deneb 브레이크 비교")


def crisis() -> str:
    rows = [
        ("2020 코로나 (실제)", -34.5, -17.7),
        ("2022 금리 인상 (실제)", -46.5, -26.6),
        ("1987 블랙먼데이 (합성)", -45.3, -25.1),
        ("2000~02 닷컴 (합성)", -74.7, -24.5),
        ("2008 금융위기 (합성)", -76.4, -60.0),
    ]
    w, left, top = 880, 200, 92
    pw = w - left - 70
    body = [
        text(24, 28, "위기 구간 손실 — 브레이크 없음 vs Deneb T 브레이크", "h"),
        text(24, 50, "구간 수익률 (계좌 기준가). 막대가 짧을수록 덜 잃었다", "s"),
        legend(24, 76, [("브레이크 없음", GRAY), ("T 브레이크", BLUE)]),
    ]
    for i, (label, a, b) in enumerate(rows):
        y = top + i * 54
        body.append(text(left - 14, y + 20, label, anchor="end"))
        for k, (v, c) in enumerate(((a, GRAY), (b, BLUE))):
            bw = pw * (-v) / 80
            yy = y + k * 18
            body.append(
                f'<rect x="{left}" y="{yy:.1f}" width="{bw:.1f}" height="14" rx="3" fill="{c}"/>'
            )
            body.append(text(left + bw + 8, yy + 11.5, f"{v:.1f}%", "b" if k else "t"))
    return svg(w, top + len(rows) * 54 + 10, "".join(body), "위기 구간 손실 비교")


def cruise() -> str:
    body = [
        text(24, 28, "Vega (T/v2 업비트) — 같은 규칙, 어디서 하느냐 (문서 190)", "h"),
        text(24, 50, "연수익 막대 · 괄호는 최대 낙폭. 비용 업비트 0.10% · 바이낸스 0.15%", "s"),
        bar_panel(
            24,
            78,
            470,
            270,
            [("바이낸스 달러", (24.1,)), ("바이낸스 원화 환산", (26.7,)), ("업비트 원화", (34.5,))],
            [ORANGE],
            title="BTC+ETH · 2020-03 ~ 2026-08",
            vmax=40,
        ),
        bar_panel(
            526,
            78,
            330,
            270,
            [("BTC+ETH", (21.7,)), ("+ SOL", (34.3,))],
            [ORANGE],
            title="업비트 · 2022-03 ~ 2026-08",
            vmax=40,
        ),
    ]
    mdd = [(103, 19.0), (260, 18.5), (417, 18.7)]
    for cx, v in mdd:
        body.append(text(cx + 24, 364, f"(낙폭 {v:.1f}%)", anchor="middle"))
    for cx, v in [(608, 13.4), (773, 18.8)]:
        body.append(text(cx, 364, f"(낙폭 {v:.1f}%)", anchor="middle"))
    body.append(
        text(
            24,
            392,
            "업비트가 높은 이유는 신호가 아니라 원화 가격 경로(환율 +2.6%p · 김치 프리미엄 +7%p). SOL 이득은 사후 선택이라 판정 근거로 쓰지 않는다.",
            "s",
        )
    )
    return svg(880, 404, "".join(body), "Vega 비교")


def verdicts() -> str:
    hist = (REPO / "docs" / "current" / "RESEARCH_HISTORY.md").read_text(encoding="utf-8")
    heads = [ln for ln in hist.splitlines() if ln.startswith("## ")]
    tok = re.findall(
        r"PASS|REJECT|HISTORICAL_SUPPORT|INCONCLUSIVE|DATA_INSUFFICIENT|NO_ROOM|NO_CLEAR_EDGE|축소|BRAKE_WEAK|유지",
        " ".join(heads),
    )
    groups = [
        ("기각 REJECT", tok.count("REJECT"), RED),
        ("통과 PASS", tok.count("PASS"), GREEN),
        ("역사적 지지", tok.count("HISTORICAL_SUPPORT"), TEAL),
        (
            "설계 선택 (유지·축소)",
            tok.count("유지") + tok.count("축소") + tok.count("BRAKE_WEAK"),
            PURPLE,
        ),
        (
            "판정 불가 · 여지 없음",
            sum(
                tok.count(x)
                for x in ("INCONCLUSIVE", "DATA_INSUFFICIENT", "NO_ROOM", "NO_CLEAR_EDGE")
            ),
            GRAY,
        ),
    ]
    total = sum(n for _, n, _ in groups)
    cx, cy, r, rw = 190, 200, 120, 38
    body = [
        text(24, 28, "연구 판정 — 대부분은 기각된다", "h"),
        text(24, 50, "RESEARCH_HISTORY 항목 기준 (한 항목에 판정이 여럿이면 각각 셈)", "s"),
    ]
    a0 = -math.pi / 2
    for _label, n, color in groups:
        if not n:
            continue
        a1 = a0 + 2 * math.pi * n / total
        large = 1 if a1 - a0 > math.pi else 0
        x0, y0 = cx + r * math.cos(a0), cy + r * math.sin(a0)
        x1, y1 = cx + r * math.cos(a1), cy + r * math.sin(a1)
        body.append(
            f'<path d="M{x0:.1f},{y0:.1f} A{r},{r} 0 {large} 1 {x1:.1f},{y1:.1f}" fill="none"'
            f' stroke="{color}" stroke-width="{rw}"/>'
        )
        a0 = a1
    body.append(text(cx, cy - 4, str(total), "h", "middle", 'style="font-size:34px"'))
    body.append(text(cx, cy + 22, "판정", "s", "middle"))
    for i, (label, n, color) in enumerate(groups):
        y = 120 + i * 40
        body.append(f'<rect x="380" y="{y - 13}" width="16" height="16" rx="4" fill="{color}"/>')
        body.append(text(406, y, label, "b"))
        body.append(text(406, y + 18, f"{n}건 · {100 * n / total:.0f}%"))
    return svg(640, 360, "".join(body), "연구 판정 분포")


def hero(stats: dict[str, str]) -> str:
    chips = [
        ("실거래", stats["live"]),
        ("Paper · 그림자", stats["paper"]),
        ("연구 문서", stats["docs"]),
        ("테스트 파일", stats["tests"]),
        ("커밋", stats["commits"]),
    ]
    body = [
        '<defs><linearGradient id="bg" x1="0" y1="0" x2="1" y2="1">'
        '<stop offset="0" stop-color="#0b1220"/><stop offset=".6" stop-color="#132a5e"/>'
        '<stop offset="1" stop-color="#1f6feb"/></linearGradient></defs>',
        '<rect width="880" height="220" rx="18" fill="url(#bg)"/>',
        '<polyline points="40,170 120,150 190,160 260,120 330,132 410,92 470,104 540,70 610,84 690,52 760,64 840,36"'
        ' fill="none" stroke="#58a6ff" stroke-width="2.5" opacity=".35"/>',
        f'<text x="40" y="70" style="fill:#ffffff;font:800 38px {FONT}">Auto_Trading</text>',
        f'<text x="40" y="102" style="fill:#c9d1d9;font:500 16px {FONT}">결과를 보기 전에 규칙을 고정하고, 한 번만 판정하고, 통과한 것만 소액으로 돌린다</text>',
    ]
    cx: float = 40
    for label, val in chips:
        wv = 30 + sum(13.5 if ord(ch) > 0x3000 else 7.8 for ch in f"{label} {val}")
        body.append(
            f'<rect x="{cx}" y="140" width="{wv:.0f}" height="34" rx="17" fill="#ffffff" opacity=".12"/>'
        )
        body.append(
            f'<text x="{cx + 14}" y="162" style="fill:#e6edf3;font:600 13px {FONT}">{label} <tspan style="fill:#79c0ff">{val}</tspan></text>'
        )
        cx += wv + 10
    return svg(880, 220, "".join(body), "Auto_Trading")


def glide_curves() -> tuple[str, str]:
    import scripts.run_brake_184 as m
    from scripts.run_brake_183 import data_real, data_synth

    def runs(data: dict[str, Any], q: dict[str, float]) -> dict[str, list[tuple[str, float]]]:
        out = {}
        for k, kw in (("M0", {"ref": None}), ("T", {"ref": "T", "park": "cash"})):
            s = m.simulate(data, q, **kw)
            out[k] = [
                (d, v)
                for i, (d, v) in enumerate(zip(s["nav_days"], s["navs"], strict=True))
                if i % 5 == 0
            ]
        return out

    r = data_real()
    rr = runs(r, m.qqq_real())
    eq = line_chart(
        [("브레이크 없음", GRAY, rr["M0"], False), ("Deneb (T 브레이크)", BLUE, rr["T"], False)],
        title="Deneb 계좌 기준가 — 실제 TQQQ + SCHD (2012 ~ 2026-08, 로그)",
        sub="매달 적립 · 변동성 비율 · 세전. 같은 돈을 넣어도 2020 · 2022 폭락에서 덜 잃은 만큼 앞선다",
        log=True,
        y_ticks=(1, 2, 5, 10, 20, 50, 100),
        y_fmt="{:g}배",
    )
    s = data_synth()
    ss = runs(s, m.qqq_synth(s["days"], s["rf"]))

    def dd(pts: list[tuple[str, float]]) -> list[tuple[str, float]]:
        peak, out = 0.0, []
        for d, v in pts:
            peak = max(peak, v)
            out.append((d, (v / peak - 1) * 100))
        return out

    a, b = dd(ss["M0"]), dd(ss["T"])
    la, lb = min(a, key=lambda p: p[1]), min(b, key=lambda p: p[1])
    dch = line_chart(
        [("브레이크 없음", GRAY, a, True), ("Deneb (T 브레이크)", BLUE, b, False)],
        title="고점 대비 낙폭 — 합성 40년 (1986 ~ 2026-08)",
        sub="닷컴 · 금융위기가 들어 있는 스트레스 시험. 바닥이 얕을수록 버티기 쉽다",
        log=False,
        y_ticks=(-90, -75, -60, -45, -30, -15, 0),
        y_fmt="{:.0f}%",
        notes=[(la[0], la[1], f"{la[1]:.0f}%", GRAY), (lb[0], lb[1], f"{lb[1]:.0f}%", BLUE)],
    )
    return eq, dch


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    stats = {"live": "2", "paper": "12", "docs": "190+", "tests": "290+", "commits": "720+"}
    files = {
        "hero.svg": hero(stats),
        "glide_tradeoff.svg": tradeoff(),
        "glide_crisis.svg": crisis(),
        "cruise_compare.svg": cruise(),
        "research_verdicts.svg": verdicts(),
    }
    eq, dch = glide_curves()
    files["glide_equity.svg"] = eq
    files["glide_drawdown.svg"] = dch
    for name, body in files.items():
        (OUT / name).write_bytes(body.encode("utf-8"))
        print(OUT / name)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
