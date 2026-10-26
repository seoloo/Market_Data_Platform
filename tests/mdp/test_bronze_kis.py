"""국내 주식 Bronze 받기 시험 — 휴장일 · 일봉 · 분봉의 이어 받기, 장 안 열린 날, 멱등, 반쪽 없음.

가짜 한국투자증권(httpx.MockTransport)과 지어낸 응답만 쓴다(실제 네트워크 · 키 없음). 가짜 원천은
실제로 확인한 동작을 흉내 낸다: 분봉은 물은 시각부터 거슬러 최대 120 개, 09:00 ~ 15:30 과 16:01 ~
18:00,
장이 안 열린 날을 물으면 그 전 거래일 분봉.
"""

from __future__ import annotations

import gzip
import json
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest

from mdp.bronze import kis as bronze_kis
from mdp.bronze.fetch import read_manifest
from mdp.bronze.kis import (
    SUCCESS,
    Kind,
    Price,
    Unit,
    UnitStatus,
    plan_units,
    run_units,
)
from mdp.errors import BronzeConflictError, DataIntegrityError, SourceUnavailableError
from mdp.kis.client import Credentials, KisClient, KisEnv, TokenCache
from mdp.runlog.recorder import NullRecorder, record_run

CREDS = Credentials(KisEnv.REAL, "KEY-TEST", "SECRET-TEST")
LATER = datetime(2027, 1, 1, tzinfo=UTC)
CLOSED = {date(2026, 10, 9)}  # 한글날
TOO_OLD = date(2025, 9, 1)


def _minutes() -> list[str]:
    """정규장 09:00 ~ 15:30 과 시간외 16:01 ~ 18:00 (실제 응답에서 본 범위)."""
    out = []
    for start, end in ((9 * 60, 15 * 60 + 30), (16 * 60 + 1, 18 * 60)):
        out += [f"{m // 60:02d}{m % 60:02d}00" for m in range(start, end + 1)]
    return out


MINUTES = _minutes()


def _yyyymmdd(text: str) -> date:
    return date(int(text[:4]), int(text[4:6]), int(text[6:8]))


def _ok(**body: Any) -> httpx.Response:
    return httpx.Response(200, json={"rt_cd": "0", "msg_cd": "MCA00000", "msg1": "정상", **body})


class FakeKis:
    def __init__(self) -> None:
        self.queries: list[httpx.Request] = []
        self.fail_minute_call: int | None = None
        self.mix_day_on_page: int | None = None
        self.minute_calls = 0

    def _previous_open(self, day: date) -> date:
        day -= timedelta(days=1)
        while day.weekday() >= 5 or day in CLOSED:
            day -= timedelta(days=1)
        return day

    def _minute(self, request: httpx.Request) -> httpx.Response:
        self.minute_calls += 1
        if self.fail_minute_call is not None and self.minute_calls >= self.fail_minute_call:
            return httpx.Response(500, json={"rt_cd": "1", "msg_cd": "EGW00300"})
        asked = _yyyymmdd(request.url.params["FID_INPUT_DATE_1"])
        if asked < TOO_OLD:
            return _ok(output1={}, output2=[])
        day = asked if asked.weekday() < 5 and asked not in CLOSED else self._previous_open(asked)
        cursor = request.url.params["FID_INPUT_HOUR_1"]
        rows = [m for m in reversed(MINUTES) if m <= cursor][:120]
        stamp = f"{day:%Y%m%d}"
        out = [
            {"stck_bsop_date": stamp, "stck_cntg_hour": m, "stck_prpr": "100", "cntg_vol": "1"}
            for m in rows
        ]
        if self.mix_day_on_page == self.minute_calls and out:
            out[-1]["stck_bsop_date"] = "19990101"
        return _ok(output1={}, output2=out)

    def handler(self, request: httpx.Request) -> httpx.Response:
        if request.url.path == "/oauth2/tokenP":
            return httpx.Response(200, json={"access_token": "T", "expires_in": 86400})
        self.queries.append(request)
        path = request.url.path
        if path.endswith("chk-holiday"):
            start = _yyyymmdd(request.url.params["BASS_DT"])
            offset = 24 if request.url.params.get("CTX_AREA_NK") else 0
            days = [start + timedelta(days=offset + i) for i in range(24)]
            out = [
                {"bass_dt": f"{d:%Y%m%d}", "opnd_yn": "N" if d.weekday() >= 5 else "Y"}
                for d in days
            ]
            nk = "" if offset else "NEXT"
            return httpx.Response(
                200,
                json={
                    "rt_cd": "0",
                    "msg_cd": "KIOK0500",
                    "output": out,
                    "ctx_area_fk": "F",
                    "ctx_area_nk": nk,
                },
                headers={"tr_cont": "M"},
            )
        if path.endswith("inquire-daily-itemchartprice"):
            first = request.url.params["FID_INPUT_DATE_1"]
            return _ok(output1={}, output2=[{"stck_bsop_date": first, "stck_clpr": "53000"}])
        if path.endswith("inquire-time-dailychartprice"):
            return self._minute(request)
        return httpx.Response(404)

    def client(self, tmp_path: Path) -> KisClient:
        return KisClient(
            CREDS,
            httpx.Client(transport=httpx.MockTransport(self.handler)),
            token_cache=TokenCache(tmp_path / "token.json"),
            sleep=lambda _: None,
            max_attempts=2,
        )


