"""선 차트 이름표 · 범례 · 큰 금액 눈금 · 끝 숫자 겹침 방지 (가짜 자료)."""

from __future__ import annotations

import re
from pathlib import Path

from auto_trading.ui.charts import Series, line_chart, won_short
from auto_trading.ui.kr_shadow_render import kospi_ma

DAY = 86_400_000


def _s(name: str, vals: list[float], tag: str = "") -> Series:
    return Series(name, "var(--c1)", tuple((i * DAY, v) for i, v in enumerate(vals)), tag=tag)


def test_legend_tags_and_won_axis() -> None:
    html = line_chart(
        [
            _s("① 가격 규칙", [10_000_000, 10_000_000], "①"),
            _s("비교", [10_000_000, 8_642_881], "비교"),
        ],
        fmt=won_short,
        show_legend=True,
    )
    assert 'class="chart-legend"' in html and "① 가격 규칙" in html
    assert "① 1,000만" in html and "비교 864만" in html
    assert "203567.8" not in html  # 예전엔 큰 금액이 왼쪽에서 잘려 보였다


def test_end_labels_do_not_overlap() -> None:
    html = line_chart([_s("a", [1, 93.84], "a"), _s("b", [1, 93.80], "b")])
    ys = sorted(float(y) for y in re.findall(r'y="([\d.]+)" class="end"', html))
    assert len(ys) == 2 and ys[1] - ys[0] >= 14


def test_kospi_moving_average(tmp_path: Path) -> None:
    base = tmp_path / "data" / "kr_shadow"
    base.mkdir(parents=True)
    rows = ["date,close"] + [f"2026{1 + i // 28:02d}{1 + i % 28:02d},{100 + i}" for i in range(100)]
    base.joinpath("kospi.csv").write_text("\n".join(rows) + "\n", encoding="utf-8")
    ma = kospi_ma(tmp_path, 60, days=5)
    assert len(ma) == 5 and abs(ma[-1][1] - sum(range(140, 200)) / 60) < 1e-9
