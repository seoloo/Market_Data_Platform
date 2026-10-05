"""Phase 7 테스트넷 14일 판정 채점 (docs/research/26 §2) — 순수 함수, I/O 없음.

기준 P1~P4 는 운영 시작 전에 문서 26 에 고정했다. 여기서는 그 기준을 훈련 기록에 대어 본다.
결과 상태는 10/24 점검(:mod:`auto_trading.ops.review`)과 같다:
``PASS`` · ``FAIL`` · ``CHECK``(사람이 원인을 적어야 함) · ``NA``(아직 기간 중).

실패 원인(P2·P3)은 사람이 판단해 ``causes`` 로 넘긴다 — 코드는 원인을 추측하지 않는다.
원인이 적히지 않은 실패는 PASS 로 두지 않고 CHECK 로 남긴다.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from auto_trading.ops.review import Criterion

__all__ = [
    "CAUSE_KINDS",
    "FINAL_STEP",
    "PHASE7_DAYS",
    "PHASE7_FIRST",
    "Cause",
    "phase7_review",
]

PHASE7_FIRST = "2026-09-25"
PHASE7_DAYS = 14
FINAL_STEP = "포지션·일반 주문·스톱 0, RUNNING"
"""훈련의 마지막 판정 (`scripts/testnet_drill.py`). 이것이 통과해야 그날 테스트넷에 흔적이 없다."""
START_STEP = "정리 뒤 평탄"
CAUSE_KINDS = ("external", "code")
"""external = 테스트넷 점검·네트워크 같은 바깥 원인(기록하고 넘어감) · code = 우리 코드 결함(P3)."""


@dataclass(frozen=True, slots=True)
class Cause:
    kind: str
    """``CAUSE_KINDS`` 중 하나."""
    text: str
    fixed: str | None = None
    """코드 결함을 고친 날(YYYY-MM-DD). 그날부터 14일을 다시 센다(P3)."""
    p4: str | None = None
    """마무리 전에 멈춘 날의 흔적 확인 근거(사람이 적음). 있으면 그날의 P4 CHECK 가 풀린다."""


def _day(at: str) -> str:
    """보고서 시각 ``20260925T120809Z`` → UTC 날짜. 21:08 KST 실행은 KST 날짜와 같다."""
    return datetime.strptime(at, "%Y%m%dT%H%M%SZ").replace(tzinfo=UTC).strftime("%Y-%m-%d")


def _shift(day: str, days: int) -> str:
    d = datetime.strptime(day, "%Y-%m-%d").replace(tzinfo=UTC) + timedelta(days=days)
    return d.strftime("%Y-%m-%d")


def _step(report: Mapping[str, Any], name: str) -> Mapping[str, Any] | None:
    return next((c for c in report.get("checks", []) if c.get("step") == name), None)


def phase7_review(
    reports: Sequence[Mapping[str, Any]],
    causes: Mapping[str, Cause],
    *,
    until: str,
    first: str = PHASE7_FIRST,
    days: int = PHASE7_DAYS,
) -> tuple[list[Criterion], dict[str, Any]]:
    """문서 26 §2 P1~P4. ``reports`` 는 ``drill-*.json`` 들, ``causes`` 는 {날짜: 원인}.

    ``until`` 은 마지막으로 셀 날(포함). 기간 끝(``first`` + 13일)보다 앞이면 중간 점검이다.
    한 날에 여러 번 돌았으면 **그날 마지막 실행**이 그날의 결과다(일일 점검과 같다).
    그날 앞선 실행의 실패도 P2 원인 설명 대상에 넣는다.
    """
    last = _shift(first, days - 1)
    end = min(until, last)
    complete = until >= last
    expected = [_shift(first, k) for k in range(days) if _shift(first, k) <= end]
    by_day: dict[str, list[Mapping[str, Any]]] = {}
    for r in sorted(reports, key=lambda r: str(r.get("at"))):
        day = _day(str(r["at"]))
        if first <= day <= end:
            by_day.setdefault(day, []).append(r)
    missing = [d for d in expected if d not in by_day]
    failed_days = sorted(d for d, runs in by_day.items() if any(not r.get("ok") for r in runs))
    unexplained = [d for d in failed_days if d not in causes]
    bad_kind = [d for d in failed_days if d in causes and causes[d].kind not in CAUSE_KINDS]
    code_days = [d for d in failed_days if d in causes and causes[d].kind == "code"]
    unfinished: list[str] = []
    dirty: list[str] = []
    for day, runs in sorted(by_day.items()):
        final = _step(runs[-1], FINAL_STEP)
        if final is None:
            unfinished.append(day)
        elif not final.get("pass"):
            dirty.append(day)

    if missing:
        p1 = Criterion("P1", "FAIL", f"실행 기록 없는 날 {len(missing)}일 {missing}")
    elif not complete:
        p1 = Criterion("P1", "NA", f"기간 중 — {len(expected)}/{days}일 모두 기록 있음")
    else:
        p1 = Criterion("P1", "PASS", f"{days}/{days}일 모두 기록 있음")

    if unexplained or bad_kind:
        p2 = Criterion(
            "P2",
            "CHECK",
            f"실패한 날 {len(failed_days)}일 중 원인 미기록 {unexplained}"
            + (f" · 원인 종류 모름 {bad_kind}" if bad_kind else ""),
        )
    else:
        p2 = Criterion(
            "P2",
            "PASS",
            f"실패한 날 {len(failed_days)}일" + (" — 모두 원인 기록" if failed_days else ""),
        )

    if code_days:
        restart = max(causes[d].fixed or d for d in code_days)
        p3 = Criterion(
            "P3",
            "FAIL",
            f"코드 결함 실패 {code_days} — 고친 날 {restart} 부터 14일을 다시 센다"
            f" ({restart} ~ {_shift(restart, days - 1)})",
        )
    elif unexplained or bad_kind:
        p3 = Criterion("P3", "CHECK", "원인 미기록 실패가 있어 코드 결함 여부를 모른다")
    else:
        p3 = Criterion("P3", "PASS", "코드 결함 실패 0")

    confirmed = [d for d in unfinished if d in causes and causes[d].p4]
    unconfirmed = [d for d in unfinished if d not in confirmed]
    if dirty:
        p4 = Criterion("P4", "FAIL", f"마무리 상태가 평탄·주문 0·스톱 0 이 아닌 날 {dirty}")
    elif unconfirmed:
        p4 = Criterion(
            "P4",
            "CHECK",
            f"마무리 전에 멈춘 날 {unconfirmed} — 다음 실행의 시작 상태로 흔적 여부를 확인",
        )
    elif confirmed:
        p4 = Criterion(
            "P4",
            "PASS",
            f"마무리된 날 모두 평탄·주문 0·스톱 0 · 마무리 전에 멈춘 날 {confirmed}"
            " 은 흔적 없음 확인",
        )
    else:
        p4 = Criterion("P4", "PASS", f"{len(by_day)}일 모두 마무리 평탄·주문 0·스톱 0")

    crit = [p1, p2, p3, p4]
    if not complete:
        verdict = "기간 중" + (
            " — 이미 실패한 기준 있음" if any(c.status == "FAIL" for c in crit) else ""
        )
    elif all(c.status == "PASS" for c in crit):
        verdict = "PASS"
    elif any(c.status == "FAIL" for c in crit):
        verdict = "FAIL"
    else:
        verdict = "CHECK"
    runs_info = {
        day: [
            {
                "at": r.get("at"),
                "ok": bool(r.get("ok")),
                "passed": sum(bool(c.get("pass")) for c in r.get("checks", [])),
                "checks": len(r.get("checks", [])),
                "failed_steps": [c.get("step") for c in r.get("checks", []) if not c.get("pass")],
                "error": r.get("error"),
                "start_state": (_step(r, START_STEP) or {}).get("detail"),
            }
            for r in runs
        ]
        for day, runs in sorted(by_day.items())
    }
    return crit, {
        "first": first,
        "last": last,
        "until": end,
        "complete": complete,
        "verdict": verdict,
        "missing": missing,
        "failed_days": failed_days,
        "unexplained": unexplained,
        "code_days": code_days,
        "unfinished": unfinished,
        "unfinished_confirmed": confirmed,
        "dirty": dirty,
        "causes": {
            d: {"kind": c.kind, "text": c.text, "fixed": c.fixed, "p4": c.p4}
            for d, c in causes.items()
        },
        "runs": runs_info,
    }