def _run(fake: FakeKis, tmp_path: Path, units: list[Unit], now: datetime = LATER) -> list[Any]:
    root, manifest = tmp_path / "bronze", tmp_path / "kis.csv"
    with fake.client(tmp_path) as client, record_run(NullRecorder(), "test", {}) as run:
        return run_units(client, root, units, manifest, run, now=lambda: now)


def _minute_unit(day: date) -> Unit:
    return Unit(Kind.MINUTE, day, "005930")


# ------------------------------------------------------------------ 단위


def test_paths_and_ready_times() -> None:
    minute = _minute_unit(date(2026, 10, 23))
    assert minute.relative_dir == "kis/domestic/minute-bars/market=J/symbol=005930/date=2026-10-23"
    assert minute.ready_at == datetime(2026, 10, 23, 11, 30, tzinfo=UTC)  # 서울 20:30
    daily = Unit(Kind.DAILY, date(2018, 5, 1), "005930", Price.RAW)
    assert daily.relative_dir.endswith("price=raw/symbol=005930/month=2018-05")
    assert daily.ready_at == datetime(2018, 5, 31, 11, 30, tzinfo=UTC)
    assert (
        Unit(Kind.HOLIDAY, date(2025, 10, 1)).relative_dir == "kis/domestic/holiday/month=2025-10"
    )


def test_unit_rejects_wrong_shape() -> None:
    with pytest.raises(ValueError):
        Unit(Kind.HOLIDAY, date(2025, 10, 1), "005930")
    with pytest.raises(ValueError):
        Unit(Kind.DAILY, date(2025, 10, 1), "005930")
    with pytest.raises(ValueError):
        Unit(Kind.DAILY, date(2025, 10, 2), "005930", Price.RAW)


def test_plan_skips_weekends_and_covers_both_prices() -> None:
    minute = plan_units(Kind.MINUTE, ["005930"], date(2026, 10, 9), date(2026, 10, 12))
    assert [u.period for u in minute] == [date(2026, 10, 9), date(2026, 10, 12)]
    daily = plan_units(Kind.DAILY, ["005930"], date(2018, 4, 1), date(2018, 5, 31))
    assert {(u.price, u.period) for u in daily} == {
        (p, m) for p in Price for m in (date(2018, 4, 1), date(2018, 5, 1))
    }


# ------------------------------------------------------------------ 분봉


