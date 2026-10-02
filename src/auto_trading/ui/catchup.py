"""대시보드 "놓친 작업 다시 실행" 버튼 — 화면 조각과 실행기 (ops/catchup.py, 2026-10-02 요청).

대시보드의 **유일한 쓰기 동작**이다 (server.py 의 ``POST /catchup``).
버튼은 무엇을 돌릴지 보내지 않는다 — 누른 순간 서버가 작업 스케줄러를 다시 읽어 고른 목록만,
작업 스케줄러에 이미 등록된 작업으로 시작한다. 새 명령·인자·키를 받지 않는다.
"""

from __future__ import annotations

import secrets
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from html import escape
from pathlib import Path

from auto_trading.ops.catchup import PREFIX, Due, Task, plan, query_tasks, run_in_order

__all__ = ["CatchupRunner", "CatchupView", "catchup_panel"]

_REASON = {"MISSED": "놓침", "FAILED": "실패"}
_CACHE_S = 15.0


@dataclass(frozen=True, slots=True)
class CatchupView:
    token: str
    due: tuple[Due, ...]
    busy: bool
    log: tuple[str, ...]
    error: str = ""


class CatchupRunner:
    """서버 프로세스 하나에 하나. 한 번에 한 묶음만 돈다."""

    def __init__(
        self,
        root: Path,
        *,
        query: Callable[[], list[Task]] = query_tasks,
        run: Callable[..., object] = run_in_order,
        clock: Callable[[], datetime] = lambda: datetime.now().astimezone(),
    ) -> None:
        self.token = secrets.token_urlsafe(24)
        self._root = root
        self._query = query
        self._run = run
        self._clock = clock
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._log: list[str] = []
        self._cache: tuple[float, list[Task]] | None = None

    @property
    def busy(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def _tasks(self, fresh: bool = False) -> list[Task]:
        now = time.monotonic()
        if fresh or self._cache is None or now - self._cache[0] > _CACHE_S:
            self._cache = (now, self._query())
        return self._cache[1]

    def view(self) -> CatchupView:
        try:
            due = tuple(plan(self._tasks(), self._clock()))
            error = ""
        except Exception as exc:  # 조회 실패가 화면을 막지 않게
            due, error = (), f"예약 작업을 읽지 못함: {type(exc).__name__}"
        with self._lock:
            log = tuple(self._log)
        return CatchupView(self.token, due, self.busy, log, error)

    def start(self) -> list[str]:
        """지금 다시 고른 목록을 뒤에서 순서대로 돌린다.

        시작한 작업 이름 (이미 도는 중이거나 고를 것이 없으면 빈 목록).
        """
        with self._lock:
            if self.busy:
                return []
            names = [d.name for d in plan(self._tasks(fresh=True), self._clock())]
            if not names:
                return []
            self._log = []
            self._thread = threading.Thread(target=self._work, args=(names,), daemon=True)
            self._thread.start()
            return names

    def _write(self, line: str) -> None:
        stamp = self._clock()
        text = f"{stamp:%m-%d %H:%M:%S} {line}"
        with self._lock:
            self._log.append(text)
        out = self._root / "reports" / "catchup"
        out.mkdir(parents=True, exist_ok=True)
        with (out / f"{stamp:%Y-%m-%d}.log").open("a", encoding="utf-8", newline="\n") as f:
            f.write(text + "\n")

    def _work(self, names: list[str]) -> None:
        self._write(f"다시 실행 시작: {', '.join(n.removeprefix(PREFIX) for n in names)}")
        try:
            self._run(names, log=self._write, query=self._query)
            self._write("다시 실행 끝")
        except Exception as exc:
            self._write(f"다시 실행 중단: {type(exc).__name__}: {exc}")
        finally:
            self._cache = None


def catchup_panel(view: CatchupView | None) -> str:
    if view is None:
        return ""
    rows = "".join(
        f"<tr><td class='nowrap'>{escape(d.name.removeprefix(PREFIX))}</td>"
        f"<td class='nowrap'>{d.due:%m-%d %H:%M}</td>"
        f"<td>{_REASON.get(d.reason, d.reason)}<div class='mute'>{escape(d.detail)}</div></td></tr>"
        for d in view.due
    )
    if view.error:
        body = f"<div class='errors'>{escape(view.error)}</div>"
    elif view.busy:
        body = "<div><b>순서대로 실행 중</b> — 새로 고침하면 진행이 보인다.</div>"
    elif view.due:
        body = (
            f"<table><tr><th>작업</th><th>예정</th><th>사유</th></tr>{rows}</table>"
            '<form method="post" action="/catchup" style="margin-top:10px">'
            f'<input type="hidden" name="token" value="{escape(view.token)}">'
            f'<button class="btn" type="submit">놓친 작업 {len(view.due)}개 순서대로 실행</button>'
            "</form>"
        )
    else:
        body = "<div class='mute'>놓친 작업 없음 — 매일 작업이 마지막 예정 시각 뒤에 돌았다.</div>"
    log = (
        "<div class='foot' style='white-space:pre-line'>"
        + escape("\n".join(view.log[-20:]))
        + "</div>"
        if view.log
        else ""
    )
    return (
        '<div class="panel"><h2>놓친 작업 다시 실행</h2>'
        f"{body}{log}"
        '<div class="foot">PC 를 켜면 작업 스케줄러가 놓친 작업을 <b>한꺼번에</b> 자동으로 돌린다'
        " (이미 돌았으면 여기 안 나온다). 버튼은 예정 시각 순서로 <b>하나씩</b>, 점검은 마지막. "
        "적립 매수는 하루 주문 1 건 · 예약 접수 10:00~22:20 밖이면 주문하지 않고 "
        "적립금은 이어진다. "
        "Paper·데모는 지금 시점 기준으로 판단하고 지나간 날을 되살리지 않는다. "
        "기록 reports/catchup/</div></div>"
    )
