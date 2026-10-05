"""문서 26 §2 Phase 7 판정 채점 — P1~P4 경계, 원인 미기록은 CHECK, 코드 결함은 다시 세기."""

from __future__ import annotations

from typing import Any

from auto_trading.ops.phase7_review import FINAL_STEP, Cause, phase7_review


def _run(
    day: str, *, ok: bool = True, final: bool | None = True, hhmm: str = "1208"
) -> dict[str, Any]:
    checks: list[dict[str, Any]] = [{"step": "정리 뒤 평탄", "pass": True, "detail": "RUNNING []"}]
    checks.append({"step": "주문 1건만 체결", "pass": ok, "detail": ""})
    if final is not None:
        checks.append({"step": FINAL_STEP, "pass": final, "detail": ""})
    return {"at": f"{day.replace('-', '')}T{hhmm}09Z", "ok": ok, "error": None, "checks": checks}


def _days(n: int, first: str = "2026-09-25") -> list[str]:
    y, m, d = (int(x) for x in first.split("-"))
    out = []
    for _ in range(n):
        out.append(f"{y:04d}-{m:02d}-{d:02d}")
        d += 1
        if d > (30 if m == 9 else 31):
            d, m = 1, m + 1
    return out


def _status(crit: list[Any], cid: str) -> str:
    return str(next(c.status for c in crit if c.id == cid))


def test_all_fourteen_clean_days_pass() -> None:
    days = _days(14)
    crit, info = phase7_review([_run(d) for d in days], {}, until="2026-10-08")
    assert [c.status for c in crit] == ["PASS"] * 4
    assert info["verdict"] == "PASS"
    assert days[-1] == "2026-10-08"


def test_midway_is_not_a_pass_and_ignores_runs_before_the_period() -> None:
    reports = [_run("2026-09-24", ok=False, final=None)] + [_run(d) for d in _days(3)]
    crit, info = phase7_review(reports, {}, until="2026-09-27")
    assert _status(crit, "P1") == "NA"
    assert info["verdict"] == "기간 중"
    assert "2026-09-24" not in info["runs"]  # 개발 중 실행은 기간 통계에 넣지 않는다


def test_missing_day_fails_p1_even_midway() -> None:
    days = _days(5)
    crit, info = phase7_review([_run(d) for d in days if d != "2026-09-27"], {}, until=days[-1])
    assert _status(crit, "P1") == "FAIL"
    assert info["missing"] == ["2026-09-27"]
    assert info["verdict"].startswith("기간 중 — 이미 실패")


def test_unexplained_failure_is_check_not_pass() -> None:
    days = _days(14)
    reports = [_run(d, ok=(d != "2026-10-01")) for d in days]
    crit, info = phase7_review(reports, {}, until="2026-10-08")
    assert _status(crit, "P2") == "CHECK"
    assert _status(crit, "P3") == "CHECK"
    assert info["verdict"] == "CHECK"


def test_external_cause_passes_p2_and_p3() -> None:
    days = _days(14)
    reports = [_run(d, ok=(d != "2026-10-01")) for d in days]
    causes = {"2026-10-01": Cause("external", "테스트넷 점검")}
    crit, info = phase7_review(reports, causes, until="2026-10-08")
    assert [c.status for c in crit] == ["PASS"] * 4
    assert info["failed_days"] == ["2026-10-01"]


def test_code_defect_fails_p3_and_names_the_restart_window() -> None:
    days = _days(14)
    reports = [_run(d, ok=(d != "2026-09-30")) for d in days]
    causes = {"2026-09-30": Cause("code", "취소 뒤 조회 지연", fixed="2026-10-01")}
    crit, info = phase7_review(reports, causes, until="2026-10-08")
    p3 = next(c for c in crit if c.id == "P3")
    assert p3.status == "FAIL"
    assert "2026-10-01 ~ 2026-10-14" in p3.detail
    assert info["verdict"] == "FAIL"


def test_unknown_cause_kind_is_check() -> None:
    days = _days(14)
    reports = [_run(d, ok=(d != "2026-10-01")) for d in days]
    crit, _ = phase7_review(reports, {"2026-10-01": Cause("script", "?")}, until="2026-10-08")
    assert _status(crit, "P2") == "CHECK"


def test_earlier_failed_run_on_a_passing_day_still_needs_a_cause() -> None:
    days = _days(14)
    reports = [_run(d) for d in days] + [_run("2026-10-02", ok=False, hhmm="1100")]
    crit, info = phase7_review(reports, {}, until="2026-10-08")
    assert info["failed_days"] == ["2026-10-02"]
    assert _status(crit, "P2") == "CHECK"
    assert _status(crit, "P4") == "PASS"  # 그날 마지막 실행은 깨끗이 마무리


def test_p4_dirty_final_fails_and_aborted_run_is_check() -> None:
    days = _days(14)
    dirty = [_run(d, final=(d != "2026-10-03")) for d in days]
    crit, _ = phase7_review(dirty, {}, until="2026-10-08")
    assert _status(crit, "P4") == "FAIL"
    aborted = [
        _run(d, ok=(d != "2026-10-03"), final=(None if d == "2026-10-03" else True)) for d in days
    ]
    crit, info = phase7_review(
        aborted, {"2026-10-03": Cause("external", "네트워크")}, until="2026-10-08"
    )
    assert _status(crit, "P4") == "CHECK"
    assert info["unfinished"] == ["2026-10-03"]


def test_p4_check_clears_only_with_a_recorded_no_trace_confirmation() -> None:
    days = _days(14)
    aborted = [
        _run(d, ok=(d != "2026-10-03"), final=(None if d == "2026-10-03" else True)) for d in days
    ]
    confirmed = Cause("external", "네트워크", p4="다음 날 시작 RUNNING [] · 그날 주문 0")
    crit, info = phase7_review(aborted, {"2026-10-03": confirmed}, until="2026-10-08")
    assert _status(crit, "P4") == "PASS"
    assert info["unfinished_confirmed"] == ["2026-10-03"]
    assert info["verdict"] == "PASS"
    # 확인이 있어도 다른 날 마무리가 더러우면 FAIL
    dirty = [*aborted[:-1], _run(days[-1], final=False)]
    crit, _ = phase7_review(dirty, {"2026-10-03": confirmed}, until="2026-10-08")
    assert _status(crit, "P4") == "FAIL"


def test_restarted_window_counts_from_first() -> None:
    days = _days(14, first="2026-10-01")
    _, info = phase7_review([_run(d) for d in days], {}, until="2026-10-14", first="2026-10-01")
    assert info["verdict"] == "PASS"
    assert info["last"] == "2026-10-14"
