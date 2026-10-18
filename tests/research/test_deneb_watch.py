from __future__ import annotations

import ast
import hashlib
import json
import sqlite3
from collections import Counter
from collections.abc import Iterator
from datetime import date
from decimal import Decimal
from pathlib import Path

import httpx
import pytest
from hypothesis import given
from hypothesis import strategies as st
from pydantic import ValidationError
from scripts.deneb_risk_watch import attach_280, derive_repo, derive_term, main, reparse, verify_raw

from auto_trading.core.errors import CausalityError, DataIntegrityError
from auto_trading.research.deneb_watch.catalog import BY_ID, FACTORS
from auto_trading.research.deneb_watch.domain import (
    Record,
    availability,
    canonical,
    decision_timing,
)
from auto_trading.research.deneb_watch.journal import Journal
from auto_trading.research.deneb_watch.report import build, format_value, markdown
from auto_trading.research.deneb_watch.sources import (
    MAX_BYTES,
    SOURCES,
    Source,
    csv_latest,
    decimal,
    end_of_ny_day,
    fetch,
    parse,
    preserve_raw,
)

NOW = end_of_ny_day(date(2026, 10, 18))
OBSERVED = end_of_ny_day(date(2026, 10, 16))
BODY = b"DATE,OPEN,HIGH,LOW,CLOSE\n10/15/2026,17,21,16,19\n10/16/2026,20,24,19,23\n"


@pytest.fixture
def journal(tmp_path: Path) -> Iterator[Journal]:
    instance = Journal(tmp_path / "watch.sqlite3")
    yield instance
    instance.close()


def fact(key: str = "a", *, factor_id: str = "vix") -> Record:
    return Record(
        key=key,
        kind="fact",
        factor_id=factor_id,
        event_key="one-shock",
        summary="관측 사실, 신호 아님",
        source_url="https://www.cboe.com/",
        source_sha256=hashlib.sha256(BODY).hexdigest(),
        observed_ms=OBSERVED,
        value=Decimal("23"),
        unit="index_points",
    )


def test_frozen_catalog_broad_but_not_strategy_votes() -> None:
    assert len(FACTORS) == len(BY_ID) == 36
    assert Counter(item.group for item in FACTORS) == {
        "취약성": 12,
        "촉발": 9,
        "확산": 11,
        "복귀": 4,
    }


def test_idempotent_replay_keeps_first_recorded_time(journal: Journal) -> None:
    first = journal.append(fact(), recorded_ms=NOW)
    assert journal.append(fact(), recorded_ms=NOW + 100) == first
    assert len(journal.entries()) == 1
    assert journal.verify() == first.sha256


def test_same_key_changed_fact_is_rejected(journal: Journal) -> None:
    journal.append(fact(), recorded_ms=NOW)
    with pytest.raises(DataIntegrityError):
        journal.append(fact().model_copy(update={"value": Decimal("24")}), recorded_ms=NOW + 1)
    assert len(journal.entries()) == 1


@pytest.mark.parametrize(
    "statement", ["UPDATE observations SET recorded_ms=0", "DELETE FROM observations"]
)
def test_sql_update_delete_denied(journal: Journal, statement: str) -> None:
    journal.append(fact(), recorded_ms=NOW)
    with pytest.raises(sqlite3.IntegrityError, match="append only"):
        journal.connection.execute(statement)
    journal.connection.rollback()
    journal.verify()


def test_tampering_detected_even_if_trigger_removed(journal: Journal) -> None:
    journal.append(fact(), recorded_ms=NOW)
    journal.connection.execute("DROP TRIGGER no_update")
    journal.connection.execute("UPDATE observations SET recorded_ms=1")
    journal.connection.commit()
    with pytest.raises(DataIntegrityError):
        journal.verify()


def test_record_key_column_tampering_detected(journal: Journal) -> None:
    journal.append(fact(), recorded_ms=NOW)
    journal.connection.execute("DROP TRIGGER no_update")
    journal.connection.execute("UPDATE observations SET record_key='other'")
    journal.connection.commit()
    with pytest.raises(DataIntegrityError):
        journal.verify()


@pytest.mark.leakage
def test_late_capture_cannot_enter_historical_view(journal: Journal) -> None:
    journal.append(fact(), recorded_ms=NOW)
    assert journal.as_of(OBSERVED) == []
    assert len(journal.as_of(NOW)) == 1


