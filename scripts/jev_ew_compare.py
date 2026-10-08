"""IDEAS 30 · 문서 172 — JEV × Elliott 복수 시나리오 재순위 비교 (합성, 시장·주문 없음).

사용::

    python scripts/jev_ew_compare.py size                 # 호출 없음: 묶음 크기·예상 비용
    python scripts/jev_ew_compare.py pilot --budget 0.10  # G0: 개발 seed 20260924 일부 (유료 호출)
    python scripts/jev_ew_compare.py confirm --confirm-holdout --budget 1.50
                                                          # G1: 예약 seed 20260927, 한 번만

* 후보 생성: EW-3e (문서 58, 120 일 창 · 새 4h 꼭짓점마다 발동), 묶음 N = 50, 기준 순위 규칙
  ``ranking.py`` 동결. 시험지는 문서 39 와 같은 칸
  (degree 3 × 패턴 2 × 잡음 0.1% 50 · 0 20 = 420 경로).
* JEV 입력에는 기준 순위·정답·``GUIDELINE_DISTANCE``·되돌림 가격이 없다 (:mod:`jev_rerank`).
* 요청마다 보수적 비용을 먼저 잡고 ``--budget`` (USD, 실측 + 이번 추정) 을 넘으면 멈춘다.
  같은 요청은 ``data/research/jev/cache`` 의 저장 응답을 다시 쓴다(재호출 없음).
* 키는 ``.env`` 의 ``TYPESAFE_API_KEY`` — 화면 · 기록 · Git 에 남기지 않는다.
"""

from __future__ import annotations

import hashlib
import json
import math
import random
import sys
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
if str(REPO_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "src"))

from scripts.audit_ew3c_hourly import CELLS, DEGREES, hour_bars  # noqa: E402

from auto_trading.config.secrets import read_env_file  # noqa: E402
from auto_trading.core.enums import Timeframe  # noqa: E402
from auto_trading.features.structure import SwingKind  # noqa: E402
from auto_trading.research.elliott import scanner  # noqa: E402
from auto_trading.research.elliott.jev_rerank import (  # noqa: E402
    MODEL,
    PRICE_PER_INPUT_TOKEN_USD,
    PROMPT_VERSION,
    Swing,
    build_request,
    estimate_cost_usd,
    parse_response,
    pessimistic_rank,
    question_digest,
)
from auto_trading.research.elliott.minute_path import replay_minute_groups_ladder  # noqa: E402
from auto_trading.research.elliott.parser import Label  # noqa: E402
from auto_trading.research.elliott.pivots import EW0_THRESHOLD_PCT  # noqa: E402
from auto_trading.research.elliott.scanner import causal_triggers, scenario_list  # noqa: E402
from auto_trading.research.elliott.scenario_contract import to_json_dict  # noqa: E402
from auto_trading.research.elliott.scenario_export import (  # noqa: E402
    _scenario_id,
    export_scenario_set,
)
from auto_trading.research.elliott.synthetic import SyntheticPath, build_path  # noqa: E402

SEEDS = {"pilot": 20260924, "size": 20260924, "confirm": 20260927}
"""20260924 = 개발 seed (문서 39 · 58 · 60 · 61 에서 이미 봄). 20260927 = JEV 예약 (문서 172 §3)."""
N = 50
URL = "https://api.typesafe.ai/v1/systemone"
DATA = REPO_ROOT / "data" / "research" / "jev"
REPORTS = REPO_ROOT / "reports" / "jev172"
PILOT_PATHS = 12
"""G0: D1 의 잡음 0.1% impulse 6 · zigzag 6 (개발 seed)."""
RETRY = (429, 529)


@dataclass(frozen=True, slots=True)
class Case:
    """경로 하나의 평가 단위 — 정답 끝 꼭짓점에서의 발동 묶음."""

    degree: str
    pattern: str
    noise: float
    triggered: bool
    truth_id: str | None
    scenario_set: dict[str, Any] | None
    sidecar: dict[str, int]
    swings: tuple[Swing, ...]

    @property
    def in_set(self) -> bool:
        return self.truth_id is not None and self.truth_id in self.sidecar


def _swing(p: Any) -> Swing:
    return Swing(
        "H" if p.kind is SwingKind.HIGH else "L",
        str(p.price),
        int(p.hour_open_ms),
        bool(p.four_hour_visible),
    )


