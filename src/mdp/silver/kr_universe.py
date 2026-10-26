"""국내 주식 유니버스 Silver — 상장폐지 종목까지 담은 상장 이력 · 가격 · 종목별 감사(K-3b).

언제 쓰나: 받아 둔 자료를 들여온 뒤(:mod:`mdp.bronze.imported`), Spark 이미지 안에서::

    docker compose --profile spark run --rm spark python -m mdp.silver.kr_universe

읽고 쓰는 것:
- 읽기: Bronze ``imported/auto_trading/research/kr_stocks/`` 의
  ``info.json`` (한국투자증권 종목 정보 + KIND 상장 여부),
  ``kind_delisted.html`` (KIND 상장폐지 현황, EUC-KR),
  ``prices/*.csv`` (수정주가 종가 · 거래량 · 거래대금).
  읽기 전에 파일마다 완료 표시의 SHA-256 과 맞춘다.
- 쓰기 (``<lake>/silver/``, Delta, 표 전체를 다시 씀):
  ``kr_listings`` (종목마다 상장일 · 폐지일 · 폐지 사유 · 두 출처 폐지일 일치),
  ``kr_universe_prices`` (종목 · 날 가격), ``kr_universe_quarantine`` (걸러 낸 가격 줄),
  ``kr_universe_audit`` (종목마다 가격 기간과 상장 기간 대조),
  실행 기록(작업 ``silver.kr_universe``)

[이유] 지금 상장된 종목만 모으면 망해서 사라진 종목이 빠져 과거 성과가 좋아 보인다(생존 편향).
       그래서 폐지 종목을 같은 표에 두고, '그 날 상장돼 있던 종목' 을 상장일 · 폐지일로 언제든
       다시 고를 수 있게 한다. 폐지일은 두 출처(한국투자증권 종목 정보 · KIND)를 맞대 다르면
       표시한다.
"""

from __future__ import annotations

import argparse
import html
import json
import os
import re
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any, Final

from mdp.bronze.fetch import _file_sha256, default_bronze_root, log_json, setup_cli_logging
from mdp.errors import BronzeConflictError
from mdp.runlog.recorder import Artifact, NullRecorder, PostgresRecorder, RunRecorder, record_run
from mdp.silver.kr_stocks import write_table
from mdp.spark.session import spark_session

__all__ = [
    "IMPORTED",
    "REASON_GROUPS",
    "UniverseTables",
    "audit_symbols",
    "build_universe",
    "kind_delistings",
    "listings_rows",
    "main",
    "parse_prices",
    "reason_group",
    "verified_files",
]

IMPORTED: Final = "imported/auto_trading/research/kr_stocks"
LONG_SILENCE_DAYS: Final = 30
"""가격 마지막 날이 폐지일보다 30 일 넘게 앞서면 '오래 끊김' 으로 본다 — 거래정지가 길었던 종목
(예: 마지막 가격 2012-07, 폐지 2017-07)."""

TINY_CLOSE: Final = 5
"""수정주가 종가가 5 원 미만이면 1 원 단위 반올림만으로 오차가 10 % 를 넘는다 — '정밀도 잃음'.
에스와이코퍼레이션(008080)은 2007 ~ 2013 년 1,472 일이 1 ~ 4 원이다(큰 감자 · 병합을 거슬러
맞추다 뭉개진 것으로 보임, 확인 필요). 이 값으로 수익률을 셈하면 한 해 +449,900 % 가 나온다."""

REASON_GROUPS: Final = (
    ("holding_company", ("완전자회사", "지주회사", "합병")),
    ("voluntary", ("신청에 의한",)),
    ("audit_opinion", ("감사의견",)),
    ("capital_impairment", ("자본잠식", "자본전액잠식")),
    ("dissolution", ("해산",)),
    ("expiry", ("존속기간", "SPAC", "상장예비심사")),
    ("default", ("부도",)),
    ("committee_decision", ("계속성", "투명성", "실질심사")),
)
"""KIND 폐지 사유 글 → 묶음 (앞에서부터 처음 맞는 것). 사유 글은 그대로도 남긴다."""


