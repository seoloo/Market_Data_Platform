"""주도주 '처음 잡힘' 알림 (IDEAS 7) — 순수 함수. 매매하지 않는 정보 도구다.

미국 LT Paper(문서 115)·국장 그림자 기록(문서 143)의 주도주 목록에서 **최근 ``lookback`` 번의 기록에
한 번도 없던 종목**을 찾고, 그 종목의 차트가 어센딩 트라이앵글(고점은 수평에서 막히고 저점은
올라가는 모양)인지 참고로 붙인다.

삼각형 판정은 **검증하지 않은 참고 규칙**이다 — 수익 근거가 아니다(IDEAS 6 연구에서 정의를 따로
사전 등록한다). 숫자는 결과를 보기 전에 정한 단순한 값이며 바꿔 가며 맞추지 않는다.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

__all__ = ["PULLBACK", "TRIANGLE_RULE", "Triangle", "ascending_triangle", "new_entries"]

WINDOW = 60
"""삼각형을 볼 거래일 수 (약 3 달)."""
TOUCH = 0.02
"""고점이 저항선(구간 최고가) 의 2% 안이면 '닿음'."""
MIN_TOUCHES = 2
MIN_GAP = 10
"""닿음끼리 최소 10 거래일 떨어져야 따로 센다."""
PULLBACK = 0.05
"""두 닿음 사이에 저가가 저항선보다 5% 이상 내려가야 한다 — 꾸준한 상승(매일 신고가)을
삼각형으로 잘못 읽지 않게 (2026-10-05, 문서 146 확정 전 합성 시험에서 발견, 수익 보기 전)."""
TRIANGLE_RULE = (
    f"최근 {WINDOW} 거래일(오늘 제외) 최고가 = 저항선 · 고점이 저항선 {TOUCH:.0%} 안에 "
    f"{MIN_TOUCHES} 번 이상(서로 {MIN_GAP} 거래일 이상 떨어지고, 그 사이 저가가 저항선보다 "
    f"{PULLBACK:.0%} 이상 내려감) · 구간을 셋으로 나눈 최저가가 차례로 높아짐 → '형성 중', "
    "오늘 종가가 저항선 위면 '돌파'. 검증 안 된 참고 규칙"
)


def new_entries(
    today: Sequence[str], history: Sequence[Sequence[str]], lookback: int = 20
) -> list[str]:
    """오늘 목록 중 직전 ``lookback`` 번의 목록에 한 번도 없던 종목 (오늘 순서 그대로)."""
    seen = {s for day in history[-lookback:] for s in day} if lookback > 0 else set()
    out: list[str] = []
    for s in today:
        if s not in seen and s not in out:
            out.append(s)
    return out


@dataclass(frozen=True, slots=True)
class Triangle:
    status: str
    """``돌파`` · ``형성 중`` · ``아님`` · ``자료 부족``."""
    resistance: float | None = None
    touches: int = 0
    gap_to_resistance: float | None = None
    """오늘 종가가 저항선보다 몇 % 아래(음수)/위(양수)."""


def ascending_triangle(
    highs: Sequence[float], lows: Sequence[float], closes: Sequence[float]
) -> Triangle:
    """마지막 값이 오늘. 고가·저가가 없으면 종가를 넣어도 된다(종가 기준 판정)."""
    if len(closes) < WINDOW + 1 or len(highs) != len(closes) or len(lows) != len(closes):
        return Triangle("자료 부족")
    hs, ls = highs[-WINDOW - 1 : -1], lows[-WINDOW - 1 : -1]
    resistance = max(hs)
    touches = 0
    last_touch: int | None = None
    floor = resistance * (1 - PULLBACK)
    for i, h in enumerate(hs):
        if h < resistance * (1 - TOUCH):
            continue
        if last_touch is None:
            touches, last_touch = 1, i
        elif i - last_touch >= MIN_GAP and min(ls[last_touch + 1 : i]) <= floor:
            touches += 1
            last_touch = i
    third = WINDOW // 3
    mins = [min(ls[k * third : (k + 1) * third]) for k in range(3)]
    rising = mins[0] < mins[1] < mins[2]
    close = closes[-1]
    gap = close / resistance - 1
    if touches >= MIN_TOUCHES and rising:
        status = "돌파" if close > resistance else "형성 중"
    else:
        status = "아님"
    return Triangle(status, resistance, touches, gap)
