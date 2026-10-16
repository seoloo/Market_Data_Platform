"""문서 240 — 실적 발표 갭 상승 따라가기 (순수 계산).

docs/research/240-earnings-gap-drift-pead.md. SEC 받기 · 실행은 ``scripts/run_pead_240.py``.
"""

from __future__ import annotations

import bisect
import random
import statistics
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, time
from zoneinfo import ZoneInfo

__all__ = [
    "Event",
    "bootstrap_mean",
    "dedupe",
    "is_signal",
    "outcome",
    "reaction_index",
]

NY = ZoneInfo("America/New_York")
GAP = 0.05
VOL_X = 2.0
HOLD = 20
COST = 0.003
DEDUPE_DAYS = 5
CONTROL_GAP_DAYS = 3


def reaction_index(days: Sequence[str], accepted_utc: str) -> int | None:
    """접수 시각(UTC ISO) → 반응일 번호.

    16:00 ET 이후면 다음 거래일, 아니면 그날(거래일이 아니면 다음 거래일)."""
    t = datetime.fromisoformat(accepted_utc.replace("Z", "+00:00")).astimezone(NY)
    d = t.date().isoformat()
    after = t.time() >= time(16, 0)
    i = bisect.bisect_right(days, d) if after else bisect.bisect_left(days, d)
    return i if i < len(days) else None


def dedupe(idx: Sequence[int], gap: int = DEDUPE_DAYS) -> list[int]:
    out: list[int] = []
    for i in sorted(set(idx)):
        if not out or i - out[-1] > gap:
            out.append(i)
    return out


def is_signal(
    close: Sequence[float], vol: Sequence[float], i: int, gap: float = GAP, vol_x: float = VOL_X
) -> int:
    """+1 = 갭 상승 신호, −1 = 갭 하락(설명용), 0 = 아님."""
    if i < 21 or close[i - 1] <= 0:
        return 0
    avg = statistics.fmean(vol[i - 20 : i])
    if avg <= 0 or vol[i] < vol_x * avg:
        return 0
    r = close[i] / close[i - 1] - 1
    return 1 if r >= gap else -1 if r <= -gap else 0


@dataclass(frozen=True, slots=True)
class Event:
    entry_day: str
    gap: float
    excess: float
    """비용 뒤 종목 수익 − SPY 수익."""


def outcome(
    days: Sequence[str],
    opn: Sequence[float],
    close: Sequence[float],
    i: int,
    spy: Mapping[str, tuple[float, float]],
    hold: int = HOLD,
) -> Event | None:
    """반응일 i → R + 1 시가 진입 · R + hold 종가 청산의 초과 수익.

    ``spy`` = 날짜 → (시가, 종가)."""
    e, x = i + 1, i + hold
    if x >= len(days) or opn[e] <= 0:
        return None
    s0, s1 = spy.get(days[e]), spy.get(days[x])
    if s0 is None or s1 is None:
        return None
    stock = close[x] / opn[e] - 1 - COST
    market = s1[1] / s0[0] - 1
    return Event(days[e], close[i] / close[i - 1] - 1, stock - market)


def bootstrap_mean(
    events: Sequence[Event], reps: int = 2000, seed: int = 240
) -> tuple[float, float, float]:
    """(평균, 2.5%, 97.5%) — 진입 달 묶음 다시 뽑기."""
    if not events:
        return float("nan"), float("nan"), float("nan")
    by: dict[str, list[float]] = {}
    for ev in events:
        by.setdefault(ev.entry_day[:7], []).append(ev.excess)
    cells = [(sum(v), len(v)) for v in by.values()]
    point = sum(s for s, _ in cells) / sum(n for _, n in cells)
    rng = random.Random(seed)
    boot = []
    for _ in range(reps):
        pick = rng.choices(cells, k=len(cells))
        boot.append(sum(s for s, _ in pick) / sum(n for _, n in pick))
    boot.sort()
    return point, boot[int(0.025 * (reps - 1))], boot[int(0.975 * (reps - 1))]