@pytest.mark.leakage
@pytest.mark.parametrize("field", ["observed_ms", "published_ms", "claimed_action_ms"])
def test_future_timestamp_refused(journal: Journal, field: str) -> None:
    with pytest.raises(CausalityError):
        journal.append(fact().model_copy(update={field: NOW + 1}), recorded_ms=NOW)
    assert journal.entries() == []


@pytest.mark.leakage
def test_report_unchanged_by_future_records(journal: Journal) -> None:
    entry = journal.append(fact(), recorded_ms=NOW)
    old = build(journal.as_of(NOW), as_of_ms=NOW, head_sha256=entry.sha256)
    journal.append(fact("b").model_copy(update={"value": Decimal("100")}), recorded_ms=NOW + 1)
    new = build(journal.as_of(NOW), as_of_ms=NOW, head_sha256=entry.sha256)
    assert new == old


def test_backward_recording_refused(journal: Journal) -> None:
    journal.append(fact(), recorded_ms=NOW)
    with pytest.raises(CausalityError):
        journal.append(fact("b"), recorded_ms=NOW - 1)


def test_correction_is_new_event_and_history_survives(journal: Journal) -> None:
    journal.append(fact(), recorded_ms=NOW)
    journal.append(
        fact("correction").model_copy(update={"value": Decimal("24"), "related_keys": ("a",)}),
        recorded_ms=NOW + 1,
    )
    assert journal.as_of(NOW)[0].record.value == Decimal("23")
    assert len(journal.entries()) == 2


def test_unknown_reference_refused(journal: Journal) -> None:
    with pytest.raises(DataIntegrityError):
        journal.append(fact().model_copy(update={"related_keys": ("unknown",)}), recorded_ms=NOW)


@pytest.mark.parametrize("value", ["NaN", "Infinity", "-Infinity"])
def test_nonfinite_source_rejected(value: str) -> None:
    with pytest.raises(DataIntegrityError):
        decimal(value)


@pytest.mark.parametrize(
    "change",
    [{"unit": "USD"}, {"value": None}, {"factor_id": "unknown"}, {"source_sha256": "wrong"}],
)
def test_invalid_fact_contract(change: dict[str, object]) -> None:
    data = fact().model_dump()
    data.update(change)
    with pytest.raises(ValidationError):
        Record.model_validate(data)


def test_missing_failed_stale_are_not_safe(journal: Journal) -> None:
    entry = journal.append(fact(), recorded_ms=NOW)
    assert availability(None, NOW) == "MISSING"
    assert availability(entry, NOW + 10 * 86_400_000) == "STALE"
    failed = journal.append(
        Record(key="failure", kind="failure", factor_id="vix", event_key="same", summary="실패"),
        recorded_ms=NOW,
    )
    assert availability(failed, NOW) == "SOURCE_FAILED"
    report = build(journal.entries(), as_of_ms=NOW, head_sha256=failed.sha256)
    assert report["risk_score"] is None and report["trading_effect"] == "NONE"
    assert report["comparison"]["incremental_pnl"] is None


def test_user_past_story_not_forward_prediction(journal: Journal) -> None:
    entry = journal.append(
        Record(
            key="pause",
            kind="decision",
            event_key="user-pause",
            summary="나중에 설명한 중지",
            action="pause_unspecified",
            claimed_action_ms=OBSERVED,
        ),
        recorded_ms=NOW,
    )
    assert decision_timing(entry) == "RETROSPECTIVE"
    assert journal.as_of(OBSERVED) == []


def test_past_user_report_without_exact_date_stays_retrospective(journal: Journal) -> None:
    entry = journal.append(
        Record(
            key="past",
            kind="decision",
            event_key="past-report",
            summary="과거 중지의 사후 진술",
            action="pause_unspecified",
            details={"timing_origin": "retrospective_user_report"},
        ),
        recorded_ms=NOW,
    )
    assert decision_timing(entry) == "RETROSPECTIVE_TIME_UNKNOWN"


def test_csv_latest_and_decimal_value() -> None:
    observed_ms, value = csv_latest(BODY, as_of_ms=NOW)
    assert observed_ms == OBSERVED and value == Decimal("23")


