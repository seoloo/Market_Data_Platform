"""서버에서 그리는 SVG 선 차트 — 외부 라이브러리·CDN 없음 (오프라인 동작)."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from html import escape

__all__ = ["Series", "legend", "line_chart", "won_short"]


@dataclass(frozen=True, slots=True)
class Series:
    name: str
    color: str
    """CSS 색 (변수 가능: ``var(--t2)``)."""
    points: tuple[tuple[int, float], ...]
    """(UTC ms, 값). 시각 오름차순."""
    tag: str = ""
    """선 끝 숫자 앞에 붙일 짧은 이름 (예: ``①``). 비우면 숫자만."""


def _label(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, tz=UTC).strftime("%m.%d")


def _fmt2(v: float) -> str:
    return f"{v:.2f}"


def won_short(v: float) -> str:
    """원 금액을 짧게 — 1,000 만 원 → '1,000만'."""
    return f"{v / 10_000:,.0f}만"


def legend(series: Sequence[Series]) -> str:
    """선 색 · 이름 범례 (HTML)."""
    items = "".join(
        f'<span><i style="background:{s.color}"></i>{escape(s.name)}</span>' for s in series
    )
    return f'<div class="chart-legend">{items}</div>'


def _spread(ys: list[float], gap: float, lo: float, hi: float) -> list[float]:
    """끝 숫자 글자가 겹치지 않게 세로 위치를 최소 ``gap`` 만큼 벌린다 (순서는 유지)."""
    order = sorted(range(len(ys)), key=lambda i: ys[i])
    out = list(ys)
    prev = None
    for i in order:
        y = max(out[i], prev + gap) if prev is not None else out[i]
        out[i] = y
        prev = y
    over = (prev or 0) - hi
    if over > 0:  # 아래로 넘치면 전체를 위로
        out = [max(lo, y - over) for y in out]
    return out


def line_chart(
    series: Sequence[Series],
    *,
    width: int = 880,
    height: int = 250,
    fmt: Callable[[float], str] = _fmt2,
    show_legend: bool = False,
) -> str:
    """여러 선을 같은 축에 그린다. 두 점 미만이면 안내 문구를 그린다.

    ``fmt`` 는 세로 눈금 · 선 끝 숫자 모양(큰 금액은 :func:`won_short`), ``show_legend`` 는
    선 이름 범례를 그래프 위에 붙인다. 선 끝 숫자는 서로 겹치지 않게 벌린다."""
    drawable = [s for s in series if len(s.points) >= 2]
    head = legend(series) if show_legend and series else ""
    if not drawable:
        days = max((len(s.points) for s in series), default=0)
        return head + f'<div class="chart-empty">기록 {days}일 — 선은 2일째부터 그려진다.</div>'
    longest = max(len(fmt(v)) for s in drawable for _, v in s.points)
    tag_w = max((len(s.tag) for s in drawable), default=0)
    left, right, top, bottom = max(52, 8 + 7 * longest), max(70, 20 + 8 * (longest + tag_w)), 12, 28
    xs = [t for s in drawable for t, _ in s.points]
    ys = [v for s in drawable for _, v in s.points]
    x0, x1 = min(xs), max(xs)
    lo, hi = min(ys), max(ys)
    pad = max((hi - lo) * 0.15, 0.5)
    lo, hi = lo - pad, hi + pad
    span_x = max(x1 - x0, 1)
    plot_w, plot_h = width - left - right, height - top - bottom

    def px(t: int) -> float:
        return left + (t - x0) / span_x * plot_w

    def py(v: float) -> float:
        return top + (hi - v) / (hi - lo) * plot_h

    parts = [
        f'<svg class="chart" viewBox="0 0 {width} {height}" role="img" aria-label="자산 변화 차트">'
    ]
    for k in range(5):  # 가로 눈금
        v = lo + (hi - lo) * k / 4
        y = py(v)
        parts.append(
            f'<line x1="{left}" x2="{width - right}" y1="{y:.1f}" y2="{y:.1f}" class="grid"/>'
            f'<text x="{left - 8}" y="{y + 4:.1f}" class="axis" text-anchor="end">{fmt(v)}</text>'
        )
    ticks: dict[str, int] = {}
    for t in (x0, x0 + span_x // 2, x1):  # 같은 날짜 글자가 겹치지 않게
        ticks.setdefault(_label(t), t)
    for t in ticks.values():
        parts.append(
            f'<text x="{px(t):.1f}" y="{height - 8}" class="axis" '
            f'text-anchor="middle">{_label(t)}</text>'
        )
    label_y = _spread([py(s.points[-1][1]) for s in drawable], 14.0, top + 4, height - bottom)
    for s, ly in zip(drawable, label_y, strict=True):
        pts = " ".join(f"{px(t):.1f},{py(v):.1f}" for t, v in s.points)
        last_t, last_v = s.points[-1]
        tag = f"{escape(s.tag)} " if s.tag else ""
        parts.append(
            f'<polyline points="{pts}" fill="none" stroke="{s.color}" stroke-width="2.2" '
            f'stroke-linejoin="round"><title>{escape(s.name)}</title></polyline>'
            f'<circle cx="{px(last_t):.1f}" cy="{py(last_v):.1f}" r="3.5" fill="{s.color}"/>'
            f'<text x="{px(last_t) + 8:.1f}" y="{ly + 4:.1f}" class="end" '
            f'fill="{s.color}">{tag}{fmt(last_v)}</text>'
        )
    parts.append("</svg>")
    return head + "".join(parts)
