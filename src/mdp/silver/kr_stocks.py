"""국내 주식 Silver — 한국투자증권 Bronze 응답을 타입 있는 표로 펴고, 장 규칙을 표시하고, 감사한다.

언제 쓰나: 국내 주식 Bronze 를 받은 뒤, Spark 이미지 안에서::

    docker compose --profile spark run --rm spark python -m mdp.silver.kr_stocks

읽고 쓰는 것:
- 읽기: Bronze ``kis/domestic/`` 의 페이지 파일 — 쓰기 전에 페이지마다 완료 표시의 SHA-256 과 맞춘다
- 쓰기 (``<lake>/silver/``, Delta, 표 전체를 다시 씀):
  ``kr_trading_calendar`` (날마다 개장 여부) · ``kr_daily_bars`` (일봉, 원주가 · 수정주가) ·
  ``kr_minute_bars`` (분봉 + 장 구간) · ``kr_minute_quarantine`` (걸러 낸 분봉) ·
  ``kr_minute_gaps`` (정규장 안 빈 구간) · ``kr_minute_audit_daily`` (종목 · 날마다 감사),
  실행 기록(작업 ``silver.kr_stocks``, 표마다 Delta 버전 · 줄 수)

장 구간 (분봉 ``session``) — 시각을 못 박지 않고 자료로 정한다(2026-10-26 받은 자료로 확인):

- ``closing_auction``: 오후에 10 분 넘게 빈 뒤 15:30 또는 16:30 에 오는 첫 봉.
  마감 동시호가(10 분) 동안은 봉이 없고 그 체결이 이 한 봉에 몰린다.
  늦게 여는 날(예: 2025-11-13)은 16:30 에 닫았다.
- ``regular``: 그 봉 앞. ``after_hours``: 그 봉 뒤(2026-09-14 부터 16:00 ~ 19:59 봉이 보인다).
- 마감 봉이 없는 날은 15:30 까지를 ``regular``, 그 뒤를 ``after_hours`` 로 둔다.

[이유] 국내 주식 표는 작다(분봉 약 30 만 줄) — 날마다 Bronze 전체로 통째로 다시 만들어도 몇십
       초다. '바꿔 끼울 칸' 을 고르는 실수가 없고, Delta 버전으로 이력은 그대로 남는다. 마감
       시각을 15:30 으로 못 박으면 늦게 여는 날(수능일로 보임)의 16:30 마감 봉을 장 후 봉으로
       잘못 읽는다.
"""

from __future__ import annotations

import argparse
import json
import os
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

from mdp.bronze.fetch import _file_sha256, default_bronze_root, log_json, setup_cli_logging
from mdp.errors import BronzeConflictError
from mdp.runlog.recorder import Artifact, NullRecorder, PostgresRecorder, RunRecorder, record_run
from mdp.spark.session import spark_session, table_path

__all__ = [
    "CLOSING_TIMES",
    "SilverTables",
    "audit_days",
    "build_silver",
    "find_gaps",
    "label_sessions",
    "main",
    "parse_calendar",
    "parse_daily",
    "parse_minutes",
    "verified_pages",
]

KIS_PREFIX: Final = "kis/domestic"
CLOSING_TIMES: Final = (15 * 60 + 30, 16 * 60 + 30)
"""마감 동시호가 봉이 올 수 있는 시각(하루 안 분) — 보통 15:30, 늦게 여는 날 16:30."""
CLOSING_CALL_MINUTES: Final = 10
"""마감 동시호가 시간 — 그 동안 봉이 없다."""
OPEN_MINUTE: Final = 9 * 60
"""정규장 시작 09:00 — 그보다 늦은 첫 봉이면 '늦게 열림' 빈 구간으로 남긴다."""
MARKET_WIDE_MIN_MINUTES: Final = 10
MARKET_WIDE_OVERLAP: Final = 0.8
"""모든 종목이 같은 때 10 분 넘게 비고 서로 80 % 넘게 겹치면 시장 전체 빈 구간으로 본다
(같은 정지에서도 종목마다 다시 열린 분이 1 분쯤 다르다 — 2026-03-09 KODEX 200 은 1 분 먼저)."""

