"""보여 주기 (판정 아님) — 촘촘한 그리드의 '화면 수익' vs 실제 계좌 손익.

사용 예::

    python -m scripts.show_grid_demo   # reports/grid_demo/*.svg · summary.json

* 145 · 144 의 결론(촘촘한 간격은 설 자리 없음)을 그림으로 보여 주려는 것이다. 새 판정이 아니다.
* 원금 3,800 만 원, 0.5% 간격 100 칸(아래로 약 −39% 까지), 칸마다 같은 금액, 한 칸 위에서 판다.
  가격이 칸 위로 벗어나고 들고 있는 칸이 없으면 칸을 따라 올린다(흔한 '추적 그리드').
* 가격은 바이낸스 1 분봉(달러) — 빗썸 원화는 김프 · 환율로 조금 다르다. 수수료 편도 0.05%.
* 한 분 안에서는 저가에서 사고 고가에서 판다고 본다(그리드에 유리한 가정).
"""

from __future__ import annotations

import csv
import io
import json
import math
import sys
import zipfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from scripts.readme_charts import BLUE, GRAY, GREEN, line_chart

REPO = Path(__file__).resolve().parent.parent
SRC = REPO / "data" / "external" / "binance_vision" / "spot"
OUT = REPO / "reports" / "grid_demo"
CAPITAL = 38_000_000.0
LEVELS = 100
STEP = 0.005
FEE = 0.0005
START = "2021-01"


def minutes(sym: str) -> Any:
    for z in sorted((SRC / sym / "1m").glob(f"{sym}-1m-*.zip")):
        if z.stem.split("-1m-")[1] < START:
            continue
        with zipfile.ZipFile(z) as f, f.open(f.namelist()[0]) as fh:
            for row in csv.reader(io.TextIOWrapper(fh, encoding="utf-8")):
                if not row or not row[0].isdigit():
                    continue
                ts = int(row[0])
                ts = ts // 1000 if ts > 10**14 else ts
                yield ts, float(row[2]), float(row[3]), float(row[4])


def simulate(sym: str) -> dict[str, Any]:
    g = math.log1p(STEP)
    base = budget = 0.0
    cash = CAPITAL
    hold: dict[int, float] = {}
    realized = 0.0
    kmax = kmin = 0
    prev = 0.0
    hodl_qty = 0.0
    day = ""
    daily: list[dict[str, Any]] = []
    day_real = 0.0
    last_close = 0.0
    below = 0

    def px(k: int) -> float:
        return base * math.exp(g * k)

    def kof(p: float) -> int:
        return math.floor(math.log(p / base) / g)

    for ts, high, low, close in minutes(sym):
        d = datetime.fromtimestamp(ts / 1000, UTC).strftime("%Y-%m-%d")
        if not base:
            base, prev = close, close
            budget = CAPITAL / LEVELS
            kmax = kof(close) - 1
            kmin = kmax - LEVELS + 1
            hodl_qty = CAPITAL * (1 - FEE) / close
            day = d
        if d != day:
            value = cash + sum(q * last_close for q in hold.values())
            daily.append(
                {
                    "d": day,
                    "grid": realized,
                    "account": value - CAPITAL,
                    "hodl": hodl_qty * last_close - CAPITAL,
                    "day_real": day_real,
                    "below": last_close < px(kmin),
                }
            )
            day, day_real = d, 0.0
        # 1) 내려가며 지나친 빈 칸에서 산다
        top = min(kof(prev), kmax)
        k = top
        while k >= kmin and px(k) >= low:
            if k not in hold and px(k) < prev and cash >= budget:
                hold[k] = budget * (1 - FEE) / px(k)
                cash -= budget
            k -= 1
        # 2) 한 칸 위에 닿은 칸은 판다
        for k in [k for k in hold if px(k + 1) <= high]:
            proceeds = hold.pop(k) * px(k + 1) * (1 - FEE)
            gain = proceeds - budget
            realized += gain
            day_real += gain
            cash += proceeds
        # 3) 들고 있는 칸 없이 위로 벗어나면 따라 올린다
        if not hold and kof(close) - 1 > kmax:
            kmax = kof(close) - 1
            kmin = kmax - LEVELS + 1
        prev = last_close = close
    value = cash + sum(q * last_close for q in hold.values())
    daily.append(
        {
            "d": day,
            "grid": realized,
            "account": value - CAPITAL,
            "hodl": hodl_qty * last_close - CAPITAL,
            "day_real": day_real,
            "below": last_close < px(kmin),
        }
    )
    return {"daily": daily}


