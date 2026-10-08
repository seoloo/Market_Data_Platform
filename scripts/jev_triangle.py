"""문서 173 §3 — 상승 삼각형 판독: JEV vs 주도주 알림 코드 규칙 (합성 일봉, 시장 · 주문 없음).

사용::

    python scripts/jev_triangle.py pilot --budget 0.02          # seed 20261102, 40 경로
    python scripts/jev_triangle.py confirm --confirm-holdout --budget 0.05  # seed 20261103

* 정답은 생성기의 모양 종류다 (코드 규칙의 문턱과 무관하게 만든다):
  상승 삼각형(수평 저항 2 ~ 4 번 닿음 · 저점 상승) 200 / 아님 200
  (대칭 · 하락 삼각형 · 박스 · 상승 추세 각 50).
* 코드 규칙: :func:`auto_trading.ops.leader_alert.ascending_triangle` 이
  ``형성 중`` · ``돌파`` 면 삼각형.
* JEV: 3% 꺾임 꼭짓점 목록(짧은 목록, 날 · 가격 · H/L)과 정의를 주고 Noul > 0.5 면 '삼각형'.
* 판정 (결과 전 고정, 문서 173 §3): 확인 400 경로에서 맞힘의 짝 비교, 한쪽 정확 McNemar p < 0.05
  그리고 정확도 차이 ≥ +5%p → JEV 우위. 반대 방향도 같은 기준으로 적는다.
"""

from __future__ import annotations

import json
import math
import random
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
if str(REPO_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "src"))

from scripts.jev_ew_compare import DATA, Budget, call, http_post, mcnemar_one_sided  # noqa: E402

from auto_trading.config.secrets import read_env_file  # noqa: E402
from auto_trading.ops.leader_alert import ascending_triangle  # noqa: E402
from auto_trading.research.elliott.jev_rerank import MODEL  # noqa: E402

SEEDS = {"pilot": 20261102, "confirm": 20261103}
COUNTS = {"pilot": 40, "confirm": 400}
DAYS = 61
ZIGZAG = 0.03
CHUNK = 40
REPORTS = REPO_ROOT / "reports" / "jev173"
NEGATIVES = ("symmetric", "descending", "rectangle", "uptrend")
QUESTION = {
    "question": "Do these price swings form an ascending triangle?",
    "definition": (
        "An ascending triangle has a roughly horizontal resistance level that the swing highs "
        "reach at least twice, while the swing lows keep rising toward it. Falling highs, a flat "
        "floor, or highs that keep climbing are not ascending triangles."
    ),
}
CRITERIA = {
    "true": "Swing highs stall near one horizontal level at least twice and swing lows rise.",
    "false": "Highs fall or keep rising, or lows are flat or falling.",
}


def _pivots(rng: random.Random, shape: str) -> list[tuple[int, float]]:
    """(날, 가격) 꼭짓점 — 고점 · 저점이 번갈아. 마지막 날(오늘)은 따로 붙인다."""
    r = 100.0
    touches = rng.randint(2, 4)
    pts: list[tuple[int, float]] = []
    lo0 = r * (1 - rng.uniform(0.12, 0.25))
    lo1 = r * (1 - rng.uniform(0.02, 0.05))
    step = rng.uniform(0.03, 0.06)
    climb = rng.uniform(0.04, 0.08)
    n_peaks = touches if shape == "ascending" else touches + 1
    gap = (DAYS - 16) / max(n_peaks - 1, 1)  # 고점끼리 고르게 떨어뜨림 (± 2 일)
    peak_days = [round(8 + gap * k) + rng.randint(-2, 2) for k in range(n_peaks)]
    for k, d in enumerate(peak_days):
        frac = d / (DAYS - 1)
        if shape == "ascending":
            hi = r * (1 + rng.uniform(-0.03, 0.005))
        elif shape in ("symmetric", "descending"):
            hi = r * (1 - step * k)
        elif shape == "rectangle":
            hi = r * (1 + rng.uniform(-0.015, 0.015))
        else:  # uptrend
            hi = r * (1 + climb * k)
        if shape in ("ascending", "symmetric"):
            lo = lo0 + (lo1 - lo0) * frac
        elif shape in ("descending", "rectangle"):
            lo = lo0 * (1 + rng.uniform(-0.0075, 0.0075))
        else:
            lo = lo0 * (1 + climb * k)
        lo = min(lo, hi * 0.97)
        prev = pts[-1][0] if pts else 0
        trough_day = max(prev + 1, d - rng.randint(3, 6))
        if trough_day < d:
            pts.append((trough_day, lo))
        pts.append((d, hi))
    return pts


