"""Binance 공개 데이터 아카이브 클라이언트 (data.binance.vision).

REST ``/fapi/v1/klines`` 로 수년치를 받으면 레이트 리밋을 크게 소모한다.
공개 아카이브는 월/일 단위 zip 으로 제공되어 대량 백필에 적합하다 (08-external-apis.md §2).

**체크섬을 반드시 검증한다.** 백테스트 신뢰도는 데이터 신뢰도를 넘을 수 없다.
손상된 파일이 조용히 들어가면 이후 모든 검증이 무의미해진다.
"""

from __future__ import annotations

import csv
import hashlib
import io
import time
import zipfile
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from typing import Final, Protocol

import httpx

from auto_trading.core.enums import Timeframe
from auto_trading.core.errors import DataIntegrityError, ExchangeError
from auto_trading.core.types import Candle, FundingRate, PerpMetrics

__all__ = [
    "ArchiveFile",
    "BinanceVisionClient",
    "FundingArchiveFile",
    "Granularity",
    "MetricsArchiveFile",
    "RemoteArchive",
    "months_between",
    "parse_funding_csv",
    "parse_klines_csv",
    "parse_metrics_csv",
]

BASE_URL: Final = "https://data.binance.vision"

#: USD-M 선물 klines CSV 컬럼 수. 헤더 없는 구버전과 헤더 있는 신버전 모두 12개다.
_EXPECTED_COLUMNS: Final = 12

#: fundingRate CSV 컬럼 수 (calc_time, funding_interval_hours, last_funding_rate).
_FUNDING_COLUMNS: Final = 3

#: metrics CSV 컬럼 수 (create_time, symbol, OI 2개, 비율 4개).
_METRICS_COLUMNS: Final = 8


#: 2100년을 밀리초로 표현한 값. 이보다 크면 마이크로초 단위로 판단한다.
_MS_UPPER_BOUND: Final = 4_102_444_800_000


class Granularity(StrEnum):
    MONTHLY = "monthly"
    DAILY = "daily"


class RemoteArchive(Protocol):
    """다운로드 대상 아카이브 파일의 공통 계약.

    klines 와 fundingRate 는 URL 경로만 다르고 받는 방식(zip + CHECKSUM)은 같다.
    클라이언트가 둘 다 다룰 수 있도록 최소 계약만 둔다.
    """

    @property
    def stem(self) -> str: ...

    @property
    def filename(self) -> str: ...

    def url(self, base_url: str = ...) -> str: ...

    def checksum_url(self, base_url: str = ...) -> str: ...


@dataclass(frozen=True, slots=True)
class ArchiveFile:
    """아카이브 파일 하나 (월 또는 일 단위)."""

    symbol: str
    timeframe: Timeframe
    period: date
    granularity: Granularity

    @property
    def stem(self) -> str:
        if self.granularity is Granularity.MONTHLY:
            return f"{self.symbol}-{self.timeframe.value}-{self.period:%Y-%m}"
        return f"{self.symbol}-{self.timeframe.value}-{self.period:%Y-%m-%d}"

    @property
    def filename(self) -> str:
        return f"{self.stem}.zip"

    def url(self, base_url: str = BASE_URL) -> str:
        return (
            f"{base_url}/data/futures/um/{self.granularity.value}/klines/"
            f"{self.symbol}/{self.timeframe.value}/{self.filename}"
        )

    def checksum_url(self, base_url: str = BASE_URL) -> str:
        return f"{self.url(base_url)}.CHECKSUM"

    def __str__(self) -> str:
        return self.filename


@dataclass(frozen=True, slots=True)
class FundingArchiveFile:
    """funding rate 아카이브 (월 단위만 제공된다)."""

    symbol: str
    period: date

    @property
    def stem(self) -> str:
        return f"{self.symbol}-fundingRate-{self.period:%Y-%m}"

    @property
    def filename(self) -> str:
        return f"{self.stem}.zip"

    def url(self, base_url: str = BASE_URL) -> str:
        return f"{base_url}/data/futures/um/monthly/fundingRate/{self.symbol}/{self.filename}"

    def checksum_url(self, base_url: str = BASE_URL) -> str:
        return f"{self.url(base_url)}.CHECKSUM"

    def __str__(self) -> str:
        return self.filename


