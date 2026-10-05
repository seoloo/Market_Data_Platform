"""문서 26 — Phase 7 테스트넷 14일 판정 보고서. 훈련 기록은 읽기만 한다.

사용 예::

    python -m scripts.review_phase7                    # 마지막으로 끝난 UTC 날까지 (중간 점검)
    python -m scripts.review_phase7 --until 2026-10-08 # 10/08 21:08 KST 훈련 뒤 (최종 판정)
    python -m scripts.review_phase7 --first 2026-10-03 # 코드 결함으로 다시 셀 때 (P3)

결과: ``reports/review/phase7-<until>.md``·``.json``.
기준은 문서 26 §2 에 고정돼 있다 — 여기서 바꾸지 않는다.
실패 원인(P2·P3)은 사람이 ``docs/research/26-phase7-causes.json`` 에 적는다
(``{"YYYY-MM-DD": {"kind": "external"|"code", "text": "...", "fixed": "YYYY-MM-DD"|null}}``)
그리고 문서 26 §6 표에도 같은 내용을 남긴다.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from auto_trading.ops.phase7_review import PHASE7_DAYS, PHASE7_FIRST, Cause, phase7_review

REPO = Path(__file__).resolve().parent.parent
DRILLS = REPO / "reports" / "testnet"
CAUSES = REPO / "docs" / "research" / "26-phase7-causes.json"
OUT = REPO / "reports" / "review"
DAY_MS = 86_400_000


def load_reports(folder: Path) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for path in sorted(folder.glob("drill-*.json")):
        try:
            out.append(json.loads(path.read_text(encoding="utf-8")))
        except (OSError, ValueError):
            # 읽을 수 없는 기록은 그날 실행이 없는 것처럼 보이게 두지 않는다 — 실패로 센다
            at = path.stem.removeprefix("drill-")
            out.append({"at": at, "ok": False, "error": f"읽기 실패 {path.name}", "checks": []})
    return out


def load_causes(path: Path) -> dict[str, Cause]:
    if not path.exists():
        return {}
    raw = json.loads(path.read_text(encoding="utf-8"))
    return {
        day: Cause(kind=str(v["kind"]), text=str(v["text"]), fixed=v.get("fixed"), p4=v.get("p4"))
        for day, v in raw.items()
    }


def render(record: dict[str, Any]) -> str:
    info = record["info"]
    lines = [
        f"# Phase 7 테스트넷 14일 판정 — {info['first']} ~ {info['last']}"
        f" (셀 날: ~{info['until']})",
        "",
        f"생성 {record['generated']} · 기준 문서 26 §2 (시작 전 고정)"
        f" · **판정: {info['verdict']}**",
        "",
        "| # | 결과 | 내용 |",
        "|---|---|---|",
    ]
    lines += [f"| {c['id']} | {c['status']} | {c['detail']} |" for c in record["criteria"]]
    lines += ["", "## 날짜별 훈련 (그날 마지막 실행이 그날의 결과)", ""]
    lines += ["| 날짜 | 실행 | 마지막 결과 | 실패 단계 | 원인 |", "|---|---:|---|---|---|"]
    for day, runs in info["runs"].items():
        r = runs[-1]
        cause = info["causes"].get(day)
        why = f"{cause['kind']}: {cause['text']}" if cause else ""
        failed = ", ".join(str(s) for s in r["failed_steps"]) or (r["error"] or "")
        mark = "✓" if r["ok"] else "✗"
        lines.append(
            f"| {day} | {len(runs)} | {r['passed']}/{r['checks']} {mark} | {failed} | {why} |"
        )
    if info["missing"]:
        lines += ["", f"**기록 없는 날:** {', '.join(info['missing'])}"]
    lines += [
        "",
        "## 읽는 법",
        "",
        "- CHECK 는 사람이 원인을 적어야 한다는 뜻이다. `docs/research/26-phase7-causes.json` 과"
        " 문서 26 §6 에 적고 다시 실행한다.",
        "- 코드 결함(P3)이면 고친 날부터 14일을 다시 센다 — `--first <고친 날>`.",
        "- Phase 7 통과는 실거래 승인이 아니다 (문서 26 §4). Phase 8 은 사용자 명시 승인이 먼저다.",
        "",
    ]
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--until", help="마지막으로 셀 날 YYYY-MM-DD (기본: 마지막으로 끝난 UTC 날)"
    )
    parser.add_argument("--first", default=PHASE7_FIRST, help="14일의 첫날 (기본 문서 26 기간)")
    args = parser.parse_args()
    now = int(time.time() * 1000)
    until = args.until or datetime.fromtimestamp((now - now % DAY_MS - 1) / 1000, UTC).strftime(
        "%Y-%m-%d"
    )
    criteria, info = phase7_review(
        load_reports(DRILLS),
        load_causes(CAUSES),
        until=until,
        first=args.first,
        days=PHASE7_DAYS,
    )
    record = {
        "generated": datetime.now(tz=UTC).strftime("%Y-%m-%d %H:%M UTC"),
        "criteria": [asdict(c) for c in criteria],
        "info": info,
    }
    OUT.mkdir(parents=True, exist_ok=True)
    stem = f"phase7-{info['until']}"
    (OUT / f"{stem}.json").write_text(
        json.dumps(record, ensure_ascii=False, indent=1, default=str) + "\n", encoding="utf-8"
    )
    text = render(record)
    (OUT / f"{stem}.md").write_text(text, encoding="utf-8")
    sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    for _stream in (sys.stdout, sys.stderr):
        _reconfigure = getattr(_stream, "reconfigure", None)
        if _reconfigure is not None:
            _reconfigure(encoding="utf-8", errors="replace")
    sys.exit(main())