def make_path(rng: random.Random, shape: str) -> dict[str, Any]:
    pts = _pivots(rng, shape)
    sigma = rng.uniform(0.003, 0.01)
    breakout = rng.random() < 0.5
    last_hi = max(p for _, p in pts)
    end = last_hi * (1 + rng.uniform(0.005, 0.03)) if breakout else last_hi * rng.uniform(0.9, 0.98)
    pts = [(0, pts[0][1] * rng.uniform(0.97, 1.03)), *pts, (DAYS - 1, end)]
    closes: list[float] = []
    for d in range(DAYS):
        j = max(k for k, (day, _) in enumerate(pts) if day <= d)
        d0, p0 = pts[j]
        d1, p1 = pts[min(j + 1, len(pts) - 1)]
        base = p0 if d1 == d0 else p0 + (p1 - p0) * (d - d0) / (d1 - d0)
        closes.append(base * math.exp(rng.gauss(0, sigma)))
    highs = [c * (1 + abs(rng.gauss(0, sigma / 2))) for c in closes]
    lows = [c * (1 - abs(rng.gauss(0, sigma / 2))) for c in closes]
    return {
        "shape": shape,
        "truth": shape == "ascending",
        "highs": highs,
        "lows": lows,
        "closes": closes,
    }


def swings(
    highs: Sequence[float], lows: Sequence[float], th: float = ZIGZAG
) -> list[dict[str, Any]]:
    """3% 꺾임 꼭짓점 (고가 · 저가). 삼각형 판단 논리는 없다 — 목록만 짧게 만든다."""
    out: list[dict[str, Any]] = []
    mode = 0
    lo_i, lo_p, hi_i, hi_p = 0, lows[0], 0, highs[0]
    for i in range(1, len(highs)):
        if mode >= 0 and highs[i] > hi_p:
            hi_i, hi_p = i, highs[i]
        if mode <= 0 and lows[i] < lo_p:
            lo_i, lo_p = i, lows[i]
        if mode >= 0 and lows[i] < hi_p * (1 - th) and (mode == 1 or hi_i > lo_i):
            out.append({"day": hi_i, "price": round(hi_p, 2), "k": "H"})
            mode, lo_i, lo_p = -1, i, lows[i]
        elif mode <= 0 and highs[i] > lo_p * (1 + th) and (mode == -1 or lo_i > hi_i):
            out.append({"day": lo_i, "price": round(lo_p, 2), "k": "L"})
            mode, hi_i, hi_p = 1, i, highs[i]
    out.append({"day": len(highs) - 1, "price": round(highs[-1], 2), "k": "last"})
    return out


