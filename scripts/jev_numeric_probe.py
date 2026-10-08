"""문서 173 §1 — JEV 숫자 읽기 기초 점검 (정답이 확실한 쉬운 문제, 연구 판정 아님).

사용::

    python scripts/jev_numeric_probe.py --budget 0.05

과제 4 개 × 100 문항 (seed 20261009 로 생성, 시장 자료 없음):

* T1 두 가격 중 큰 쪽 (Choice A/B, 무작위 50%)
* T2 꼭짓점 20 개 중 가장 높은 것 (Choice 20 개, 무작위 5%)
* T3 값이 범위 안인가 (Noul, 절반은 안 · 절반은 밖, 무작위 50%)
* T4 파동 0~5 가격에서 3 파가 1 · 3 · 5 파 중 가장 짧은가 (Noul, 절반씩, 무작위 50%)

통과 기준 (결과 전 고정, 문서 173 §1): T1 ≥ 80% · T2 ≥ 50% · T3 ≥ 80% · T4 ≥ 70%
중 **3 개 이상** → 1 단계로 간다.
과제마다 요청 하나(문항이 독립 질문). 키는 ``.env`` ``TYPESAFE_API_KEY``.
"""

from __future__ import annotations

import json
import random
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
if str(REPO_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "src"))

from scripts.jev_ew_compare import DATA, Budget, call, http_post  # noqa: E402

from auto_trading.config.secrets import read_env_file  # noqa: E402
from auto_trading.research.elliott.jev_rerank import MODEL  # noqa: E402

SEED = 20261009
ITEMS = 100
GATE = {"T1": 0.8, "T2": 0.5, "T3": 0.8, "T4": 0.7}
GATE_NEED = 3
CHUNK = {"T2": 25}
"""T2 는 100 문항 한 요청이 토큰 한도를 넘었다(HTTP 400 max_tokens_exceeded) — 25 문항씩 나눔."""
REPORT = REPO_ROOT / "reports" / "jev173" / "probe.json"
STATE = "Numeric reading check. Each question is independent and contains its own numbers."


def _price(rng: random.Random) -> float:
    return round(10 ** rng.uniform(3, 5), 2)


def t1(rng: random.Random) -> tuple[dict[str, Any], str]:
    a = _price(rng)
    b = round(a * (1 + rng.choice((-1, 1)) * rng.uniform(0.005, 0.2)), 2)
    q = {
        "type": "choice",
        "instructions": {"question": "Which price is higher?", "price_A": a, "price_B": b},
        "criteria": {"A": "price_A is the higher price", "B": "price_B is the higher price"},
    }
    return q, "A" if a > b else "B"


def t2(rng: random.Random) -> tuple[dict[str, Any], str]:
    p = _price(rng)
    swings = []
    for i in range(20):
        p = round(p * (1 + (1 if i % 2 == 0 else -1) * rng.uniform(0.01, 0.08)), 2)
        swings.append(p)
    if swings.count(max(swings)) > 1:
        swings[swings.index(max(swings))] += 0.01
    keys = [f"s{i:02d}" for i in range(20)]
    q = {
        "type": "choice",
        "instructions": {
            "question": "Which swing has the highest price?",
            "swings": dict(zip(keys, swings, strict=True)),
        },
        "criteria": {k: f"swing {k}" for k in keys},
    }
    return q, keys[swings.index(max(swings))]


def t3(rng: random.Random, inside: bool) -> tuple[dict[str, Any], bool]:
    lo = round(rng.uniform(0.2, 1.0), 3)
    hi = round(lo * rng.uniform(1.5, 3.0), 3)
    if inside:
        v = round(rng.uniform(lo, hi), 3)
    elif rng.random() < 0.5:
        v = round(lo * rng.uniform(0.7, 0.98), 3)
    else:
        v = round(hi * rng.uniform(1.02, 1.3), 3)
    q = {
        "type": "noul",
        "instructions": {
            "question": "Is the value inside the range (inclusive)?",
            "value": v,
            "range_low": lo,
            "range_high": hi,
        },
        "criteria": {"true": "low <= value <= high", "false": "value is below low or above high"},
    }
    return q, lo <= v <= hi


