# ruff: noqa: E501 — 한국어 설명 · PowerShell 한 줄 명령이 길다
"""자동 실행(Windows 예약 작업) 켜기 · 끄기 — 설정 화면 '자동 실행' (2026-10-10 사용자).

* 예약 틀은 ``deploy/tasks/<이름>.xml`` — 이 PC 의 예약을 그대로 내보내고(``scripts/tasks_export.py``)
  폴더 경로를 ``{REPO}`` 로, 사용자 ID 는 지웠다. 등록할 때 그 PC 의 저장소 위치를 넣고 지금 Windows
  사용자로 등록한다(관리자 권한 필요 없음).
* **이미 있는 예약은 덮어쓰지 않는다.** '켜기' 는 없는 것만 새로 만들고, 꺼져 있는 것은 다시 켠다.
  '끄기' 는 지우지 않고 사용 안 함으로만 바꾼다.
* PowerShell 호출은 ``runner`` 로 바꿔 끼울 수 있다(시험).
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import tempfile
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from html import escape
from pathlib import Path

__all__ = [
    "GROUPS",
    "TASKS",
    "TaskState",
    "disable",
    "enable",
    "register_missing",
    "render_xml",
    "status",
]

TEMPLATE_DIR = Path("deploy") / "tasks"


@dataclass(frozen=True, slots=True)
class TaskDef:
    name: str
    group: str
    what: str
    when: str


TASKS: tuple[TaskDef, ...] = (
    TaskDef(
        "AutoTrading_Toss_Accumulate", "toss", "토스 매수 (Glide 월 매수 · 미국 적립)", "평일 21:00"
    ),
    TaskDef("AutoTrading_Toss_FX", "toss", "환전 알림 (달러 모자랄 때)", "평일 09 · 12 · 15시"),
    TaskDef(
        "AutoTrading_Toss_M3_Chat",
        "glide",
        "Glide 금액 묻기 · 버튼 답 받기",
        "평일 09:00 부터 5분마다",
    ),
    TaskDef("AutoTrading_Toss_M3_Retry", "glide", "Glide 체결 확인 · 재시도", "평일 23:40 · 03:30"),
    TaskDef("AutoTrading_Toss_KR", "toss_kr", "국내 주식 적립", "평일 12:10"),
    TaskDef("AutoTrading_Binance_Cruise", "cruise", "Cruise 매매 (바이낸스)", "매일 09:05"),
    TaskDef("AutoTrading_Telegram_Bot", "common", "텔레그램 '현황' 봇", "2분마다"),
    TaskDef("AutoTrading_Risk_News", "common", "보유 종목 위험 뉴스 감시 (AI)", "매시 17분"),
    TaskDef("AutoTrading_Ledger_Sync", "common", "기록 DB · 그래프 기록", "매일 23:00"),
    TaskDef("AutoTrading_Fire_Report", "common", "파이어 진행 보고", "매달 1일 09:15"),
    TaskDef("AutoTrading_Backup", "extra", "상태 백업 (F: 디스크)", "매일 23:30"),
    TaskDef("AutoTrading_Paper_Watchdog", "extra", "매일 점검 (봇 · 실행 기록)", "매일 21:20"),
    TaskDef("AutoTrading_Info_Bot", "info", "정보 봇 (종목 뉴스 답)", "1분마다"),
    TaskDef("AutoTrading_Theme_Alert", "info", "주간 테마", "토 09:00"),
    TaskDef("AutoTrading_Market_Brief", "info", "시장 브리핑", "화 ~ 토 07:30"),
    TaskDef(
        "AutoTrading_Dashboard",
        "phone",
        "휴대폰으로 보기 화면 서버 (읽기 전용)",
        "로그인하면 켜져 계속",
    ),
)

GROUPS: tuple[tuple[str, str, str], ...] = (
    ("glide", "Glide (토스 미국 주식)", "토스 키 필요 · 토스 매수 · 환전 알림도 함께 켜짐"),
    ("toss", "토스 매수 · 환전", "Glide 나 토스 미국 적립에 필요"),
    ("toss_kr", "토스 국내 적립", "국내 주식 적립을 쓸 때만"),
    (
        "cruise",
        "Cruise (바이낸스 코인)",
        "바이낸스 거래 키(BINANCE_LIVE_*) 필요 — 2026-10-15 업비트에서 옮김",
    ),
    ("common", "공통 (텔레그램 봇 · 기록 · 보고)", "텔레그램 봇 토큰 필요"),
    ("extra", "이 PC 관리 (백업 · 매일 점검)", "백업은 F: 디스크가 있어야 해요"),
    ("info", "정보 봇 (뉴스 · 테마 · 브리핑)", "정보 봇 토큰 · Gemini 키 필요"),
    ("phone", "휴대폰으로 보기 (화면 서버)", "'휴대폰' 메뉴의 Tailscale 연결과 함께 써요"),
)
_NEEDS = {"glide": ("toss",)}
"""Glide 를 고르면 함께 켜야 하는 묶음 (토스 매수 실행기가 Glide 월 매수도 한다)."""


def expand(groups: Sequence[str]) -> list[str]:
    out: list[str] = []
    for g in groups:
        for x in (g, *_NEEDS.get(g, ())):
            if x not in out:
                out.append(x)
    return out


def tasks_for(groups: Sequence[str]) -> list[TaskDef]:
    picked = set(expand(groups))
    return [t for t in TASKS if t.group in picked]


def _this_user() -> str:
    dom, user = os.environ.get("USERDOMAIN", ""), os.environ.get("USERNAME", "")
    return f"{dom}\\{user}" if dom and user else user


def render_xml(template: str, repo: Path, user: str | None = None) -> str:
    """틀 → 이 PC 용 XML (순수). ``{REPO}`` 를 저장소 경로로, ``{USER}`` 를 지금 Windows 사용자로
    (로그인 때 켜는 예약은 '누가 로그인할 때' 가 있어야 관리자 권한 없이 등록된다)."""
    return template.replace("{REPO}", str(repo)).replace(
        "{USER}", escape(user if user is not None else _this_user())
    )


def normalize_export(xml: str, repo: Path) -> str:
    """이 PC 에서 내보낸 XML → 다른 PC 용 틀 (순수).

    경로 → ``{REPO}``, 사용자 ID · 만든 사람 줄 지움. '로그인 안 해도 실행(S4U · 암호)' 은 관리자 권한이
    있어야 등록돼서 '로그인한 동안 실행(InteractiveToken)' 으로, 배터리일 때 건너뛰지 않게 바꾼다.
    """
    xml = xml.replace(str(repo), "{REPO}")
    xml = re.sub(r"\s*<UserId>[^<]*</UserId>", "", xml)
    xml = re.sub(r"\s*<Author>[^<]*</Author>", "", xml)
    xml = re.sub(
        r"<LogonType>(S4U|Password)</LogonType>", "<LogonType>InteractiveToken</LogonType>", xml
    )
    xml = xml.replace(
        "<DisallowStartIfOnBatteries>true</DisallowStartIfOnBatteries>",
        "<DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>",
    ).replace(
        "<StopIfGoingOnBatteries>true</StopIfGoingOnBatteries>",
        "<StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>",
    )
    return xml.replace('encoding="UTF-16"', 'encoding="UTF-8"')


Runner = Callable[[str], str]


def _powershell(script: str) -> str:
    done = subprocess.run(
        [
            "powershell",
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-Command",
            script,
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=90,
        check=False,
    )
    if done.returncode != 0:
        raise RuntimeError((done.stderr or done.stdout).strip()[:400])
    return done.stdout


@dataclass(frozen=True, slots=True)
class TaskState:
    name: str
    state: str
    """Ready · Running · Disabled · 없음."""
    next_run: str = ""
    last_run: str = ""
    last_result: int | None = None


_STATUS_PS = (
    "[Console]::OutputEncoding=[Text.Encoding]::UTF8;"
    "$r=@(Get-ScheduledTask -TaskName 'AutoTrading_*' -ErrorAction SilentlyContinue | ForEach-Object {"
    "$i=$_ | Get-ScheduledTaskInfo;"
    "[pscustomobject]@{n=$_.TaskName;s=[string]$_.State;"
    "nx=if($i.NextRunTime){$i.NextRunTime.ToString('MM/dd HH:mm')}else{''};"
    "lr=if($i.LastRunTime -and $i.LastRunTime.Year -gt 2000){$i.LastRunTime.ToString('MM/dd HH:mm')}else{''};"
    "res=$i.LastTaskResult}});"
    "ConvertTo-Json -InputObject $r -Compress"
)


def status(runner: Runner = _powershell) -> dict[str, TaskState]:
    """이 PC 의 AutoTrading_* 예약 상태 (이름 → 상태)."""
    raw = runner(_STATUS_PS).strip() or "[]"
    rows = json.loads(raw)
    if isinstance(rows, dict):
        rows = [rows]
    out: dict[str, TaskState] = {}
    for r in rows:
        out[str(r.get("n"))] = TaskState(
            str(r.get("n")),
            str(r.get("s") or ""),
            str(r.get("nx") or ""),
            str(r.get("lr") or ""),
            r.get("res") if isinstance(r.get("res"), int) else None,
        )
    return out


def _quote(s: str) -> str:
    return "'" + s.replace("'", "''") + "'"


def register_missing(
    repo: Path,
    groups: Sequence[str],
    existing: Mapping[str, TaskState],
    runner: Runner = _powershell,
) -> tuple[list[str], list[str]]:
    """고른 묶음에서 **없는** 예약만 만들고, 꺼진(Disabled) 것은 켠다. (새로 만든 것, 다시 켠 것)."""
    made, enabled = [], []
    for t in tasks_for(groups):
        cur = existing.get(t.name)
        if cur is not None:
            if cur.state == "Disabled":
                runner(f"Enable-ScheduledTask -TaskName {_quote(t.name)} | Out-Null")
                enabled.append(t.name)
            continue
        template = (repo / TEMPLATE_DIR / f"{t.name}.xml").read_text(encoding="utf-8")
        # PowerShell 은 XML 을 글자(UTF-16)로 넘긴다 — 'encoding="UTF-8"' 선언이 있으면
        # '인코딩을 전환할 수 없습니다' 로 등록이 거부돼서 선언 줄을 뺀다 (2026-10-11 실제 등록에서 확인)
        xml = re.sub(r"^﻿?<\?xml[^>]*\?>\s*", "", render_xml(template, repo))
        with tempfile.NamedTemporaryFile("w", suffix=".xml", delete=False, encoding="utf-8") as f:
            f.write(xml)
            path = f.name
        try:
            runner(
                f"$x=Get-Content -Raw -Encoding UTF8 {_quote(path)};"
                f"Register-ScheduledTask -TaskName {_quote(t.name)} -Xml $x | Out-Null"
            )
        finally:
            Path(path).unlink(missing_ok=True)
        made.append(t.name)
    return made, enabled


def disable(
    groups: Sequence[str], existing: Mapping[str, TaskState], runner: Runner = _powershell
) -> list[str]:
    """고른 묶음의 예약을 '사용 안 함' 으로 (지우지 않음)."""
    done = []
    for t in tasks_for(groups):
        cur = existing.get(t.name)
        if cur is not None and cur.state != "Disabled":
            runner(f"Disable-ScheduledTask -TaskName {_quote(t.name)} | Out-Null")
            done.append(t.name)
    return done


def enable(
    groups: Sequence[str], existing: Mapping[str, TaskState], runner: Runner = _powershell
) -> list[str]:
    done = []
    for t in tasks_for(groups):
        cur = existing.get(t.name)
        if cur is not None and cur.state == "Disabled":
            runner(f"Enable-ScheduledTask -TaskName {_quote(t.name)} | Out-Null")
            done.append(t.name)
    return done


def start(name: str, runner: Runner = _powershell) -> None:
    """예약 하나를 지금 실행 (휴대폰 화면 서버처럼 로그인 때 켜지는 것을 바로 켤 때)."""
    runner(f"Start-ScheduledTask -TaskName {_quote(name)}")