@dataclass(frozen=True, slots=True)
class UniverseTables:
    """[쓰임] 만든 표들 (아직 쓰지 않은 DataFrame)."""

    listings: Any
    prices: Any
    quarantine: Any
    audit: Any

    def items(self) -> list[tuple[str, Any]]:
        """[쓰임] (표 이름, DataFrame) — 쓰는 순서."""
        return [
            ("kr_listings", self.listings),
            ("kr_universe_prices", self.prices),
            ("kr_universe_quarantine", self.quarantine),
            ("kr_universe_audit", self.audit),
        ]


def reason_group(text: str) -> str:
    """[쓰임] KIND 폐지 사유 글 → 묶음 이름. 아무것도 안 맞으면 ``other``."""
    for group, words in REASON_GROUPS:
        if any(word in text for word in words):
            return group
    return "other"


def kind_delistings(raw: bytes) -> list[dict[str, str]]:
    """[쓰임] KIND 상장폐지 현황(엑셀 내려받기용 HTML 표, EUC-KR) → 줄마다 종목코드 · 회사명 ·
    폐지일 · 사유.
    [이유] 내려받은 그대로 Bronze 에 있으므로 글자를 읽는 일은 Silver 가 한다. 칸 순서는
           번호 · 회사명 · 종목코드 · 폐지일자 · 폐지사유 · 비고(2026-10-26 확인)."""
    text = raw.decode("cp949")  # EUC-KR 을 넓힌 것 — 한 글자라도 못 읽으면 멈춘다
    out = []
    for row in re.findall(r"<tr>(.*?)</tr>", text, re.S):
        cells = [
            html.unescape(re.sub(r"<[^>]+>", "", cell)).strip()
            for cell in re.findall(r"<td[^>]*>(.*?)</td>", row, re.S)
        ]
        if len(cells) >= 5 and re.fullmatch(r"\d{4}-\d{2}-\d{2}", cells[3]):
            out.append(
                {
                    "symbol": cells[2],
                    "kind_name": cells[1],
                    "kind_delisted_on": cells[3],
                    "delist_reason": re.sub(r"\s+", " ", cells[4]),
                }
            )
    return out


def _yyyymmdd(text: str) -> date | None:
    """[쓰임] ``YYYYMMDD`` → 날짜, 비었으면 None."""
    return date(int(text[:4]), int(text[4:6]), int(text[6:8])) if text else None


def listings_rows(
    info: dict[str, dict[str, str]], kind: list[dict[str, str]]
) -> list[dict[str, Any]]:
    """[쓰임] 종목 정보(``info.json``)와 KIND 폐지 현황을 종목코드로 맞춰 상장 이력 한 줄씩.
    [이유] 폐지일은 한국투자증권 종목 정보의 것을 쓰고(가격과 같은 출처), KIND 폐지일과 다르면
           ``delisted_on_agrees`` 를 거짓으로 남긴다 — 고치지 않고 표시만 한다."""
    by_symbol = {row["symbol"]: row for row in kind}
    rows = []
    for symbol, item in sorted(info.items()):
        delisted = _yyyymmdd(item.get("abol", ""))
        k = by_symbol.get(symbol)
        kind_date = date.fromisoformat(k["kind_delisted_on"]) if k else None
        reason = k["delist_reason"] if k else None
        rows.append(
            {
                "symbol": symbol,
                "name": item.get("name") or None,
                "market": item.get("market") or None,
                "kind_code": item.get("kind") or None,
                "sector": item.get("sector") or None,
                "ksic": item.get("ksic") or None,
                "ksic_name": item.get("ksic_name") or None,
                "listed_on": _yyyymmdd(item.get("lstg", "")),
                "delisted_on": delisted,
                "kind_delisted_on": kind_date,
                "delisted_on_agrees": None if delisted is None else delisted == kind_date,
                "delist_reason": reason,
                "delist_reason_group": reason_group(reason) if reason else None,
                "in_kind_listed": item.get("kind_listed") == "1",
            }
        )
    return rows


