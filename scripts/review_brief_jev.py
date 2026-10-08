"""시장 브리핑 JEV 대조 기록 모아 보기 (문서 173 §7, 읽기 전용 · 호출 없음).

사용::

    python scripts/review_brief_jev.py            # 표시된 문장만
    python scripts/review_brief_jev.py --all      # 모든 문장

``reports/brief/<세션>.json`` 의 ``jev_check`` 를 모아 세션 수 · 문장 수 · 표시 수 · 비용을 적고,
표시된 문장을 근거와 함께 보여 준다. 사람이 맞다/틀리다를 확인하는 자료다.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parent.parent
BRIEF = REPO / "reports" / "brief"


def collect(folder: Path = BRIEF) -> list[dict[str, Any]]:
    out = []
    for path in sorted(folder.glob("????-??-??.json")):
        d = json.loads(path.read_text(encoding="utf-8"))
        check = d.get("jev_check") or {}
        ev = {e["id"]: e for e in d.get("evidence", [])}
        ai = d.get("ai") or {}
        for r in check.get("results", []):
            item = (ai.get(r["where"]) or [])[r["index"]] if ai.get(r["where"]) else {}
            out.append(
                {
                    "session": d.get("session", path.stem),
                    "prompt_version": check.get("prompt_version"),
                    **r,
                    "sources": [ev[x]["title"] for x in item.get("evidence", []) if x in ev],
                }
            )
    return out


def main(argv: list[str]) -> int:
    rows = collect()
    sessions = sorted({r["session"] for r in rows})
    flagged = [r for r in rows if r["flag"]]
    print(f"세션 {len(sessions)} · 문장 {len(rows)} · 표시 {len(flagged)}")
    for r in rows if "--all" in argv else flagged:
        mark = "표시" if r["flag"] else "통과"
        print(
            f"\n[{r['session']} · {r['where']} · {mark}] A(출처) {r['source']} · B(표 모순) "
            f"{r['contradiction']}\n  {r['text']}"
        )
        for s in r["sources"]:
            print(f"   └ {s}")
    return 0


if __name__ == "__main__":
    for _stream in (sys.stdout,):
        _reconfigure = getattr(_stream, "reconfigure", None)
        if _reconfigure is not None:
            _reconfigure(encoding="utf-8", errors="replace")
    raise SystemExit(main(sys.argv[1:]))
