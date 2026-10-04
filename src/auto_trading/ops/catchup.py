"""놓친 예약 작업 다시 실행 — 대시보드 "실행 현황" 버튼 (사용자 요청 2026-10-02).

밤 9 시 무렵 PC 가 꺼져 있었으면 작업이 그 시각에 돌지 못한다.
작업 스케줄러의 "놓친 실행 바로 시작"(StartWhenAvailable)이 켜는 순간 한꺼번에 돌려 주지만,
**모두 같은 순간에** 시작해서 순서가 깨진다 (2026-10-01 21:50: 점검이 Paper 기록보다 먼저 돌아
CRITICAL, 테스트넷 훈련이 같은 계좌의 오버레이 훈련 주문에 막혀 실패).

이 모듈은 (1) 각 작업의 가장 최근 예정 시각보다 마지막 실행이 이르거나 마지막 결과가 실패인 작업을
고르고(:func:`plan`), (2) 예정 시각 순서대로 **하나씩, 앞 작업이 끝난 뒤** 작업 스케줄러로 시작한다
(:func:`run_in_order`). 점검(Watchdog)은 늘 마지막.

* 작업 스케줄러에 등록된 작업을 그대로 시작한다 — 예약 실행과 같은 계정·명령·.env.
  각 스크립트의 멱등 장치(Paper 는 UTC 하루 1 번, 적립은 하루 주문 1 건)가 그대로 지킨다.
* 놓친 Paper UTC 날짜를 되살리지는 않는다 — 각 실행기는 지금 시점 기준으로 판단한다.
* 상주 실행기(데모 Elliott)는 고르지 않는다.
* (2026-10-04) 꺼진 작업은 고르지 않는다 · 등록하기 전 예정 시각은 놓친 것이 아니다 ·
  점검(Watchdog)의 결과 1 은 "경고 있음"(정상 동작) · 시작에 실패한 작업은 적고 다음으로 ·
  주간(월~금 등) 트리거도 본다.
"""

from __future__ import annotations

import json
import subprocess
import sys
import time
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from auto_trading.ops.task_result import RESIDENT, RESULT_OK, result_text

__all__ = [
    "PREFIX",
    "Due",
    "Task",
    "due_time",
    "parse_tasks",
    "plan",
    "query_tasks",
    "run_in_order",
    "start_task",
]

PREFIX = "AutoTrading_"
LAST = "AutoTrading_Paper_Watchdog"
"""다른 작업의 기록을 읽어 점검하므로 늘 마지막."""
EXTRA_OK = {LAST: frozenset({1})}
"""작업별로 실패가 아닌 결과 — 점검은 경고가 있으면 1, CRITICAL 이면 2 로 끝난다."""


@dataclass(frozen=True, slots=True)
class Task:
    name: str
    times: tuple[str, ...]
    """트리거 시각 (현지 시각): 매일 "HH:MM", 주간 "HH:MM@0,1,2,3,4" (월 0 … 일 6).
    비어 있으면 정해진 시각 작업이 아니다."""
    last_run: datetime | None
    last_result: int
    running: bool
    disabled: bool = False
    created: datetime | None = None
    """트리거가 처음 돌 수 있는 시각(StartBoundary 중 가장 이른 것) — 그 전 예정 시각은 없었다."""


@dataclass(frozen=True, slots=True)
class Due:
    name: str
    due: datetime
    """놓쳤거나 실패한 예정 시각."""
    reason: str
    """MISSED(그 시각 뒤로 실행 없음) · FAILED(실행은 했으나 실패 결과)."""
    detail: str


def due_time(times: Iterable[str], now: datetime) -> datetime | None:
    """``now`` 이전(같거나)의 가장 최근 예정 시각.

    오늘 것이 아직이면 그 전 날 — 주간 트리거면 그 전 해당 요일.
    """
    best: datetime | None = None
    for spec in times:
        hhmm, _, days = spec.partition("@")
        allowed = {int(d) for d in days.split(",") if d} if days else set(range(7))
        if not allowed:
            continue
        h, m = (int(x) for x in hhmm.split(":"))
        at = now.replace(hour=h, minute=m, second=0, microsecond=0)
        if at > now:
            at -= timedelta(days=1)
        for _ in range(7):
            if at.weekday() in allowed:
                break
            at -= timedelta(days=1)
        if best is None or at > best:
            best = at
    return best


def plan(tasks: Sequence[Task], now: datetime) -> list[Due]:
    """다시 실행할 작업, 실행 순서대로 (예정 시각 순 · 점검은 마지막).

    ``now`` 는 현지 시간대가 붙은 시각 — 트리거 "HH:MM" 은 그 시간대로 읽는다.
    """
    out: list[Due] = []
    for t in tasks:
        if not t.name.startswith(PREFIX) or t.name in RESIDENT or not t.times or t.running:
            continue
        if t.disabled:
            continue
        due = due_time(t.times, now)
        if due is None:
            continue
        if t.created is not None and due < t.created:
            continue  # 트리거가 시작되기 전 시각 — 놓친 것이 아니다 (등록 전)
        if t.last_run is None or t.last_run < due:
            last = t.last_run.strftime("%m-%d %H:%M") if t.last_run else "기록 없음"
            out.append(Due(t.name, due, "MISSED", f"마지막 실행 {last}"))
        elif t.last_result not in RESULT_OK and t.last_result not in EXTRA_OK.get(t.name, ()):
            out.append(Due(t.name, due, "FAILED", result_text(t.last_result)))
    out.sort(key=lambda d: (d.name == LAST, d.due, d.name))
    return out