def verified_files(bronze_root: Path) -> Path:
    """[쓰임] 들여온 폴더의 모든 파일이 완료 표시의 SHA-256 과 같은지 보고 폴더를 돌려준다.
    [이유] Silver 는 검증한 Bronze 만 읽는다."""
    folder = bronze_root / Path(*IMPORTED.split("/"))
    marker = json.loads((folder / "_SUCCESS.json").read_text(encoding="utf-8"))
    for entry in marker["files"]:
        path = folder / Path(*entry["path"].split("/"))
        if _file_sha256(path) != (entry["sha256"], entry["size_bytes"]):
            raise BronzeConflictError(f"들여온 파일이 완료 표시와 다르다: {entry['path']}")
    return folder


def parse_prices(raw: Any, listings: Any, run_id: str) -> tuple[Any, Any]:
    """[쓰임] 가격 CSV 줄(``file_path`` · ``date`` · ``close`` · ``volume`` · ``value``)
    → (깨끗한 줄, 격리).
    [이유] 종가 0 이하 · 같은 날 두 줄 · 상장 전이나 폐지 뒤의 날은 원천 오류라 격리한다(지우지
           않음). 원본 CSV 는 큰 숫자를 지수 표기(``1.02165e+06``)로 유효 숫자 6 자리만 적었다
           (2026-10-26 확인 — 100 만 주 넘는 거래량은 모두, 종가 18,130 줄, 거래대금은 거의 다).
           사라진 자리는 되살릴 수 없으므로 실수로 읽어 숫자로 바꾸되 ``*_rounded`` 로 표시해,
           정확한 값처럼 쓰지 않게 한다."""
    from pyspark.sql import Window
    from pyspark.sql import functions as F

    exponent = r"[eE][+-]?\d+$"
    rows = raw.select(
        F.regexp_extract("file_path", r"prices/([0-9A-Z]+)\.csv$", 1).alias("symbol"),
        F.to_date("date", "yyyyMMdd").alias("trade_date"),
        F.col("close").cast("double").cast("decimal(18,2)").alias("close"),
        F.round(F.col("volume").cast("double")).cast("long").alias("volume"),
        F.col("value").cast("double").alias("value"),
        F.col("close").rlike(exponent).alias("close_rounded"),
        F.col("volume").rlike(exponent).alias("volume_rounded"),
        F.col("value").rlike(exponent).alias("value_rounded"),
    ).withColumn("close_precision_lost", F.col("close") < TINY_CLOSE)
    window = Window.partitionBy("symbol", "trade_date")
    life = listings.select("symbol", "listed_on", "delisted_on")
    rows = rows.join(life, "symbol", "left").withColumn("copies", F.count(F.lit(1)).over(window))
    reason = (
        F.when(F.col("trade_date").isNull() | F.col("close").isNull(), "unparsable")
        .when(F.col("copies") > 1, "duplicate_date")
        .when(F.col("close") <= 0, "non_positive_close")
        .when(
            F.col("listed_on").isNotNull() & (F.col("trade_date") < F.col("listed_on")),
            "before_listing",
        )
        .when(
            F.col("delisted_on").isNotNull() & (F.col("trade_date") > F.col("delisted_on")),
            "after_delisting",
        )
    )
    rows = rows.withColumn("reason", reason).withColumn("run_id", F.lit(run_id))
    clean = rows.where(F.col("reason").isNull()).drop(
        "reason", "copies", "listed_on", "delisted_on"
    )
    return clean, rows.where(F.col("reason").isNotNull()).drop("copies")


