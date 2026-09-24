"""서버에서 그리는 SVG 선 차트 — 외부 라이브러리·CDN 없음 (오프라인 동작)."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from html import escape

__all__ = ["Series", "line_chart"]


@dataclass(frozen=True, slots=True)
class Series:
    name: str
    color: str
    """CSS 색 (변수 가능: ``var(--t2)``)."""
    points: tuple[tuple[int, float], ...]
    """(UTC ms, 값). 시각 오름차순."""


def _label(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, tz=UTC).strftime("%m.%d")


def line_chart(series: Sequence[Series], *, width: int = 880, height: int = 250) -> str:
    """여러 선을 같은 축에 그린다. 두 점 미만이면 안내 문구를 그린다."""
    drawable = [s for s in series if len(s.points) >= 2]
    if not drawable:
        days = max((len(s.points) for s in series), default=0)
        return f'<div class="chart-empty">기록 {days}일 — 선은 2일째부터 그려진다.</div>'
    left, right, top, bottom = 52, 70, 12, 28
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
            f'<text x="{left - 8}" y="{y + 4:.1f}" class="axis" text-anchor="end">{v:.1f}</text>'
        )
    ticks: dict[str, int] = {}
    for t in (x0, x0 + span_x // 2, x1):  # 같은 날짜 글자가 겹치지 않게
        ticks.setdefault(_label(t), t)
    for t in ticks.values():
        parts.append(
            f'<text x="{px(t):.1f}" y="{height - 8}" class="axis" '
            f'text-anchor="middle">{_label(t)}</text>'
        )
    for s in drawable:
        pts = " ".join(f"{px(t):.1f},{py(v):.1f}" for t, v in s.points)
        last_t, last_v = s.points[-1]
        parts.append(
            f'<polyline points="{pts}" fill="none" stroke="{s.color}" stroke-width="2.2" '
            f'stroke-linejoin="round"><title>{escape(s.name)}</title></polyline>'
            f'<circle cx="{px(last_t):.1f}" cy="{py(last_v):.1f}" r="3.5" fill="{s.color}"/>'
            f'<text x="{px(last_t) + 8:.1f}" y="{py(last_v) + 4:.1f}" class="end" '
            f'fill="{s.color}">{last_v:.2f}</text>'
        )
    parts.append("</svg>")
    return "".join(parts)