MINUTE_SCHEMA: Final = (
    "rt_cd STRING, output2 ARRAY<STRUCT<stck_bsop_date: STRING, stck_cntg_hour: STRING,"
    " stck_prpr: STRING, stck_oprc: STRING, stck_hgpr: STRING, stck_lwpr: STRING,"
    " cntg_vol: STRING, acml_tr_pbmn: STRING>>"
)
DAILY_SCHEMA: Final = (
    "rt_cd STRING, output2 ARRAY<STRUCT<stck_bsop_date: STRING, stck_oprc: STRING,"
    " stck_hgpr: STRING, stck_lwpr: STRING, stck_clpr: STRING, acml_vol: STRING,"
    " acml_tr_pbmn: STRING, flng_cls_code: STRING, prtt_rate: STRING, mod_yn: STRING,"
    " revl_issu_reas: STRING>>"
)
CALENDAR_SCHEMA: Final = (
    "rt_cd STRING, output ARRAY<STRUCT<bass_dt: STRING, wday_dvsn_cd: STRING,"
    " bzdy_yn: STRING, tr_day_yn: STRING, opnd_yn: STRING, sttl_day_yn: STRING>>"
)
PRICE: Final = "decimal(18,2)"
AMOUNT: Final = "decimal(28,0)"


@dataclass(frozen=True, slots=True)
class SilverTables:
    """[쓰임] 만든 표들 (아직 쓰지 않은 DataFrame)."""

    calendar: Any
    daily: Any
    minutes: Any
    quarantine: Any
    gaps: Any
    audit: Any

    def items(self) -> list[tuple[str, Any]]:
        """[쓰임] (표 이름, DataFrame) — 쓰는 순서."""
        return [
            ("kr_trading_calendar", self.calendar),
            ("kr_daily_bars", self.daily),
            ("kr_minute_bars", self.minutes),
            ("kr_minute_quarantine", self.quarantine),
            ("kr_minute_gaps", self.gaps),
            ("kr_minute_audit_daily", self.audit),
        ]


def verified_pages(bronze_root: Path, part: str) -> list[str]:
    """[쓰임] Bronze ``kis/domestic/<part>/`` 의 완료된 단위의 페이지 경로.
    하나라도 완료 표시와 다르면 멈춘다.
    [이유] Silver 는 검증한 Bronze 만 읽는다(바이낸스 Silver 와 같은 약속). 완료 표시가 없는
           폴더(받다 만 것)는 이름이 ``.part`` 라 처음부터 보이지 않는다."""
    paths: list[str] = []
    for marker_path in sorted((bronze_root / KIS_PREFIX / part).rglob("_SUCCESS.json")):
        marker = json.loads(marker_path.read_text(encoding="utf-8"))
        for page in marker["pages"]:
            path = marker_path.parent / page["name"]
            if _file_sha256(path) != (page["sha256"], page["size_bytes"]):
                raise BronzeConflictError(f"Bronze 페이지가 완료 표시와 다르다: {path}")
            paths.append(str(path))
    return paths


def _read_pages(spark: Any, paths: Sequence[str], schema: str) -> Any:
    """[쓰임] 페이지 파일들(응답 본문 하나 = JSON 하나, gzip)을 읽는다
    — 파일 경로 칸 ``file_path`` 와 함께."""
    from pyspark.sql import functions as F

    return (
        spark.read.schema(schema)
        .option("multiLine", True)
        .json(list(paths))
        .withColumn("file_path", F.col("_metadata.file_path"))
    )


def _local_minute(time_col: Any) -> Any:
    """[쓰임] ``HHMMSS`` 문자열 → 하루 안 분(09:00 = 540)."""
    from pyspark.sql import functions as F

    return F.substring(time_col, 1, 2).cast("int") * 60 + F.substring(time_col, 3, 2).cast("int")


