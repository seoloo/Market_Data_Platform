"""예약 작업을 **창 없이** 돌리는 실행기 (2026-10-04, 사용자 요청 "cmd 창이 계속 켜 있다").

작업 스케줄러가 ``pythonw.exe scripts\\task_launcher.py <원래 인자>`` 로 부른다. 원래 인자는
``scripts\\lt_paper.py daily`` (파일) 또는 ``-m scripts.qs_paper daily`` (모듈) 그대로.

* ``pythonw`` 에는 콘솔이 없어 ``sys.stdout`` 이 ``None`` 이다 — 출력을
  ``reports/tasklog/<KST 날짜>_<대상>.log`` 로 돌린다(실행 기록이 남는다).
* 대상의 종료 코드를 그대로 돌려준다 — 작업 스케줄러 결과·점검·다시 실행 판정이 같다.
* 파일 대상은 예전처럼 그 파일의 폴더를, 모듈 대상은 저장소 루트를 ``sys.path`` 앞에 둔다.
"""

from __future__ import annotations

import runpy
import sys
import traceback
from datetime import UTC, datetime, timedelta
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent


def _name(argv: list[str]) -> str:
    target = argv[1] if argv and argv[0] == "-m" and len(argv) > 1 else (argv[0] if argv else "x")
    stem = Path(target).stem if target.endswith(".py") else target.rsplit(".", 1)[-1]
    extra = "_".join(a.lstrip("-") for a in argv[2 if argv[0] == "-m" else 1 :] if a)
    raw = f"{stem}_{extra}" if extra else stem
    return "".join(c if c.isalnum() or c in "-_" else "_" for c in raw)[:80]


def main(argv: list[str]) -> int:
    if not argv:
        print("usage: task_launcher.py <script.py | -m module> [args...]", file=sys.stderr)
        return 2
    kst = datetime.now(UTC) + timedelta(hours=9)
    folder = REPO / "reports" / "tasklog"
    folder.mkdir(parents=True, exist_ok=True)
    log = (folder / f"{kst:%Y-%m-%d}_{_name(argv)}.log").open("a", encoding="utf-8")
    saved = (sys.stdout, sys.stderr, list(sys.argv), list(sys.path))
    sys.stdout = sys.stderr = log
    print(f"===== {kst:%Y-%m-%d %H:%M:%S} KST 시작: {' '.join(argv)}", flush=True)
    if str(REPO) not in sys.path:
        sys.path.insert(0, str(REPO))
    code: int = 0
    try:
        if argv[0] == "-m":
            sys.argv = [argv[1], *argv[2:]]
            runpy.run_module(argv[1], run_name="__main__", alter_sys=True)
        else:
            path = Path(argv[0])
            if not path.is_absolute():
                path = REPO / path
            sys.path.insert(0, str(path.parent))
            sys.argv = [str(path), *argv[1:]]
            runpy.run_path(str(path), run_name="__main__")
    except SystemExit as exc:
        c = exc.code
        code = c if isinstance(c, int) else (0 if c is None else 1)
        if not isinstance(c, int) and c is not None:
            print(c)
    except BaseException:
        traceback.print_exc()
        code = 1
    end = datetime.now(UTC) + timedelta(hours=9)
    print(f"===== {end:%H:%M:%S} 끝: 종료 코드 {code}", flush=True)
    log.close()
    sys.stdout, sys.stderr, sys.argv, sys.path[:] = saved[0], saved[1], saved[2], saved[3]
    return code


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
