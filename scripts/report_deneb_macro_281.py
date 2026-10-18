"""Render saved research 281 results only; never train or rerun any account."""

from __future__ import annotations

import json
import math
from collections import defaultdict
from decimal import Decimal
from html import escape
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parent.parent
OUT = REPO / "reports/deneb_macro_281"
COLORS = {"A_DENEB": "#155c4d", "B_STATIC": "#bb7a19", "C_MACRO": "#326ac3"}
NAMES = {"A_DENEB": "현재 Deneb 규칙", "B_STATIC": "상시 비중 축소", "C_MACRO": "거시 위험 보완"}


def main() -> None:
    result = json.loads((OUT / "pilot_result.json").read_text(encoding="utf-8"))
    ledgers = json.loads((REPO / result["ledgers_path"]).read_text(encoding="utf-8"))
    books = ledgers["historical_confirmation"]["base"]
    source = books["A_DENEB"]["ledger"]
    svg = [
        '<svg xmlns="http://www.w3.org/2000/svg" width="1400" height="1250" '
        'viewBox="0 0 1400 1250">',
        '<rect width="1400" height="1250" fill="#f7faf9"/>',
        "<style>text{font-family:Malgun Gothic,Arial,sans-serif;fill:#17392f}"
        " .muted{fill:#567268}</style>",
    ]

    def text(x: float, y: float, label: str, size: int = 19, fill: str | None = None) -> None:
        color = f' style="fill:{fill}"' if fill else ""
        svg.append(
            f'<text x="{x:.1f}" y="{y:.1f}" font-size="{size}"{color}>{escape(label)}</text>'
        )

    def path(
        values: list[float],
        x: float,
        y: float,
        width: float,
        height: float,
        lo: float,
        hi: float,
        color: str,
        dash: bool = False,
    ) -> None:
        points = " ".join(
            f"{x + width * i / max(len(values) - 1, 1):.1f},{y + height * (hi - v) / (hi - lo):.1f}"
            for i, v in enumerate(values)
        )
        svg.append(
            f'<polyline points="{points}" fill="none" stroke="{color}" stroke-width="3"'
            + (' stroke-dasharray="7 5"' if dash else "")
            + "/>"
        )

    def axes(y: float, height: float, lo: float, hi: float, unit: str) -> None:
        text(100, y - 13, unit, 17)
        for j in range(5):
            pos = y + height * j / 4
            value = hi - (hi - lo) * j / 4
            svg.append(f'<line x1="100" y1="{pos}" x2="1300" y2="{pos}" stroke="#dbe6e0"/>')
            text(20, pos + 6, f"{value:,.0f}", 17)
        for year in range(2020, 2027):
            positions = [i for i, r in enumerate(source) if r["day"].startswith(str(year))]
            if positions:
                x = 100 + 1200 * positions[0] / (len(source) - 1)
                text(x, y + height + 26, str(year), 17)

    text(50, 48, "거시 위험 보완 · Deneb 비교", 32)
    text(50, 80, "설명용 후향 파일럿  |  2020.01–2026.08  |  매월 10만원  |  총 납입 800만원", 19)
    for j, (name, book) in enumerate(books.items()):
        x = 50 + 445 * j
        svg.append(
            f'<rect x="{x}" y="105" width="420" height="109" rx="12" '
            'fill="white" stroke="#dde6e0"/>'
        )
        text(x + 20, 136, NAMES[name], 21, COLORS[name])
        text(
            x + 20,
            172,
            f"세후 청산금액 {float(book['after_tax_liquidation_krw']) / 10000:,.1f}만원",
            24,
        )
        text(
            x + 20,
            198,
            f"세후 IRR {book['after_tax_irr']:.1%} · "
            f"운용중 낙폭 {book['tax_payment_nav']['mdd']:.1%}",
            17,
        )
    text(50, 257, "운용 중 평가액 — 납입 포함·최종 전량 매도 전", 23)
    values = {k: [float(r["value"]) / 10000 for r in b["ledger"]] for k, b in books.items()}
    high = math.ceil(max(max(v) for v in values.values()) / 500) * 500
    axes(300, 270, 0, high, "만원")
    for name, points in values.items():
        path(points, 100, 300, 1200, 270, 0, high, COLORS[name])
    for j, name in enumerate(books):
        text(110 + 390 * j, 622, "━ " + NAMES[name], 19, COLORS[name])
    text(50, 671, "거시 보완 − 현재 Deneb 평가액 차이", 23)
    gap = [c - a for c, a in zip(values["C_MACRO"], values["A_DENEB"], strict=True)]
    limit = max(100, math.ceil(max(abs(v) for v in gap) / 100) * 100)
    axes(710, 165, -limit, limit, "만원 · 음수는 Deneb보다 적음")
    path(gap, 100, 710, 1200, 165, -limit, limit, "#bd4a53")
    text(50, 952, "월말 위험 확률 — 향후 126거래일 내 결정가 대비 −15%", 23)
    pred = [r for r in result["predictions"] if "2020" <= r["execute"][:4] <= "2026"]
    probs = [r["probabilities"]["LR"] * 100 for r in pred]
    threshold = [2 * r["base"] * 100 for r in pred]
    axes(995, 145, 0, 100, "%")
    path(probs, 100, 995, 1200, 145, 0, 100, "#326ac3")
    path(threshold, 100, 995, 1200, 145, 0, 100, "#bb7a19", True)
    text(120, 1195, "파랑: LR 위험 확률   /   주황 점선: 사전 고정 경고 문턱", 18)
    text(
        50, 1230, "반복 사용한 과거 자료 · 검정력 부족 예외 계산 · 정식 PASS 및 운영 승인 아님", 17
    )
    svg.append("</svg>")
    (OUT / "overview.svg").write_text("\n".join(svg), encoding="utf-8")

    yearly: dict[str, Any] = {}
    for period, variants in ledgers.items():
        yearly[period] = {}
        for name, book in variants["base"].items():
            groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
            for row in book["ledger"]:
                groups[row["day"][:4]].append(row)
            previous = Decimal(1)
            yearly[period][name] = {}
            for year, rows in groups.items():
                final = Decimal(rows[-1]["after_tax_nav"])
                yearly[period][name][year] = {
                    "return_before_final_liquidation": float(final / previous - 1),
                    "ending_value_krw": rows[-1]["value"],
                    "macro_events": [e for e in book["events"] if e["day"].startswith(year)],
                }
                previous = final
    (OUT / "yearly_and_events.json").write_text(
        json.dumps(yearly, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


if __name__ == "__main__":
    main()
