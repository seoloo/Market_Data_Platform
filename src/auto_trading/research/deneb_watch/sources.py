"""허용된 공식 공개 원문만 수동 수집. 로그인·키·브로커 호출 없음."""

from __future__ import annotations

import calendar
import csv
import hashlib
import io
import re
from dataclasses import dataclass
from datetime import UTC, date, datetime, time
from decimal import Decimal, InvalidOperation
from html.parser import HTMLParser
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx

from auto_trading.core.errors import CausalityError, DataIntegrityError
from auto_trading.research.deneb_watch.catalog import FINRA
from auto_trading.research.deneb_watch.domain import Record

MAX_BYTES = 2_000_000
NY = ZoneInfo("America/New_York")
PARSER_VERSION = "282.2"


@dataclass(frozen=True)
class Source:
    id: str
    url: str
    factors: tuple[str, ...]


SOURCES = (
    Source("finra", FINRA, ("margin_debt", "margin_cash")),
    Source(
        "vix",
        "https://cdn-api.cboe.com/api/global/us_indices/daily_prices/VIX_History.csv",
        ("vix",),
    ),
    Source(
        "vvix",
        "https://cdn-api.cboe.com/api/global/us_indices/daily_prices/VVIX_History.csv",
        ("vvix",),
    ),
    Source(
        "vix9d",
        "https://cdn-api.cboe.com/api/global/us_indices/daily_prices/VIX9D_History.csv",
        ("vol_term",),
    ),
    Source(
        "coreweave",
        "https://investors.coreweave.com/news/news-details/2026/"
        "CoreWeave-Reports-Strong-Second-Quarter-2026-Results/default.aspx",
        ("interest_burden", "ai_loss"),
    ),
    Source("cxmt", "https://www.cxmt.com/en/product.html", ("memory_competition",)),
    Source(
        "fred_rates",
        "https://fred.stlouisfed.org/graph/graph.csv?id=DFII10,SOFR,IORB&cosd=2026-09-01",
        ("real_yield", "repo"),
    ),
    Source("fred_real_html", "https://fred.stlouisfed.org/series/DFII10", ("real_yield",)),
    Source("fred_sofr_html", "https://fred.stlouisfed.org/series/SOFR", ("repo",)),
    Source("fred_iorb_html", "https://fred.stlouisfed.org/series/IORB", ("repo",)),
)


def utc_ms() -> int:
    return int(datetime.now(UTC).timestamp() * 1000)


def end_of_ny_day(day: date) -> int:
    return int(datetime.combine(day, time(23, 59, 59), NY).timestamp() * 1000)


def decimal(text: str) -> Decimal:
    cleaned = text.replace(",", "").replace("$", "").strip()
    if cleaned.startswith("(") and cleaned.endswith(")"):
        cleaned = "-" + cleaned[1:-1].strip()
    try:
        value = Decimal(cleaned)
    except InvalidOperation as exc:
        raise DataIntegrityError("수치 형식 변경") from exc
    if not value.is_finite():
        raise DataIntegrityError("비유한 원문 수치")
    return value