@dataclass(frozen=True, slots=True)
class MetricsArchiveFile:
    """포지셔닝 지표 아카이브 (**일 단위만** 제공된다 — 월별 파일이 없다)."""

    symbol: str
    period: date

    @property
    def stem(self) -> str:
        return f"{self.symbol}-metrics-{self.period:%Y-%m-%d}"

    @property
    def filename(self) -> str:
        return f"{self.stem}.zip"

    def url(self, base_url: str = BASE_URL) -> str:
        return f"{base_url}/data/futures/um/daily/metrics/{self.symbol}/{self.filename}"

    def checksum_url(self, base_url: str = BASE_URL) -> str:
        return f"{self.url(base_url)}.CHECKSUM"

    def __str__(self) -> str:
        return self.filename


def months_between(start: date, end: date) -> list[date]:
    """``start`` 부터 ``end`` 까지 각 달의 1일을 나열한다 (양끝 포함)."""
    if end < start:
        return []
    months: list[date] = []
    cursor = start.replace(day=1)
    last = end.replace(day=1)
    while cursor <= last:
        months.append(cursor)
        cursor = (cursor.replace(day=28) + timedelta(days=4)).replace(day=1)
    return months


def days_between(start: date, end: date) -> list[date]:
    """``start`` 부터 ``end`` 까지 날짜를 나열한다 (양끝 포함)."""
    if end < start:
        return []
    return [start + timedelta(days=i) for i in range((end - start).days + 1)]


def _normalize_timestamp(raw: int) -> int:
    """타임스탬프를 밀리초로 맞춘다.

    Binance 아카이브는 원래 밀리초를 쓰지만 일부 시기의 파일이 마이크로초로 제공된 적이 있다.
    단위를 잘못 읽으면 봉 시각이 통째로 어긋나고, 그 사실을 알아채기 어렵다.
    방어적으로 정규화하고, 아래에서 봉 길이를 교차 검증한다.
    """
    return raw // 1000 if raw > _MS_UPPER_BOUND else raw


def _to_decimal(text: str, field: str, open_ms: int) -> Decimal:
    try:
        return Decimal(text.strip())
    except (InvalidOperation, AttributeError) as exc:
        raise DataIntegrityError(
            f"{field} 를 Decimal 로 읽을 수 없다: {text!r} (open_ms={open_ms})"
        ) from exc


def parse_klines_csv(data: bytes, timeframe: Timeframe) -> tuple[Candle, ...]:
    """Binance klines CSV 를 :class:`Candle` 로 변환한다.

    컬럼: open_time, open, high, low, close, volume, close_time, quote_volume,
    count, taker_buy_volume, taker_buy_quote_volume, ignore

    신버전 파일에는 헤더 행이 있고 구버전에는 없다. 둘 다 처리한다.

    Raises:
        DataIntegrityError: 컬럼 수 불일치, 봉 경계 미정렬, 봉 길이 불일치.
    """
    text = data.decode("utf-8")
    reader = csv.reader(io.StringIO(text))
    candles: list[Candle] = []

    for line_no, row in enumerate(reader, start=1):
        if not row:
            continue
        if len(row) < _EXPECTED_COLUMNS:
            raise DataIntegrityError(
                f"CSV 컬럼 수가 부족하다 (line {line_no}): {len(row)} < {_EXPECTED_COLUMNS}"
            )
        if not row[0].strip().lstrip("-").isdigit():
            if line_no == 1:
                continue  # 헤더 행
            raise DataIntegrityError(f"open_time 이 숫자가 아니다 (line {line_no}): {row[0]!r}")

        open_ms = _normalize_timestamp(int(row[0]))
        close_ms = _normalize_timestamp(int(row[6]))

        if open_ms % timeframe.ms != 0:
            raise DataIntegrityError(
                f"봉 시작 시각이 {timeframe.value} 경계에 정렬되지 않았다: "
                f"{open_ms} (line {line_no})"
            )
        # 단위 오판을 잡는 교차 검증.
        # Binance 는 close_time = open_time + interval - 1 ms 를 보장한다.
        if close_ms - open_ms != timeframe.ms - 1:
            raise DataIntegrityError(
                f"봉 길이가 {timeframe.value} 와 다르다 (line {line_no}): "
                f"{close_ms - open_ms + 1}ms, 기대값 {timeframe.ms}ms. "
                "타임스탬프 단위가 다를 수 있다."
            )

        candles.append(
            Candle(
                open_ms=open_ms,
                close_ms=close_ms,
                open=_to_decimal(row[1], "open", open_ms),
                high=_to_decimal(row[2], "high", open_ms),
                low=_to_decimal(row[3], "low", open_ms),
                close=_to_decimal(row[4], "close", open_ms),
                volume=_to_decimal(row[5], "volume", open_ms),
                quote_volume=_to_decimal(row[7], "quote_volume", open_ms),
                trade_count=int(row[8]),
            )
        )

    candles.sort(key=lambda c: c.open_ms)
    return tuple(candles)


