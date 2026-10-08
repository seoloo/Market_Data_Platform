"""문서 173 §4 — 브리핑 문장 · 숫자 일치: JEV vs 지금의 숫자 검사 (합성 자료표, 읽기 전용).

사용::

    python scripts/jev_brief_numbers.py pilot --budget 0.01  # 실제 10-07 브리핑 표
    python scripts/jev_brief_numbers.py confirm --confirm-holdout --budget 0.05

* 자료표는 시장 브리핑이 AI 에게 주는 수치 블록
  (:func:`ops.market_brief.describe_numbers`) 그대로의 꼴.
  확인 시험은 seed 로 만든 합성 표 30 개(종목 이름은 실제 SPY · QQQ 상위 종목, 숫자는 무작위).
* 표마다 한국어 문장 10 개: 맞는 문장 5 · 틀린 문장 5
  (부호 뒤집기 · 다른 종목 숫자 · 틀린 숫자 돌아가며).
* 지금의 검사 = :func:`ops.market_brief.validate_answer` (문장 속 숫자가 표에 있나) — 통과면 '맞음'.
* JEV = 표를 state 로, 문장마다 Noul "표와 일치하나" > 0.5 면 '맞음'.
* 판정 (결과 전 고정, 문서 173 §4): 300 문장 맞힘의 짝 비교,
  한쪽 정확 McNemar p < 0.05 그리고 ≥ +5%p → JEV 우위.
"""

from __future__ import annotations

import json
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
from auto_trading.ops.market_brief import Evidence, describe_numbers, validate_answer  # noqa: E402
from auto_trading.research.elliott.jev_rerank import MODEL  # noqa: E402

SEEDS = {"pilot": 20261104, "confirm": 20261105}
TABLES = 30
REPORTS = REPO_ROOT / "reports" / "jev173"
REAL = REPO_ROOT / "reports" / "brief" / "2026-10-07.json"
SPY_NAMES = ("NVDA", "AAPL", "MSFT", "AMZN", "GOOGL", "AVGO", "META", "TSLA", "BRK.B", "JPM")
QQQ_NAMES = ("NVDA", "MSFT", "AAPL", "AMZN", "AVGO", "META", "NFLX", "TSLA", "COST", "GOOGL")
WATCH = ("QNDX", "BTQ", "WQTM", "QS")
QUESTION = {
    "question": (
        "Is this Korean sentence about the market fully consistent with the data block in the "
        "state — the same ticker, the same direction (up or down) and the same number?"
    ),
}
CRITERIA = {
    "true": "Ticker, direction and number all match the data block.",
    "false": (
        "The direction is reversed, the number belongs to another ticker, or the number is wrong."
    ),
}
_EV = [Evidence("E1", "news", "test", "", "", "")]


def synthetic_brief(rng: random.Random, k: int) -> dict[str, Any]:
    rets = {s: rng.gauss(0, 0.015) for s in dict.fromkeys((*SPY_NAMES, *QQQ_NAMES))}
    # 같은 종목은 두 목록에서 같은 수익률 (실제 자료처럼)

    def rows(names: Sequence[str]) -> dict[str, Any]:
        out = []
        for s in names:
            w, r = rng.uniform(0.02, 0.09), rets[s]
            out.append({"symbol": s, "weight": w, "ret": r, "contrib": w * r})
        top = sum(x["contrib"] for x in out)
        return {"rows": out, "top_sum": top, "rest": rng.gauss(0, 0.004)}

    vix = rng.uniform(12, 35)
    return {
        "session": f"S{k:02d}",
        "market": {
            "SPY": rng.gauss(0, 0.01),
            "QQQ": rng.gauss(0, 0.013),
            "vix": {
                "date": f"S{k:02d}",
                "close": vix,
                "change": rng.gauss(0, 1.2),
                "pct_rank": rng.uniform(0, 100),
            },
        },
        "contrib": {"spy": rows(SPY_NAMES), "qqq": rows(QQQ_NAMES)},
        "watch": {s: rng.gauss(0, 0.03) for s in WATCH},
    }


def _facts(brief: dict[str, Any]) -> list[tuple[str, float]]:
    """(종목, 수익률 %) — 문장 재료. VIX 는 변화(포인트)를 쓴다."""
    m = brief["market"]
    out = [(k, m[k] * 100) for k in ("SPY", "QQQ") if m.get(k) is not None]
    for c in (brief.get("contrib") or {}).values():
        if c:
            out += [(r["symbol"], r["ret"] * 100) for r in c["rows"] if r["ret"] is not None]
    out += [(s, r * 100) for s, r in (brief.get("watch") or {}).items() if r is not None]
    seen: dict[str, float] = {}
    for s, v in out:
        seen.setdefault(s, v)
    return [(s, v) for s, v in seen.items() if round(abs(v), 2) >= 0.05]


def _sentence(symbol: str, pct: float) -> str:
    return f"{symbol}는 {abs(pct):.2f}% {'상승' if pct > 0 else '하락'}했다."