def parse_minutes(pages: Any, run_id: str) -> tuple[Any, Any]:
    """[쓰임] 분봉 페이지 → (깨끗한 분봉, 격리).
    같은 분이 두 번이면 값이 같으면 하나만 남기고, 다르면 모두 격리한다.
    [이유] 가격이 0 이하거나 고가 < 저가 · 고가가 시가 · 종가보다 낮은 봉은 원천 오류라 봉을 만들지
           않고 이유와 함께 격리 표로 옮긴다(지우지 않는다)."""
    from pyspark.sql import Window
    from pyspark.sql import functions as F

    unit = r"symbol=([0-9A-Z]+)/date=(\d{4}-\d{2}-\d{2})/page-(\d+)"
    rows = (
        pages.select("file_path", F.posexplode("output2").alias("row_in_page", "r"))
        .select(
            F.regexp_extract("file_path", unit, 1).alias("symbol"),
            F.to_date(F.regexp_extract("file_path", unit, 2)).alias("unit_date"),
            F.regexp_extract("file_path", unit, 3).cast("int").alias("page"),
            "row_in_page",
            F.to_date(F.col("r.stck_bsop_date"), "yyyyMMdd").alias("trade_date"),
            F.col("r.stck_cntg_hour").alias("bar_time"),
            F.col("r.stck_oprc").cast(PRICE).alias("open"),
            F.col("r.stck_hgpr").cast(PRICE).alias("high"),
            F.col("r.stck_lwpr").cast(PRICE).alias("low"),
            F.col("r.stck_prpr").cast(PRICE).alias("close"),
            F.col("r.cntg_vol").cast("long").alias("volume"),
            F.col("r.acml_tr_pbmn").cast(AMOUNT).alias("cum_value"),
        )
        .withColumn("minute_of_day", _local_minute(F.col("bar_time")))
    )
    key = ["symbol", "trade_date", "bar_time"]
    values = ["open", "high", "low", "close", "volume", "cum_value"]
    by_key = Window.partitionBy(*key)
    rows = rows.withColumn("copies", F.count(F.lit(1)).over(by_key)).withColumn(
        "versions", F.size(F.collect_set(F.struct(*values)).over(by_key))
    )
    reason = (
        F.when(F.col("trade_date") != F.col("unit_date"), "date_differs_from_unit")
        .when(F.col("versions") > 1, "duplicate_with_different_values")
        .when(
            F.col("open").isNull()
            | F.col("high").isNull()
            | F.col("low").isNull()
            | F.col("close").isNull()
            | F.col("volume").isNull(),
            "unparsable",
        )
        .when(F.least("open", "high", "low", "close") <= 0, "non_positive_price")
        .when(F.col("volume") < 0, "negative_volume")
        .when(
            (F.col("high") < F.greatest("open", "close"))
            | (F.col("low") > F.least("open", "close")),
            "ohlc_inconsistent",
        )
    )
    rows = rows.withColumn("reason", reason)
    quarantine = rows.where(F.col("reason").isNotNull()).withColumn("run_id", F.lit(run_id))
    first_copy = Window.partitionBy(*key).orderBy("page", "row_in_page")
    clean = (
        rows.where(F.col("reason").isNull())
        .withColumn("copy_rank", F.row_number().over(first_copy))
        .where(F.col("copy_rank") == 1)
        .withColumn("duplicate_copies", F.col("copies") - 1)
        .drop("copy_rank", "copies", "versions", "reason", "unit_date", "row_in_page")
        .withColumn(
            "bar_start",
            F.to_utc_timestamp(
                F.to_timestamp(
                    F.concat(F.date_format("trade_date", "yyyy-MM-dd"), F.lit(" "), "bar_time"),
                    "yyyy-MM-dd HHmmss",
                ),
                "Asia/Seoul",
            ),
        )
        .withColumn("run_id", F.lit(run_id))
    )
    return clean, quarantine.drop("copies", "versions", "unit_date")


def label_sessions(minutes: Any) -> Any:
    """[쓰임] 분봉마다 장 구간(``regular`` · ``closing_auction`` · ``after_hours``)과 앞 봉과의
    간격을 붙인다.
    [이유] 마감 봉 = 오후에 마감 동시호가(10 분)만큼 빈 뒤 15:30 · 16:30 에 오는 첫 봉.
           시각만으로 정하면 16:30 에 닫은 날을 틀린다."""
    from pyspark.sql import Window
    from pyspark.sql import functions as F

    day = Window.partitionBy("symbol", "trade_date")
    ordered = day.orderBy("minute_of_day")
    gap_before = F.col("minute_of_day") - F.lag("minute_of_day").over(ordered)
    labelled = minutes.withColumn("gap_before", gap_before)
    is_closing = F.col("minute_of_day").isin(*CLOSING_TIMES) & (
        F.col("gap_before") >= CLOSING_CALL_MINUTES
    )
    labelled = labelled.withColumn(
        "closing_minute", F.min(F.when(is_closing, F.col("minute_of_day"))).over(day)
    )
    session = (
        F.when(F.col("closing_minute").isNull(), None)
        .when(F.col("minute_of_day") < F.col("closing_minute"), "regular")
        .when(F.col("minute_of_day") == F.col("closing_minute"), "closing_auction")
        .otherwise("after_hours")
    )
    fallback = F.when(F.col("minute_of_day") <= CLOSING_TIMES[0], "regular").otherwise(
        "after_hours"
    )
    return labelled.withColumn("session", F.coalesce(session, fallback)).drop("closing_minute")


