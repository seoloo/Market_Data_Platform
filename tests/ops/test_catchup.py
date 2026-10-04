"""놓친 예약 작업 다시 실행 (ops/catchup.py) — 고르기·순서·하나씩 기다리기."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from auto_trading.ops.catchup import Task, due_time, parse_tasks, plan, run_in_order, start_task

KST = timezone(timedelta(hours=9))
NOW = datetime(2026, 10, 2, 9, 0, tzinfo=KST)  # 어젯밤 PC 가 꺼져 있었다고 치고 아침에 켬


def _t(name: str, times: tuple[str, ...], last: datetime | None, result: int = 0) -> Task:
    return Task(f"AutoTrading_{name}", times, last, result, running=False)


def test_due_time_is_latest_past_occurrence() -> None:
    assert due_time(["21:00"], NOW) == datetime(2026, 10, 1, 21, 0, tzinfo=KST)
    night = datetime(2026, 10, 2, 21, 0, tzinfo=KST)
    assert due_time(["21:00"], night) == night
    assert due_time(["07:00", "22:45"], NOW) == datetime(2026, 10, 2, 7, 0, tzinfo=KST)
    assert due_time([], NOW) is None


def test_plan_picks_missed_and_failed_in_schedule_order_watchdog_last() -> None:
    before = datetime(2026, 9, 30, 21, 5, tzinfo=KST)
    after = datetime(2026, 10, 1, 21, 30, tzinfo=KST)
    tasks = [
        _t("Paper_Watchdog", ("21:20",), before),
        _t("Paper_O1", ("21:02",), before),
        _t("Paper_T2", ("21:00",), before),
        _t("Testnet_Overlay", ("20:45",), before),
        _t("Shadow_T2", ("21:10",), after),  # 돌았다
        _t("Testnet_Drill", ("21:08",), after, result=1),  # 돌았지만 실패
        _t("Demo_T2", ("21:12",), after, result=0x41301),  # 실행 중 코드는 실패 아님
        _t("Demo_EW", ("13:31",), before),  # 상주 실행기
        _t("Hourly", (), before),  # 매일 작업 아님
        Task("Other_Task", ("21:00",), before, 0, running=False),
    ]
    due = plan(tasks, NOW)
    assert [d.name.removeprefix("AutoTrading_") for d in due] == [
        "Testnet_Overlay",
        "Paper_T2",
        "Paper_O1",
        "Testnet_Drill",
        "Paper_Watchdog",
    ]
    assert {d.reason for d in due if d.name.endswith("Drill")} == {"FAILED"}
    assert "0x00000001" in due[3].detail


def test_running_task_is_not_started_again() -> None:
    t = Task("AutoTrading_Paper_T2", ("21:00",), None, 0, running=True)
    assert plan([t], NOW) == []


def test_parse_powershell_rows() -> None:
    rows: list[dict[str, object]] = [
        {
            "name": "AutoTrading_TradFi_Snapshot",
            "times": ["22:45", "23:30"],
            "last_run": "2026-10-02T07:33:33.0000000+09:00",
            "last_result": 0,
            "state": "Ready",
        },
        {"name": "AutoTrading_Paper_T2", "times": "21:00", "last_run": "1999-11-30T00:00:00"},
        {"name": "AutoTrading_Demo_EW", "times": None, "last_run": "", "state": "Running"},
    ]
    a, b, c = parse_tasks(rows)
    assert a.times == ("22:45", "23:30") and a.last_run is not None and not a.running
    assert b.times == ("21:00",) and b.last_run is None  # 한 번도 안 돌았다
    assert c.times == () and c.running


def test_start_task_only_accepts_plain_autotrading_names() -> None:
    for bad in ("Other", "AutoTrading_X'; Remove-Item C:\\", "AutoTrading_A B"):
        with pytest.raises(ValueError):
            start_task(bad)


def test_run_in_order_waits_for_each_task_to_finish() -> None:
    t0 = datetime(2026, 10, 1, 21, 0, tzinfo=KST)
    t1 = datetime(2026, 10, 2, 9, 1, tzinfo=KST)
    tasks = {
        "AutoTrading_A": Task("AutoTrading_A", ("21:00",), t0, 0, False),
        "AutoTrading_B": Task("AutoTrading_B", ("21:02",), t0, 0, False),
    }
    events: list[str] = []
    polls: dict[str, int] = {}

    def start(name: str) -> None:
        events.append(f"start {name}")
        polls[name] = 0

    def query() -> list[Task]:
        for name, n in polls.items():
            old = tasks[name]
            polls[name] = n + 1
            if n == 0:  # 첫 확인: 돌고 있다
                tasks[name] = Task(name, old.times, t1, 0x41301, True)
            elif n == 1:  # 다음: 끝남 (A 성공, B 실패)
                code = 0 if name.endswith("A") else 1
                tasks[name] = Task(name, old.times, t1, code, False)
        return list(tasks.values())

    lines: list[str] = []
    results = run_in_order(
        ["AutoTrading_A", "AutoTrading_B", "AutoTrading_Gone"],
        log=lines.append,
        query=query,
        start=start,
        sleep=lambda s: None,
    )
    assert events == ["start AutoTrading_A", "start AutoTrading_B"]
    assert results == [("AutoTrading_A", 0), ("AutoTrading_B", 1), ("AutoTrading_Gone", None)]
    assert any("실패 코드" in x for x in lines) and any("건너뜀" in x for x in lines)


def test_run_in_order_gives_up_after_timeout() -> None:
    t = Task("AutoTrading_A", ("21:00",), datetime(2026, 10, 1, 21, 0, tzinfo=KST), 0, False)
    lines: list[str] = []
    results = run_in_order(
        ["AutoTrading_A"],
        log=lines.append,
        query=lambda: [t],  # 시작해도 마지막 실행 시각이 바뀌지 않는다
        start=lambda name: None,
        sleep=lambda s: None,
        poll_s=1.0,
        task_timeout_s=3.0,
    )
    assert results == [("AutoTrading_A", None)]
    assert lines[-1].endswith("시간 초과")


def test_disabled_late_registered_and_watchdog_warning_are_not_due() -> None:
    """2026-10-04: 꺼진 훈련 작업 · 등록 전 예정 시각 · 점검 경고(1) 를 다시 돌리려다 막혔다."""
    night = datetime(2026, 10, 4, 12, 0, tzinfo=KST)
    before = datetime(2026, 10, 2, 20, 45, tzinfo=KST)
    tasks = [
        Task("AutoTrading_Testnet_Overlay", ("20:45",), before, 0, False, disabled=True),
        Task(
            "AutoTrading_AG_Paper",
            ("21:17",),
            None,
            0x41303,
            False,
            created=datetime(2026, 10, 4, 0, 31, tzinfo=KST),
        ),
        _t("Paper_Watchdog", ("21:20",), datetime(2026, 10, 3, 21, 20, tzinfo=KST), result=1),
        _t("Paper_T2", ("21:00",), datetime(2026, 10, 3, 21, 0, tzinfo=KST), result=2),
    ]
    assert [d.name for d in plan(tasks, night)] == ["AutoTrading_Paper_T2"]
    # 점검이라도 2 (CRITICAL) 는 실패
    crit = _t("Paper_Watchdog", ("21:20",), datetime(2026, 10, 3, 21, 20, tzinfo=KST), result=2)
    assert [d.reason for d in plan([crit], night)] == ["FAILED"]


def test_weekly_triggers_skip_weekends() -> None:
    sunday_noon = datetime(2026, 10, 4, 12, 0, tzinfo=KST)  # 일요일
    fri = datetime(2026, 10, 2, 21, 0, tzinfo=KST)
    assert due_time(["21:00@0,1,2,3,4"], sunday_noon) == fri
    ran_fri = _t("Toss_Accumulate", ("21:00@0,1,2,3,4",), datetime(2026, 10, 2, 21, 1, tzinfo=KST))
    assert plan([ran_fri], sunday_noon) == []
    monday_late = datetime(2026, 10, 5, 22, 0, tzinfo=KST)
    assert [d.name for d in plan([ran_fri], monday_late)] == ["AutoTrading_Toss_Accumulate"]
    rows = [
        {
            "name": "AutoTrading_Toss_FX",
            "times": ["09:00#62", "15:00#62"],
            "state": "Ready",
            "created": "2026-10-04T10:00:00+09:00",
        }
    ]
    (t,) = parse_tasks(rows)
    assert t.times == ("09:00@0,1,2,3,4", "15:00@0,1,2,3,4") and t.created is not None
    assert parse_tasks([{"name": "x", "state": "Disabled"}])[0].disabled


def test_start_failure_skips_only_that_task() -> None:
    t0 = datetime(2026, 10, 1, 21, 0, tzinfo=KST)
    t1 = datetime(2026, 10, 2, 9, 1, tzinfo=KST)
    tasks = {
        "AutoTrading_Off": Task("AutoTrading_Off", ("20:45",), t0, 0, False),
        "AutoTrading_B": Task("AutoTrading_B", ("21:02",), t0, 0, False),
    }
    started: list[str] = []

    def start(name: str) -> None:
        if name.endswith("Off"):
            raise RuntimeError("disabled")
        started.append(name)
        tasks[name] = Task(name, ("21:02",), t1, 0, False)

    lines: list[str] = []
    results = run_in_order(
        ["AutoTrading_Off", "AutoTrading_B"],
        log=lines.append,
        query=lambda: list(tasks.values()),
        start=start,
        sleep=lambda s: None,
    )
    assert started == ["AutoTrading_B"]
    assert results == [("AutoTrading_Off", None), ("AutoTrading_B", 0)]
    assert any("시작 실패" in x for x in lines)
