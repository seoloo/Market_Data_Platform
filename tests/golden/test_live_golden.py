"""리팩토링 전후 비교 기준 (docs/REFACTOR_PLAN.md 0 단계).

실거래 코드의 **출력이 글자까지 같은지** 본다.

[쓰임] 가짜 거래소 · 고정 가격 · 고정 날짜로 실거래 흐름을 돌려, 알림 문구 · 화면 출력 · 낸 주문 ·
       장부(JSON)를 ``tests/golden/expected/*.txt`` 와 비교한다.
[이유] 리팩토링은 이름 · 주석 · 정리만 하고 동작은 바꾸지 않는다(사용자 2026-10-20). 그 약속을
       사람 눈이 아니라 시험으로 지키려고 만든 기준이다.

기준을 새로 찍을 때(동작을 일부러 바꾼 경우만):  ``GOLDEN_UPDATE=1 pytest tests/golden``
"""

from __future__ import annotations

import json
import os
import re
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from auto_trading.exchange.toss import TossOrderStatus

EXPECTED_DIR = Path(__file__).parent / "expected"


def _normalize(text: str, tmp_path: Path) -> str:
    """실행할 때마다 바뀌는 값만 가린다 — 임시 폴더 경로, 텔레그램 버튼 무작위 번호, 보고 기록 시각.

    [이유] 이 셋은 리팩토링과 관계없이 매번 달라서, 그대로 두면 코드를 안 바꿔도 비교가 깨진다."""
    for form in (str(tmp_path), tmp_path.as_posix(), str(tmp_path).replace("\\", "\\\\")):
        text = text.replace(form, "<TMP>")
    text = re.sub(r"(acc|liq|stk):[0-9a-f]{8}:", r"\1:<NONCE>:", text)
    return re.sub(r'"at_ms": \d+', '"at_ms": <NOW>', text)


def _check_golden(name: str, text: str, tmp_path: Path) -> None:
    """``text`` 가 기준 파일과 같아야 한다. ``GOLDEN_UPDATE=1`` 이면 기준을 새로 쓴다."""
    text = _normalize(text, tmp_path)
    path = EXPECTED_DIR / f"{name}.txt"
    if os.environ.get("GOLDEN_UPDATE") == "1":
        EXPECTED_DIR.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8", newline="\n")
        return
    assert path.exists(), f"기준 파일이 없다: {path} — GOLDEN_UPDATE=1 로 먼저 찍는다"
    expected = path.read_text(encoding="utf-8")
    assert text == expected, f"{name}: 출력이 리팩토링 전과 다르다"


def _raw_text(path: Path) -> str:
    """파일 바이트 그대로 — 줄바꿈 CR 은 ``␍`` 로 보이게 (기준 파일을 읽을 때 사라지지 않게)."""
    return path.read_bytes().decode("utf-8").replace("\r", "␍")


def _json_text(path: Path) -> str:
    return json.dumps(
        json.loads(path.read_text(encoding="utf-8")), ensure_ascii=False, indent=1, sort_keys=True
    )


# ------------------------------------------------------------------ 1. Deneb 첫 달 · 다음 날 채우기


