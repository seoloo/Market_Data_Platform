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
