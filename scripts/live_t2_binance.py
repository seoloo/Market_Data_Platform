"""옛 이름 껍데기 — 2026-10-15 :mod:`scripts.vega_live` 로 이름을 바꿨다.

이미 등록된 예약 작업이 이 경로를 부르므로 남겨 둔다. 새 예약으로 바꾼 뒤 지운다.
"""

from __future__ import annotations

import sys

from scripts.vega_live import main

if __name__ == "__main__":
    for _stream in (sys.stdout, sys.stderr):
        _reconfigure = getattr(_stream, "reconfigure", None)
        if _reconfigure is not None:
            _reconfigure(encoding="utf-8", errors="replace")
    raise SystemExit(main(sys.argv[1:]))