def find_gaps(labelled: Any, run_id: str) -> Any:
    """[쓰임] 정규장 안의 빈 구간 — 첫 봉이 09:00 보다 늦으면 ``opening``, 봉 사이가 비면
    ``intraday``. 모든 종목이 같은 때(10 분 넘게, 80 % 넘게 겹침) 비면 ``market_wide``.
    [이유] 한 종목만 빈 분은 '그 분에 체결이 없었다' 이고, 모든 종목이 함께 30 분 빈 것은 시장
           전체 정지로 보인다 — 둘을 섞으면 대사에서 원인을 못 가른다. 원인(서킷브레이커 등)은
           확인 필요라 이름은 '시장 전체' 까지만 붙인다."""
    from pyspark.sql import Window
    from pyspark.sql import functions as F

    regular = labelled.where(F.col("session") == "regular")
    ordered = Window.partitionBy("symbol", "trade_date").orderBy("minute_of_day")
    pairs = regular.withColumn("prev_minute", F.lag("minute_of_day").over(ordered))
    intraday = pairs.where(F.col("minute_of_day") - F.col("prev_minute") > 1).select(
        "symbol",
        "trade_date",
        F.lit("intraday").alias("kind"),
        (F.col("prev_minute") + 1).alias("start_minute"),
        (F.col("minute_of_day") - 1).alias("end_minute"),
    )
    first = regular.groupBy("symbol", "trade_date").agg(F.min("minute_of_day").alias("first"))
    opening = first.where(F.col("first") > OPEN_MINUTE).select(
        "symbol",
        "trade_date",
        F.lit("opening").alias("kind"),
        F.lit(OPEN_MINUTE).alias("start_minute"),
        (F.col("first") - 1).alias("end_minute"),
    )
    gaps = intraday.unionByName(opening).withColumn(
        "minutes", F.col("end_minute") - F.col("start_minute") + 1
    )
    symbols_that_day = labelled.groupBy("trade_date").agg(
        F.countDistinct("symbol").alias("symbols_that_day")
    )
    a, b = gaps.alias("a"), gaps.alias("b")
    overlap = (
        F.least("a.end_minute", "b.end_minute") - F.greatest("a.start_minute", "b.start_minute") + 1
    )
    covered = (
        a.join(b, F.col("a.trade_date") == F.col("b.trade_date"))
        .where(overlap >= F.col("a.minutes") * MARKET_WIDE_OVERLAP)
        .groupBy("a.symbol", "a.trade_date", "a.kind", "a.start_minute", "a.end_minute")
        .agg(F.countDistinct("b.symbol").alias("symbols_with_gap"))
    )
    key = ["symbol", "trade_date", "kind", "start_minute", "end_minute"]
    return (
        gaps.join(covered, key, "left")
        .join(symbols_that_day, "trade_date", "left")
        .withColumn(
            "market_wide",
            (F.col("minutes") >= MARKET_WIDE_MIN_MINUTES)
            & (F.col("symbols_that_day") >= 2)
            & (F.coalesce("symbols_with_gap", F.lit(0)) == F.col("symbols_that_day")),
        )
        .withColumn("start_time", _hhmm(F.col("start_minute")))
        .withColumn("end_time", _hhmm(F.col("end_minute")))
        .drop("symbols_with_gap")
        .withColumn("run_id", F.lit(run_id))
    )


def _hhmm(minute_col: Any) -> Any:
    """[쓰임] 하루 안 분 → ``HH:MM``."""
    from pyspark.sql import functions as F

    return F.format_string("%02d:%02d", (minute_col / 60).cast("int"), minute_col % 60)