def dataset(seed: int, n: int) -> list[dict[str, Any]]:
    rng = random.Random(seed)
    shapes = ["ascending"] * (n // 2) + [NEGATIVES[k % 4] for k in range(n - n // 2)]
    rng.shuffle(shapes)
    return [make_path(rng, s) for s in shapes]


def code_says(path: dict[str, Any]) -> bool:
    t = ascending_triangle(path["highs"], path["lows"], path["closes"])
    return t.status in ("형성 중", "돌파")


def question(path: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": "noul",
        "instructions": {**QUESTION, "swings": swings(path["highs"], path["lows"])},
        "criteria": CRITERIA,
    }


def summary(rows: Sequence[dict[str, Any]]) -> dict[str, Any]:
    n = len(rows)
    jev_ok = [r["jev"] == r["truth"] for r in rows]
    code_ok = [r["code"] == r["truth"] for r in rows]
    jev_only = sum(1 for a, b in zip(jev_ok, code_ok, strict=True) if a and not b)
    code_only = sum(1 for a, b in zip(jev_ok, code_ok, strict=True) if b and not a)
    by_shape: dict[str, dict[str, float]] = {}
    for s in ("ascending", *NEGATIVES):
        sub = [r for r in rows if r["shape"] == s]
        if sub:
            by_shape[s] = {
                "n": len(sub),
                "jev_acc": round(sum(r["jev"] == r["truth"] for r in sub) / len(sub), 4),
                "code_acc": round(sum(r["code"] == r["truth"] for r in sub) / len(sub), 4),
            }
    return {
        "n": n,
        "jev_acc": round(sum(jev_ok) / n, 4),
        "code_acc": round(sum(code_ok) / n, 4),
        "jev_only": jev_only,
        "code_only": code_only,
        "p_jev_better": round(mcnemar_one_sided(jev_only, code_only), 6),
        "p_code_better": round(mcnemar_one_sided(code_only, jev_only), 6),
        "by_shape": by_shape,
    }


def main(argv: list[str]) -> int:
    if not argv or argv[0] not in SEEDS:
        raise SystemExit(__doc__)
    mode = argv[0]
    if mode == "confirm":
        if "--confirm-holdout" not in argv:
            raise SystemExit("확인 seed 는 --confirm-holdout 과 함께 한 번만 (문서 173 §3)")
        if (REPORTS / "triangle_confirm.json").exists():
            raise SystemExit("확인 시험은 이미 했다")
    limit = float(argv[argv.index("--budget") + 1]) if "--budget" in argv else 0.0
    key = read_env_file(REPO_ROOT / ".env").get("TYPESAFE_API_KEY", "").strip()
    if not key or limit <= 0:
        raise SystemExit("TYPESAFE_API_KEY 와 --budget 이 필요하다")
    budget, post = Budget(limit), http_post(key)
    paths = dataset(SEEDS[mode], COUNTS[mode])
    answers: dict[str, Any] = {}
    for start in range(0, len(paths), CHUNK):
        request = {
            "state": "Chart pattern check. Each question has its own list of daily price swings.",
            "model": MODEL,
            "questions": {
                f"q{k:03d}": question(p) for k, p in enumerate(paths) if start <= k < start + CHUNK
            },
        }
        raw, err = call(request, post, budget, DATA / "cache")
        if raw is None:
            raise SystemExit(f"호출 실패 {err} — 판정 불가")
        answers.update(json.loads(raw)["answers"])
    rows = [
        {
            "shape": p["shape"],
            "truth": p["truth"],
            "code": code_says(p),
            "noul": answers[f"q{k:03d}"]["noul"],
            "jev": answers[f"q{k:03d}"]["noul"] > 0.5,
        }
        for k, p in enumerate(paths)
    ]
    report = {
        "mode": mode,
        "seed": SEEDS[mode],
        "model": MODEL,
        "spent_usd": round(budget.spent_usd, 6),
        "calls": budget.calls,
        **summary(rows),
    }
    REPORTS.mkdir(parents=True, exist_ok=True)
    (REPORTS / f"triangle_{mode}.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=1) + "\n", encoding="utf-8"
    )
    with (DATA / "ledger.jsonl").open("a", encoding="utf-8", newline="\n") as f:
        for e in budget.ledger:
            f.write(json.dumps({"mode": f"triangle_{mode}", **e}, ensure_ascii=False) + "\n")
    print(json.dumps(report, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
