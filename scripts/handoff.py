"""인계 요약 — 새 세션이 맨 처음 실행해 '지금 누가 무엇을 했고 무엇이 남았나' 를 본다 (AGENTS §10).

사용 예::

    python -m scripts.handoff            # 최근 24 시간
    python -m scripts.handoff --hours 6

보여 주는 것 (읽기만 · 주문 없음, 네트워크는 git fetch 하나):

1. 최근 커밋 — 시각 · 해시 · 제목, 저장소에 아직 안 올린 커밋 수
2. 상황판 ``data/agents/*.md`` — 에이전트마다 상태 · 하는 일 · 잡은 파일 · 결정 대기,
   24 시간 넘으면 '지난 것'
3. 커밋 안 된 파일 (남이 작업 중일 수 있음)
4. 예약 작업 ``AutoTrading_*`` 중 마지막 결과가 실패인 것 · 오늘 처음 생긴 것
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
BOARDS = REPO / "data" / "agents"
STALE_H = 24
_UPDATED = re.compile(r"갱신:\s*(\d{4}-\d{2}-\d{2})(?:\s+(\d{1,2}:\d{2}))?")
_FIELDS = ("하는 일", "잡은 파일", "상태", "결정 대기", "다음", "커밋")
OK_RESULTS = {0, 267009, 267011}
"""0 성공 · 267009 실행 중 · 267011 아직 안 돌았음."""


def _git(*args: str) -> str:
    r = subprocess.run(
        ["git", *args], cwd=REPO, capture_output=True, text=True, encoding="utf-8", check=False
    )
    return r.stdout.strip()


def board_summary(text: str, now: datetime, stale_h: int = STALE_H) -> dict[str, object]:
    """상황판 한 장 → {에이전트, 갱신, 지난 것, 칸들} (순수)."""
    lines = [x.strip() for x in text.splitlines() if x.strip()]
    head = lines[0] if lines else ""
    agent = head.split("에이전트:", 1)[1].split("갱신:")[0].strip() if "에이전트:" in head else "?"
    m = _UPDATED.search(text)
    updated = None
    if m:
        updated = datetime.fromisoformat(f"{m.group(1)}T{m.group(2) or '00:00'}")
    fields = {}
    for x in lines[1:]:
        for f in _FIELDS:
            if x.startswith(f"{f}:"):
                fields[f] = x.split(":", 1)[1].strip()
    return {
        "agent": agent,
        "updated": updated.isoformat(timespec="minutes") if updated else None,
        "stale": updated is None or now - updated > timedelta(hours=stale_h),
        **fields,
    }


def _tasks() -> list[dict[str, object]]:
    ps = (
        "Get-ScheduledTask -TaskName 'AutoTrading_*' | ForEach-Object { $i = $_ | "
        "Get-ScheduledTaskInfo; [pscustomobject]@{n=$_.TaskName; s=[string]$_.State; "
        "r=$i.LastTaskResult; d=$_.Date} } | ConvertTo-Json -Compress"
    )
    try:
        r = subprocess.run(
            ["powershell.exe", "-NoProfile", "-Command", ps],
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=60,
            check=False,
        )
        data = json.loads(r.stdout or "[]")
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return []
    return data if isinstance(data, list) else [data]


def report(hours: int) -> str:
    now = datetime.now()  # noqa: DTZ005 — PC 시계(KST)
    out = [f"🧭 인계 요약 · {now:%m-%d %H:%M} · 최근 {hours} 시간"]
    _git("fetch", "-q")
    log = _git(
        "log", f"--since={hours} hours ago", "--format=%ad %h %s", "--date=format:%m-%d %H:%M"
    )
    ahead = _git("rev-list", "--count", "origin/main..HEAD") or "?"
    out += ["", f"## 커밋 (안 올린 커밋 {ahead} 개)"]
    out += [f"- {x[:140]}" for x in log.splitlines()] or ["- 없음"]
    out += ["", "## 상황판"]
    for p in sorted(BOARDS.glob("*.md")):
        if p.name == "README.md":
            continue
        b = board_summary(p.read_text(encoding="utf-8", errors="replace"), now)
        mark = "⚪ 지난 것" if b["stale"] else "🟢"
        out.append(f"- {mark} **{b['agent']}** (갱신 {b['updated']}) — {b.get('상태', '?')}")
        for f in ("하는 일", "잡은 파일", "결정 대기", "다음", "커밋"):
            if b.get(f):
                out.append(f"    {f}: {str(b[f])[:200]}")
    dirty = _git("status", "--short")
    out += ["", "## 커밋 안 된 파일 (남이 작업 중일 수 있음)"]
    out += [f"- {x}" for x in dirty.splitlines()] or ["- 없음"]
    tasks = _tasks()
    today = now.strftime("%Y-%m-%d")
    bad = [t for t in tasks if t.get("r") not in OK_RESULTS and t.get("s") != "Disabled"]
    new = [t for t in tasks if str(t.get("d") or "").startswith(today)]
    out += ["", f"## 예약 작업 ({len(tasks)} 개)"]
    out += [f"- ⚠ 마지막 결과 실패: {t['n']} (코드 {t['r']})" for t in bad] or ["- 실패 없음"]
    out += [f"- 🆕 오늘 생김: {t['n']}" for t in new]
    return "\n".join(out)


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--hours", type=int, default=24)
    args = ap.parse_args(argv)
    sys.stdout.write(report(args.hours) + "\n")
    return 0


if __name__ == "__main__":
    for _stream in (sys.stdout, sys.stderr):
        _reconfigure = getattr(_stream, "reconfigure", None)
        if _reconfigure is not None:
            _reconfigure(encoding="utf-8", errors="replace")
    raise SystemExit(main(sys.argv[1:]))