def audit_symbols(prices: Any, listings: Any, run_id: str) -> Any:
    """[쓰임] 종목마다 가격 기간(첫 · 마지막 날 · 줄 수)과 상장 기간을 맞대 판정 한 줄.
    [이유] 판정은 넷이다.
           ``no_prices`` — 가격 파일이 없거나 비었음,
           ``long_silence_before_delisting`` — 마지막 가격이 폐지일보다
           :data:`LONG_SILENCE_DAYS` 일 넘게 앞섬(긴 거래정지로 보임),
           ``tiny_adjusted_price`` — 수정주가가 :data:`TINY_CLOSE` 원 미만인 날이 있음, ``ok``.
           생존 편향을 셀 때 어느 종목의 값을 믿기 어려운지 보여 준다."""
    from pyspark.sql import functions as F

    spans = prices.groupBy("symbol").agg(
        F.count(F.lit(1)).alias("price_rows"),
        F.min("trade_date").alias("first_price_date"),
        F.max("trade_date").alias("last_price_date"),
        F.sum(F.col("close_precision_lost").cast("int")).alias("tiny_close_days"),
    )
    silence = F.datediff("delisted_on", "last_price_date")
    status = (
        F.when(F.col("price_rows").isNull(), "no_prices")
        .when(
            F.col("delisted_on").isNotNull() & (silence > LONG_SILENCE_DAYS),
            "long_silence_before_delisting",
        )
        .when(F.col("tiny_close_days") > 0, "tiny_adjusted_price")
        .otherwise("ok")
    )
    return (
        listings.select("symbol", "name", "listed_on", "delisted_on", "delist_reason_group")
        .join(spans, "symbol", "left")
        .withColumn(
            "days_silent_before_delisting", F.when(F.col("delisted_on").isNotNull(), silence)
        )
        .withColumn("status", status)
        .withColumn("run_id", F.lit(run_id))
    )


def build_universe(spark: Any, bronze_root: Path, run_id: str) -> UniverseTables:
    """[쓰임] 검증한 들여온 자료 → Silver 표 넷(아직 쓰지 않음)."""
    from pyspark.sql import functions as F

    folder = verified_files(bronze_root)
    info = json.loads((folder / "info.json").read_text(encoding="utf-8"))
    kind = kind_delistings((folder / "kind_delisted.html").read_bytes())
    schema = (
        "symbol STRING, name STRING, market STRING, kind_code STRING, sector STRING, ksic STRING,"
        " ksic_name STRING, listed_on DATE, delisted_on DATE, kind_delisted_on DATE,"
        " delisted_on_agrees BOOLEAN, delist_reason STRING, delist_reason_group STRING,"
        " in_kind_listed BOOLEAN"
    )
    listings = (
        spark.createDataFrame(listings_rows(info, kind), schema)
        .withColumn("run_id", F.lit(run_id))
        .cache()
    )
    raw = (
        spark.read.option("header", True)
        .schema("date STRING, close STRING, volume STRING, value STRING")
        .csv(str(folder / "prices" / "*.csv"))
        .withColumn("file_path", F.col("_metadata.file_path"))
    )
    clean, quarantine = parse_prices(raw, listings, run_id)
    clean = clean.cache()
    return UniverseTables(listings, clean, quarantine, audit_symbols(clean, listings, run_id))


def run_silver(bronze_root: Path, recorder: RunRecorder) -> dict[str, tuple[int, int]]:
    """[쓰임] 들여온 자료로 유니버스 Silver 를 다시 만들고 표마다 실행 기록에 남긴다."""
    written: dict[str, tuple[int, int]] = {}
    with record_run(recorder, "silver.kr_universe", {"bronze_root": str(bronze_root)}) as run:
        spark = spark_session("silver.kr_universe")
        spark.sparkContext.setLogLevel("ERROR")
        for name, df in build_universe(spark, bronze_root, str(run.run_id)).items():
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
    parser = argparse.ArgumentParser(prog="python -m mdp.silver.kr_universe")
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