def _local(text: str | None) -> datetime | None:
    """PowerShell 'o' 형식 → 시간대 붙은 현지 시각. 실행한 적 없으면 1999/0001 년이 온다."""
    if not text:
        return None
    try:
        at = datetime.fromisoformat(text)
    except ValueError:
        return None
    if at.year < 2000:
        return None
    return at.astimezone()


_DAY_BITS = ((2, 0), (4, 1), (8, 2), (16, 3), (32, 4), (64, 5), (1, 6))
"""작업 스케줄러 DaysOfWeek 비트 (일 1 · 월 2 · … · 토 64) → 파이썬 요일 (월 0 … 일 6)."""


def _spec(text: str) -> str:
    """'HH:MM' 그대로, 주간 'HH:MM#비트' → 'HH:MM@요일들'."""
    hhmm, _, mask = text.partition("#")
    if not mask:
        return hhmm
    bits = int(mask)
    return hhmm + "@" + ",".join(str(d) for bit, d in _DAY_BITS if bits & bit)


def parse_tasks(rows: Sequence[Mapping[str, Any]]) -> list[Task]:
    tasks = []
    for r in rows:
        times = r.get("times") or []
        if isinstance(times, str):  # 하나면 배열이 아니라 문자열로 온다
            times = [times]
        state = str(r.get("state", ""))
        tasks.append(
            Task(
                name=str(r.get("name", "")),
                times=tuple(_spec(str(x)) for x in times if x),
                last_run=_local(r.get("last_run")),
                last_result=int(r.get("last_result") or 0),
                running=state == "Running",
                disabled=state == "Disabled",
                created=_local(r.get("created")),
            )
        )
    return tasks


# ------------------------------------------------------------------ I/O (Windows)

_QUERY = r"""
$rows = @(Get-ScheduledTask -TaskName 'AutoTrading_*' | ForEach-Object {
  $i = $_ | Get-ScheduledTaskInfo
  $times = @($_.Triggers | ForEach-Object {
    $k = $_.CimClass.CimClassName; $hm = ([datetime]$_.StartBoundary).ToString('HH:mm')
    if ($k -eq 'MSFT_TaskDailyTrigger') { $hm }
    elseif ($k -eq 'MSFT_TaskWeeklyTrigger') { $hm + '#' + [int]$_.DaysOfWeek } })
  $made = @($_.Triggers | Where-Object { $_.StartBoundary } |
    ForEach-Object { ([datetime]$_.StartBoundary).ToString('o') } |
    Sort-Object | Select-Object -First 1)
  $made = if ($made.Count -gt 0) { $made[0] } else { '' }
  [pscustomobject]@{ name=$_.TaskName; times=$times; last_run=$i.LastRunTime.ToString('o');
    last_result=[int64]$i.LastTaskResult; state=[string]$_.State; created=$made } })
ConvertTo-Json -InputObject $rows -Depth 3 -Compress
"""


def _powershell(command: str, timeout: float) -> str:
    done = subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive", "-Command", command],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=timeout,
        check=True,
    )
    return done.stdout


def query_tasks() -> list[Task]:
    """등록된 AutoTrading_* 작업. 윈도우가 아니면 빈 목록."""
    if sys.platform != "win32":
        return []
    data = json.loads(_powershell(_QUERY, 60) or "[]")
    if isinstance(data, dict):
        data = [data]
    return parse_tasks(data)


def start_task(name: str) -> None:
    if not name.startswith(PREFIX) or not name.replace("_", "").isalnum():
        raise ValueError(f"다시 실행할 수 없는 작업 이름: {name}")
    _powershell(f"Start-ScheduledTask -TaskName '{name}'", 60)


Log = Callable[[str], None]


def run_in_order(
    names: Sequence[str],
    *,
    log: Log,
    query: Callable[[], list[Task]] = query_tasks,
    start: Callable[[str], None] = start_task,
    sleep: Callable[[float], None] = time.sleep,
    poll_s: float = 5.0,
    task_timeout_s: float = 1200.0,
) -> list[tuple[str, int | None]]:
    """하나씩 시작하고 끝날 때까지 기다린다. (이름, 결과 코드 · 시간 초과면 None).

    앞 작업이 실패해도 다음 작업은 돈다 — 예약 실행과 같다.
    """
    results: list[tuple[str, int | None]] = []
    for name in names:
        before = next((t for t in query() if t.name == name), None)
        if before is None:
            log(f"{name}: 등록된 작업이 아니다 — 건너뜀")
            results.append((name, None))
            continue
        log(f"{name}: 시작")
        try:
            start(name)
        except Exception as exc:  # 꺼진 작업 등 — 이 작업만 건너뛰고 다음으로
            log(f"{name}: 시작 실패 ({type(exc).__name__}) — 건너뜀")
            results.append((name, None))
            continue
        code: int | None = None
        waited = 0.0
        while waited < task_timeout_s:
            sleep(poll_s)
            waited += poll_s
            now = next((t for t in query() if t.name == name), None)
            if now is None:
                break
            started = now.last_run is not None and (
                before.last_run is None or now.last_run > before.last_run
            )
            if started and not now.running:
                code = now.last_result
                break
        log(f"{name}: {'시간 초과' if code is None else result_text(code)}")
        results.append((name, code))
    return results
