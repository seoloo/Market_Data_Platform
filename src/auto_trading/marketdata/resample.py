"""캔들 재집계.

1분봉을 상위 타임프레임으로 합친다. 두 가지 용도가 있다.

1. **무결성 교차 검증** — 우리가 합친 값과 거래소 원본이 일치하는지 확인 (게이트 1)
2. 상위 TF 데이터가 없을 때의 파생 (현재는 쓰지 않는다 — 거래소 원본을 그대로 저장한다)

파생 집계 로직에 버그가 있으면 백테스트 전체가 오염된다. 그래서 거래소 원본을 저장하고
이 함수는 **대조용**으로만 쓴다 (03-data-model.md §3.2).

이 모듈은 순수하다 — I/O 가 없다.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence

from auto_trading.core.enums import Timeframe
from auto_trading.core.errors import DataIntegrityError
from auto_trading.core.types import Candle

__all__ = ["bucket_candles", "resample_candles"]


def _validate_ratio(source: Timeframe, target: Timeframe) -> int:
    if target.ms <= source.ms:
        raise DataIntegrityError(
            f"상위 타임프레임으로만 합칠 수 있다: {source.value} -> {target.value}"
        )
    if target.ms % source.ms != 0:
        raise DataIntegrityError(
            f"{target.value} 는 {source.value} 의 정수배가 아니다 "
            f"({target.ms} % {source.ms} = {target.ms % source.ms})"
        )
    return target.ms // source.ms


def bucket_candles(candles: Iterable[Candle], target: Timeframe) -> dict[int, list[Candle]]:
    """봉을 상위 타임프레임 버킷으로 나눈다. 키는 버킷의 ``open_ms``."""
    buckets: dict[int, list[Candle]] = {}
    for candle in candles:
        buckets.setdefault(target.open_ms_of(candle.open_ms), []).append(candle)
    for bucket in buckets.values():
        bucket.sort(key=lambda c: c.open_ms)
    return buckets


def resample_candles(
    candles: Sequence[Candle],
    source: Timeframe,
    target: Timeframe,
    *,
    require_complete: bool = True,
) -> tuple[Candle, ...]:
    """``source`` 봉들을 ``target`` 봉으로 합친다.

    합치는 규칙:
        open  = 첫 봉의 open
        high  = 전체 high 중 최댓값
        low   = 전체 low 중 최솟값
        close = 마지막 봉의 close
        volume / quote_volume / trade_count = 합계

    Args:
        require_complete: True 면 봉이 다 모이지 않은 버킷을 **건너뛴다**.
            불완전한 버킷을 결과에 넣으면 대조 시 거짓 불일치가 발생한다.
            (예: 1시간봉 대조에서 마지막 버킷에 1분봉이 30개만 있는 경우)

    Raises:
        DataIntegrityError: 타임프레임 배수 관계가 성립하지 않을 때.
    """
    ratio = _validate_ratio(source, target)
    buckets = bucket_candles(candles, target)

    result: list[Candle] = []
    for bucket_open_ms in sorted(buckets):
        members = buckets[bucket_open_ms]
        if require_complete and len(members) != ratio:
            continue
        result.append(
            Candle(
                open_ms=bucket_open_ms,
                close_ms=bucket_open_ms + target.ms - 1,
                open=members[0].open,
                high=max(c.high for c in members),
                low=min(c.low for c in members),
                close=members[-1].close,
                volume=sum((c.volume for c in members), start=members[0].volume * 0),
                quote_volume=sum(
                    (c.quote_volume for c in members), start=members[0].quote_volume * 0
                ),
                trade_count=sum(c.trade_count for c in members),
            )
        )
    return tuple(result)