def audit_days(labelled: Any, gaps: Any, calendar: Any, run_id: str) -> Any:
    """[쓰임] 종목 · 날마다 감사 한 줄 — 봉 수(구간별) · 첫 봉 · 마감 봉 · 빈 구간 · 달력 개장 여부
    · 판정.
    [이유] '그 날 자료가 온전한가' 를 한 표로 본다. 판정은 ``ok`` · ``no_closing_bar``(마감
           동시호가 봉 없음) · ``not_open_in_calendar``(달력은 쉬는 날인데 분봉이 있음).
           달력이 아직 없는 달은 비워 둔다."""
    from pyspark.sql import functions as F

    def count_session(name: str) -> Any:
        return F.sum(F.when(F.col("session") == name, 1).otherwise(0))

    days = labelled.groupBy("symbol", "trade_date").agg(
        F.count(F.lit(1)).alias("bars"),
        count_session("regular").alias("regular_bars"),
        count_session("closing_auction").alias("closing_bars"),
        count_session("after_hours").alias("after_hours_bars"),
        F.min("bar_time").alias("first_bar_time"),
        F.max(F.when(F.col("session") == "closing_auction", F.col("bar_time"))).alias(
            "closing_bar_time"
        ),
        F.sum("duplicate_copies").alias("duplicate_copies"),
    )
    gap_summary = gaps.groupBy("symbol", "trade_date").agg(
        F.count(F.lit(1)).alias("gap_runs"),
        F.sum("minutes").alias("gap_minutes"),
        F.sum(F.when(F.col("market_wide"), F.col("minutes")).otherwise(0)).alias(
            "market_wide_gap_minutes"
        ),
    )
    open_days = calendar.select("trade_date", F.col("is_open").alias("calendar_is_open"))
    status = (
        F.when(F.col("calendar_is_open") == F.lit(False), "not_open_in_calendar")
        .when(F.col("closing_bars") == 0, "no_closing_bar")
        .otherwise("ok")
    )
    return (
        days.join(gap_summary, ["symbol", "trade_date"], "left")
        .join(open_days, "trade_date", "left")
        .fillna(0, ["gap_runs", "gap_minutes", "market_wide_gap_minutes"])
        .withColumn("status", status)
        .withColumn("run_id", F.lit(run_id))
    )


def parse_daily(pages: Any, run_id: str) -> Any:
    """[쓰임] 일봉 페이지 → 한 줄 = 종목 · 날 · 가격 기준(``raw`` · ``adjusted``).
    [이유] 원천 칸(``flng_cls_code`` · ``prtt_rate`` · ``mod_yn`` · ``revl_issu_reas``)은 뜻을
           확인하기 전이라 이름만 풀어 그대로 둔다. 거래량 0 인 날은 매매 정지로 보이는 날이라
           ``volume_zero`` 로 표시한다(삼성전자 2018-05-02 · 03 — 분할 전 정지)."""
    from pyspark.sql import functions as F

    unit = r"price=(raw|adjusted)/symbol=([0-9A-Z]+)/month="
    return pages.select("file_path", F.explode("output2").alias("r")).select(
        F.regexp_extract("file_path", unit, 2).alias("symbol"),
        F.to_date(F.col("r.stck_bsop_date"), "yyyyMMdd").alias("trade_date"),
        F.regexp_extract("file_path", unit, 1).alias("price_basis"),
        F.col("r.stck_oprc").cast(PRICE).alias("open"),
        F.col("r.stck_hgpr").cast(PRICE).alias("high"),
        F.col("r.stck_lwpr").cast(PRICE).alias("low"),
        F.col("r.stck_clpr").cast(PRICE).alias("close"),
        F.col("r.acml_vol").cast("long").alias("volume"),
        F.col("r.acml_tr_pbmn").cast(AMOUNT).alias("value"),
        (F.col("r.acml_vol").cast("long") == 0).alias("volume_zero"),
        F.col("r.flng_cls_code").alias("lock_code"),
        F.col("r.prtt_rate").cast("decimal(18,4)").alias("split_rate"),
        F.col("r.mod_yn").alias("modified_flag"),
        F.col("r.revl_issu_reas").alias("revaluation_reason"),
        F.lit(run_id).alias("run_id"),
    )