def make_case(path: SyntheticPath, degree: str, noise: float) -> Case:
    """문서 39 ``audit_ew_discovery._one`` 과 같은 발동 찾기 (EW-3e) + 내보내기."""
    four = path.four_hour
    hours = hour_bars(path.minutes)
    groups = (path.minutes[240 * i : 240 * (i + 1)] for i in range(len(four)))
    (replay,) = replay_minute_groups_ladder(four, groups, four[-1].close_ms, (EW0_THRESHOLD_PCT,))
    truth = [
        (kind, Timeframe.H1.open_ms_of(path.minutes[t].open_ms))
        for t, kind in zip(path.vertex_minutes, path.vertex_kinds, strict=True)
    ]
    hit = next(
        (
            trig
            for trig in causal_triggers(replay, hours, four, all_new_pivots=True)
            if (trig.segment[-1].kind, trig.segment[-1].hour_open_ms) == truth[-1]
        ),
        None,
    )
    if hit is None:
        return Case(degree, path.pattern, noise, False, None, None, {}, ())
    where = {(p.kind, p.hour_open_ms): k for k, p in enumerate(hit.segment)}
    bounds = tuple(where.get(v, -1) for v in truth)
    label = Label.IMPULSE if path.pattern == "IMPULSE" else Label.ZIGZAG
    scen = scenario_list(hit, hours, size=N)
    truth_parse = next(
        (c for c, _ in scen.ranked if c.pattern is label and c.bounds == bounds), None
    )
    truth_id = None if truth_parse is None else _scenario_id(truth_parse, hit.segment, hit.close_ms)
    sset, sidecar = export_scenario_set(
        hit, scen, venue="synthetic", instrument="SYNTH", rule_profile="EW-3e"
    )
    return Case(
        degree,
        path.pattern,
        noise,
        True,
        truth_id,
        to_json_dict(sset),
        sidecar,
        tuple(_swing(p) for p in hit.segment),
    )


