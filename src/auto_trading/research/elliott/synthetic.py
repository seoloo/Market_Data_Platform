"""교과서 Elliott 합성 경로 — 판독기 보정 전용 (33 §22). 시장 자료가 아니다.

로그 가격의 계층 파동(부모 → 하위 → 그 아래 한 층, 맨 아래는 직선)을 꼭짓점으로 만들고
1m 로 선형 보간한 뒤 독립 가우시안 잡음을 더한다. 난수는 호출자가 넘긴 ``random.Random`` 만 쓴다.
"""

from __future__ import annotations

import math
import random
from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal
from itertools import pairwise

from auto_trading.core.enums import Timeframe
from auto_trading.core.types import Candle
from auto_trading.features.structure import SwingKind

__all__ = [
    "BASE_W1",
    "CORRECTIVE",
    "EW5_PATTERNS",
    "MOTIVE",
    "NESTED_PATTERNS",
    "SEED",
    "SPEED_PER_MINUTE",
    "MarketNoise",
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
FLATLEG = "FLATLEG"
"""EW-5 (문서 100): 이 다리를 플랫(3-3-5)으로 나눈다 — 이중 3 의 W·Y."""
TRILEG = "TRILEG"
"""EW-5: 이 다리를 수렴 삼각형으로 나눈다 — 이중 3 의 Y."""
_BASE_PRICE = 30_000.0
_ORIGIN_MS = 1_000 * Timeframe.H4.ms
_CENT = Decimal("0.01")

_Wave = tuple[str, float]  # (모양, 부호 있는 로그 크기)


@dataclass(frozen=True, slots=True)
class SyntheticPath:
    pattern: str
    """IMPULSE | ZIGZAG | FLAT | TRIANGLE | :data:`NESTED_PATTERNS` 이름 (첫 다리 상승)."""
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


NESTED_PATTERNS: dict[str, tuple[str, int, str]] = {
    "IMPULSE_W4_TRIANGLE": ("IMPULSE", 3, "TRIANGLE"),
    "IMPULSE_W4_EXPFLAT": ("IMPULSE", 3, "EXPFLAT"),
    "ZIGZAG_B_TRIANGLE": ("ZIGZAG", 1, "TRIANGLE"),
    "ZIGZAG_B_EXPFLAT": ("ZIGZAG", 1, "EXPFLAT"),
}
"""문서 61: 이름 → (최상위 패턴, 안에 든 다리 색인, 안의 모양)."""


def _inner(rng: random.Random, form: str, a: float) -> list[_Wave]:
    """아래로 가는 조정 다리 안의 모양 (A 크기 a). 문서 61 §3."""
    if form == "TRIANGLE":
        sizes = [1.0]
        for _ in range(4):
            sizes.append(sizes[-1] * rng.uniform(0.55, 0.85))
        return [(CORRECTIVE, a * s * (-1 if k % 2 == 0 else 1)) for k, s in enumerate(sizes)]
    rb = rng.uniform(1.05, 1.382)
    kc = rng.uniform(1.0, 1.618)
    return [(CORRECTIVE, -a), (CORRECTIVE, a * rb), (MOTIVE, -a * kc)]


def _lowest(start: float, legs: list[_Wave]) -> float:
    level, low = start, start
    for _, amp in legs:
        level += amp
        low = min(low, level)
    return low


def _nested(rng: random.Random, name: str) -> tuple[list[_Wave], dict[int, list[_Wave]]]:
    """최상위 파동(1파·A = 1)과 안에 든 모양의 다리. 절대 규칙을 어기면 다시 뽑는다."""
    top, _, form = NESTED_PATTERNS[name]
    while True:
        if top == "IMPULSE":
            r2 = rng.uniform(0.382, 0.786)
            k3 = rng.uniform(1.0, 2.618)
            r4 = rng.uniform(0.236, 0.5)
            k5 = rng.uniform(0.618, 1.0)
            kids = _inner(rng, form, r4 * k3)
            if _lowest(1 - r2 + k3, kids) <= 1.0:  # 4파가 1파 끝과 겹침
                continue
            net = sum(amp for _, amp in kids)
            waves = [(MOTIVE, 1.0), (CORRECTIVE, -r2), (MOTIVE, k3), (form, net), (MOTIVE, k5)]
            return waves, {3: kids}
        rb = rng.uniform(0.382, 0.786)
        kc = rng.uniform(0.618, 1.618)
        kids = _inner(rng, form, rb)
        net = sum(amp for _, amp in kids)
        if (
            _lowest(1.0, kids) <= 0.0 or 1.0 + net + kc <= 1.0
        ):  # B 가 시작을 넘음 · C 가 A 끝 못 넘음
            continue
        return [(MOTIVE, 1.0), (form, net), (MOTIVE, kc)], {1: kids}


def _split(rng: random.Random, shape: str, amp: float, strict: bool = False) -> list[_Wave]:
    if shape == FLATLEG:
        return _flat_leg(rng, amp)
    if shape == TRILEG:
        legs = _triangle(rng, 1.0)
        net = sum(a for _, a in legs)
        return [(s, a * amp / net) for s, a in legs]
    if strict:
        return _motive_strict(rng, amp) if shape == MOTIVE else _zigzag_strict(rng, amp)
    return _motive(rng, amp) if shape == MOTIVE else _zigzag(rng, amp)


_NO_TRUNCATION = 0.1
"""문서 61 §3: 새 패턴의 하위 5파·C 는 3파·A 끝을 (단위의) 10% 이상 넘는다 — 절단형 없음."""


def _motive_strict(rng: random.Random, amp: float) -> list[_Wave]:
    while True:
        waves = _motive(rng, amp)
        unit = waves[0][1]
        if (waves[4][1] + waves[3][1]) / unit > _NO_TRUNCATION:  # 5파 끝 − 3파 끝
            return waves


def _zigzag_strict(rng: random.Random, amp: float) -> list[_Wave]:
    while True:
        waves = _zigzag(rng, amp)
        unit = waves[0][1]
        if (waves[2][1] + waves[1][1]) / unit > _NO_TRUNCATION:  # C 끝 − A 끝
            return waves


# ---------------------------------------------------------------- EW-5 (문서 100 §5)


def _diagonal(rng: random.Random, leading: bool) -> list[_Wave]:
    """대각선 1-2-3-4-5: 4 파가 1 파와 겹치고 2 파 끝 위, L1 > L3 > L5 · L2 > L4 · L5 > L4."""
    side = MOTIVE if leading else CORRECTIVE
    while True:
        r2 = rng.uniform(0.5, 0.85)
        k3 = rng.uniform(0.6, 0.95)
        lo, hi = k3 - r2, min(k3, r2)  # q4 < q1 · q4 > q2 · L4 < L2
        if k3 <= r2 or hi - lo < 0.05:
            continue
        l4 = rng.uniform(lo + 0.02, hi - 0.02)
        top = 0.95 * k3  # L5 < L3
        if top <= 1.05 * l4:
            continue
        l5 = rng.uniform(1.05 * l4, top)  # q5 > q3
        return [(side, 1.0), (CORRECTIVE, -r2), (side, k3), (CORRECTIVE, -l4), (side, l5)]


def _double_zigzag(rng: random.Random) -> list[_Wave]:
    rx = rng.uniform(0.382, 0.786)
    ky = rng.uniform(max(1.05 * rx, 0.618), 1.618)
    return [(CORRECTIVE, 1.0), (CORRECTIVE, -rx), (CORRECTIVE, ky)]


def _flat_leg(rng: random.Random, amp: float) -> list[_Wave]:
    """이중 3 의 W·Y 플랫: 정규(B/A 0.786~1.0) · C 가 A 끝을 넘음 — 다리 끝이 극값."""
    rb = rng.uniform(0.786, 1.0)
    kc = rng.uniform(rb + 0.05, 1.5)
    unit = amp / (1 - rb + kc)
    return [(CORRECTIVE, unit), (CORRECTIVE, -rb * unit), (MOTIVE, kc * unit)]


def _double_three(rng: random.Random) -> list[_Wave]:
    """W(지그재그·플랫) − X(지그재그, W 의 60~95%) − Y(W 가 지그재그면 플랫·삼각형), 옆걸음."""
    w = rng.choice([CORRECTIVE, FLATLEG])
    y = rng.choice([FLATLEG, TRILEG] if w == CORRECTIVE else [CORRECTIVE, FLATLEG, TRILEG])
    return [(w, 1.0), (CORRECTIVE, -rng.uniform(0.6, 0.95)), (y, rng.uniform(0.7, 1.3))]


def _expanding_triangle(rng: random.Random) -> list[_Wave]:
    sizes = [1.0]
    for _ in range(4):
        sizes.append(sizes[-1] * rng.uniform(1.05, 1.45))
    return [(CORRECTIVE, s * (1 if k % 2 == 0 else -1)) for k, s in enumerate(sizes)]


def _running_triangle(rng: random.Random) -> list[_Wave]:
    sizes = [1.0, rng.uniform(1.05, 1.3)]
    for _ in range(3):
        sizes.append(sizes[-1] * rng.uniform(0.55, 0.85))
    return [(CORRECTIVE, s * (1 if k % 2 == 0 else -1)) for k, s in enumerate(sizes)]


EW5_PATTERNS: dict[str, object] = {
    "ENDING_DIAGONAL": lambda rng: _diagonal(rng, leading=False),
    "LEADING_DIAGONAL": lambda rng: _diagonal(rng, leading=True),
    "DOUBLE_ZIGZAG": _double_zigzag,
    "DOUBLE_THREE": _double_three,
    "EXPANDING_TRIANGLE": _expanding_triangle,
    "RUNNING_TRIANGLE": _running_triangle,
}
"""문서 100 §5: 새 합성 모양 이름 → 최상위 다리 만들기 (첫 다리 상승, 크기 1 기준)."""


def _durations(rng: random.Random, amps: list[float], total: int) -> list[int]:
    weights = [abs(a) * rng.uniform(0.7, 1.3) for a in amps]
    scale = total / sum(weights)
    out = [max(1, round(w * scale)) for w in weights]
    out[-1] = max(1, out[-1] + total - sum(out))
    return out


def _expand(
    rng: random.Random, shape: str, amp: float, minutes: int, depth: int, strict: bool = False
) -> list[tuple[float, int]]:
    """(로그 변화, 분) 직선 조각들. 깊이 0 이거나 너무 짧으면 직선 하나."""
    if depth == 0:
        return [(amp, minutes)]
    kids = _split(rng, shape, amp, strict)
    if minutes < 2 * len(kids):
        return [(amp, minutes)]
    out: list[tuple[float, int]] = []
    for (kid_shape, kid_amp), d in zip(
        kids, _durations(rng, [a for _, a in kids], minutes), strict=True
    ):
        out.extend(_expand(rng, kid_shape, kid_amp, d, depth - 1, strict))
    return out


@dataclass(frozen=True, slots=True)
class MarketNoise:
    """문서 103 시장형 합성: 다리마다 실제 1m 로그수익 묶음을 끝점 고정(양 끝 0)으로 더한다.

    ``returns`` 실제 1m 로그수익 열 · ``k`` 크기 배율 · ``step`` 실제 1 분이 합성 몇 분인가
    (1h 설정은 4) · ``depth`` 교과서 하위 분할 깊이 (기존 2).
    """

    returns: Sequence[float]
    k: float
    step: int = 1
    depth: int = 2


def _bridge(rng: random.Random, noise: MarketNoise, length: int) -> list[float]:
    """길이 ``length`` 분의 끝점 고정 누적 흔들림 (0 .. length, 양 끝 0)."""
    need = -(-length // noise.step)
    start = rng.randrange(0, len(noise.returns) - need)
    path = [0.0]
    for t in range(1, length + 1):
        path.append(path[-1] + noise.returns[start + (t - 1) // noise.step] / noise.step)
    end = path[-1]
    return [noise.k * (v - end * t / length) for t, v in enumerate(path)]


def build_path(
    rng: random.Random,
    pattern: str,
    scale: float,
    parent_threshold: float,
    noise_sd: float,
    market: MarketNoise | None = None,
    lead_w1: float | None = None,
) -> SyntheticPath:
    """상승 impulse(5-3-5-3-5)·zigzag(5-3-5)·flat(3-3-5)·수렴 triangle(3-3-3-3-3) 하나와
    앞뒤 진입·이탈 직선. ``market`` 이 있으면 문서 103 시장형 흔들림을 더한다(없으면 기존)."""
    w1 = BASE_W1 * scale
    children: dict[int, list[_Wave]] = {}
    if pattern in NESTED_PATTERNS:
        top_waves, rel_children = _nested(rng, pattern)
        unit = w1 / top_waves[0][1]
        top_waves = [(s, a * unit) for s, a in top_waves]
        children = {k: [(s, a * unit) for s, a in v] for k, v in rel_children.items()}
    elif pattern == "IMPULSE":
        top_waves = _motive(rng, 1.0)
        unit = w1 / top_waves[0][1]
        top_waves = [(s, a * unit) for s, a in top_waves]
    elif pattern in ("ZIGZAG", "FLAT", "TRIANGLE"):
        maker = {"ZIGZAG": _zigzag, "FLAT": _flat, "TRIANGLE": _triangle}[pattern]
        top_waves = maker(rng, 1.0)
        unit = w1 / top_waves[0][1]
        top_waves = [(s, a * unit) for s, a in top_waves]
    elif pattern in EW5_PATTERNS:
        make = EW5_PATTERNS[pattern]
        top_waves = make(rng)  # type: ignore[operator]
        unit = w1 / top_waves[0][1]
        top_waves = [(s, a * unit) for s, a in top_waves]
    else:
        raise ValueError(pattern)
    # 문서 107: lead_w1 이 있으면 진입·이탈 다리 = 첫 다리 × lead_w1 (큰 추세 다리 뒤 패턴)
    lead = math.log(1 + 2 * parent_threshold) if lead_w1 is None else lead_w1 * w1
    pieces: list[tuple[float, int]] = [(-lead, max(1, round(lead / SPEED_PER_MINUTE)))]
    nominal = [pieces[0][1]]
    nested = pattern in NESTED_PATTERNS
    ew5 = pattern in EW5_PATTERNS
    depth = 2 if market is None else market.depth
    exact = nested or ew5 or market is not None
    inner_edges: dict[
        int, tuple[int, int]
    ] = {}  # 안 모양 파동 → (첫 안쪽 다리, 마지막 안쪽 다리) 길이
    for index, (shape, amp) in enumerate(top_waves):
        before = len(pieces)
        if index in children:
            kids = children[index]
            span = sum(abs(a) for _, a in kids)
            minutes = max(2 * len(kids), round(span / SPEED_PER_MINUTE * rng.uniform(0.7, 1.3)))
            kid_minutes: list[int] = []
            for (kid_shape, kid_amp), d in zip(
                kids, _durations(rng, [a for _, a in kids], minutes), strict=True
            ):
                kid_pieces = _expand(rng, kid_shape, kid_amp, d, 1, strict=True)
                pieces.extend(kid_pieces)
                kid_minutes.append(sum(m for _, m in kid_pieces))
            inner_edges[index] = (kid_minutes[0], kid_minutes[-1])
        else:
            minutes = max(1, round(abs(amp) / SPEED_PER_MINUTE * rng.uniform(0.7, 1.3)))
            pieces.extend(_expand(rng, shape, amp, minutes, depth, strict=nested or ew5))
        # 새 패턴은 실제 조각 길이 합 (정답 꼭짓점을 조각 경계에서 찾는다), 기존 패턴은 그대로
        added = sum(m for _, m in pieces[before:])
        nominal.append(nominal[-1] + (added if exact else minutes))
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
    if market is not None:
        spans = [(0, nominal[0]), *pairwise(nominal), (nominal[-1], len(points) - 1)]
        for a, b in spans:
            if b > a:
                for t, v in enumerate(_bridge(rng, market, b - a)):
                    points[a + t] += v
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
    windows = [range(bounds[i], bounds[i + 1]) for i in range(len(kinds))]
    for index, (first, last) in inner_edges.items():
        # 문서 61 §3: 안 모양 쪽 창은 첫·마지막 안쪽 다리 절반까지 (삼각형 C 끝·플랫 B 끝 제외)
        start, end = windows[index], windows[index + 1]
        windows[index] = range(start.start, min(start.stop, nominal[index] + max(1, first // 2)))
        windows[index + 1] = range(max(end.start, nominal[index + 1] - last // 2), end.stop)
    if ew5 or market is not None:
        # 문서 100 §5: 끝 다리가 삼각형·플랫이면 반쪽 창의 극값이 진짜 끝이 아니다 —
        # 꼭짓점은 조각 경계 ± (이웃 다리 중 짧은 쪽의 1/4) 안에서 찾는다
        edges = [nominal[0], *(b - a for a, b in pairwise(nominal)), exit_minutes]
        windows = [
            range(
                max(0, at - max(1, min(edges[k], edges[k + 1]) // 4)),
                min(len(minutes_out), at + max(1, min(edges[k], edges[k + 1]) // 4) + 1),
            )
            for k, at in enumerate(nominal)
        ]
    vertex_minutes: list[int] = []
    for kind, window in zip(kinds, windows, strict=True):
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
