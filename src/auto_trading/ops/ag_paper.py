"""AG Paper (문서 117) — 112 소액 공격형 세 계좌를 Paper 원장들로 합쳐 보는 순수 계산.

- AG-US: QS Paper(문서 116)의 AG 장부 그대로 (1,000 USD, QQQ 추세 켜짐 TQQQ 전액 / 꺼짐 현금).
- AG-CR: 오버레이 Paper 원장 ``ag_cr3`` (T/v2 × 노출 3 배, 비상 스톱 −20%, 계좌 정지 없음)를
  1,000 USD 로 환산.
- AG-MIX: 500 · 500 으로 시작해 해마다 시작 기념일 뒤 첫날 50:50 으로 다시 맞춘다.
- 셋 다 112 §2: 평가액이 처음 돈의 30% 이하가 된 날 정리하고 그 값으로 멈춘다.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date

START_USD = 1_000.0
STOP_LEVEL = 0.30

Path_ = dict[str, float]
"""날짜(YYYY-MM-DD) → 평가액."""


def scale(path: Mapping[str, float], to: float = START_USD) -> Path_:
    """첫날 값을 ``to`` 로 맞춘다 (원장 초기 자본이 달라도 수익률은 같다)."""
    days = sorted(path)
    if not days:
        return {}
    base = path[days[0]]
    return {d: path[d] / base * to for d in days}


def apply_stop(path: Mapping[str, float], base: float = START_USD) -> tuple[Path_, str | None]:
    """처음 돈의 30% 이하가 된 날 정리 — 그 뒤는 그 값으로 멈춘다 (112 §2, run_ag_small 과 같다)."""
    out: Path_ = {}
    stopped: str | None = None
    frozen = 0.0
    for d in sorted(path):
        if stopped is None and path[d] <= STOP_LEVEL * base:
            stopped, frozen = d, path[d]
        out[d] = frozen if stopped else path[d]
    return out, stopped


def _carry(path: Mapping[str, float], days: list[str]) -> Path_:
    """``days`` 의 각 날에 그날까지 가장 최근 값 (미국 휴장일·주말은 직전 종가)."""
    src = sorted(path)
    out: Path_ = {}
    i, last = 0, None
    for d in days:
        while i < len(src) and src[i] <= d:
            last = path[src[i]]
            i += 1
        if last is not None:
            out[d] = last
    return out


def _anniversary(start: str, k: int) -> str:
    y, m, d = (int(x) for x in start.split("-"))
    return date(y + k, m, min(d, 28)).isoformat()


def mix(us: Mapping[str, float], cr: Mapping[str, float], total0: float = START_USD) -> Path_:
    """AG-MIX: 두 계좌가 모두 값이 있는 첫날 반반, 해마다 기념일 뒤 첫날 다시 반반.

    날짜는 코인 쪽(매일)을 따르고, 미국 쪽은 직전 종가를 쓴다.
    """
    days = sorted(cr)
    us_c = _carry(us, days)
    days = [d for d in days if d in us_c]
    if not days:
        return {}
    start = days[0]
    a = total0 / 2 / us_c[start]
    b = total0 / 2 / cr[start]
    k = 1
    out: Path_ = {}
    for d in days:
        value = a * us_c[d] + b * cr[d]
        if d >= _anniversary(start, k):
            a = value / 2 / us_c[d]
            b = value / 2 / cr[d]
            k += 1
        out[d] = value
    return out


def summary(path: Mapping[str, float], base: float = START_USD) -> dict[str, object]:
    """정리선 적용 뒤 평가액·고점 대비·처음 돈 대비 최저."""
    stopped_path, stopped = apply_stop(path, base)
    days = sorted(stopped_path)
    if not days:
        return {"value_usd": None}
    peak = worst = base
    for d in days:
        peak = max(peak, stopped_path[d])
        worst = min(worst, stopped_path[d])
    value = stopped_path[days[-1]]
    return {
        "date": days[-1],
        "value_usd": round(value, 2),
        "return": round(value / base - 1, 4),
        "peak_usd": round(peak, 2),
        "from_peak": round(value / peak - 1, 4),
        "worst_vs_start": round(worst / base - 1, 4),
        "stop_line_usd": round(STOP_LEVEL * base, 2),
        "stopped": stopped,
        "days": len(days),
    }
