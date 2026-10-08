"""문서 177 — 나스닥 + 추세 추종 소매 Paper (176 B) 의 순수 계산. 주문 없음.

* 장부 둘: MIX(주식 80% + 소매 20%) · BASE(주식 100%). 같은 날 같은 돈으로 시작한다.
* 매달 첫 거래일 종가에 목표 비중으로 다시 맞추고, 회전 × 편도 비용을 뺀다.
* 판단(등록 §4)은 월 수익으로 한다 — 소매가 주식과 따로 움직이는가, 시험한 지수(AQR)를 따라가는가,
  섞은 장부의 낙폭 · 샤프가 주식만보다 나쁘지 않은가.
"""

from __future__ import annotations

import math
import statistics
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field


@dataclass(slots=True)
class Book:
    units: dict[str, float]
    peak: float
    mdd: float = 0.0
    costs: float = 0.0
    history: list[tuple[str, float]] = field(default_factory=list)


def value(book: Book, prices: Mapping[str, float]) -> float:
    return sum(u * prices[s] for s, u in book.units.items())


def open_book(
    capital: float, targets: Mapping[str, float], prices: Mapping[str, float], cost: float
) -> Book:
    """처음 사기: 목표 비중대로, 산 금액 × 편도 비용을 뺀다 (순수)."""
    units = {s: capital * w * (1 - cost) / prices[s] for s, w in targets.items() if w > 0}
    return Book(units=units, peak=0.0, costs=capital * cost)


def rebalance(
    book: Book, targets: Mapping[str, float], prices: Mapping[str, float], cost: float
) -> float:
    """목표 비중으로 다시 맞추고 비용(회전 × 편도)을 뺀다 (순수, 장부를 고친다).

    낸 비용을 돌려준다."""
    total = value(book, prices)
    cur = {s: book.units.get(s, 0.0) * prices[s] / total for s in set(book.units) | set(targets)}
    turn = sum(abs(targets.get(s, 0.0) - cur.get(s, 0.0)) for s in cur)
    fee = total * turn * cost
    after = total - fee
    book.units = {s: after * w / prices[s] for s, w in targets.items() if w > 0}
    book.costs += fee
    return fee


def mark(book: Book, day: str, prices: Mapping[str, float]) -> float:
    """그날 평가액을 기록하고 최고점 · 최대 낙폭을 고친다 (순수, 장부를 고친다)."""
    v = value(book, prices)
    book.peak = max(book.peak, v)
    if book.peak > 0:
        book.mdd = max(book.mdd, 1 - v / book.peak)
    book.history.append((day, v))
    return v


def new_month(prev_day: str | None, day: str) -> bool:
    return prev_day is not None and prev_day[:7] != day[:7]


def monthly_returns(history: Sequence[tuple[str, float]]) -> dict[str, float]:
    """일별 평가액 → 달마다(마지막 날 기준) 수익. 첫 달은 시작값 대비 (순수)."""
    last: dict[str, float] = {}
    for d, v in history:
        last[d[:7]] = v
    months = sorted(last)
    out: dict[str, float] = {}
    prev = history[0][1] if history else 0.0
    for m in months:
        if prev > 0:
            out[m] = last[m] / prev - 1
        prev = last[m]
    return out


def corr(a: Sequence[float], b: Sequence[float]) -> float | None:
    if len(a) < 3 or len(a) != len(b):
        return None
    sa, sb = statistics.pstdev(a), statistics.pstdev(b)
    if sa == 0 or sb == 0:
        return None
    ma, mb = statistics.fmean(a), statistics.fmean(b)
    return sum((x - ma) * (y - mb) for x, y in zip(a, b, strict=True)) / (len(a) * sa * sb)


def sharpe(monthly: Sequence[float]) -> float | None:
    if len(monthly) < 3:
        return None
    sd = statistics.pstdev(monthly)
    return statistics.fmean(monthly) / sd * math.sqrt(12) if sd > 0 else None


def judge(
    sleeve_m: Mapping[str, float],
    equity_m: Mapping[str, float],
    aqr_m: Mapping[str, float],
    mix_mdd: float,
    base_mdd: float,
    mix_m: Mapping[str, float],
    base_m: Mapping[str, float],
) -> dict[str, object]:
    """등록 §4 판단 (순수). J1 소매 ↔ 주식 월 상관 ≤ 0.3 · J2 소매 ↔ AQR 월 상관 ≥ 0.5 ·
    J3 MIX 낙폭 ≤ BASE 그리고 MIX 샤프 ≥ BASE − 0.10."""
    common = sorted(set(sleeve_m) & set(equity_m))
    c1 = corr([sleeve_m[m] for m in common], [equity_m[m] for m in common])
    both = sorted(set(sleeve_m) & set(aqr_m))
    c2 = corr([sleeve_m[m] for m in both], [aqr_m[m] for m in both])
    ms = sorted(set(mix_m) & set(base_m))
    s_mix, s_base = sharpe([mix_m[m] for m in ms]), sharpe([base_m[m] for m in ms])
    j1 = c1 is not None and c1 <= 0.3
    j2 = c2 is not None and c2 >= 0.5
    j3 = s_mix is not None and s_base is not None and mix_mdd <= base_mdd and s_mix >= s_base - 0.10
    return {
        "months": len(common),
        "aqr_months": len(both),
        "J1_sleeve_equity_corr": c1,
        "J2_sleeve_aqr_corr": c2,
        "J3_mix_vs_base": {"mdd": [mix_mdd, base_mdd], "sharpe": [s_mix, s_base]},
        "J1": j1,
        "J2": j2,
        "J3": j3,
        "eligible_for_small_live_review": j1 and j2 and j3,
    }


# ------------------------------------------------------------------ 182 레버리지 + 배당 장부

LEV_MAX = 0.6
DD_FULL = 0.30
VOL_REF = 0.20


def month_end_values(history: Sequence[tuple[str, float]], before: str) -> list[float]:
    """``before`` (YYYY-MM-DD) 달 전까지 달마다 마지막 평가액 (순수, 오래된 것부터)."""
    last: dict[str, float] = {}
    for d, v in history:
        if d[:7] < before[:7]:
            last[d[:7]] = v
    return [last[m] for m in sorted(last)]


def lev_share_dd(values: Sequence[float]) -> float:
    """182 M2 낙폭 브레이크: 60% × clip(1 − DD12 / 0.30, 0, 1).

    DD12 는 최근 12 개 달 말 고점 대비 낙폭."""
    if not values:
        return LEV_MAX
    peak = max(values[-12:])
    dd = 1 - values[-1] / peak if peak > 0 else 0.0
    return LEV_MAX * min(1.0, max(0.0, 1 - dd / DD_FULL))


def realized_vol(closes: Sequence[float], n: int = 20) -> float | None:
    """마지막 ``n`` 개 일간 수익의 표준편차 × √252 (순수). 모자라면 None."""
    if len(closes) < n + 1:
        return None
    rets = [b / a - 1 for a, b in zip(closes[-n - 1 : -1], closes[-n:], strict=True)]
    return statistics.pstdev(rets) * math.sqrt(252)


def lev_share_vol(vol: float | None) -> float:
    """182 M3 변동성 맞춤: 60% × min(1, 0.20 / σ20)."""
    if vol is None or vol <= 0:
        return LEV_MAX
    return LEV_MAX * min(1.0, VOL_REF / vol)
