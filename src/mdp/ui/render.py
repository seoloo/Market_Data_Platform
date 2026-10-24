"""상태 화면 그리기 — :class:`~mdp.ui.data.PageData` → HTML 한 장.

글자 · 표 · SVG 그래프만, 외부 파일 없음.

언제 쓰나: :mod:`mdp.ui.report` 가 부른다. 순수 함수 — 파일 · 네트워크 · 시계를 만지지 않는다.
읽고 쓰는 것: 없음.

그리는 것 (위에서 아래로):
1. 한눈에 — 마지막 실행 · 표 수 · 행 수 · 대사 일치율
2. 작업별 마지막 실행 (상태는 아이콘 + 글자, 색만으로 말하지 않는다)
3. Delta 표 — 버전 · 행 수 · 마지막 커밋
4. Silver 품질 — 날 대조 · 격리 · 빈 곳
5. Gold = 공식 1분봉 — 날마다 칸마다 같은 분 비율 (막대)
6. 매일 대사 — 날마다 분 구성 (100% 누적 막대 + 범례 + 표)
7. 최근 실행 기록

[이유] 값 · 이름은 모두 이스케이프한다(표 이름 · 오류 문구가 HTML 로 해석되지 않게).
       색은 역할 토큰으로만 쓰고 밝은 · 어두운 화면 값을 따로 둔다. 범주 색은 정해진 순서로만
       붙이고(돌려 쓰지 않음), 상태 색은 상태에만 쓴다.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from datetime import datetime
from decimal import Decimal
from html import escape
from typing import Any, Final

from mdp.ui.data import PageData, Section

__all__ = ["RECONCILE_PARTS", "render"]

RECONCILE_PARTS: Final = (
    ("matched", "같음 (스트림이 맞힘)"),
    ("repaired", "메움 (배치 봉으로)"),
    ("stream_gap", "끊김"),
    ("late", "늦음"),
    ("duplicate", "중복"),
    ("other", "기타"),
)
"""대사 분 구성 — 이 순서대로 범주 색 1 ~ 6 번을 붙인다."""

FOLD_AFTER: Final = 12
"""이보다 긴 표는 '표 보기' 로 접는다."""

STATUS: Final = {
    "succeeded": ("good", "✓", "성공"),
    "failed": ("critical", "✕", "실패"),
    "running": ("warning", "●", "도는 중"),
    "normal_day": ("good", "✓", "정상"),
    "suspect_day": ("serious", "⚠", "의심"),
}

CSS: Final = """
:root {
  color-scheme: light;
  --page: #f9f9f7; --surface-1: #fcfcfb; --text-primary: #0b0b0b; --text-secondary: #52514e;
  --text-muted: #898781; --grid: #e1e0d9; --axis: #c3c2b7; --border: rgba(11,11,11,0.10);
  --series-1: #2a78d6; --series-2: #eb6834; --series-3: #1baf7a; --series-4: #eda100;
  --series-5: #e87ba4; --series-6: #008300;
  --good: #0ca30c; --warning: #fab219; --serious: #ec835a; --critical: #d03b3b;
}
@media (prefers-color-scheme: dark) {
  :root:not([data-theme="light"]) {
    color-scheme: dark;
    --page: #0d0d0d; --surface-1: #1a1a19; --text-primary: #f0efec; --text-secondary: #c3c2b7;
    --text-muted: #898781; --grid: #2c2c2a; --axis: #383835; --border: rgba(255,255,255,0.10);
    --series-1: #3987e5; --series-2: #d95926; --series-3: #199e70; --series-4: #c98500;
    --series-5: #d55181; --series-6: #008300;
  }
}
:root[data-theme="dark"] {
  color-scheme: dark;
  --page: #0d0d0d; --surface-1: #1a1a19; --text-primary: #f0efec; --text-secondary: #c3c2b7;
  --text-muted: #898781; --grid: #2c2c2a; --axis: #383835; --border: rgba(255,255,255,0.10);
  --series-1: #3987e5; --series-2: #d95926; --series-3: #199e70; --series-4: #c98500;
  --series-5: #d55181; --series-6: #008300;
}
* { box-sizing: border-box; }
body { margin: 0; background: var(--page); color: var(--text-primary);
  font: 14px/1.5 system-ui, -apple-system, "Segoe UI", sans-serif; }
