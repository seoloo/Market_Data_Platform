"""이 PC 의 자동 실행 예약을 틀로 내보내기 → ``deploy/tasks/*.xml`` (예약은 바꾸지 않음).

사용 예::

    python -m scripts.tasks_export   # 설정 화면 '자동 실행' 이 쓰는 틀을 지금 예약으로 새로 씀

* 폴더 경로는 ``{REPO}`` 로, Windows 사용자 ID 는 지운다 → 다른 PC 에서 그 PC 경로 · 사용자로 등록.
* 비밀값은 예약에 들어 있지 않다(키는 ``.env``).
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from auto_trading.ops.task_setup import TASKS, TEMPLATE_DIR, normalize_export

REPO = Path(__file__).resolve().parent.parent


def main() -> int:
    out = REPO / TEMPLATE_DIR
    out.mkdir(parents=True, exist_ok=True)
    for t in TASKS:
        done = subprocess.run(
            [
                "powershell",
                "-NoProfile",
                "-NonInteractive",
                "-Command",
                "[Console]::OutputEncoding=[Text.Encoding]::UTF8;"
                f"Export-ScheduledTask -TaskName '{t.name}'",
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
        if done.returncode != 0 or "<Task" not in done.stdout:
            print(f"건너뜀 (이 PC 에 없음): {t.name}")
            continue
        xml = normalize_export(done.stdout.strip(), REPO)
        (out / f"{t.name}.xml").write_text(xml + "\n", encoding="utf-8", newline="\n")
        print(f"틀 저장: {t.name}")
    return 0


if __name__ == "__main__":
    for _stream in (sys.stdout, sys.stderr):
        _reconfigure = getattr(_stream, "reconfigure", None)
        if _reconfigure is not None:
            _reconfigure(encoding="utf-8", errors="replace")
    raise SystemExit(main())
