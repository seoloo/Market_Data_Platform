"""리팩토링 4 단계 전후 비교 기준 — Vega 의 덜 다니는 길 · 국내 적립 · 입금 나누기 글.

[쓰임] Vega 정지 30 일 알림 · 정지 풀기 · 주간 요약 · 현황 · 김프 줄,
       국내 적립 실행기(체결 · 원화 부족 · 정지 · 휴장), 입금 나누기 글을 가짜 거래소로 돌려
       기준 파일과 글자까지 비교한다.
[이유] 4 단계에서 ``scripts/vega_upbit.py`` · ``scripts/toss_kr.py`` · ``ops/deposit_split.py``
       를 손대서, 바꾸기 전 코드로 기준을 찍어 둔다. 흐름은 기존 시험(tests/scripts)을 따른다.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from auto_trading.exchange.toss import TossOrderStatus
from tests.golden.test_live_golden import _check_golden, _json_text
from tests.scripts.test_vega_upbit import DAY
from tests.scripts.test_vega_upbit import env as env  # 가짜 업비트 준비 (pytest fixture)


def test_golden_vega_stop_release_weekly_status(
    env: tuple[Any, Any, list[str]],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    import json

    vega, fake, sent = env
    monkeypatch.setattr(vega, "kimp_line", lambda upbit_btc, get=None: "🇰🇷 김프 (가짜)")
    out: list[str] = []
    assert vega.main(["run"]) == 0
    out += ["## day300 run", capsys.readouterr().out, *sent]
    sent.clear()
    state = json.loads(vega.STATE.read_text(encoding="utf-8"))
    state.update(stopped=True, stopped_since=state["last_decision"], stop_reminded=[])
    vega.STATE.write_text(json.dumps(state), encoding="utf-8")
    monkeypatch.setattr("scripts.vega_upbit.time.time", lambda: 330 * DAY / 1000 + 3600)
    assert vega.main(["run"]) == 0
    out += ["## day330 정지 30일", capsys.readouterr().out, *sent]
    sent.clear()
    assert vega.main(["release-dd", "시험"]) == 0
    out += ["## release-dd", capsys.readouterr().out, *sent]
    sent.clear()
    fake.px["BTC"] = Decimal(110_000)
    monkeypatch.setattr("scripts.vega_upbit.time.time", lambda: 331 * DAY / 1000 + 3600)
    assert vega.main(["run"]) == 0
    out += ["## day331 run", capsys.readouterr().out, *sent, repr(fake.sent)]
    sent.clear()
    for command in (["weekly"], ["status"], ["plan"]):
        assert vega.main(command) == 0
        out += [f"## {command[0]}", capsys.readouterr().out, *sent]
        sent.clear()
    out += ["## 김프 줄", vega.kimp_line(Decimal(100_000_000), get=lambda url: 1 / 0)]
    out += ["## state.json", _json_text(vega.STATE)]
    # 정지 풀기 기록에만 실제 시계(UTC ISO)가 들어가 그것만 가린다
    iso_now = r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(\.\d+)?\+00:00"
    text = re.sub(iso_now, "<NOW>", "\n".join(out))
    _check_golden("vega_more", text, tmp_path)


def test_golden_toss_kr_flow(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    import scripts.toss_kr as kr

    from tests.scripts.test_toss_kr import FakeToss, _day, _setup

    fake = FakeToss()
    sent = _setup(tmp_path, monkeypatch, fake)
    out: list[str] = []
    for day in (12, 13):
        _day(monkeypatch, day)
        assert kr.run() == 0
        out += [f"## 10-{day}", capsys.readouterr().out, *sent, repr(fake.sent)]
        sent.clear()
    fake.statuses["OID-1"] = TossOrderStatus(
        "FILLED", Decimal(1), Decimal(25_050), Decimal(25_050), Decimal(4)
    )
    _day(monkeypatch, 14)
    assert kr.run() == 0
    out += ["## 10-14 체결", capsys.readouterr().out, *sent]
    sent.clear()
    fake.power = Decimal(10_000)
    for day in (15, 16):
        _day(monkeypatch, day)
        assert kr.run() == 0
    out += ["## 원화 부족", capsys.readouterr().out, *sent]
    sent.clear()
    fake.power = Decimal(1_000_000)
    (tmp_path / "data" / "HALT").write_text("시험", encoding="utf-8")
    _day(monkeypatch, 19)
    assert kr.run() == 0
    (tmp_path / "data" / "HALT").unlink()
    fake.open_today = False
    _day(monkeypatch, 20)
    assert kr.run() == 0
    monkeypatch.setattr(kr, "_kst_now", lambda: datetime(2026, 10, 21, 16, 0, tzinfo=UTC))
    fake.open_today = True
    assert kr.run() == 0
    out += ["## 정지 · 휴장 · 장 끝", capsys.readouterr().out, *sent, repr(fake.sent)]
    out += ["## 장부", _json_text(tmp_path / "data" / "toss" / "KR_069500.json")]
    _check_golden("toss_kr", "\n".join(out), tmp_path)


def test_golden_deposit_split_texts(tmp_path: Path) -> None:
    from auto_trading.ops import deposit_split as ds

    out = [
        repr([ds.split(total) for total in (0, 1, 333_333, 1_000_000, 5_555_555)]),
        repr([ds.default_total(krw) for krw in (0, 300_000, 450_000)]),
        ds.vega_line(300_000),
        ds.ask_text("2026-11-02", 300_000),
        ds.ask_text("2026-11-02", 300_000, "🛑 브레이크 한 줄"),
        ds.split_text("2026-11", 1_000_000, 1_400.0),
        ds.split_text("2026-11", 1_000_000, None),
    ]
    _check_golden("deposit_split", "\n".join(out), tmp_path)