def test_golden_deneb_first_month_fill_preview_status(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    from tests.ops.test_toss_accumulate import FakeToss, _mod, _seed

    fake = FakeToss(usd="500")
    mod = _mod(monkeypatch, tmp_path, fake)
    monkeypatch.setattr(mod, "DENEB_ENABLED", True)
    monkeypatch.setattr(mod, "DENEB_START", "2026-10-06")
    notified: list[str] = []
    monkeypatch.setattr(mod, "_notify", lambda text, telegram: notified.append(text))
    _seed(mod)
    out: list[str] = []

    # 10-06 밤: 첫 달 계획
    assert mod.run(telegram=True) == 0
    out += [
        "## 10-06 run 출력",
        capsys.readouterr().out,
        "## 10-06 알림",
        *notified,
        "## 10-06 주문",
        repr(fake.sent),
    ]
    notified.clear()

    # 10-07 밤: 어제 주문 체결 → 다음 날 채우기
    state = json.loads((tmp_path / "data" / "DENEB.json").read_text(encoding="utf-8"))
    for order in state["orders"]:
        fake.statuses[order["order_id"]] = TossOrderStatus(
            "FILLED",
            Decimal(order["qty"]),
            Decimal("80"),
            Decimal(order["qty"]) * Decimal("33"),
            Decimal("0.1"),
        )
    for k in range(1, len(fake.sent) + 1):
        fake.statuses.setdefault(
            f"OID-{k}", TossOrderStatus("FILLED", Decimal(1), Decimal(1), Decimal(1), Decimal(0))
        )
    monkeypatch.setattr(mod, "_kst_now", lambda: datetime(2026, 10, 7, 21, 0, tzinfo=UTC))
    assert mod.run(telegram=True) == 0
    out += [
        "## 10-07 run 출력",
        capsys.readouterr().out,
        "## 10-07 알림",
        *notified,
        "## 10-07 주문",
        repr(fake.sent),
    ]

    assert mod.preview() == 0
    out += ["## preview", capsys.readouterr().out]
    assert mod.status() == 0
    out += ["## status", capsys.readouterr().out]
    out += ["## DENEB.json", _json_text(tmp_path / "data" / "DENEB.json")]
    _check_golden("deneb_first_month", "\n".join(out), tmp_path)


# ------------------------------------------------------------------ 2. Deneb 하락 방어


def test_golden_deneb_brake_cycle(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    from tests.ops.test_deneb_brake_live import _at, _setup, _state

    mod, fake, ctl, msgs = _setup(monkeypatch, tmp_path)
    out: list[str] = []

    _at(monkeypatch, mod, "2026-10-06")
    assert mod.run(telegram=True) == 0
    oid = _state(tmp_path)["orders"][-1]["order_id"]
    out += ["## 10-06 브레이크 매도", capsys.readouterr().out, repr(fake.sent)]

    fake.statuses[oid] = TossOrderStatus(
        "FILLED", Decimal(5), Decimal(85), Decimal(425), Decimal("0.43")
    )
    _at(monkeypatch, mod, "2026-10-07", fake)
    assert mod.run(telegram=True) == 0
    out += ["## 10-07 보관금", capsys.readouterr().out]

    ctl["qqq_up"] = False
    _at(monkeypatch, mod, "2026-10-08", fake)
    assert mod.run(telegram=True) == 0
    ctl["qqq_up"] = True
    _at(monkeypatch, mod, "2026-10-09", fake)
    assert mod.run(telegram=True) == 0
    oid2 = _state(tmp_path)["orders"][-1]["order_id"]
    out += ["## 10-08 ~ 10-09 되사기", capsys.readouterr().out, repr(fake.sent)]

    fake.statuses[oid2] = TossOrderStatus(
        "FILLED", Decimal(5), Decimal(82), Decimal(410), Decimal("0.41")
    )
    _at(monkeypatch, mod, "2026-10-12", fake)
    assert mod.run(telegram=True) == 0
    out += ["## 10-12 브레이크 끝", capsys.readouterr().out, "## 알림", *msgs]
    out += ["## DENEB.json", _json_text(tmp_path / "data" / "DENEB.json")]
    for report in sorted((tmp_path / "reports" / "DENEB_BRAKE").glob("*.json")):
        out += [f"## 보고 {report.name}", _json_text(report)]
    _check_golden("deneb_brake_cycle", "\n".join(out), tmp_path)


# ------------------------------------------------------------------ 3. Vega (업비트)


def test_golden_vega_upbit_buy_deposit_status(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    import scripts.vega_upbit as vega

    from tests.scripts.test_vega_upbit import DAY, FakeUpbit

    fake = FakeUpbit()
    sent: list[str] = []
    monkeypatch.setattr(vega, "DATA", tmp_path / "data")
    monkeypatch.setattr(vega, "STATE", tmp_path / "data" / "state.json")
    monkeypatch.setattr(vega, "REPORTS", tmp_path / "reports")
    monkeypatch.setattr(vega, "HALT", tmp_path / "HALT")
    monkeypatch.setattr(vega, "_client", lambda: fake)
    monkeypatch.setattr(vega, "_notify", sent.append)
    monkeypatch.setattr("scripts.vega_upbit.time.sleep", lambda s: None)
    monkeypatch.setattr("scripts.vega_upbit.time.time", lambda: 300 * DAY / 1000 + 3600)
    out: list[str] = []

    assert vega.main(["plan"]) == 0
    out += ["## plan", capsys.readouterr().out]
    assert vega.main(["run"]) == 0
    out += ["## day300 run", capsys.readouterr().out, repr(fake.sent)]
    fake.krw += Decimal(300_000)
    monkeypatch.setattr("scripts.vega_upbit.time.time", lambda: 301 * DAY / 1000 + 3600)
    assert vega.main(["run"]) == 0
    out += ["## day301 run (입금)", capsys.readouterr().out, repr(fake.sent)]
    assert vega.main(["status"]) == 0
    out += ["## status", capsys.readouterr().out, "## 알림", *sent]
    out += ["## state.json", _json_text(tmp_path / "data" / "state.json")]
    _check_golden("vega_upbit", "\n".join(out), tmp_path)


# ------------------------------------------------------------------ 4. 텔레그램 문구


def test_golden_telegram_texts(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from auto_trading.notify import inbox
    from auto_trading.ops.liquidate import Store
    from scripts import telegram_bot as tb
    from tests.ops.test_telegram_acc import FakeBot

    monkeypatch.setattr(tb, "ACC_STORE", Store(tmp_path / "acc_buttons"))
    monkeypatch.setattr(tb, "ACC_PENDING", tmp_path / "acc_pending.json")
    monkeypatch.setattr(inbox, "acc_ask_path", lambda: tmp_path / "acc_ask.json")
    bot = FakeBot()
    # 가짜 봇 — 보내기 함수 모양만 진짜 봇과 같다
    tb._acc_menu(bot, tmp_path)  # type: ignore[arg-type]
    out = [
        "## HELP",
        tb.HELP,
        "## COMMANDS_MENU",
        repr(tb.COMMANDS_MENU),
        "## 적립 메뉴",
        repr(bot.out),
    ]
    _check_golden("telegram_texts", "\n".join(out), tmp_path)


# ------------------------------------------------------------ 5. 사용자 설정 (2 단계에 더함)


def test_golden_user_settings_round_trip(tmp_path: Path) -> None:
    """설정 화면 저장 전 과정 — 기본값 · 폼 검사 · 바뀐 칸 · 저장 파일 · 바꾼 기록 ·
    종목 추가/빼기 · 정지."""
    from auto_trading.ops import user_settings as us
    from auto_trading.ops.kr_accumulate import Listed

    universe = [
        Listed("005930", "삼성전자", "KOSPI", "STOCK"),
        Listed("069500", "KODEX 200", "KOSPI", "ETF"),
        Listed("NVDA", "엔비디아", "NASDAQ", "STOCK"),
        Listed("NVDL", "엔비디아 2배", "NASDAQ", "ETF"),
    ]
    first = datetime(2026, 10, 20, 3, 0, tzinfo=UTC)
    second = datetime(2026, 10, 21, 3, 0, tzinfo=UTC)
    out: list[str] = ["## 기본값", repr(sorted(us.load(tmp_path).items()))]

    # 1. 큰 폼: 금액 · 상한 · 체크칸(안 실린 bool 은 꺼짐) · 국내 종목 추가 · 오류 칸
    current = us.load(tmp_path)
    form = {
        "deneb.monthly_krw": "400,000원",
        "deneb.max_krw": "6000000",
        "acc.BTQ.usd": "$2.5",
        "acc.QS.usd": "99",
        "fire.rate_good": "25",
        "deneb.ask_on": "on",
        "screen.theme": "dark",
        "new.query": "삼성전자",
        "new.mkt": "KR",
        "new.daily": "7,000",
    }
    new, errors = us.validate(form, current, universe)
    out += ["## 폼 오류 (QS 99 달러)", repr(sorted(errors.items()))]
    form["acc.QS.usd"] = "3"
    new, errors = us.validate(form, current, universe)
    changes = us.diff(current, new)
    out += ["## 폼 오류 (고친 뒤)", repr(errors), "## 바뀐 칸", repr(changes)]
    us.apply(tmp_path, changes, first, new=new)

    # 2. 여러 개 맞는 이름 · 이미 있는 종목 · 텔레그램 추가 · 하루 금액 · 빼기
    current = us.load(tmp_path)
    out += ["## 엔비디아 (여럿)", repr(us.add_item(current, "엔비디아", "US", "", universe)[1])]
    out += ["## 삼성전자 (이미)", repr(us.add_item(current, "005930", "KR", "", universe)[1])]
    new, errors = us.add_item(current, "NVDA", "US", "", universe)
    out += ["## NVDA 추가 오류", repr(errors)]
    us.apply(tmp_path, us.diff(current, new), second, by="텔레그램", new=new)
    current = us.load(tmp_path)
    new, errors = us.set_daily(current, "x.KR.005930", "12000")
    out += [
        "## 하루 금액 오류",
        repr(errors),
        "## 범위 밖",
        repr(us.set_daily(current, "acc.BTQ", "0.1")[1]),
    ]
    new = us.remove_item(new, "acc.WQTM")
    us.apply(tmp_path, us.diff(current, new), second, by="텔레그램", new=new)

    # 3. 비상 정지 켜기 (파일로)
    current = us.load(tmp_path)
    us.apply(tmp_path, us.diff(current, {**current, "safety.halt": True}), second)
    settings_dir = tmp_path / "data" / "settings"
    out += [
        "## 적립 목록",
        repr(us.acc_items(us.load(tmp_path))),
        "## 적립 금액 기록 BTQ · 005930 · WQTM",
        repr([us.acc_history(tmp_path, s) for s in ("BTQ", "005930", "WQTM")]),
        "## 남은 칸 수",
        repr(us.active_count(us.load(tmp_path))),
        "## HALT",
        (tmp_path / "data" / "HALT").read_text(encoding="utf-8"),
        "## settings.json (바이트 그대로)",
        _raw_text(settings_dir / "settings.json"),
        "## changes.jsonl",
        _raw_text(settings_dir / "changes.jsonl"),
        "## read_log",
        repr(us.read_log(tmp_path, limit=5)),
        "## 표시",
        repr([us.show(k, v) for k, v in (("x.KR.1", 5000), ("fire.rate_bad", 4.0), ("a", 3))]),
    ]
    # 보안 검사(gitleaks)가 '"key": "x.KR.…"' 을 API 키로 오인해서 칸 이름만 '키' 로 보이게 한다
    text = "\n".join(out).replace('"key": ', '"키": ').replace("'key': ", "'키': ")
    _check_golden("user_settings", text, tmp_path)


# ------------------------------------------------------------ 6. 실거래 기록 DB (2 단계에 더함)


def test_golden_live_ledger_rows_sqlite_supabase(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """장부 → 거래 줄 · 하루 평가 줄 → SQLite 두 번(같은 번호는 바꿔 쓰기) · Supabase 로 보낼 것."""
    import sqlite3

    from auto_trading.ops.live_ledger import (
        event_rows,
        push_supabase,
        snapshot_rows,
        write_sqlite,
    )
    from auto_trading.ui import live_home
    from auto_trading.ui.live_home import Quotes, build_books
    from tests.ops.test_live_ledger import _seed

    monkeypatch.setattr(live_home, "SHOW_UPBIT", True)
    _seed(tmp_path)
    events = event_rows(build_books(tmp_path, Quotes()), now="t1")
    history: list[dict[str, Any]] = [
        {"date": "2026-10-09", "value": 1.5e6, "deposits": 6e5, "priced": True, "fx": 1344.5,
         "books": {"deneb": 1e6}, "at_ms": 1760000000000},
        {"date": "bad", "value": 1},
        {"date": "2026-10-10", "value": None, "priced": 0, "at_ms": 0},
    ]  # fmt: skip
    snapshots = snapshot_rows(history, now="t1")
    db = tmp_path / "ledger.db"
    counts = [write_sqlite(db, events, snapshots)]
    _seed(tmp_path, status="FILLED", filled=3.0)
    counts.append(write_sqlite(db, event_rows(build_books(tmp_path, Quotes()), now="t2"), []))
    con = sqlite3.connect(db)
    try:
        dumped = [
            repr(con.execute(f"SELECT * FROM {table} ORDER BY 1").fetchall())
            for table in ("ledger_events", "daily_snapshots")
        ]
    finally:
        con.close()
    sent: list[str] = []

    def fake_post(url: str, headers: Any, body: list[dict[str, Any]]) -> int:
        sent.append(f"{url} {sorted(headers.items())} {body}")
        return 201

    pushed = push_supabase("https://p.supabase.co/", "eyJx", events, snapshots, fake_post)
    out = ["## events", repr(events), "## snapshots", repr(snapshots), "## counts", repr(counts)]
    out += ["## sqlite", *dumped, "## supabase", repr(pushed), *sent]
    _check_golden("live_ledger", "\n".join(out), tmp_path)