def t4(rng: random.Random, shortest: bool) -> tuple[dict[str, Any], bool]:
    while True:
        legs = [rng.uniform(0.5, 2.0) for _ in range(3)]  # 1 · 3 · 5 파 길이
        if (legs[1] < min(legs[0], legs[2])) == shortest:
            break
    r2, r4 = rng.uniform(0.3, 0.7), rng.uniform(0.2, 0.45)
    base = _price(rng)
    unit = base * 0.02
    p = [base]
    p.append(p[-1] + legs[0] * unit)
    p.append(p[-1] - r2 * legs[0] * unit)
    p.append(p[-1] + legs[1] * unit)
    p.append(p[-1] - r4 * legs[1] * unit)
    p.append(p[-1] + legs[2] * unit)
    q = {
        "type": "noul",
        "instructions": {
            "question": (
                "These are the prices at the ends of waves 0 to 5 of an upward move. Wave 1 runs "
                "from point 0 to 1, wave 3 from 2 to 3, wave 5 from 4 to 5. Is wave 3 the "
                "shortest of waves 1, 3 and 5 (by price distance)?"
            ),
            "points": {str(i): round(x, 2) for i, x in enumerate(p)},
        },
        "criteria": {
            "true": "wave 3 is shorter than both wave 1 and wave 5",
            "false": "wave 3 is longer than wave 1 or longer than wave 5",
        },
    }
    return q, shortest


def tasks() -> dict[str, list[tuple[dict[str, Any], Any]]]:
    rng = random.Random(SEED)
    out: dict[str, list[tuple[dict[str, Any], Any]]] = {
        "T1": [t1(rng) for _ in range(ITEMS)],
        "T2": [t2(rng) for _ in range(ITEMS)],
        "T3": [t3(rng, k % 2 == 0) for k in range(ITEMS)],
        "T4": [t4(rng, k % 2 == 0) for k in range(ITEMS)],
    }
    return out


def grade(task: str, answer: dict[str, Any], truth: Any) -> bool:
    if task in ("T1", "T2"):
        return bool(answer.get("choice") == truth)
    return bool((float(answer.get("noul", -1)) > 0.5) == truth)


def main(argv: list[str]) -> int:
    limit = float(argv[argv.index("--budget") + 1]) if "--budget" in argv else 0.0
    key = read_env_file(REPO_ROOT / ".env").get("TYPESAFE_API_KEY", "").strip()
    if not key or limit <= 0:
        raise SystemExit("TYPESAFE_API_KEY 와 --budget 이 필요하다")
    budget, post = Budget(limit), http_post(key)
    result: dict[str, Any] = {"seed": SEED, "model": MODEL, "gate": GATE, "tasks": {}}
    for task, items in tasks().items():
        size = CHUNK.get(task, ITEMS)
        answers: dict[str, Any] = {}
        err = None
        for start in range(0, ITEMS, size):
            request = {
                "state": STATE,
                "model": MODEL,
                "questions": {
                    f"q{k:03d}": q for k, (q, _) in enumerate(items) if start <= k < start + size
                },
            }
            raw, err = call(request, post, budget, DATA / "cache")
            if raw is None:
                break
            answers.update(json.loads(raw)["answers"])
        if err is not None:
            result["tasks"][task] = {"error": err}
            continue
        hits = [grade(task, answers[f"q{k:03d}"], t) for k, (_, t) in enumerate(items)]
        result["tasks"][task] = {"accuracy": sum(hits) / len(hits), "n": len(hits)}
    passed = [t for t, v in result["tasks"].items() if v.get("accuracy", 0) >= GATE[t]]
    result.update(
        passed=passed,
        proceed=len(passed) >= GATE_NEED,
        spent_usd=round(budget.spent_usd, 6),
        calls=budget.calls,
    )
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text(json.dumps(result, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    with (DATA / "ledger.jsonl").open("a", encoding="utf-8", newline="\n") as f:
        for e in budget.ledger:
            f.write(json.dumps({"mode": "probe", **e}, ensure_ascii=False) + "\n")
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
