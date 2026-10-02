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


@dataclass(frozen=True, slots=True)
class Task:
    name: str
    times: tuple[str, ...]
    """매일 트리거 시각 "HH:MM" (현지 시각). 비어 있으면 매일 작업이 아니다."""
    last_run: datetime | None
    last_result: int
    running: bool


@dataclass(frozen=True, slots=True)
class Due:
    name: str
    due: datetime
    """놓쳤거나 실패한 예정 시각."""
    reason: str
    """MISSED(그 시각 뒤로 실행 없음) · FAILED(실행은 했으나 실패 결과)."""
    detail: str


def due_time(times: Iterable[str], now: datetime) -> datetime | None:
    """``now`` 이전(같거나)의 가장 최근 예정 시각 — 오늘 것이 아직이면 어제 것."""
    best: datetime | None = None
    for hhmm in times:
        h, m = (int(x) for x in hhmm.split(":"))
        at = now.replace(hour=h, minute=m, second=0, microsecond=0)
        if at > now:
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
        due = due_time(t.times, now)
        if due is None:
            continue
        if t.last_run is None or t.last_run < due:
            last = t.last_run.strftime("%m-%d %H:%M") if t.last_run else "기록 없음"
            out.append(Due(t.name, due, "MISSED", f"마지막 실행 {last}"))
        elif t.last_result not in RESULT_OK:
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


def parse_tasks(rows: Sequence[Mapping[str, Any]]) -> list[Task]:
    tasks = []
    for r in rows:
        times = r.get("times") or []
        if isinstance(times, str):  # 하나면 배열이 아니라 문자열로 온다
            times = [times]
        tasks.append(
            Task(
                name=str(r.get("name", "")),
                times=tuple(str(x) for x in times if x),
                last_run=_local(r.get("last_run")),
                last_result=int(r.get("last_result") or 0),
                running=str(r.get("state", "")) == "Running",
            )
        )
    return tasks


# ------------------------------------------------------------------ I/O (Windows)

_QUERY = r"""
$rows = @(Get-ScheduledTask -TaskName 'AutoTrading_*' | ForEach-Object {
  $i = $_ | Get-ScheduledTaskInfo
  $times = @($_.Triggers | Where-Object { $_.CimClass.CimClassName -eq 'MSFT_TaskDailyTrigger' } |
    ForEach-Object { ([datetime]$_.StartBoundary).ToString('HH:mm') })
  [pscustomobject]@{ name=$_.TaskName; times=$times; last_run=$i.LastRunTime.ToString('o');
    last_result=[int64]$i.LastTaskResult; state=[string]$_.State } })
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
        start(name)
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