def parse_funding_csv(data: bytes) -> tuple[FundingRate, ...]:
    """fundingRate CSV 를 파싱한다.

    컬럼: ``calc_time, funding_interval_hours, last_funding_rate``.
    구버전 파일에는 헤더가 없을 수 있으므로 첫 줄을 보고 판단한다.

    캔들과 달리 "봉 경계" 같은 교차 검증 수단이 없다. 대신 주기 값을 함께 저장해
    다음 정산 시각을 역산하지 않는다 (core/types.FundingRate 참조).
    """
    text = data.decode("utf-8-sig", errors="strict")
    rows = list(csv.reader(io.StringIO(text)))
    if not rows:
        return ()
    if rows[0] and not rows[0][0].strip().lstrip("-").isdigit():
        rows = rows[1:]  # 헤더

    rates: list[FundingRate] = []
    seen: set[int] = set()
    for line_no, row in enumerate(rows, start=1):
        if not row or not row[0].strip():
            continue
        if len(row) < _FUNDING_COLUMNS:
            raise DataIntegrityError(
                f"fundingRate CSV 컬럼 수가 {len(row)} 다 (line {line_no}), "
                f"{_FUNDING_COLUMNS}개를 기대한다"
            )
        try:
            funding_ms = _normalize_timestamp(int(row[0]))
            interval_hours = int(row[1])
        except ValueError as exc:
            raise DataIntegrityError(f"fundingRate 정수 파싱 실패 (line {line_no}): {exc}") from exc
        if funding_ms in seen:
            raise DataIntegrityError(f"funding 정산 시각 중복: {funding_ms} (line {line_no})")
        seen.add(funding_ms)
        rates.append(
            FundingRate(
                funding_ms=funding_ms,
                rate=_to_decimal(row[2], "last_funding_rate", funding_ms),
                interval_hours=interval_hours,
            )
        )

    rates.sort(key=lambda r: r.funding_ms)
    return tuple(rates)