@pytest.mark.leakage
def test_future_csv_rejected_not_silently_used() -> None:
    with pytest.raises(CausalityError):
        csv_latest(BODY, as_of_ms=OBSERVED - 1)


@pytest.mark.parametrize("body", [b"DATE,CLOSE\n", b"bad,header\n", b"DATE,CLOSE\n10/16/2026,0\n"])
def test_empty_or_changed_csv_rejected(body: bytes) -> None:
    with pytest.raises(DataIntegrityError):
        csv_latest(body, as_of_ms=NOW)


def test_finra_units_and_cash_not_ratio() -> None:
    source = SOURCES[0]
    body = (
        b"<p>shown in $ millions</p><table><tr><td>Sep-26</td><td>1,480,054</td>"
        b"<td>215,328</td><td>218,581</td></tr></table>"
    )
    records = parse(source, body, captured_ms=NOW)
    assert records[0].value == Decimal("1480054")
    assert records[1].value == Decimal("433909")
    assert all(record.unit == "USD_million" and record.published_ms is None for record in records)


def test_coreweave_gaap_not_bankruptcy_claim() -> None:
    body = (
        b"<p>August 11, 2026. In millions. Three Months Ended June 30, 2026</p>"
        b"<table><tr><td>Revenue</td><td>$</td><td>2,575</td><td>1,212</td></tr>"
        b"<tr><td>Interest expense, net</td><td>(640)</td><td>(267)</td></tr>"
        b"<tr><td>Net loss</td><td>(626)</td><td>(290)</td></tr></table>"
    )
    records = parse(SOURCES[4], body, captured_ms=NOW)
    assert records[0].value == Decimal(640) / Decimal(2575)
    assert records[1].value == Decimal("-626")
    assert "counterpoint" in records[0].details
    with pytest.raises(DataIntegrityError):
        parse(SOURCES[4], body.replace(b"In millions", b"In thousands"), captured_ms=NOW)


def test_cxmt_product_is_undated_context(journal: Journal) -> None:
    source = next(source for source in SOURCES if source.id == "cxmt")
    record = parse(source, b"<p>DDR5 module</p>", captured_ms=NOW)[0]
    entry = journal.append(record, recorded_ms=NOW)
    assert availability(entry, NOW) == "UNDATED_CONTEXT" and record.value is None


def test_no_arbitrary_endpoint_or_real_network() -> None:
    transport = httpx.MockTransport(lambda request: httpx.Response(200, content=BODY))
    with httpx.Client(transport=transport) as client:
        assert fetch(SOURCES[1], client) == BODY
        with pytest.raises(DataIntegrityError):
            fetch(Source("unknown", "http://127.0.0.1/", ()), client)


def test_source_size_limit_and_http_failure() -> None:
    transport = httpx.MockTransport(
        lambda request: httpx.Response(200, content=b"x" * (MAX_BYTES + 1))
    )
    with httpx.Client(transport=transport) as client, pytest.raises(DataIntegrityError):
        fetch(SOURCES[1], client)
    denied = httpx.MockTransport(lambda request: httpx.Response(403))
    with httpx.Client(transport=denied) as client, pytest.raises(httpx.HTTPStatusError):
        fetch(SOURCES[1], client)


def test_raw_hash_verification(journal: Journal, tmp_path: Path) -> None:
    preserve_raw(tmp_path, BODY)
    journal.append(fact(), recorded_ms=NOW)
    assert verify_raw(journal, tmp_path) == 1
    (tmp_path / "sources" / f"{fact().source_sha256}.bin").write_bytes(b"corrupt")
    with pytest.raises(DataIntegrityError):
        verify_raw(journal, tmp_path)


def test_term_ratio_requires_matching_dates(journal: Journal, tmp_path: Path) -> None:
    a = parse(SOURCES[1], BODY, captured_ms=NOW)[0]
    b = parse(SOURCES[3], BODY.replace(b",23", b",46"), captured_ms=NOW)[0]
    journal.append(a, recorded_ms=NOW)
    journal.append(b, recorded_ms=NOW)
    assert derive_term(journal, tmp_path, recorded_ms=NOW)
    assert journal.entries()[-1].record.value == Decimal("2")
    assert journal.entries()[-1].record.unit == "ratio"


