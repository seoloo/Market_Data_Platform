"""바이낸스 공개 아카이브의 '파일 하나' 를 가리키는 이름표.

언제 쓰나: Bronze 받기(:mod:`mdp.bronze.fetch`)가 받을 주소와 저장할 경로를 정할 때.
읽고 쓰는 것: 없음 (주소 · 경로 문자열만 만든다).

경로 규칙 (원천 그대로):
``data/<market>/<granularity>/<dataset>/<symbol>[/<interval>]/<파일 이름>.zip``,
파일 이름은 ``<symbol>-<dataset 또는 interval>-<기간>``.

- 체결: ``data/futures/um/monthly/aggTrades/BTCUSDT/BTCUSDT-aggTrades-2024-03.zip``
- 1 분봉: ``data/futures/um/monthly/klines/BTCUSDT/1m/BTCUSDT-1m-2024-03.zip``
- funding: ``data/futures/um/monthly/fundingRate/BTCUSDT/BTCUSDT-fundingRate-2024-03.zip``

[이유] Bronze 경로를 원천 주소와 똑같이 두면 '이 파일이 어디서 왔나' 를 따로 적지 않아도 되고, 파일
       하나만 보고도 다시 받을 주소를 알 수 있다.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from enum import StrEnum
from typing import Final

__all__ = ["BASE_URL", "ArchiveFile", "Dataset", "Granularity", "months_between"]

BASE_URL: Final = "https://data.binance.vision"


class Granularity(StrEnum):
    """[쓰임] 아카이브 파일 단위 — 한 달치(``monthly``) 또는 하루치(``daily``)."""

    MONTHLY = "monthly"
    DAILY = "daily"


class Dataset(StrEnum):
    """[쓰임] 받을 자료 종류. 값은 원천 경로에 쓰이는 이름 그대로다."""

    AGG_TRADES = "aggTrades"
    KLINES = "klines"
    FUNDING_RATE = "fundingRate"


@dataclass(frozen=True, slots=True)
class ArchiveFile:
    """[쓰임] 원천 파일 하나 — 종류 · 종목 · 기간(월이면 1 일) · 단위 · 봉 간격(봉일 때만).
    [이유] 주소 · 파일 이름 · Bronze 경로를 한 곳에서 만들어, 받기와 체크섬 목록이 같은 이름을
           쓰게."""

    dataset: Dataset
    symbol: str
    period: date
    granularity: Granularity = Granularity.MONTHLY
    interval: str | None = None
    """봉 간격(``1m`` 등). 봉(klines)일 때만 필요하고 다른 종류에서는 비워 둔다."""
    market: str = "futures/um"
    """원천 시장 경로. USD-M 선물이 기본."""

    def __post_init__(self) -> None:
        """[쓰임] 봉이면 간격이 꼭 있어야 하고, 봉이 아니면 없어야 한다.
        [이유] 간격이 빠지면 주소가 틀려 404 가 나는데, 그러면 '그 달 파일이 없다' 로 잘못
               읽힌다."""
        if (self.dataset is Dataset.KLINES) != (self.interval is not None):
            raise ValueError(
                f"봉(klines)일 때만 interval 이 필요하다: {self.dataset} {self.interval}"
            )

    @property
    def stem(self) -> str:
        """[쓰임] 확장자 없는 파일 이름 — 예 ``BTCUSDT-aggTrades-2024-03`` ·
        ``BTCUSDT-1m-2024-03-01``."""
        kind = self.interval if self.dataset is Dataset.KLINES else self.dataset.value
        period_text = (
            f"{self.period:%Y-%m}"
            if self.granularity is Granularity.MONTHLY
            else f"{self.period:%Y-%m-%d}"
        )
        return f"{self.symbol}-{kind}-{period_text}"

    @property
    def filename(self) -> str:
        """[쓰임] zip 파일 이름 (``<stem>.zip``)."""
        return f"{self.stem}.zip"

    @property
    def relative_path(self) -> str:
        """[쓰임] 원천 주소의 경로 부분 = Bronze 안 상대 경로 (``/`` 구분).
        [이유] 체크섬 목록에 운영체제와 상관없이 같은 문자열이 남도록 ``/`` 로 고정한다."""
        parts = ["data", self.market, self.granularity.value, self.dataset.value, self.symbol]
        if self.interval is not None:
            parts.append(self.interval)
        parts.append(self.filename)
        return "/".join(parts)

    def url(self, base_url: str = BASE_URL) -> str:
        """[쓰임] zip 을 받을 전체 주소."""
        return f"{base_url.rstrip('/')}/{self.relative_path}"

    def checksum_url(self, base_url: str = BASE_URL) -> str:
        """[쓰임] 같은 zip 의 SHA-256 이 적힌 ``.CHECKSUM`` 주소 (원천이 함께 올려 둔다)."""
        return f"{self.url(base_url)}.CHECKSUM"


def months_between(first_month: date, last_month: date) -> list[date]:
    """[쓰임] 두 달 사이(양끝 포함)의 매달 1 일 목록 — 월 파일을 여러 개 받을 때.
    [이유] 날짜가 15 일이어도 그 달 1 일로 맞춰, 같은 달을 두 번 받지 않게."""
    if last_month < first_month:
        raise ValueError(f"끝 달이 첫 달보다 앞이다: {first_month} > {last_month}")
    months: list[date] = []
    year, month = first_month.year, first_month.month
    while (year, month) <= (last_month.year, last_month.month):
        months.append(date(year, month, 1))
        year, month = (year + 1, 1) if month == 12 else (year, month + 1)
    return months
