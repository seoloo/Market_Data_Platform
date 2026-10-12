"""문서 226 후속 — 카피 트레이딩 리더 10 명 앞으로 90 일 기록 (하루 한 번, 주문 없음).

사용 예::

    python -m scripts.copytrade_watch run      # 새로 닫힌 포지션 · 계정 상태 기록
    python -m scripts.copytrade_watch status   # 기록 시작 뒤 합계

* 대상은 226 에서 고른 10 명(``data/research/copytrade_226/meta.json`` 의 chosen)이다.
  추세 · 짧은 손절형 3 명(风雪哥 · 天天上涨 · 小周同学o)이 주 관심이고, 나머지 7 명은 비교용이다.
* 포지션 기록만 받는다(리더당 요청 2 번 — 상태 · 최근 100 건). 체결 기록은 받지 않는다(요청 줄이기).
* 웹 화면용 공개 주소라 막힐 수 있다. 실패하면 끝 코드 1 — 예약 점검(PC 작업 실패)에 보인다.
* 텔레그램은 보내지 않는다(실거래 알림만 보내는 규칙). 90 일 뒤 판단은 226 §5.4 · IDEAS 35.
"""

from __future__ import annotations

import json
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

REPO = Path(__file__).resolve().parent.parent
if str(REPO / "src") not in sys.path:
    sys.path.insert(0, str(REPO / "src"))

from auto_trading.research.copytrade_watch import forward_summary, merge_new  # noqa: E402

SRC = REPO / "data" / "research" / "copytrade_226"
ROOT = REPO / "data" / "copytrade_watch"
BAPI = "https://www.binance.com/bapi/futures/v1/friendly/future/copy-trade"
UA = {"User-Agent": "Mozilla/5.0", "Content-Type": "application/json"}
START = "2026-10-13"
END = "2027-01-11"
PAUSE = 3.0
FOCUS = ("5133452281665177088", "4708220152086930177", "5156835347707766529")
"""天天上涨 · 风雪哥 · 小周同学o — 226 에서 '추세 · 짧은 손절형'."""


def _ms(day: str) -> int:
    return int(datetime.fromisoformat(day).replace(tzinfo=UTC).timestamp() * 1000)


def _call(c: httpx.Client, method: str, path: str, body: dict[str, Any] | None = None) -> Any:
    for attempt in range(3):
        time.sleep(PAUSE)
        r = c.request(method, f"{BAPI}/{path}", json=body, headers=UA)
        data = r.json() if r.status_code == 200 else {}
        if data.get("data") is not None:
            return data["data"]
        time.sleep(20 * (attempt + 1))
    raise RuntimeError(f"받기 실패: {path}")


def _ids() -> list[str]:
    return list(json.loads((SRC / "meta.json").read_text(encoding="utf-8"))["chosen"])


def run() -> int:
    ROOT.mkdir(parents=True, exist_ok=True)
    start = _ms(START)
    today = datetime.now(UTC).date().isoformat()
    if today > END:
        print("기록 기간이 끝났다 — 예약을 꺼도 된다")
        return 0
    errors = 0
    with httpx.Client(timeout=30.0) as c:
        for pid in _ids():
            seen_path = ROOT / f"seen_{pid}.json"
            seen = (
                set(json.loads(seen_path.read_text(encoding="utf-8")))
                if seen_path.exists()
                else set()
            )
            try:
                det = _call(c, "GET", f"lead-portfolio/detail?portfolioId={pid}")
                page = _call(
                    c,
                    "POST",
                    "lead-portfolio/position-history",
                    {"pageNumber": 1, "pageSize": 100, "portfolioId": pid},
                )
            except (RuntimeError, httpx.HTTPError, ValueError) as exc:
                print(f"{pid}: {exc}")
                errors += 1
                continue
            new = merge_new(seen, page.get("list") or [], start)
            with (ROOT / f"pos_{pid}.jsonl").open("a", encoding="utf-8") as f:
                for r in new:
                    f.write(json.dumps(r, ensure_ascii=False) + "\n")
            snap = {
                "day": today,
                "status": det.get("status"),
                "marginBalance": det.get("marginBalance"),
                "aumAmount": det.get("aumAmount"),
                "currentCopyCount": det.get("currentCopyCount"),
                "new_positions": len(new),
            }
            with (ROOT / f"state_{pid}.jsonl").open("a", encoding="utf-8") as f:
                f.write(json.dumps(snap, ensure_ascii=False) + "\n")
            seen_path.write_text(json.dumps(sorted(seen)), encoding="utf-8")
            print(f"{det.get('nickname')}: 새 포지션 {len(new)} · {det.get('status')}")
    return 1 if errors else 0


def status() -> int:
    before = json.loads(
        (REPO / "reports" / "copytrade_226" / "result.json").read_text(encoding="utf-8")
    )
    avg_win = {t["id"]: t.get("avg_win") for t in before["traders"]}
    names = {t["id"]: t.get("nickname") for t in before["traders"]}
    for pid in _ids():
        path = ROOT / f"pos_{pid}.jsonl"
        rows = (
            [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x.strip()]
            if path.exists()
            else []
        )
        s = forward_summary(rows, avg_win.get(pid))
        mark = "★" if pid in FOCUS else " "
        print(f"{mark} {names.get(pid)}: {json.dumps(s, ensure_ascii=False, default=float)}")
    return 0


def main(argv: list[str]) -> int:
    cmd = argv[0] if argv else ""
    if cmd == "run":
        return run()
    if cmd == "status":
        return status()
    print(__doc__)
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