def test_minute_day_pages_back_to_the_open(tmp_path: Path) -> None:
    fake = FakeKis()
    [result] = _run(fake, tmp_path, [_minute_unit(date(2026, 10, 23))])
    assert result.status is UnitStatus.DOWNLOADED
    assert result.records == len(MINUTES)  # 391 + 120
    assert result.pages == -(-len(MINUTES) // 120)
    folder = tmp_path / "bronze" / result.unit.relative_dir
    marker = json.loads((folder / SUCCESS).read_text(encoding="utf-8"))
    cursors = [p["request"]["FID_INPUT_HOUR_1"] for p in marker["pages"]]
    assert cursors[:2] == ["200000", "160000"]  # 첫 페이지는 18:00 ~ 16:01 → 그 1 분 앞
    body = json.loads(gzip.decompress((folder / "page-00000.json.gz").read_bytes()))
    assert body["output2"][0]["stck_cntg_hour"] == "180000"


def test_closed_day_is_not_stored(tmp_path: Path) -> None:
    [result] = _run(FakeKis(), tmp_path, [_minute_unit(date(2026, 10, 9))])
    assert result.status is UnitStatus.NOT_OPEN
    assert not (tmp_path / "bronze" / result.unit.relative_dir).exists()
    assert not list((tmp_path / "bronze").rglob("*.part"))


def test_day_beyond_retention_is_missing(tmp_path: Path) -> None:
    [result] = _run(FakeKis(), tmp_path, [_minute_unit(date(2025, 8, 29))])
    assert result.status is UnitStatus.MISSING_AT_SOURCE


def test_other_day_mixed_mid_way_stops(tmp_path: Path) -> None:
    fake = FakeKis()
    fake.mix_day_on_page = 2
    with pytest.raises(DataIntegrityError):
        _run(fake, tmp_path, [_minute_unit(date(2026, 10, 23))])
    assert not list((tmp_path / "bronze").rglob("date=2026-10-23*"))


def test_failure_mid_day_leaves_nothing_then_resumes(tmp_path: Path) -> None:
    broken = FakeKis()
    broken.fail_minute_call = 3
    unit = _minute_unit(date(2026, 10, 23))
    with pytest.raises(SourceUnavailableError):
        _run(broken, tmp_path, [unit])
    assert not list((tmp_path / "bronze").rglob("date=2026-10-23*"))
    [result] = _run(FakeKis(), tmp_path, [unit])
    assert result.status is UnitStatus.DOWNLOADED


def test_unfinished_day_is_not_fetched(tmp_path: Path) -> None:
    fake = FakeKis()
    evening = datetime(2026, 10, 23, 10, tzinfo=UTC)  # 서울 19:00
    [result] = _run(fake, tmp_path, [_minute_unit(date(2026, 10, 23))], now=evening)
    assert result.status is UnitStatus.NOT_READY and fake.queries == []


# ------------------------------------------------------------------ 휴장일 · 일봉


def test_holiday_month_stops_after_month_end(tmp_path: Path) -> None:
    fake = FakeKis()
    [result] = _run(fake, tmp_path, [Unit(Kind.HOLIDAY, date(2025, 10, 1))])
    assert (result.status, result.pages, result.records) == (UnitStatus.DOWNLOADED, 2, 48)
    assert fake.queries[1].headers["tr_cont"] == "N"


def test_daily_month_one_call(tmp_path: Path) -> None:
    fake = FakeKis()
    unit = Unit(Kind.DAILY, date(2018, 5, 1), "005930", Price.RAW)
    [result] = _run(fake, tmp_path, [unit])
    assert (result.status, result.pages, result.records) == (UnitStatus.DOWNLOADED, 1, 1)
    params = fake.queries[0].url.params
    assert (params["FID_INPUT_DATE_1"], params["FID_INPUT_DATE_2"]) == ("20180501", "20180531")
    assert params["FID_ORG_ADJ_PRC"] == "1"


def test_daily_rows_outside_month_are_refused(tmp_path: Path) -> None:
    fake = FakeKis()
    original = fake.handler

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("inquire-daily-itemchartprice"):
            return _ok(output1={}, output2=[{"stck_bsop_date": "20180601"}])
        return original(request)

    fake.handler = handler  # type: ignore[method-assign]
    with pytest.raises(DataIntegrityError):
        _run(fake, tmp_path, [Unit(Kind.DAILY, date(2018, 5, 1), "005930", Price.RAW)])


# ------------------------------------------------------------------ 멱등 · 대조


def test_second_run_asks_nothing_and_manifest_is_stable(tmp_path: Path) -> None:
    units = [Unit(Kind.HOLIDAY, date(2025, 10, 1)), _minute_unit(date(2026, 10, 23))]
    _run(FakeKis(), tmp_path, units)
    manifest = tmp_path / "kis.csv"
    before = manifest.read_bytes()
    again = FakeKis()
    results = _run(again, tmp_path, units)
    assert again.queries == [] and {r.status for r in results} == {UnitStatus.SKIPPED_KNOWN}
    assert manifest.read_bytes() == before
    assert any(p.endswith(SUCCESS) for p in read_manifest(manifest))


def test_lost_manifest_is_rebuilt_and_tampering_stops(tmp_path: Path) -> None:
    unit = _minute_unit(date(2026, 10, 23))
    _run(FakeKis(), tmp_path, [unit])
    manifest = tmp_path / "kis.csv"
    before = manifest.read_bytes()
    manifest.unlink()
    [again] = _run(FakeKis(), tmp_path, [unit])
    assert again.status is UnitStatus.ALREADY_PRESENT and manifest.read_bytes() == before
    manifest.unlink()
    page = tmp_path / "bronze" / unit.relative_dir / "page-00001.json.gz"
    page.write_bytes(gzip.compress(b"{}", mtime=0))
    with pytest.raises(BronzeConflictError):
        _run(FakeKis(), tmp_path, [unit])


def test_cli_without_key_or_symbols_exits_2(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    assert (
        bronze_kis.main(["--kind", "minute", "--start", "2026-10-23", "--end", "2026-10-23"]) == 2
    )
    from mdp.kis.client import MissingCredentialsError

    def no_key(cls: type) -> Credentials:
        raise MissingCredentialsError("키 없음")

    monkeypatch.setattr(Credentials, "from_env", classmethod(no_key))
    code = bronze_kis.main(
        [
            "--kind",
            "holiday",
            "--start",
            "2025-10-01",
            "--end",
            "2025-10-31",
            "--root",
            str(tmp_path),
        ]
    )
    assert code == 2