def test_term_ratio_different_dates_not_filled(journal: Journal, tmp_path: Path) -> None:
    a = parse(SOURCES[1], BODY, captured_ms=NOW)[0]
    b = parse(SOURCES[3], b"DATE,CLOSE\n10/15/2026,20\n", captured_ms=NOW)[0]
    journal.append(a, recorded_ms=NOW)
    journal.append(b, recorded_ms=NOW)
    assert not derive_term(journal, tmp_path, recorded_ms=NOW)
    assert availability(journal.entries()[-1], NOW) == "PARTIAL"


def test_attach_280_readonly_does_not_invent_actual_return(
    journal: Journal, tmp_path: Path
) -> None:
    path = tmp_path / "original.json"
    raw = json.dumps(
        {
            "generated_utc": "2026-10-18T09:20:00+00:00",
            "start": "2026-10-09",
            "last_day": "2026-10-16",
            "paid_krw": 300000,
            "paid_usd": 223.13,
            "value_usd": 213.81,
        }
    ).encode()
    path.write_bytes(raw)
    attach_280(journal, tmp_path, path, recorded_ms=NOW)
    assert path.read_bytes() == raw
    report = build(journal.entries(), as_of_ms=NOW, head_sha256=journal.verify())
    assert report["comparison"]["avoided_loss"] is None
    assert report["new_money_g0"]["power_80_verified"] is False
    assert "기존 보고서를 읽은 값" in markdown(report)


def test_cli_note_idempotent_and_prevents_source_fabrication(tmp_path: Path) -> None:
    source = tmp_path / "input.json"
    source.write_text(canonical(fact()), encoding="utf-8")
    with pytest.raises(DataIntegrityError):
        main(["note", "--root", str(tmp_path), "--input", str(source)])
    note = Record(
        key="note", kind="decision", event_key="new-decision", summary="새 판단", action="pause_new"
    )
    source.write_text(canonical(note), encoding="utf-8")
    argv = ["note", "--root", str(tmp_path), "--input", str(source), "--decision-now"]
    assert main(argv) == main(argv) == 0
    journal = Journal(tmp_path / "watch.sqlite3")
    try:
        assert len(journal.entries()) == 1
        assert decision_timing(journal.entries()[0]) == "CONTEMPORANEOUS"
    finally:
        journal.close()


@given(
    value=st.decimals(min_value="0.001", max_value="100000", allow_nan=False, allow_infinity=False)
)
def test_decimal_roundtrip_has_no_float_money(value: Decimal) -> None:
    record = fact().model_copy(update={"value": value})
    assert Record.model_validate_json(canonical(record)).value == value


def test_observatory_has_no_trading_imports() -> None:
    root = Path(__file__).resolve().parents[2]
    package = root / "src/auto_trading/research/deneb_watch"
    forbidden = (
        "auto_trading.exchange",
        "auto_trading.execution",
        "auto_trading.ops",
        "auto_trading.notify",
        "auto_trading.config.secrets",
        "subprocess",
    )
    for path in [*package.glob("*.py"), root / "scripts/deneb_risk_watch.py"]:
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            names = (
                [node.module or ""]
                if isinstance(node, ast.ImportFrom)
                else ([item.name for item in node.names] if isinstance(node, ast.Import) else [])
            )
            assert not any(name.startswith(forbidden) for name in names), path
    for folder in ("ops", "execution", "exchange", "strategy", "risk", "app"):
        for path in (root / "src/auto_trading" / folder).rglob("*.py"):
            assert "research.deneb_watch" not in path.read_text(encoding="utf-8"), path


def test_vvix_has_distinct_official_header() -> None:
    record = parse(SOURCES[2], b"DATE,VVIX\n10/16/2026,109.91\n", captured_ms=NOW)[0]
    assert record.factor_id == "vvix" and record.value == Decimal("109.91")


def test_reparse_preserves_original_failure_and_late_knowledge(
    journal: Journal, tmp_path: Path
) -> None:
    body = b"DATE,VVIX\n10/16/2026,109.91\n"
    sha = preserve_raw(tmp_path, body)
    journal.append(
        Record(
            key="first-failure",
            kind="failure",
            factor_id="vvix",
            event_key="vvix",
            summary="최초 실패 보존",
            source_url=SOURCES[2].url,
            source_sha256=sha,
        ),
        recorded_ms=NOW,
    )
    assert reparse(journal, tmp_path, "vvix", sha, recorded_ms=NOW + 1) == 1
    assert len(journal.entries()) == 2
    assert journal.as_of(NOW)[0].record.kind == "failure"
    assert journal.entries()[-1].record.value == Decimal("109.91")