def cases(seed: int, *, pilot: bool) -> list[Case]:
    """문서 39 와 같은 순서로 경로를 만든다 (난수 흐름을 바꾸지 않으려고 모두 만든다)."""
    scanner.LOOKBACK_BARS = scanner.EW3E_LOOKBACK_BARS
    rng = random.Random(seed)
    out: list[Case] = []
    for degree, (scale, threshold) in DEGREES.items():
        for pattern in ("IMPULSE", "ZIGZAG"):
            for noise, count in CELLS:
                for k in range(count):
                    path = build_path(rng, pattern, scale, threshold, noise)
                    if pilot and not (degree == "D1" and noise > 0 and k < PILOT_PATHS // 2):
                        continue
                    out.append(make_case(path, degree, noise))
    return out


# ---------------------------------------------------------------- 호출 · 예산 · 캐시


def request_hash(request: Mapping[str, Any]) -> str:
    payload = json.dumps(request, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


@dataclass(slots=True)
class Budget:
    limit_usd: float
    spent_usd: float = 0.0
    calls: int = 0
    cached: int = 0
    stopped: bool = False
    ledger: list[dict[str, Any]] = field(default_factory=list)


Post = Callable[[Mapping[str, Any]], tuple[int, str]]
"""(요청 JSON) → (HTTP 상태, 본문). 시험에서는 가짜를 넘긴다."""


def http_post(key: str, timeout: float = 60.0) -> Post:
    def post(request: Mapping[str, Any]) -> tuple[int, str]:
        r = httpx.post(
            URL,
            headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
            json=request,
            timeout=timeout,
        )
        return r.status_code, r.text

    return post


def call(
    request: Mapping[str, Any],
    post: Post,
    budget: Budget,
    cache_dir: Path,
    *,
    sleep: Callable[[float], None] = time.sleep,
) -> tuple[str | None, str | None]:
    """(원응답, 오류). 캐시 우선. 예산을 넘길 요청은 보내지 않는다."""
    h = request_hash(request)
    cached = cache_dir / f"{h}.json"
    if cached.exists():
        budget.cached += 1
        return cached.read_text(encoding="utf-8"), None
    est = estimate_cost_usd(request)
    if budget.stopped or budget.spent_usd + est > budget.limit_usd:
        budget.stopped = True
        return None, "BUDGET_STOP"
    status, body = 0, ""
    for attempt in range(4):
        try:
            status, body = post(request)
        except httpx.HTTPError as exc:
            status, body = -1, type(exc).__name__
        budget.calls += 1
        if status not in RETRY:
            break
        sleep(min(2.0**attempt, 8.0))
    entry: dict[str, Any] = {"hash": h, "status": status, "est_usd": round(est, 6)}
    if status != 200:
        budget.spent_usd += est  # 과금 여부를 모르면 추정만큼 썼다고 본다
        entry["error"] = body[:300]
        budget.ledger.append(entry)
        return None, f"HTTP {status}"
    try:
        usage = json.loads(body).get("usage") or {}
        tokens = usage.get("input_tokens")
    except ValueError:
        tokens = None
    cost = est if tokens is None else int(tokens) * PRICE_PER_INPUT_TOKEN_USD
    budget.spent_usd += cost
    entry.update(input_tokens=tokens, cost_usd=round(cost, 6))
    budget.ledger.append(entry)
    cache_dir.mkdir(parents=True, exist_ok=True)
    cached.write_text(body, encoding="utf-8")
    return body, None


# ---------------------------------------------------------------- 채점


def mcnemar_one_sided(jev_only: int, base_only: int) -> float:
    """JEV 만 맞힌 수 ``jev_only`` 가 이만큼 이상일 확률 (불일치 쌍 이항 0.5, 정확 검정)."""
    n = jev_only + base_only
    if n == 0:
        return 1.0
    return float(sum(math.comb(n, k) for k in range(jev_only, n + 1)) / 2**n)


def score(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """문서 172 §4: recall@50 과 묶음 안 선택률을 나눠 적고, 전체 1등 성공의 짝 차이를 본다."""
    total = len(rows)
    inset = [r for r in rows if r["in_set"]]
    out: dict[str, Any] = {
        "paths": total,
        "no_trigger": sum(not r["triggered"] for r in rows),
        "recall_at_50": round(len(inset) / total, 4) if total else None,
        "jev_unavailable": sum(
            1 for r in rows if r["jev_verdict"] not in (None, "RELATIVE_SUPPORT")
        ),
    }
    for who in ("base", "jev"):
        for k in (1, 3, 10):
            hit = sum(1 for r in inset if r[f"{who}_rank"] is not None and r[f"{who}_rank"] <= k)
            out[f"{who}_inset_top{k}"] = round(hit / len(inset), 4) if inset else None
        out[f"{who}_top1_all"] = (
            round(sum(1 for r in rows if r[f"{who}_rank"] == 1) / total, 4) if total else None
        )
    jev_only = sum(1 for r in rows if r["jev_rank"] == 1 and r["base_rank"] != 1)
    base_only = sum(1 for r in rows if r["base_rank"] == 1 and r["jev_rank"] != 1)
    out.update(
        jev_only=jev_only,
        base_only=base_only,
        diff_top1_all=round((jev_only - base_only) / total, 4) if total else None,
        mcnemar_p_one_sided=round(mcnemar_one_sided(jev_only, base_only), 6),
    )
    return out


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
            request, keymap = build_request(c.scenario_set, c.swings)
            row["est_usd"] = round(estimate_cost_usd(request), 6)
            if post is not None:
                raw, err = call(request, post, budget, DATA / "cache")
                ev = parse_response(
                    c.scenario_set, keymap, raw, evaluated_at_ms=int(time.time() * 1000), error=err
                )
                row["jev_verdict"] = ev.verdict.value
                row["model"] = ev.model_version
                if c.in_set and c.truth_id is not None:
                    row["jev_rank"] = pessimistic_rank(ev, c.truth_id)
        rows.append(row)
    return rows


def main(argv: list[str]) -> int:
    if not argv or argv[0] not in SEEDS:
        raise SystemExit(__doc__)
    mode = argv[0]
    if mode == "confirm":
        if "--confirm-holdout" not in argv:
            raise SystemExit("예약 seed 는 --confirm-holdout 과 함께 한 번만 (문서 172 §3)")
        if (REPORTS / "confirm.json").exists():
            raise SystemExit("확인 시험은 이미 했다 — 다시 채점하지 않는다")
    budget = Budget(float(argv[argv.index("--budget") + 1]) if "--budget" in argv else 0.0)
    post: Post | None = None
    if mode != "size":
        key = read_env_file(REPO_ROOT / ".env").get("TYPESAFE_API_KEY", "").strip()
        if not key:
            raise SystemExit("TYPESAFE_API_KEY 없음")
        if budget.limit_usd <= 0:
            raise SystemExit("--budget (USD) 를 밝혀야 한다")
        post = http_post(key)
    started = time.time()
    cs = cases(SEEDS[mode], pilot=mode == "pilot")
    rows = evaluate(cs, post, budget)
    groups: dict[str, list[dict[str, Any]]] = {}
    for r in rows:
        groups.setdefault(f"noise={r['noise']}|{r['pattern']}", []).append(r)
        groups.setdefault(f"noise={r['noise']}|pooled", []).append(r)
    est = [r["est_usd"] for r in rows if "est_usd" in r]
    report = {
        "mode": mode,
        "seed": SEEDS[mode],
        "model": MODEL,
        "prompt_version": PROMPT_VERSION,
        "question_sha256": question_digest(),
        "generator": "EW-3e",
        "set_size_n": N,
        "est_total_usd": round(sum(est), 4),
        "spent_usd": round(budget.spent_usd, 6),
        "calls": budget.calls,
        "cached": budget.cached,
        "budget_stopped": budget.stopped,
        "elapsed_s": round(time.time() - started, 1),
        "groups": {k: score(v) for k, v in sorted(groups.items())},
    }
    REPORTS.mkdir(parents=True, exist_ok=True)
    (REPORTS / f"{mode}.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=1) + "\n", encoding="utf-8"
    )
    DATA.mkdir(parents=True, exist_ok=True)
    with (DATA / f"{mode}_rows.jsonl").open("w", encoding="utf-8", newline="\n") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    with (DATA / "ledger.jsonl").open("a", encoding="utf-8", newline="\n") as f:
        for e in budget.ledger:
            f.write(json.dumps({"mode": mode, **e}, ensure_ascii=False) + "\n")
    print(json.dumps({k: v for k, v in report.items() if k != "groups"}, ensure_ascii=False))
    for k, v in report["groups"].items():
        print(k, json.dumps(v, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
