"""문서 114 — LT 재현: S&P 400 중형주에 113 규칙·코드를 그대로 (자료 폴더만 바꿈).

사용 예::

    python -m scripts.run_lt_sp400 audit   # 111 과 같은 점검, 유니버스 해시
    python -m scripts.run_lt_sp400 run     # 판정 한 번 (해시 고정 · 트리 깨끗 · 결과 파일 없음)
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from scripts import run_lt_leaders as lt
from scripts import run_sb_breakout as sb

REPO = Path(__file__).resolve().parent.parent
STOCKS = REPO / "data" / "research" / "stocks_sp400"
OUT = REPO / "reports" / "lt_sp400"
UNIVERSE_SHA256 = ""
"""audit 뒤(수익 보기 전) 문서 114 와 함께 고정한다."""


def _point_to_sp400() -> None:
    sb.STOCKS = STOCKS
    sb.OUT = OUT
    lt.STOCKS = STOCKS
    lt.AUDIT = OUT / "audit.json"
    lt.OUTPUT = OUT
    lt.WATCH = ()


def main(argv: list[str]) -> int:
    _point_to_sp400()
    if argv == ["audit"]:
        result = sb.audit()
    elif argv == ["run"]:
        if not UNIVERSE_SHA256:
            raise SystemExit("유니버스 해시가 고정되지 않았다 — audit 뒤 고정하고 커밋")
        lt.UNIVERSE_SHA256 = UNIVERSE_SHA256
        result = lt.run()
    else:
        raise SystemExit("usage: python -m scripts.run_lt_sp400 audit | run")
    sys.stdout.write(json.dumps(result, ensure_ascii=True, indent=1) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