class Tables(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.rows: list[list[str]] = []
        self.row: list[str] = []
        self.cell: list[str] | None = None
        self.text: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "tr":
            self.row = []
        if tag in ("td", "th"):
            self.cell = []

    def handle_data(self, data: str) -> None:
        self.text.append(data)
        if self.cell is not None:
            self.cell.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag in ("td", "th") and self.cell is not None:
            self.row.append(" ".join(" ".join(self.cell).split()))
            self.cell = None
        if tag == "tr" and self.row:
            self.rows.append(self.row)
            self.row = []


def csv_latest(body: bytes, *, as_of_ms: int, close_column: str = "CLOSE") -> tuple[int, Decimal]:
    reader = csv.DictReader(io.StringIO(body.decode("utf-8-sig")))
    if not reader.fieldnames or not {"DATE", close_column}.issubset(reader.fieldnames):
        raise DataIntegrityError("Cboe DATE/CLOSE 헤더 미확보")
    latest: tuple[int, Decimal] | None = None
    for row in reader:
        if not all(isinstance(row.get(key), str) for key in ("DATE", close_column)):
            raise DataIntegrityError("Cboe 행 필드 누락")
        try:
            day = datetime.strptime(row["DATE"], "%m/%d/%Y").replace(tzinfo=NY).date()
        except ValueError as exc:
            raise DataIntegrityError("Cboe 날짜 형식 변경") from exc
        observed_ms = end_of_ny_day(day)
        value = decimal(row[close_column])
        if value <= 0:
            raise DataIntegrityError("변동성 종가 0/음수")
        if observed_ms > as_of_ms:
            raise CausalityError("미래/미마감 날짜의 CSV 행")
        if latest is None or observed_ms > latest[0]:
            latest = observed_ms, value
    if latest is None:
        raise DataIntegrityError("빈 Cboe CSV")
    return latest


def _fact(
    source: Source,
    body: bytes,
    factor: str,
    summary: str,
    observed_ms: int | None,
    value: Decimal | None = None,
    unit: str = "",
    published_ms: int | None = None,
    details: dict[str, str] | None = None,
) -> Record:
    sha = hashlib.sha256(body).hexdigest()
    return Record(
        key=f"{PARSER_VERSION}:{source.id}:{factor}:{sha}",
        kind="fact",
        event_key=f"{source.id}:{observed_ms or 'snapshot'}",
        factor_id=factor,
        summary=summary,
        source_url=source.url,
        source_sha256=sha,
        observed_ms=observed_ms,
        published_ms=published_ms,
        value=value,
        unit=unit,
        details=details or {},
    )


def parse(source: Source, body: bytes, *, captured_ms: int) -> list[Record]:
    if source.id in ("vix", "vvix", "vix9d"):
        observed_ms, value = csv_latest(
            body, as_of_ms=captured_ms, close_column="VVIX" if source.id == "vvix" else "CLOSE"
        )
        if source.id == "vix9d":
            return [
                _fact(
                    source,
                    body,
                    "vol_term",
                    "VIX9D 단독 종가: VIX와 날짜 일치 확인 전 비율 없음",
                    observed_ms,
                    value,
                    "index_points",
                    details={"component": "VIX9D"},
                )
            ]
        return [
            _fact(
                source,
                body,
                source.id,
                "공식 일별 종가. 장중 값·방향 예측 아님",
                observed_ms,
                value,
                "index_points",
            )
        ]
    if source.id == "fred_rates":
        return parse_fred(source, body, captured_ms=captured_ms)
    document = Tables()
    document.feed(body.decode("utf-8-sig"))
    text = " ".join(" ".join(document.text).split())
    if source.id.startswith("fred_") and source.id.endswith("_html"):
        series = source.url.rsplit("/", 1)[-1]
        if series not in text or "Percent" not in text or "Observations" not in text:
            raise DataIntegrityError("FRED HTML 시리즈·단위 확인 실패")
        values = {
            day: decimal(value)
            for day, value in re.findall(r"(\d{4}-\d{2}-\d{2}):\s*([+-]?\d+(?:\.\d+)?)", text)
        }
        if not values:
            raise DataIntegrityError("FRED HTML 현재 관측 미확보")
        records: list[Record] = []
        for day, value in sorted(values.items()):
            observed = end_of_ny_day(date.fromisoformat(day))
            if observed > captured_ms:
                raise CausalityError("미래 FRED HTML 관측")
            details = {"series": series, "vintage": "captured_now_html"}
            if series != "DFII10":
                details["component"] = series
            factor_id = source.factors[0]
            record = _fact(
                source,
                body,
                factor_id,
                f"FRED 공식 페이지 {series} 관측. 현재 수집 판본, 과거 가용성 미인증",
                observed,
                value,
                "percent",
                details=details,
            )
            records.append(
                record.model_copy(
                    update={"key": f"{PARSER_VERSION}:{series}:{day}:{record.source_sha256}"}
                )
            )
        return records
    if source.id == "finra":
        if "millions" not in text.lower():
            raise DataIntegrityError("FINRA million 단위 확인 실패")
        rows: list[tuple[date, list[str]]] = []
        for row in document.rows:
            if len(row) < 4 or not re.fullmatch(r"[A-Z][a-z]{2}-\d{2}", row[0]):
                continue
            day = datetime.strptime(row[0], "%b-%y").replace(tzinfo=NY).date()
            day = day.replace(day=calendar.monthrange(day.year, day.month)[1])
            rows.append((day, row))
        if not rows:
            raise DataIntegrityError("FINRA 월별 표 미확보")
        day, row = max(rows, key=lambda item: item[0])
        debt, cash, margin = (decimal(cell) for cell in row[1:4])
        if min(debt, cash, margin) < 0:
            raise DataIntegrityError("FINRA 음수 잔고")
        return [
            _fact(
                source,
                body,
                "margin_debt",
                "고객 신용계좌 차입잔액. 빚투 비율 아님",
                end_of_ny_day(day),
                debt,
                "USD_million",
            ),
            _fact(
                source,
                body,
                "margin_cash",
                "cash/margin 계좌 free credit 합계. 순레버리지 아님",
                end_of_ny_day(day),
                cash + margin,
                "USD_million",
            ),
        ]
    if source.id == "cxmt":
        if "DDR5" not in " ".join(document.text):
            raise DataIntegrityError("CXMT DDR5 제품 표기 미확인")
        return [
            _fact(
                source,
                body,
                "memory_competition",
                "공식 제품 목록에 DDR5 표기 확인",
                None,
                details={
                    "not_established": "생산량·수율·가격·이익 영향은 미확인",
                    "counterpoint": "메모리 구매자에게는 비용 감소 가능: 가설",
                },
            )
        ]
    if source.id == "coreweave":
        if not all(
            token in text
            for token in ("August 11, 2026", "Three Months Ended June 30", "In millions")
        ):
            raise DataIntegrityError("CoreWeave 발표일·분기·단위 확인 실패")
        metrics: dict[str, Decimal] = {}
        for row in document.rows:
            if not row:
                continue
            label = row[0].strip().lower()
            if label not in ("revenue", "interest expense, net", "net loss") or label in metrics:
                continue
            numbers = re.findall(
                r"\(\s*[\d,]+(?:\.\d+)?\s*\)|-?\d[\d,]*(?:\.\d+)?", " ".join(row[1:])
            )
            if numbers:
                metrics[label] = decimal(numbers[0])
        if set(metrics) != {"revenue", "interest expense, net", "net loss"}:
            raise DataIntegrityError("CoreWeave 분기 수치 표 미확보")
        if metrics["revenue"] <= 0:
            raise DataIntegrityError("매출 분모 0/음수")
        observed = end_of_ny_day(date(2026, 6, 30))
        published = end_of_ny_day(date(2026, 8, 11))
        details = {
            "period": "2026Q2",
            "revenue_usd_million": str(metrics["revenue"]),
            "net_interest_usd_million": str(metrics["interest expense, net"]),
            "counterpoint": "매출·계약 증가와 금융 부담을 함께 확인. 부도/전염 미입증",
        }
        return [
            _fact(
                source,
                body,
                "interest_burden",
                "순이자비용/매출. 이자보상배율·부도 확률 아님",
                observed,
                abs(metrics["interest expense, net"]) / metrics["revenue"],
                "ratio",
                published,
                details,
            ),
            _fact(
                source,
                body,
                "ai_loss",
                "GAAP 분기 순손익. 시장 하락의 원인 확정 아님",
                observed,
                metrics["net loss"],
                "USD_million",
                published,
                details,
            ),
        ]
    raise DataIntegrityError("미등록 파서")


def parse_fred(source: Source, body: bytes, *, captured_ms: int) -> list[Record]:
    reader = csv.DictReader(io.StringIO(body.decode("utf-8-sig")))
    if not reader.fieldnames or not {"observation_date", "DFII10", "SOFR", "IORB"}.issubset(
        reader.fieldnames
    ):
        raise DataIntegrityError("FRED 시리즈·날짜 헤더 확인 실패")
    real: tuple[int, Decimal] | None = None
    funding: tuple[int, Decimal] | None = None
    for row in reader:
        if not all(
            isinstance(row.get(key), str) for key in ("observation_date", "DFII10", "SOFR", "IORB")
        ):
            raise DataIntegrityError("FRED 행 필드 누락")
        observed = end_of_ny_day(date.fromisoformat(row["observation_date"]))
        if observed > captured_ms:
            raise CausalityError("미래 FRED 관측")
        if row["DFII10"] not in (".", "") and (real is None or observed > real[0]):
            real = observed, decimal(row["DFII10"])
        if all(row[key] not in (".", "") for key in ("SOFR", "IORB")) and (
            funding is None or observed > funding[0]
        ):
            funding = observed, decimal(row["SOFR"]) - decimal(row["IORB"])
    if real is None or funding is None:
        raise DataIntegrityError("FRED 실질금리/동일 날짜 조달금리 미확보")
    return [
        _fact(
            source,
            body,
            "real_yield",
            "DFII10 실질 10년 금리 수준. 충격/매도 신호 아님",
            real[0],
            real[1],
            "percent",
            details={"series": "DFII10", "vintage": "captured_now"},
        ),
        _fact(
            source,
            body,
            "repo",
            "같은 관측일 SOFR−IORB. 부호만으로 자금 경색을 단정하지 않음",
            funding[0],
            funding[1],
            "percentage_points",
            details={"series": "SOFR-IORB", "vintage": "captured_now"},
        ),
    ]


def fetch(source: Source, client: httpx.Client) -> bytes:
    if source not in SOURCES:
        raise DataIntegrityError("허용 목록 밖의 수집 주소")
    with client.stream("GET", source.url, follow_redirects=False) as response:
        response.raise_for_status()
        chunks: list[bytes] = []
        size = 0
        for part in response.iter_bytes():
            size += len(part)
            if size > MAX_BYTES:
                raise DataIntegrityError("원문 크기 한도 초과")
            chunks.append(part)
    body = b"".join(chunks)
    if not body:
        raise DataIntegrityError("빈 원문")
    return body


def preserve_raw(root: Path, body: bytes) -> str:
    sha = hashlib.sha256(body).hexdigest()
    folder = root / "sources"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{sha}.bin"
    if path.exists():
        if hashlib.sha256(path.read_bytes()).hexdigest() != sha:
            raise DataIntegrityError("보존 원문 해시 불일치")
    else:
        with path.open("xb") as stream:
            stream.write(body)
    return sha