main { max-width: 1120px; margin: 0 auto; padding: 24px 16px 48px; }
header h1 { font-size: 22px; margin: 0 0 4px; }
header p { margin: 0; color: var(--text-secondary); }
section { background: var(--surface-1); border: 1px solid var(--border); border-radius: 10px;
  padding: 16px 18px; margin-top: 16px; }
section h2 { font-size: 16px; margin: 0 0 4px; }
section > p.note { margin: 0 0 12px; color: var(--text-secondary); }
.tiles { display: grid; grid-template-columns: repeat(auto-fit, minmax(170px, 1fr)); gap: 12px;
  margin-top: 16px; }
.tile { background: var(--surface-1); border: 1px solid var(--border); border-radius: 10px;
  padding: 12px 14px; }
.tile .label { color: var(--text-secondary); font-size: 12px; }
.tile .value { font-size: 24px; font-weight: 600; margin-top: 2px; }
.tile .sub { color: var(--text-muted); font-size: 12px; }
.scroll { overflow-x: auto; }
table { border-collapse: collapse; width: 100%; font-size: 13px; }
th, td { text-align: left; padding: 6px 10px 6px 0; border-bottom: 1px solid var(--grid);
  white-space: nowrap; }
th { color: var(--text-secondary); font-weight: 600; }
td.num, th.num { text-align: right; font-variant-numeric: tabular-nums; }
.badge { display: inline-flex; align-items: center; gap: 4px; }
.badge .dot { font-weight: 700; }
.badge.good .dot { color: var(--good); } .badge.critical .dot { color: var(--critical); }
.badge.warning .dot { color: var(--warning); } .badge.serious .dot { color: var(--serious); }
.empty { color: var(--text-muted); font-style: italic; }
.legend { display: flex; flex-wrap: wrap; gap: 6px 16px; margin: 4px 0 10px;
  color: var(--text-secondary); font-size: 12px; }
.legend i { display: inline-block; width: 10px; height: 10px; border-radius: 3px;
  margin-right: 6px; vertical-align: -1px; }