def claims(rng: random.Random, brief: dict[str, Any], n: int = 10) -> list[tuple[str, bool, str]]:
    """(문장, 맞나, 종류). 맞음 n/2 · 틀림 n/2 (sign · ticker · number 돌아가며)."""
    facts = _facts(brief)
    picks = rng.sample(facts, n)
    out: list[tuple[str, bool, str]] = []
    kinds = ("sign", "ticker", "number")
    for i, (s, v) in enumerate(picks):
        if i % 2 == 0:
            out.append((_sentence(s, v), True, "true"))
            continue
        kind = kinds[(i // 2) % 3]
        if kind == "sign":
            out.append((_sentence(s, -v), False, kind))
        elif kind == "ticker":
            other = rng.choice(
                [f for f in facts if f[0] != s and round(abs(f[1]), 2) != round(abs(v), 2)]
            )
            out.append((_sentence(s, other[1]), False, kind))
        else:
            out.append((_sentence(s, v * rng.uniform(1.3, 2.0)), False, kind))
    return out


def validator_says(text: str, allowed: str) -> bool:
    kept = validate_answer({"facts": [{"text": text, "evidence": ["E1"]}]}, _EV, allowed)
    return bool(kept["facts"])


def main(argv: list[str]) -> int:
    if not argv or argv[0] not in SEEDS:
        raise SystemExit(__doc__)
    mode = argv[0]
    if mode == "confirm":
        if "--confirm-holdout" not in argv:
            raise SystemExit("확인 seed 는 --confirm-holdout 과 함께 한 번만 (문서 173 §4)")
        if (REPORTS / "brief_confirm.json").exists():
            raise SystemExit("확인 시험은 이미 했다")
    limit = float(argv[argv.index("--budget") + 1]) if "--budget" in argv else 0.0
    key = read_env_file(REPO_ROOT / ".env").get("TYPESAFE_API_KEY", "").strip()
    if not key or limit <= 0:
        raise SystemExit("TYPESAFE_API_KEY 와 --budget 이 필요하다")
    rng = random.Random(SEEDS[mode])
    briefs = (
        [json.loads(REAL.read_text(encoding="utf-8"))]
        if mode == "pilot"
        else [synthetic_brief(rng, k) for k in range(TABLES)]
    )
    budget, post = Budget(limit), http_post(key)
    rows: list[dict[str, Any]] = []
    for brief in briefs:
        table = describe_numbers(brief)
        items = claims(rng, brief)
        request = {
            "state": {"data_block": table},
            "model": MODEL,
            "questions": {
                f"q{i:02d}": {
                    "type": "noul",
                    "instructions": {**QUESTION, "sentence": text},
                    "criteria": CRITERIA,
                }
                for i, (text, _, _) in enumerate(items)
            },
        }
        raw, err = call(request, post, budget, DATA / "cache")
        if raw is None:
            raise SystemExit(f"호출 실패 {err} — 판정 불가")
        answers = json.loads(raw)["answers"]
        for i, (text, truth, kind) in enumerate(items):
            noul = float(answers[f"q{i:02d}"]["noul"])
            rows.append(
                {
                    "kind": kind,
                    "truth": truth,
                    "jev": noul > 0.5,
                    "noul": noul,
                    "validator": validator_says(text, table),
                    "text": text,
                }
            )
    n = len(rows)
    jev_ok = [r["jev"] == r["truth"] for r in rows]
    val_ok = [r["validator"] == r["truth"] for r in rows]
    jev_only = sum(1 for a, b in zip(jev_ok, val_ok, strict=True) if a and not b)
    val_only = sum(1 for a, b in zip(jev_ok, val_ok, strict=True) if b and not a)
    by_kind = {
        k: {
            "n": len(sub),
            "jev_acc": round(sum(r["jev"] == r["truth"] for r in sub) / len(sub), 4),
            "validator_acc": round(sum(r["validator"] == r["truth"] for r in sub) / len(sub), 4),
        }
        for k in ("true", "sign", "ticker", "number")
        if (sub := [r for r in rows if r["kind"] == k])
    }
    report = {
        "mode": mode,
        "seed": SEEDS[mode],
        "model": MODEL,
        "n": n,
        "jev_acc": round(sum(jev_ok) / n, 4),
        "validator_acc": round(sum(val_ok) / n, 4),
        "jev_only": jev_only,
        "validator_only": val_only,
        "p_jev_better": round(mcnemar_one_sided(jev_only, val_only), 6),
        "by_kind": by_kind,
        "spent_usd": round(budget.spent_usd, 6),
        "calls": budget.calls,
    }
    REPORTS.mkdir(parents=True, exist_ok=True)
    (REPORTS / f"brief_{mode}.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=1) + "\n", encoding="utf-8"
    )
    with (DATA / f"brief_{mode}_rows.jsonl").open("w", encoding="utf-8", newline="\n") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    with (DATA / "ledger.jsonl").open("a", encoding="utf-8", newline="\n") as f:
        for e in budget.ledger:
            f.write(json.dumps({"mode": f"brief_{mode}", **e}, ensure_ascii=False) + "\n")
    print(json.dumps(report, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