def parse_calendar(pages: Any, run_id: str) -> Any:
    """[쓰임] 휴장일 페이지 → 날마다 한 줄. 한 달 단위가 그 달 밖의 날도 담고 있어 그 달 날만 쓴다.
    [이유] 이어 받은 페이지가 다음 달 앞날까지 담는다(24 일씩). 두 달 단위가 같은 날을 다르게 말하면
           안 되므로, 날짜의 달 = 단위의 달인 줄만 남긴다 — 한 날은 한 단위에서만 온다."""
    from pyspark.sql import functions as F

    unit = r"holiday/month=(\d{4}-\d{2})/"
    rows = pages.select("file_path", F.explode("output").alias("r")).select(
        F.regexp_extract("file_path", unit, 1).alias("unit_month"),
        F.to_date(F.col("r.bass_dt"), "yyyyMMdd").alias("trade_date"),
        (F.col("r.opnd_yn") == "Y").alias("is_open"),
        (F.col("r.bzdy_yn") == "Y").alias("is_business_day"),
        (F.col("r.tr_day_yn") == "Y").alias("is_trading_day"),
        (F.col("r.sttl_day_yn") == "Y").alias("is_settlement_day"),
        F.col("r.wday_dvsn_cd").alias("weekday_code"),
    )
    return (
        rows.where(F.date_format("trade_date", "yyyy-MM") == F.col("unit_month"))
        .dropDuplicates(["trade_date"])
        .drop("unit_month")
        .withColumn("run_id", F.lit(run_id))
    )


def build_silver(spark: Any, bronze_root: Path, run_id: str) -> SilverTables:
    """[쓰임] Bronze 전체 → Silver 표 여섯 개(아직 쓰지 않음)."""
    calendar = parse_calendar(
        _read_pages(spark, verified_pages(bronze_root, "holiday"), CALENDAR_SCHEMA), run_id
    ).cache()
    daily = parse_daily(
        _read_pages(spark, verified_pages(bronze_root, "daily-bars"), DAILY_SCHEMA), run_id
    )
    clean, quarantine = parse_minutes(
        _read_pages(spark, verified_pages(bronze_root, "minute-bars"), MINUTE_SCHEMA), run_id
    )
    labelled = label_sessions(clean).cache()
    gaps = find_gaps(labelled, run_id).cache()
    audit = audit_days(labelled, gaps, calendar, run_id)
    return SilverTables(calendar, daily, labelled, quarantine, gaps, audit)


def write_table(df: Any, layer: str, name: str) -> tuple[int, int]:
    """[쓰임] Delta 표를 통째로 다시 쓰고 (새 버전, 줄 수)를 돌려준다.
    칸이 바뀌어도 쓴다(스키마 덮어쓰기).
    [이유] 작은 표라 통째로 — 이전 버전은 Delta 시간여행으로 그대로 볼 수 있다."""
    from delta.tables import DeltaTable

    path = table_path(layer, name)
    df.write.format("delta").mode("overwrite").option("overwriteSchema", "true").save(path)
    table = DeltaTable.forPath(df.sparkSession, path)
    version = int(table.history(1).collect()[0]["version"])
    rows = int(df.sparkSession.read.format("delta").load(path).count())
    return version, rows


def run_silver(bronze_root: Path, recorder: RunRecorder) -> dict[str, tuple[int, int]]:
    """[쓰임] Bronze 전체로 Silver 를 다시 만들고 표마다 실행 기록에 남긴다."""
    written: dict[str, tuple[int, int]] = {}
    with record_run(recorder, "silver.kr_stocks", {"bronze_root": str(bronze_root)}) as run:
        spark = spark_session("silver.kr_stocks")
        spark.sparkContext.setLogLevel("ERROR")
        tables = build_silver(spark, bronze_root, str(run.run_id))
        for name, df in tables.items():
            version, rows = write_table(df, "silver", name)
            written[name] = (version, rows)
            run.add(
                Artifact(
                    f"silver/{name}", "silver", "rebuilt", row_count=rows, table_version=version
                )
            )
            log_json(event="table", table=f"silver/{name}", version=version, rows=rows)
    return written


def main(argv: Sequence[str] | None = None) -> int:
    """[쓰임] 명령줄 실행 (Spark 이미지 안에서)."""
    setup_cli_logging()
    parser = argparse.ArgumentParser(prog="python -m mdp.silver.kr_stocks")
    parser.add_argument("--root", type=Path, help="Bronze 위치 (기본: MDP_BRONZE_ROOT)")
    parser.add_argument("--pg-dsn", default=os.environ.get("MDP_PG_DSN"))
    args = parser.parse_args(argv)
    bronze_root: Path = args.root or default_bronze_root()
    if args.pg_dsn:
        with PostgresRecorder(args.pg_dsn) as recorder:
            run_silver(bronze_root, recorder)
    else:
        log_json(event="runlog_off", reason="MDP_PG_DSN 없음 — 실행 기록 없이")
        run_silver(bronze_root, NullRecorder())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