def parse_metrics_csv(data: bytes) -> tuple[PerpMetrics, ...]:
    """포지셔닝 지표 CSV 를 파싱한다.

    컬럼: ``create_time, symbol, sum_open_interest, sum_open_interest_value,
    count_toptrader_long_short_ratio, sum_toptrader_long_short_ratio,
    count_long_short_ratio, sum_taker_long_short_vol_ratio``.

    ``create_time`` 은 epoch 가 아니라 ``YYYY-MM-DD HH:MM:SS`` UTC 문자열이다.

    ⚠ **같은 시각이 두 번 들어 있는 파일이 있다** (2026-09-22 실측: 2021-01-15 파일은
    288행이 아니라 577행으로 전 행이 중복). 조용히 넘기면 표본 수가 두 배로 부풀어
    모든 통계가 틀어지므로, 중복은 **값이 같을 때만** 하나로 합치고 다르면 거부한다.
    """
    text = data.decode("utf-8-sig", errors="strict")
    rows = list(csv.reader(io.StringIO(text)))
    if not rows:
        return ()
    if rows[0] and not rows[0][0].strip()[:1].isdigit():
        rows = rows[1:]  # 헤더

    seen: dict[int, PerpMetrics] = {}
    for line_no, row in enumerate(rows, start=1):
        if not row or not row[0].strip():
            continue
        if len(row) < _METRICS_COLUMNS:
            raise DataIntegrityError(
                f"metrics CSV 컬럼 수가 {len(row)} 다 (line {line_no}), "
                f"{_METRICS_COLUMNS}개를 기대한다"
            )
        at_ms = _parse_metrics_time(row[0], line_no)
        if any(not row[i].strip() for i in range(2, _METRICS_COLUMNS)):
            # 값이 비어 있는 스냅샷이 실제로 존재한다 (2026-09-22 실측: 2021-05-07 등).
            # 0 이나 직전값으로 메우지 않고 **그 시각을 없는 것으로** 둔다 — 결측은
            # find_gaps 가 드러내고, 그 구간에서는 신호를 만들지 않는다.
            continue
        entry = PerpMetrics(
            at_ms=at_ms,
            open_interest=_to_decimal(row[2], "sum_open_interest", at_ms),
            open_interest_value=_to_decimal(row[3], "sum_open_interest_value", at_ms),
            toptrader_account_ratio=_to_decimal(row[4], "toptrader_account_ratio", at_ms),
            toptrader_position_ratio=_to_decimal(row[5], "toptrader_position_ratio", at_ms),
            account_long_short_ratio=_to_decimal(row[6], "account_long_short_ratio", at_ms),
            taker_buy_sell_ratio=_to_decimal(row[7], "taker_buy_sell_ratio", at_ms),
        )
        previous = seen.get(at_ms)
        if previous is not None and previous != entry:
            raise DataIntegrityError(
                f"같은 시각에 서로 다른 지표가 있다 (line {line_no}, at_ms={at_ms})"
            )
        seen[at_ms] = entry

    # 빈 행은 거래소 측 결측이다. 하루 전체가 비어 있는 날도 실제로 있다
    # (2026-09-22 실측: 2021-12-31, 2022-01-01). 오류가 아니라 "그 시각 데이터 없음"
    # 이므로 빈 튜플을 돌려주고, 결과는 find_gaps 가 드러낸다.
    return tuple(seen[key] for key in sorted(seen))


def _parse_metrics_time(text: str, line_no: int) -> int:
    try:
        parsed = datetime.strptime(text.strip(), "%Y-%m-%d %H:%M:%S").replace(tzinfo=UTC)
    except ValueError as exc:
        raise DataIntegrityError(f"create_time 을 읽을 수 없다 (line {line_no}): {text!r}") from exc
    return int(parsed.timestamp() * 1000)


def _extract_single_csv(payload: bytes, expected_stem: str) -> bytes:
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        names = [n for n in archive.namelist() if n.lower().endswith(".csv")]
        if len(names) != 1:
            raise DataIntegrityError(f"zip 안에 CSV 가 정확히 1개여야 한다: {names}")
        if names[0] != f"{expected_stem}.csv":
            raise DataIntegrityError(f"zip 내용물 이름이 예상과 다르다: {names[0]}")
        return archive.read(names[0])


def _parse_checksum(text: str) -> str:
    """``<sha256>  <filename>`` 형식에서 해시만 뽑는다."""
    parts = text.strip().split()
    if not parts or len(parts[0]) != 64:
        raise DataIntegrityError(f"CHECKSUM 형식을 읽을 수 없다: {text[:80]!r}")
    return parts[0].lower()


