"""윈도우 작업 스케줄러 결과 코드 — 점검(pc_health)과 다시 실행(catchup)이 같이 쓴다.

순수 상수·함수만. 대시보드(ui)가 import 할 수 있게 매매 계층에 닿지 않는다.
"""

from __future__ import annotations

__all__ = ["ALREADY_RUNNING", "RESIDENT", "RESULT_OK", "result_text"]

RESULT_OK = {
    0: "성공",
    0x41301: "실행 중",
    0x41303: "아직 실행 전",
}
ALREADY_RUNNING = 0x800710E0
"""작업이 이미 돌고 있어 새 실행을 건너뜀(MultipleInstances=IgnoreNew). 상주 실행기는 정상이다."""
RESIDENT = ("AutoTrading_Demo_EW",)
"""한 번 띄우면 계속 도는 상주 실행기 (시간마다 살아 있는지 확인용으로 다시 띄운다)."""


def result_text(code: int) -> str:
    if code in RESULT_OK:
        return RESULT_OK[code]
    if code == ALREADY_RUNNING:
        return "이미 실행 중"
    return f"실패 코드 0x{code & 0xFFFFFFFF:08X}"
