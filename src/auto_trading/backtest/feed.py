"""봉 재생용 인메모리 피드.

백테스트는 같은 창을 수십만 번 읽는다. 매번 DB 를 조회하면 ``SnapshotBuilder``
한 번에 387ms 가 걸려 3년치가 며칠이 된다 (2026-09-21 실측). 그래서 구간 전체를
한 번 읽어 메모리에 두고, 이후에는 **슬라이스만** 한다.

인과성 규칙은 그대로다 — ``close_ms <= as_of_ms`` 인 봉만 내보낸다. DB 를 거치지
않는다고 해서 미래 봉이 보이면 안 되므로, 경계 계산을 이 모듈 한 곳에만 둔다.

같은 캔들 객체를 재사용하는 것은 성능뿐 아니라 **해시 캐시**를 위해서도 중요하다
(core/types.Candle.digest 참조).
"""

from __future__ import annotations

import bisect
from dataclasses import dataclass
from decimal import Decimal

from auto_trading.core.enums import Timeframe
from auto_trading.core.errors import DataIntegrityError
from auto_trading.core.types import Candle, TimeframeSeries
from auto_trading.storage.repositories.candles import CandleRepository

__all__ = ["CandleFeed", "FeedWindow"]


@dataclass(frozen=True, slots=True)
class FeedWindow:
    """한 타임프레임에서 유지할 봉 수와 적재 구간."""

    timeframe: Timeframe
    bars: int

    def __post_init__(self) -> None:
        if self.bars <= 0:
            raise ValueError(f"bars 는 1 이상이어야 한다: {self.bars}")


class CandleFeed:
    """구간 전체를 메모리에 올려두고 인과적 슬라이스를 제공한다."""

    __slots__ = ("_candles", "_opens", "_windows", "symbol")

    def __init__(self, symbol: str) -> None:
        self.symbol = symbol
        self._candles: dict[Timeframe, tuple[Candle, ...]] = {}
        self._opens: dict[Timeframe, list[int]] = {}
        self._windows: dict[Timeframe, int] = {}

    @classmethod
    def load(
        cls,
        repo: CandleRepository,
        symbol: str,
        windows: tuple[FeedWindow, ...],
        *,
        from_ms: int,
        to_ms: int,
    ) -> CandleFeed:
        """``[from_ms, to_ms]`` 재생에 필요한 봉을 **warm-up 포함**해 적재한다.

        warm-up 을 빼고 적재하면 구간 앞부분에서 feature 가 계속 미충족이 되어
        조용히 거래가 사라진다. 그래서 각 TF 마다 ``bars`` 만큼 앞을 더 읽는다.
        """
        feed = cls(symbol)
        for window in windows:
            tf = window.timeframe
            lower = from_ms - (window.bars + 1) * tf.ms
            candles = repo.get_range(symbol, tf, from_ms=lower, to_ms=to_ms)
            feed._install(tf, candles, window.bars)
        return feed

    def _install(self, timeframe: Timeframe, candles: tuple[Candle, ...], bars: int) -> None:
        opens = [c.open_ms for c in candles]
        if opens != sorted(opens):  # pragma: no cover - repo 가 정렬을 보장한다
            raise DataIntegrityError(f"{timeframe.value} 봉이 정렬되어 있지 않다")
        self._candles[timeframe] = candles
        self._opens[timeframe] = opens
        self._windows[timeframe] = bars

    @property
    def timeframes(self) -> tuple[Timeframe, ...]:
        """짧은 TF 부터. 스냅샷의 mark price 출처 결정이 이 순서에 의존한다."""
        return tuple(sorted(self._candles, key=lambda tf: tf.ms))

    def count(self, timeframe: Timeframe) -> int:
        return len(self._candles.get(timeframe, ()))

    def required_bars(self, timeframe: Timeframe) -> int:
        return self._windows[timeframe]

    def closed_index(self, timeframe: Timeframe, as_of_ms: int) -> int:
        """``as_of_ms`` 까지 마감된 봉의 개수.

        ``close_ms = open_ms + tf.ms - 1 <= as_of_ms`` 이므로
        ``open_ms <= as_of_ms - tf.ms + 1`` 인 봉까지가 마감된 것이다.
        """
        opens = self._opens.get(timeframe)
        if opens is None:
            raise DataIntegrityError(f"피드에 {timeframe.value} 가 없다")
        return bisect.bisect_right(opens, as_of_ms - timeframe.ms + 1)

    def series(self, timeframe: Timeframe, as_of_ms: int) -> TimeframeSeries:
        """``as_of_ms`` 시점의 마감봉 창. 형성 중인 봉은 포함하지 않는다."""
        end = self.closed_index(timeframe, as_of_ms)
        start = max(0, end - self._windows[timeframe])
        return TimeframeSeries(timeframe=timeframe, candles=self._candles[timeframe][start:end])

    def bar_at(self, timeframe: Timeframe, index: int) -> Candle:
        return self._candles[timeframe][index]

    def bars_between(self, timeframe: Timeframe, from_ms: int, to_ms: int) -> tuple[Candle, ...]:
        """``open_ms`` 가 ``[from_ms, to_ms]`` 에 드는 봉. 체결 재생에 쓴다."""
        opens = self._opens[timeframe]
        lo = bisect.bisect_left(opens, from_ms)
        hi = bisect.bisect_right(opens, to_ms)
        return self._candles[timeframe][lo:hi]

    def last_close(self, timeframe: Timeframe, as_of_ms: int) -> Decimal | None:
        end = self.closed_index(timeframe, as_of_ms)
        return None if end == 0 else self._candles[timeframe][end - 1].close

    def has_warmup(self, timeframe: Timeframe, as_of_ms: int) -> bool:
        return self.closed_index(timeframe, as_of_ms) >= self._windows[timeframe]