class BinanceVisionClient:
    """공개 아카이브에서 캔들을 가져온다.

    ``httpx.Client`` 를 주입받으므로 테스트에서 ``MockTransport`` 로 대체할 수 있다.
    실제 네트워크 없이 전 경로를 검증한다.
    """

    __slots__ = ("_client", "_owns_client", "base_url", "max_retries", "request_delay_s")

    def __init__(
        self,
        client: httpx.Client | None = None,
        *,
        base_url: str = BASE_URL,
        max_retries: int = 3,
        request_delay_s: float = 0.0,
        timeout_s: float = 60.0,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.max_retries = max_retries
        self.request_delay_s = request_delay_s
        self._owns_client = client is None
        self._client = client if client is not None else httpx.Client(timeout=timeout_s)

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def __enter__(self) -> BinanceVisionClient:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    # ------------------------------------------------------------- 내부

    def _get(self, url: str) -> httpx.Response | None:
        """GET 요청. 404 는 ``None`` (해당 기간 파일이 없음), 그 외 오류는 재시도."""
        last_error: Exception | None = None
        for attempt in range(self.max_retries):
            if self.request_delay_s and attempt == 0:
                time.sleep(self.request_delay_s)
            try:
                response = self._client.get(url)
            except httpx.HTTPError as exc:
                last_error = exc
            else:
                if response.status_code == httpx.codes.NOT_FOUND:
                    return None
                if response.is_success:
                    return response
                last_error = ExchangeError(f"HTTP {response.status_code}: {url}")
            if attempt < self.max_retries - 1:
                time.sleep(min(2.0**attempt, 8.0) * max(self.request_delay_s, 0.1))
        raise ExchangeError(f"다운로드 실패 ({self.max_retries}회 시도): {url}") from last_error

    # ------------------------------------------------------------- 공개

    def download(self, file: RemoteArchive, *, verify_checksum: bool = True) -> bytes | None:
        """zip 원문을 가져온다. 해당 기간 파일이 없으면 ``None``.

        Raises:
            DataIntegrityError: 체크섬 불일치.
        """
        response = self._get(file.url(self.base_url))
        if response is None:
            return None
        payload = response.content

        if verify_checksum:
            checksum_response = self._get(file.checksum_url(self.base_url))
            if checksum_response is None:
                raise DataIntegrityError(
                    f"CHECKSUM 파일이 없다: {file}. 검증 없이 데이터를 받지 않는다."
                )
            expected = _parse_checksum(checksum_response.text)
            actual = hashlib.sha256(payload).hexdigest()
            if actual != expected:
                raise DataIntegrityError(
                    f"체크섬 불일치: {file}\n  기대: {expected[:16]}\n  실제: {actual[:16]}"
                )
        return payload

    def fetch_candles(
        self, file: ArchiveFile, *, verify_checksum: bool = True
    ) -> tuple[Candle, ...] | None:
        """아카이브를 받아 파싱한다. 파일이 없으면 ``None``."""
        payload = self.download(file, verify_checksum=verify_checksum)
        if payload is None:
            return None
        csv_bytes = _extract_single_csv(payload, file.stem)
        return parse_klines_csv(csv_bytes, file.timeframe)

    def fetch_funding(
        self, file: FundingArchiveFile, *, verify_checksum: bool = True
    ) -> tuple[FundingRate, ...] | None:
        """funding 아카이브를 받아 파싱한다. 해당 달 파일이 없으면 ``None``."""
        payload = self.download(file, verify_checksum=verify_checksum)
        if payload is None:
            return None
        return parse_funding_csv(_extract_single_csv(payload, file.stem))

    def fetch_metrics(
        self, file: MetricsArchiveFile, *, verify_checksum: bool = True
    ) -> tuple[PerpMetrics, ...] | None:
        """지표 아카이브를 받아 파싱한다. 해당 날짜 파일이 없으면 ``None``."""
        payload = self.download(file, verify_checksum=verify_checksum)
        if payload is None:
            return None
        return parse_metrics_csv(_extract_single_csv(payload, file.stem))


def utc_today(now_ms: int) -> date:
    """epoch ms 를 UTC 날짜로 변환한다."""
    return datetime.fromtimestamp(now_ms / 1000, tz=UTC).date()