def summary(daily: list[dict[str, Any]]) -> dict[str, Any]:
    pos = [x for x in daily if x["day_real"] > 0]
    worst = min(daily, key=lambda x: x["account"])
    best_grid_day = max(daily, key=lambda x: x["day_real"])
    return {
        "기간": [daily[0]["d"], daily[-1]["d"]],
        "화면 그리드 수익 (끝)": round(daily[-1]["grid"]),
        "실제 계좌 손익 (끝)": round(daily[-1]["account"]),
        "그냥 보유 손익 (끝)": round(daily[-1]["hodl"]),
        "그리드 수익이 난 날 비율": round(len(pos) / len(daily), 3),
        "하루 평균 그리드 수익": round(sum(x["day_real"] for x in daily) / len(daily)),
        "그리드 수익 가장 많은 날": [best_grid_day["d"], round(best_grid_day["day_real"])],
        "계좌 가장 나빴던 날": [worst["d"], round(worst["account"]), round(worst["grid"])],
        "칸 아래로 떨어져 멈춘 날": sum(1 for x in daily if x["below"]),
    }


def chart(sym: str, daily: list[dict[str, Any]]) -> str:
    def pts(key: str) -> list[tuple[str, float]]:
        return [
            (x["d"], x[key] / 10_000)
            for i, x in enumerate(daily)
            if i % 3 == 0 or i == len(daily) - 1
        ]

    vals = [x[k] / 10_000 for x in daily for k in ("grid", "account", "hodl")]
    lo, hi = min(vals), max(vals)
    step = 10 ** math.floor(math.log10(max(hi - lo, 1) / 5))
    step *= 2 if (hi - lo) / step > 10 else 1
    ticks = [step * i for i in range(math.floor(lo / step), math.ceil(hi / step) + 1)]
    return line_chart(
        [
            ("화면에 보이는 그리드 수익 (판 차익 누적)", GREEN, pts("grid"), False),
            ("실제 계좌 손익 (쌓인 코인 평가 포함)", BLUE, pts("account"), False),
            (f"{sym[:3]} 그냥 보유", GRAY, pts("hodl"), False),
        ],
        title=f"{sym[:3]} 0.5% 그리드 100칸 · 원금 3,800만 원 (2021-01 ~ 2026-08, 판정 아님)",
        sub="단위 만 원. 초록 선은 내려가지 않는다 — 판 것만 더하기 때문. 파란 선이 실제 내 돈이다",
        log=False,
        y_ticks=ticks,
        y_fmt="{:,.0f}",
    )


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    out: dict[str, Any] = {}
    for sym in ("BTCUSDT", "ETHUSDT"):
        res = simulate(sym)
        out[sym] = summary(res["daily"])
        (OUT / f"{sym[:3].lower()}_grid.svg").write_bytes(chart(sym, res["daily"]).encode("utf-8"))
    (OUT / "summary.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    sys.stdout.write(json.dumps(out, ensure_ascii=False, indent=1) + "\n")
    return 0


if __name__ == "__main__":
    for _stream in (sys.stdout, sys.stderr):
        _reconfigure = getattr(_stream, "reconfigure", None)
        if _reconfigure is not None:
            _reconfigure(encoding="utf-8", errors="replace")
    raise SystemExit(main())
