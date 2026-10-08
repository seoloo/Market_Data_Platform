"""문서 173 §2 — JEV × Elliott 2 차 시도: 근거 문장 · 두 후보 비교 (합성, 시장 · 주문 없음).

사용::

    python scripts/jev_ew_pairwise.py size                  # 호출 없음
    python scripts/jev_ew_pairwise.py pilot --budget 0.10   # 개발 seed 20260924 12 묶음
    python scripts/jev_ew_pairwise.py confirm --confirm-holdout --budget 0.80  # seed 20261101

후보 생성 · 시험지 · 채점은 :mod:`scripts.jev_ew_compare` (172) 와 같다.
다른 것은 평가자 입력과 질문
(:mod:`jev_pairwise`). JEV 는 기준 순위 상위 10 안에서만 고른다 — 정답이 상위 10 밖이면 JEV 는 실패.
"""

from __future__ import annotations

import json
import sys
import time
from collections.abc import Sequence
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
if str(REPO_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "src"))

from scripts.jev_ew_compare import (  # noqa: E402
    DATA,
    Budget,
    Case,
    Post,
    call,
    cases,
    http_post,
    score,
)

from auto_trading.config.secrets import read_env_file  # noqa: E402
from auto_trading.research.elliott.jev_pairwise import (  # noqa: E402
    PROMPT_VERSION,
    QUESTION,
    TOP_K,
    build_pairwise_request,
    parse_pairwise,
    top_k_ids,
)
from auto_trading.research.elliott.jev_rerank import (  # noqa: E402
    MODEL,
    estimate_cost_usd,
    pessimistic_rank,
)

SEEDS = {"size": 20260924, "pilot": 20260924, "confirm": 20261101}
REPORTS = REPO_ROOT / "reports" / "jev173"


def evaluate(cs: Sequence[Case], post: Post | None, budget: Budget) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for c in cs:
        row: dict[str, Any] = {
            "degree": c.degree,
            "pattern": c.pattern,
            "noise": c.noise,
            "triggered": c.triggered,
            "in_set": c.in_set,
            "set_size": len(c.sidecar),
            "base_rank": c.sidecar.get(c.truth_id or "") if c.in_set else None,
            "jev_rank": None,
            "jev_verdict": None,
        }
        if c.scenario_set is not None and c.sidecar:
            ids = top_k_ids(c.sidecar)
            row["in_top_k"] = c.truth_id in ids
            request, pairs = build_pairwise_request(c.scenario_set, ids)
            row["est_usd"] = round(estimate_cost_usd(request), 6)
            if post is not None:
                raw, err = call(request, post, budget, DATA / "cache")
                answers = None
                model = MODEL
                if raw is not None:
                    try:
                        body = json.loads(raw)
                        answers, model = body.get("answers"), str(body.get("model") or MODEL)
                    except ValueError:
                        answers = None
                ev = parse_pairwise(
                    c.scenario_set,
                    pairs,
                    answers,
                    evaluated_at_ms=int(time.time() * 1000),
                    raw=raw or (err or ""),
                    model=model,
                )
                row["jev_verdict"] = ev.verdict.value
                if c.truth_id is not None and c.truth_id in ids:
                    row["jev_rank"] = pessimistic_rank(ev, c.truth_id)
        rows.append(row)
    return rows


def main(argv: list[str]) -> int:
    if not argv or argv[0] not in SEEDS:
        raise SystemExit(__doc__)
    mode = argv[0]
    if mode == "confirm":
        if "--confirm-holdout" not in argv:
            raise SystemExit("확인 seed 는 --confirm-holdout 과 함께 한 번만 (문서 173 §2)")
        if (REPORTS / "pairwise_confirm.json").exists():
            raise SystemExit("확인 시험은 이미 했다 — 다시 채점하지 않는다")
    budget = Budget(float(argv[argv.index("--budget") + 1]) if "--budget" in argv else 0.0)
    post: Post | None = None
    if mode != "size":
        key = read_env_file(REPO_ROOT / ".env").get("TYPESAFE_API_KEY", "").strip()
        if not key or budget.limit_usd <= 0:
            raise SystemExit("TYPESAFE_API_KEY 와 --budget 이 필요하다")
        post = http_post(key)
    started = time.time()
    rows = evaluate(cases(SEEDS[mode], pilot=mode == "pilot"), post, budget)
    groups: dict[str, list[dict[str, Any]]] = {}
    for r in rows:
        groups.setdefault(f"noise={r['noise']}|{r['pattern']}", []).append(r)
        groups.setdefault(f"noise={r['noise']}|pooled", []).append(r)
    summary = {}
    for k, v in sorted(groups.items()):
        s = score(v)
        s["recall_at_10"] = round(sum(1 for r in v if r.get("in_top_k")) / len(v), 4)
        summary[k] = s
    report = {
        "mode": mode,
        "seed": SEEDS[mode],
        "model": MODEL,
        "prompt_version": PROMPT_VERSION,
        "question": QUESTION,
        "top_k": TOP_K,
        "est_total_usd": round(sum(r.get("est_usd", 0) for r in rows), 4),
        "spent_usd": round(budget.spent_usd, 6),
        "calls": budget.calls,
        "cached": budget.cached,
        "budget_stopped": budget.stopped,
        "elapsed_s": round(time.time() - started, 1),
        "groups": summary,
    }
    REPORTS.mkdir(parents=True, exist_ok=True)
    (REPORTS / f"pairwise_{mode}.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=1) + "\n", encoding="utf-8"
    )
    DATA.mkdir(parents=True, exist_ok=True)
    with (DATA / f"pairwise_{mode}_rows.jsonl").open("w", encoding="utf-8", newline="\n") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    with (DATA / "ledger.jsonl").open("a", encoding="utf-8", newline="\n") as f:
        for e in budget.ledger:
            f.write(json.dumps({"mode": f"pairwise_{mode}", **e}, ensure_ascii=False) + "\n")
    print(json.dumps({k: v for k, v in report.items() if k not in ("groups", "question")}))
    for k, v in summary.items():
        print(k, json.dumps(v, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
