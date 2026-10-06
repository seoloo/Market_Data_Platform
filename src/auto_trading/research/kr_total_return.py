"""국장 배당 권리일과 KOSPI 총수익 대리 지수 (순수 함수).

관문: docs/research/FUNDAMENTAL_KR_GATES.md §5·§6 (사용자 결정 2026-10-06 — K4 의 KOSPI 비교는
이 대리 지수로 판정).

* 결제는 매매일 다음 **2 영업일**(T+2). 영업일 = 거래일 + 연말 휴장이지만 결제는 되는 평일
  (12 월 29 ~ 31 일 중 거래가 없는 평일). 기준일 R 에 주주이려면 결제일 ≤ R 이어야 한다 →
  **권리 마지막 매수일(cum)** = 그 조건을 만족하는 마지막 거래일, 배당락일 = 그다음 거래일.
  예: 2023 기준일 12-31(일) → 12-27 까지 매수, 12-28 배당락.
* 대리 지수: TR_t = TR_{t-1} × (P_t / P_{t-1} + D_t / MC_{t-1}). P = KOSPI 가격 지수,
  D_t = 그날 배당락인 보통주들의 (주당 배당금 × cum 날 상장주식수) 합,
  MC = 전날 유가증권시장 시가총액.
"""

from __future__ import annotations

import bisect
from collections.abc import Mapping, Sequence
from datetime import date

__all__ = ["business_days", "cum_index", "total_return_index"]


def business_days(trading: Sequence[str]) -> list[str]:
    """거래일(YYYYMMDD, 오름차순) + 12-29 ~ 12-31 의 거래 없는 평일 → 결제 영업일."""
    extra: set[str] = set()
    years = {d[:4] for d in trading}
    have = set(trading)
    for y in years:
        for day in (29, 30, 31):
            d = date(int(y), 12, day)
            s = d.strftime("%Y%m%d")
            if d.weekday() < 5 and s not in have:
                extra.add(s)
    return sorted(have | extra)


def cum_index(trading: Sequence[str], biz: Sequence[str], record: str) -> int | None:
    """기준일 ``record`` 의 권리 마지막 매수일 색인(``trading`` 안). 없으면 None.

    매매일 T 의 결제일 = ``biz`` 에서 T 다음 둘째 영업일. 결제일 ≤ record 인 마지막 T."""
    hi = bisect.bisect_right(trading, record) - 1
    for i in range(hi, max(hi - 6, -1), -1):
        j = bisect.bisect_right(biz, trading[i])  # T 다음 영업일 색인
        if j + 1 < len(biz) and biz[j + 1] <= record:
            return i
    return None


def total_return_index(
    price: Sequence[float],
    dividends: Mapping[int, float],
    mcap: Sequence[float],
) -> list[float]:
    """가격 지수 ``price``(거래일 순) + 그날 배당락 총액 ``dividends``{색인: 원} ÷ 전날 시가총액."""
    out = [1.0]
    for t in range(1, len(price)):
        d = dividends.get(t, 0.0) / mcap[t - 1] if mcap[t - 1] > 0 else 0.0
        out.append(out[-1] * (price[t] / price[t - 1] + d))
    return out