def test_fred_rates_units_and_same_day_funding() -> None:
    body = b"observation_date,DFII10,SOFR,IORB\n2026-10-15,2.78,4.40,4.40\n2026-10-16,.,4.42,4.40\n"
    source = next(source for source in SOURCES if source.id == "fred_rates")
    records = parse(source, body, captured_ms=NOW)
    assert records[0].value == Decimal("2.78") and records[0].unit == "percent"
    assert records[1].value == Decimal("0.02") and records[1].unit == "percentage_points"


@pytest.mark.leakage
def test_future_fred_row_rejected() -> None:
    body = b"observation_date,DFII10,SOFR,IORB\n2026-10-20,2.78,4.40,4.40\n"
    with pytest.raises(CausalityError):
        source = next(source for source in SOURCES if source.id == "fred_rates")
        parse(source, body, captured_ms=NOW)


def test_fred_html_requires_series_units_and_uses_common_repo_date(
    journal: Journal, tmp_path: Path
) -> None:
    for series, source_id, observations in (
        ("SOFR", "fred_sofr_html", "2026-10-15: 4.40"),
        ("IORB", "fred_iorb_html", "2026-10-16: 4.50 2026-10-15: 4.35"),
    ):
        source = next(source for source in SOURCES if source.id == source_id)
        body = f"<p>{series} Observations {observations} Units: Percent</p>".encode()
        for record in parse(source, body, captured_ms=NOW):
            journal.append(record, recorded_ms=NOW)
        with pytest.raises(DataIntegrityError):
            parse(source, body.replace(b"Percent", b"unknown"), captured_ms=NOW)
    assert derive_repo(journal, tmp_path, recorded_ms=NOW)
    assert journal.entries()[-1].record.value == Decimal("0.05")
    assert journal.entries()[-1].record.observed_ms == end_of_ny_day(date(2026, 10, 15))


@pytest.mark.leakage
def test_html_late_capture_not_historical_rate(journal: Journal) -> None:
    source = next(source for source in SOURCES if source.id == "fred_real_html")
    record = parse(
        source, b"<p>DFII10 Observations 2026-10-15: 2.78 Units: Percent</p>", captured_ms=NOW
    )[0]
    journal.append(record, recorded_ms=NOW)
    assert journal.as_of(end_of_ny_day(date(2026, 10, 16))) == []


def test_new_york_observation_date_and_korean_units(journal: Journal) -> None:
    entry = journal.append(fact(), recorded_ms=NOW)
    report = build(journal.entries(), as_of_ms=NOW, head_sha256=entry.sha256)
    assert "뉴욕 관측일 2026-10-16" in markdown(report)
    assert "뉴욕 관측일 2026-10-17" not in markdown(report)
    assert format_value("1480054", "USD_million", "margin_debt") == "1.4801조 달러"
    assert format_value("1.006503", "ratio", "vol_term") == "1.0065배"


def test_incomplete_csv_row_is_domain_error() -> None:
    with pytest.raises(DataIntegrityError):
        csv_latest(b"DATE,CLOSE\n10/16/2026\n", as_of_ms=NOW)


def test_user_sale_scope_correction_does_not_fabricate_pnl(journal: Journal) -> None:
    journal.append(
        Record(
            key="unknown",
            kind="decision",
            event_key="pause",
            summary="범위 불명",
            action="pause_unspecified",
        ),
        recorded_ms=NOW,
    )
    journal.append(
        Record(
            key="confirmed",
            kind="decision",
            event_key="pause",
            summary="전량 매도 후 정지 사용자 확인",
            action="sell_existing",
            related_keys=("unknown",),
            details={"timing_origin": "retrospective_user_report"},
        ),
        recorded_ms=NOW + 1,
    )
    report = build(journal.entries(), as_of_ms=NOW + 1, head_sha256=journal.verify())
    assert report["comparison"]["reported_action"] == "sell_existing"
    assert report["comparison"]["user_confirmed_scope"] is True
    assert report["comparison"]["incremental_pnl"] is None
    assert len(report["decisions"]) == 2
