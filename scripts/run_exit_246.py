"""문서 246 — 💹 '−3% 첫 손절 + 느린 추세 꺾임(113 나가기)' 을 국장에서. 1회 판정.

사용 예::

    python -m scripts.run_exit_246 run   # 깨끗한 트리 · 자료 해시 일치 · 판정 파일 없음일 때만

242 의 판 · 크기(1% ÷ 첫 손절 폭, 25% 상한, 추매 없음, 최대 10 종목) · 진입을 그대로 쓰고 나가기만:

* S3 (대조): 진입가 −3% 손절만 — 242 국장 결과를 그대로 재현해야 한다(코드가 확인).
* F_S3SLOW (후보): S3 + 113 의 느린 나가기(추세 꺾임 또는 고점 −35% → 다음 거래일 종가).

판정은 국장(폐지 포함, 이 규칙은 처음). 미국(113 · 238 · 242 와 같은 자료, 이미 본 것)은 참고 줄.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

from scripts.run_a1_train import git_state
from scripts.run_exit_242 import ARMS as ARMS_242
from scripts.run_exit_242 import OUT as OUT_242
from scripts.run_exit_242 import Exit, evaluate, kr_market, us_market

REPO = Path(__file__).resolve().parent.parent
OUT = REPO / "reports" / "exit_246"
SHARPE_GAIN = 0.10
S3 = ARMS_242[0]
CANDIDATE = Exit("F_S3SLOW", 0.03, None, None, slow=True)


def judge(kr: dict[str, Any]) -> str:
    """사전 등록 §4 (순수): 국장 샤프 S3 + 0.10 이상 · 낙폭 S3 이하 · 두 반쪽 샤프 S3 이상."""
    f, s = kr["F_S3SLOW"], kr["S3"]
    ok = (
        f["full"]["sharpe"] >= s["full"]["sharpe"] + SHARPE_GAIN
        and f["full"]["mdd"] <= s["full"]["mdd"]
        and all(f[h]["sharpe"] >= s[h]["sharpe"] for h in ("first", "second"))
    )
    return "ADOPT" if ok else "NONE"


def run() -> dict[str, Any]:
    target = OUT / "verdict.json"
    if target.exists():
        raise SystemExit(f"이미 판정했다: {target}")
    sha, dirty = git_state()
    if dirty:
        raise SystemExit("작업 트리가 깨끗하지 않다 — 커밋 뒤에 실행")
    arms = (S3, CANDIDATE)
    kr = evaluate(*kr_market(), arms=arms)
    ref = json.loads((OUT_242 / "verdict.json").read_text(encoding="utf-8"))["kr"]["S3"]["full"]
    if kr["S3"]["full"] != ref:
        raise SystemExit(f"242 국장 S3 재현 실패: {kr['S3']['full']} vs {ref}")
    us = evaluate(*us_market(), arms=arms)
    out = {
        "git": sha,
        "verdict": judge(kr),
        "kr": kr,
        "us_reference": us,
        "note": (
            "문서 246 1 회 판정. 국장 판정, 미국은 이미 본 자료라 참고. BASE = 141 · 113 추세 보유."
        ),
    }
    OUT.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    return out


def main(argv: list[str]) -> int:
    if argv[1:] != ["run"]:
        print(__doc__)
        return 2
    print(json.dumps({"verdict": run()["verdict"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