svg { display: block; width: 100%; height: auto; }
svg text { fill: var(--text-muted); font-size: 11px; }
svg .mark { transition: opacity .1s; }
svg .mark:hover { opacity: .75; }
code { font-size: 12px; }
details > summary { cursor: pointer; color: var(--text-secondary); margin: 6px 0; }
footer { margin-top: 20px; color: var(--text-muted); font-size: 12px; }
"""


def _fmt(value: Any) -> str:
    """[쓰임] 칸 값 하나를 글자로 (없음은 '—', 시각은 UTC 분까지, 큰 수는 쉼표)."""
    if value is None:
        return "—"
    if isinstance(value, bool):
        return "예" if value else "아니오"
    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%d %H:%M UTC")
    if isinstance(value, int):
        return f"{value:,}"
    if isinstance(value, (float, Decimal)):
        return f"{float(value):,.4g}" if abs(float(value)) < 1000 else f"{float(value):,.0f}"
    return str(value)


def _cell(key: str, value: Any) -> str:
    """[쓰임] 칸 이름에 맞춘 글자 — 걸린 초는 소수 한 자리, 코드 버전은 커밋 앞 7 자."""
    if value is None:
        return "—"
    if key == "seconds":
        return f"{float(value):,.1f}"
    if key == "code_version":
        text = str(value)
        return text[:7] + ("-dirty" if text.endswith("-dirty") else "") if len(text) >= 40 else text
    return _fmt(value)


def _badge(status: str) -> str:
    role, icon, word = STATUS.get(status, ("serious", "?", status))
    dot = f'<span class="dot" aria-hidden="true">{icon}</span>'
    return f'<span class="badge {role}">{dot}{escape(word)}</span>'


def _table(
    rows: Sequence[dict[str, Any]],
    columns: Sequence[tuple[str, str]],
    *,
    numeric: Iterable[str] = (),
) -> str:
    """[쓰임] 줄 목록 → 표. ``columns`` 는 (칸 이름, 머리글)."""
    if not rows:
        return '<p class="empty">아직 기록이 없다.</p>'
    nums = set(numeric)
    head = "".join(
        f'<th class="num">{escape(h)}</th>' if k in nums else f"<th>{escape(h)}</th>"
        for k, h in columns
    )
    body = []
    for row in rows:
        cells = []
        for key, _ in columns:
            value = row.get(key)
            if key == "status":
                cells.append(f"<td>{_badge(str(value))}</td>")
            elif key == "suspect":
                cells.append(f"<td>{_badge('suspect_day' if value else 'normal_day')}</td>")
            else:
                cls = ' class="num"' if key in nums else ""
                cells.append(f"<td{cls}>{escape(_cell(key, value))}</td>")
        body.append("<tr>" + "".join(cells) + "</tr>")
    table = f"<table><thead><tr>{head}</tr></thead><tbody>{''.join(body)}</tbody></table>"
    wrapped = f'<div class="scroll">{table}</div>'
    if len(rows) > FOLD_AFTER:  # 긴 표는 접어 둔다 — 그래프가 요약이고 표는 확인용
        return f"<details><summary>표 보기 ({len(rows):,} 줄)</summary>{wrapped}</details>"
    return wrapped


def _section(title: str, note: str, inner: str) -> str:
    return f'<section><h2>{escape(title)}</h2><p class="note">{escape(note)}</p>{inner}</section>'


def _or_error(section: Section, draw: str) -> str:
    return f'<p class="empty">{escape(section.error)}</p>' if section.error else draw


def _rounded_top(x: float, y: float, w: float, h: float, r: float = 4.0) -> str:
    """[쓰임] 위쪽 두 모서리만 둥근 막대 (바닥선에 붙은 쪽은 각지게)."""
    r = min(r, w / 2, h)
    left_side = f"M{x:.1f},{y + h:.1f} V{y + r:.1f} Q{x:.1f},{y:.1f} {x + r:.1f},{y:.1f}"
    right_side = f"H{x + w - r:.1f} Q{x + w:.1f},{y:.1f} {x + w:.1f},{y + r:.1f} V{y + h:.1f} Z"
    return f"{left_side} {right_side}"


def _bar_chart(points: Sequence[tuple[str, float, str]], *, max_value: float, unit: str) -> str:
    """[쓰임] 세로 막대 하나짜리 그래프 — (이름, 값, 풍선 글) 목록. 축은 0 ~ ``max_value``."""
    if not points:
        return ""
    width, height, left, bottom, top = 720, 180, 40, 28, 10
    plot_w, plot_h = width - left - 8, height - top - bottom
    step = plot_w / len(points)
    bar_w = max(2.0, min(28.0, step * 0.6))
    parts = [f'<svg viewBox="0 0 {width} {height}" role="img" aria-label="막대 그래프">']
    for frac in (0, 0.5, 1):
        y = top + plot_h * (1 - frac)
        stroke = "var(--axis)" if frac == 0 else "var(--grid)"
        parts.append(
            f'<line x1="{left}" x2="{width - 8}" y1="{y:.1f}" y2="{y:.1f}"'
            f' stroke="{stroke}" stroke-width="1"/>'
        )
        parts.append(
            f'<text x="{left - 6}" y="{y + 4:.1f}" text-anchor="end">'
            f"{_fmt(max_value * frac)}{escape(unit)}</text>"
        )
    every = max(1, len(points) // 10)
    for i, (label, value, tip) in enumerate(points):
        h = plot_h * max(0.0, min(value, max_value)) / max_value if max_value else 0
        x = left + step * i + (step - bar_w) / 2
        y = top + plot_h - h
        parts.append(
            f'<path class="mark" d="{_rounded_top(x, y, bar_w, max(h, 0.5))}"'
            ' fill="var(--series-1)">'
            f"<title>{escape(tip)}</title></path>"
        )
        if i % every == 0:
            parts.append(
                f'<text x="{x + bar_w / 2:.1f}" y="{height - 10}" text-anchor="middle">'
                f"{escape(label)}</text>"
            )
    parts.append("</svg>")
    return "".join(parts)


def _segment_widths(values: Sequence[float], width: float, min_w: float = 5.0) -> list[float]:
    """[쓰임] 누적 막대 칸 너비 — 0 이 아닌 칸은 적어도 ``min_w`` px (작은 원인이 사라지지 않게).
    [이유] 1,440 분 중 6 분(0.4%)은 비례로 그리면 3px 도 안 돼 보이지 않는다. 작은 칸을 늘린 만큼
           큰 칸에서 비례로 줄인다. 정확한 값은 풍선 글 · 표에 있다."""
    total = sum(values) or 1.0
    raw = [width * v / total for v in values]
    small = [v > 0 and r < min_w for v, r in zip(values, raw, strict=True)]
    taken = min_w * sum(small)
    big_total = sum(r for r, s in zip(raw, small, strict=True) if not s) or 1.0
    scale = (width - taken) / big_total
    return [min_w if s else r * scale for r, s in zip(raw, small, strict=True)]


def _stacked_rows(rows: Sequence[tuple[str, Sequence[tuple[int, str, float]]]]) -> str:
    """[쓰임] 가로 100% 누적 막대 — 줄마다 (이름, [(색 번호, 이름, 값)]). 칸 사이 2px 틈."""
    if not rows:
        return ""
    width, label_w, row_h, gap = 720, 150, 22, 10
    height = len(rows) * (row_h + gap)
    plot_w = width - label_w - 8
    parts = [f'<svg viewBox="0 0 {width} {height}" role="img" aria-label="누적 막대 그래프">']
    for r, (label, segments) in enumerate(rows):
        y = r * (row_h + gap)
        parts.append(f'<text x="0" y="{y + row_h / 2 + 4:.1f}">{escape(label)}</text>')
        total = sum(v for _, _, v in segments) or 1
        x = float(label_w)
        widths = _segment_widths([v for _, _, v in segments], plot_w)
        for (slot, name, value), w in zip(segments, widths, strict=True):
            if value <= 0:
                continue
            seg_w = max(w - 2, 1)  # 2px 틈
            parts.append(
                f'<rect class="mark" x="{x:.1f}" y="{y}" width="{seg_w:.1f}" height="{row_h}"'
                f' rx="3" fill="var(--series-{slot})"><title>{escape(label)} · {escape(name)}'
                f" {value:,.0f}분 ({100 * value / total:.1f}%)</title></rect>"
            )
            x += w
    parts.append("</svg>")
    return "".join(parts)


def _legend(items: Sequence[tuple[int, str]]) -> str:
    return (
        '<div class="legend">'
        + "".join(
            f'<span><i style="background: var(--series-{slot})"></i>{escape(name)}</span>'
            for slot, name in items
        )
        + "</div>"
    )


def _tiles(data: PageData, reconcile: Sequence[dict[str, Any]]) -> str:
    jobs = data.sections["jobs"].rows
    last = max(jobs, key=lambda r: r["started_at"]) if jobs else None
    rows_by_table = {f"{t.layer}/{t.name}": t.rows for t in data.tables}
    latest_rate = max(reconcile, key=lambda r: r["reconciled_at"]) if reconcile else None
    tiles = [
        (
            "마지막 실행",
            _badge(last["status"]) if last else "—",
            escape(f"{last['job']} · {_fmt(last['started_at'])}") if last else "실행 기록 없음",
        ),
        ("Delta 표", escape(_fmt(len(data.tables))), "Silver · Gold"),
        ("Silver 체결", escape(_fmt(rows_by_table.get("silver/agg_trades"))), "깨끗한 체결 행"),
        ("Gold 1분봉", escape(_fmt(rows_by_table.get("gold/bars_1m"))), "배치 봉"),
        (
            "대사 일치율 (최근)",
            escape(f"{float(latest_rate['match_rate']) * 100:.1f}%") if latest_rate else "—",
            escape(f"{latest_rate['symbol']} {latest_rate['trade_date']} · 스트림 몫")
            if latest_rate
            else "대사 기록 없음",
        ),
    ]
    return (
        '<div class="tiles">'
        + "".join(
            f'<div class="tile"><div class="label">{escape(label)}</div>'
            f'<div class="value">{value}</div><div class="sub">{sub}</div></div>'
            for label, value, sub in tiles
        )
        + "</div>"
    )


def render(data: PageData) -> str:
    """[쓰임] 화면 HTML 한 장."""
    s = data.sections
    lake = data.lake.replace("\\", "/")  # Windows 경로의 역슬래시가 한글 글꼴에서 ₩ 로 보이지 않게
    lake_symbols = {r["symbol"] for r in s["silver_daily"].rows} | {
        r["symbol"] for r in s["kline_check"].rows
    }
    reconcile_rows = [
        r for r in s["reconcile"].rows if not lake_symbols or r["symbol"] in lake_symbols
    ]
    reconcile = Section(rows=reconcile_rows, error=s["reconcile"].error)  # 이 레이크의 종목만

    tables_html = (
        _table(
            [
                {
                    "table": f"{t.layer}/{t.name}",
                    "version": t.version,
                    "rows": t.rows,
                    "op": t.operation,
                    "at": t.committed_at,
                }
                for t in data.tables
            ],
            [
                ("table", "표"),
                ("version", "버전"),
                ("rows", "행"),
                ("op", "마지막 작업"),
                ("at", "마지막 커밋"),
            ],
            numeric=("version", "rows"),
        )
        if data.tables
        else '<p class="empty">레이크에 Delta 표가 없다.</p>'
    )

    kline = s["kline_check"].rows
    kline_chart = _bar_chart(
        [
            (
                f"{r['trade_date']:%m-%d}",
                100 * r["all_fields_equal"] / r["gold_minutes"] if r["gold_minutes"] else 0,
                f"{r['symbol']} {r['trade_date']} — {r['all_fields_equal']:,}"
                f" / {r['gold_minutes']:,}분 칸마다 같음",
            )
            for r in kline
        ],
        max_value=100,
        unit="%",
    )
    stacked = _stacked_rows(
        [
            (
                f"{r['symbol']} {r['trade_date']}",
                [
                    (i + 1, name, float(r[key] or 0))
                    for i, (key, name) in enumerate(RECONCILE_PARTS)
                ],
            )
            for r in reconcile_rows
        ]
    )
    body = [
        "<header><h1>Market Data Platform — 파이프라인 상태</h1>"
        f"<p>{escape(_fmt(data.generated_at))} 기준 · 레이크 <code>{escape(lake)}</code></p>"
        "</header>",
        _tiles(data, reconcile_rows),
        _section(
            "작업별 마지막 실행",
            "실행 기록(PostgreSQL pipeline_runs) — 작업마다 가장 최근 한 번.",
            _or_error(
                s["jobs"],
                _table(
                    s["jobs"].rows,
                    [
                        ("job", "작업"),
                        ("status", "상태"),
                        ("started_at", "시작"),
                        ("seconds", "걸린 초"),
                        ("runs", "실행 수"),
                        ("code_version", "코드 버전"),
                    ],
                    numeric=("seconds", "runs"),
                ),
            ),
        ),
        _section(
            "Delta 표",
            "Silver · Gold 표의 최신 버전과 행 수 (DuckDB 로 읽음, Spark 없이).",
            tables_html,
        ),
        _section(
            "Silver 품질 — 날 대조",
            "그날 거래량을 공식 1분봉 합계와 비교. 0.5% 넘게 다르거나 번호가 비면 의심일.",
            _or_error(
                s["silver_daily"],
                _table(
                    s["silver_daily"].rows,
                    [
                        ("symbol", "종목"),
                        ("trade_date", "날짜"),
                        ("rows", "체결"),
                        ("suspect", "판정"),
                        ("volume_diff_pct", "거래량 차 %"),
                        ("missing_ids_inside_day", "빠진 번호"),
                    ],
                    numeric=("rows", "volume_diff_pct", "missing_ids_inside_day"),
                ),
            ),
        ),
        _section(
            "Silver 품질 — 격리 · 빈 곳",
            "못 쓰는 줄은 이유와 함께 격리 표로, 번호가 빈 곳은 위치와 함께.",
            _or_error(
                s["quarantine"],
                _table(
                    s["quarantine"].rows,
                    [("symbol", "종목"), ("reason", "이유"), ("rows", "줄")],
                    numeric=("rows",),
                ),
            )
            + _or_error(
                s["gaps"],
                _table(
                    s["gaps"].rows,
                    [
                        ("symbol", "종목"),
                        ("kind", "종류"),
                        ("prev_id", "앞 번호"),
                        ("next_id", "뒤 번호"),
                        ("missing_ids", "빠진 수"),
                    ],
                    numeric=("prev_id", "next_id", "missing_ids"),
                ),
            ),
        ),
        _section(
            "Gold 1분봉 = 공식 1분봉?",
            "날마다 시고저종 · 거래량 · 체결 수가 모두 같은 분의 비율."
            " 분 경계 배정이 달라 100%가 아닐 수 있다(연구 216).",
            _or_error(
                s["kline_check"],
                kline_chart
                + _table(
                    kline,
                    [
                        ("symbol", "종목"),
                        ("trade_date", "날짜"),
                        ("gold_minutes", "Gold 분"),
                        ("all_fields_equal", "칸마다 같음"),
                        ("ohlc_equal", "시고저종 같음"),
                        ("volume_equal", "거래량 같음"),
                    ],
                    numeric=("gold_minutes", "all_fields_equal", "ohlc_equal", "volume_equal"),
                ),
            ),
        ),
        _section(
            "매일 대사 — 스트림 봉 = 배치 봉?",
            "그날 배치 분을 스트림이 맞힌 분 · 메운 분 · 원인별로 나눔 (그날 마지막 대사).",
            _or_error(
                reconcile,
                (
                    _legend([(i + 1, name) for i, (_, name) in enumerate(RECONCILE_PARTS)])
                    + stacked
                    if reconcile_rows
                    else ""
                )
                + _table(
                    reconcile_rows,
                    [
                        ("symbol", "종목"),
                        ("trade_date", "날짜"),
                        ("matched", "같음"),
                        ("repaired", "메움"),
                        ("stream_gap", "끊김"),
                        ("late", "늦음"),
                        ("duplicate", "중복"),
                        ("other", "기타"),
                        ("trades_not_received", "못 받은 체결"),
                        ("trades_late", "늦은 체결"),
                        ("stream_source", "원천"),
                    ],
                    numeric=(
                        "matched",
                        "repaired",
                        "stream_gap",
                        "late",
                        "duplicate",
                        "other",
                        "trades_not_received",
                        "trades_late",
                    ),
                ),
            ),
        ),
        _section(
            "스트림 1분봉",
            "Kafka → Spark Structured Streaming 이 확정한 봉."
            " 끊김 표시(묶음 번호가 이어지지 않음) · 메운 봉 수.",
            _or_error(
                s["stream"],
                _table(
                    s["stream"].rows,
                    [
                        ("symbol", "종목"),
                        ("trade_date", "날짜"),
                        ("bars", "봉"),
                        ("broken_bars", "끊김 표시"),
                        ("repaired_bars", "메운 봉"),
                    ],
                    numeric=("bars", "broken_bars", "repaired_bars"),
                ),
            ),
        ),
        _section(
            "최근 실행 기록",
            "최근 25 번 — 실패하면 오류 첫 줄.",
            _or_error(
                s["runs"],
                _table(
                    s["runs"].rows,
                    [
                        ("started_at", "시작"),
                        ("job", "작업"),
                        ("symbol", "종목"),
                        ("status", "상태"),
                        ("seconds", "걸린 초"),
                        ("error", "오류"),
                    ],
                    numeric=("seconds",),
                ),
            ),
        ),
        "<footer>만든 곳: <code>python -m mdp.ui.report</code> — 읽기만 한다."
        " 실주문 · 실계좌 기능은 없다.</footer>",
    ]
    return (
        '<!doctype html><html lang="ko"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        f"<title>파이프라인 상태</title><style>{CSS}</style></head><body><main>"
        + "".join(body)
        + "</main></body></html>"
    )
