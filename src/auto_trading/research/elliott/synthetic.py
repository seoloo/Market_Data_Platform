"""교과서 Elliott 합성 경로 — 판독기 보정 전용 (33 §22). 시장 자료가 아니다.

로그 가격의 계층 파동(부모 → 하위 → 그 아래 한 층, 맨 아래는 직선)을 꼭짓점으로 만들고
1m 로 선형 보간한 뒤 독립 가우시안 잡음을 더한다. 난수는 호출자가 넘긴 ``random.Random`` 만 쓴다.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass
from decimal import Decimal
from itertools import pairwise

from auto_trading.core.enums import Timeframe
from auto_trading.core.types import Candle
from auto_trading.features.structure import SwingKind

__all__ = [
    "BASE_W1",
    "CORRECTIVE",
    "MOTIVE",
    "SEED",
    "SPEED_PER_MINUTE",
    "SyntheticPath",
    "build_path",
]

SEED = 20260924
BASE_W1 = 0.30
"""D2 기준 부모 1파(또는 A) 크기, 로그."""
SPEED_PER_MINUTE = 0.00004
"""부모 파동의 1분당 로그 속도 — 1파 ≈ 31 개 4h 봉."""
MOTIVE = "MOTIVE"
CORRECTIVE = "CORRECTIVE"
_BASE_PRICE = 30_000.0
_ORIGIN_MS = 1_000 * Timeframe.H4.ms
_CENT = Decimal("0.01")

_Wave = tuple[str, float]  # (모양, 부호 있는 로그 크기)


@dataclass(frozen=True, slots=True)
class SyntheticPath:
    pattern: str
    """IMPULSE | ZIGZAG | FLAT | TRIANGLE (첫 다리 상승)."""
    minutes: tuple[Candle, ...]
    four_hour: tuple[Candle, ...]
    vertex_minutes: tuple[int, ...]
    """진짜 부모 꼭짓점의 1m 봉 색인 (잡음 경로의 실제 극값)."""
    vertex_kinds: tuple[SwingKind, ...]
    leg_truth: tuple[str, ...]
    """부모 다리마다 MOTIVE | CORRECTIVE."""


def _motive(rng: random.Random, amp: float) -> list[_Wave]:
    while True:
        r2 = rng.uniform(0.382, 0.786)
        k3 = rng.uniform(1.0, 2.618)
        r4 = rng.uniform(0.236, 0.5)
        k5 = rng.uniform(0.618, 1.0)
        if k3 * (1 - r4) > r2:  # 4파가 1파 끝을 넘지 않음
            break
    unit = amp / (1 - r2 + k3 - r4 * k3 + k5)
    return [
        (MOTIVE, unit),
        (CORRECTIVE, -r2 * unit),
        (MOTIVE, k3 * unit),
        (CORRECTIVE, -r4 * k3 * unit),
        (MOTIVE, k5 * unit),
    ]


def _zigzag(rng: random.Random, amp: float) -> list[_Wave]:
    rb = rng.uniform(0.382, 0.786)
    kc = rng.uniform(0.618, 1.618)
    unit = amp / (1 - rb + kc)
    return [(MOTIVE, unit), (CORRECTIVE, -rb * unit), (MOTIVE, kc * unit)]


def _flat(rng: random.Random, amp: float) -> list[_Wave]:
    """플랫 A-B-C (3-3-5), 문서 60 §5: B/A ~ U(0.618, 1.382), C/A ~ U(0.618, 1.618)."""
    rb = rng.uniform(0.618, 1.382)
    kc = rng.uniform(0.618, 1.618)
    unit = amp / (1 - rb + kc) if abs(1 - rb + kc) > 1e-9 else amp
    return [(CORRECTIVE, unit), (CORRECTIVE, -rb * unit), (MOTIVE, kc * unit)]


def _triangle(rng: random.Random, amp: float) -> list[_Wave]:
    """수렴 삼각형 A-B-C-D-E (3-3-3-3-3), 문서 60 §5: 연속 다리 비율 ~ U(0.55, 0.85)."""
    sizes = [1.0]
    for _ in range(4):
        sizes.append(sizes[-1] * rng.uniform(0.55, 0.85))
    return [(CORRECTIVE, s * amp * (1 if k % 2 == 0 else -1)) for k, s in enumerate(sizes)]


def _split(rng: random.Random, shape: str, amp: float) -> list[_Wave]:
    return _motive(rng, amp) if shape == MOTIVE else _zigzag(rng, amp)


def _durations(rng: random.Random, amps: list[float], total: int) -> list[int]:
    weights = [abs(a) * rng.uniform(0.7, 1.3) for a in amps]
    scale = total / sum(weights)
    out = [max(1, round(w * scale)) for w in weights]
    out[-1] = max(1, out[-1] + total - sum(out))
    return out


def _expand(
    rng: random.Random, shape: str, amp: float, minutes: int, depth: int
) -> list[tuple[float, int]]:
    """(로그 변화, 분) 직선 조각들. 깊이 0 이거나 너무 짧으면 직선 하나."""
    if depth == 0:
        return [(amp, minutes)]
    kids = _split(rng, shape, amp)
    if minutes < 2 * len(kids):
        return [(amp, minutes)]
    out: list[tuple[float, int]] = []
    for (kid_shape, kid_amp), d in zip(
        kids, _durations(rng, [a for _, a in kids], minutes), strict=True
    ):
        out.extend(_expand(rng, kid_shape, kid_amp, d, depth - 1))
    return out


def build_path(
    rng: random.Random,
    pattern: str,
    scale: float,
    parent_threshold: float,
    noise_sd: float,
) -> SyntheticPath:
    """상승 impulse(5-3-5-3-5)·zigzag(5-3-5)·flat(3-3-5)·수렴 triangle(3-3-3-3-3) 하나와
    앞뒤 진입·이탈 직선."""
    w1 = BASE_W1 * scale
    if pattern == "IMPULSE":
        top_waves = _motive(rng, 1.0)
        unit = w1 / top_waves[0][1]
        top_waves = [(s, a * unit) for s, a in top_waves]
    elif pattern in ("ZIGZAG", "FLAT", "TRIANGLE"):
        maker = {"ZIGZAG": _zigzag, "FLAT": _flat, "TRIANGLE": _triangle}[pattern]
        top_waves = maker(rng, 1.0)
        unit = w1 / top_waves[0][1]
        top_waves = [(s, a * unit) for s, a in top_waves]
    else:
        raise ValueError(pattern)
    lead = math.log(1 + 2 * parent_threshold)
    pieces: list[tuple[float, int]] = [(-lead, max(1, round(lead / SPEED_PER_MINUTE)))]
    nominal = [pieces[0][1]]
    for shape, amp in top_waves:
        minutes = max(1, round(abs(amp) / SPEED_PER_MINUTE * rng.uniform(0.7, 1.3)))
        pieces.extend(_expand(rng, shape, amp, minutes, 2))
        nominal.append(nominal[-1] + minutes)
    exit_minutes = max(1, round(lead / SPEED_PER_MINUTE))
    pieces.append((-lead, exit_minutes))
    total = sum(m for _, m in pieces)
    pad = (-total) % 240  # 4h 봉 경계에 맞춰 이탈 직선을 이어 붙인다
    if pad:
        pieces.append((-lead * pad / exit_minutes, pad))

    # 분 경계 가격점 P_0..P_M (로그), 선형 보간 + 독립 잡음
    level = lead
    points = [level]
    for delta, minutes in pieces:
        for step in range(1, minutes + 1):
            points.append(level + delta * step / minutes)
        level += delta
    noisy = [p + (rng.gauss(0.0, noise_sd) if noise_sd > 0 else 0.0) for p in points]
    prices = [Decimal(str(_BASE_PRICE * math.exp(v))).quantize(_CENT) for v in noisy]
    minutes_out = tuple(
        Candle(
            open_ms=_ORIGIN_MS + t * Timeframe.M1.ms,
            close_ms=_ORIGIN_MS + (t + 1) * Timeframe.M1.ms - 1,
            open=prices[t],
            high=max(prices[t], prices[t + 1]),
            low=min(prices[t], prices[t + 1]),
            close=prices[t + 1],
            volume=Decimal(1),
        )
        for t in range(len(prices) - 1)
    )
    four_hour = tuple(
        Candle(
            open_ms=group[0].open_ms,
            close_ms=group[-1].close_ms,
            open=group[0].open,
            high=max(c.high for c in group),
            low=min(c.low for c in group),
            close=group[-1].close,
            volume=Decimal(len(group)),
        )
        for group in (minutes_out[i : i + 240] for i in range(0, len(minutes_out), 240))
    )

    kinds = tuple(
        SwingKind.LOW if i % 2 == 0 else SwingKind.HIGH for i in range(len(top_waves) + 1)
    )
    bounds = [
        0,
        *((a + b) // 2 for a, b in pairwise(nominal)),
        len(minutes_out),
    ]
    vertex_minutes: list[int] = []
    for i, kind in enumerate(kinds):
        window = range(bounds[i], bounds[i + 1])
        if kind is SwingKind.HIGH:
            best = max(window, key=lambda t: (minutes_out[t].high, -t))
        else:
            best = min(window, key=lambda t: (minutes_out[t].low, t))
        vertex_minutes.append(best)
    return SyntheticPath(
        pattern=pattern,
        minutes=minutes_out,
        four_hour=four_hour,
        vertex_minutes=tuple(vertex_minutes),
        vertex_kinds=kinds,
        leg_truth=tuple(shape for shape, _ in top_waves),
    )
