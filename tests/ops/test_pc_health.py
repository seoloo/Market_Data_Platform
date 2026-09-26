"""집 PC 운영 점검 (84): 작업 실패·밀림·비정상 종료 → 경고, 상주 실행기의 '이미 실행 중'은 정상."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from auto_trading.ops.pc_health import pc_findings, result_text

NOW = int(datetime(2026, 9, 26, 12, 30, tzinfo=UTC).timestamp() * 1000)


def task(name: str, result: int = 0, hours_ago: float = 12, **kw: Any) -> dict[str, Any]:
    last = datetime.fromtimestamp((NOW - hours_ago * 3_600_000) / 1000, UTC).isoformat()
    base: dict[str, Any] = {
        "name": name,
        "last_run": last,
        "last_result": result,
        "start_when_available": True,
        "wake": True,
        "logon": "S4U",
    }
    base.update(kw)
    return base


def test_all_good_is_one_line_and_no_findings() -> None:
    lines, found = pc_findings(
        [task("AutoTrading_Paper_T2")],
        now_ms=NOW,
        unexpected_shutdowns_24h=0,
        last_boot="09-26 06:24",
    )
    assert found == []
    assert lines[0].startswith("PC: 작업 1개 · 실패 0 · 밀림 0") and "설정 남음" not in lines[0]


def test_failure_missed_run_and_power_loss_warn() -> None:
    tasks = [
        task("AutoTrading_Paper_T2", result=1),
        task("AutoTrading_Shadow_T2", hours_ago=30),
        task("AutoTrading_Demo_EW", result=0x800710E0, hours_ago=0.5),  # 상주: 이미 실행 중
    ]
    lines, found = pc_findings(tasks, now_ms=NOW, unexpected_shutdowns_24h=2, last_boot=None)
    messages = [f.message for f in found]
    assert {f.level for f in found} == {"WARN"}
    assert any("Paper_T2 마지막 결과 실패 코드 0x00000001" in m for m in messages)
    assert any("Shadow_T2 30시간째" in m for m in messages)
    assert any("비정상 종료 2회" in m for m in messages)
    assert not any("Demo_EW" in m for m in messages)
    assert "실패 1 · 밀림 1" in lines[0]


def test_already_running_is_a_failure_for_daily_tasks() -> None:
    _, found = pc_findings(
        [task("AutoTrading_Paper_T2", result=0x800710E0)],
        now_ms=NOW,
        unexpected_shutdowns_24h=0,
        last_boot=None,
    )
    assert found and "이미 실행 중" in found[0].message


def test_settings_left_are_shown_in_the_line() -> None:
    lines, _ = pc_findings(
        [task("AutoTrading_Demo_T2", start_when_available=False, logon="Interactive")],
        now_ms=NOW,
        unexpected_shutdowns_24h=0,
        last_boot=None,
    )
    assert "놓치면 켤 때 실행 꺼짐 1 · 로그인 필요 1" in lines[0]


def test_no_tasks_and_result_text() -> None:
    _, found = pc_findings([], now_ms=NOW, unexpected_shutdowns_24h=0, last_boot=None)
    assert found[0].level == "WARN"
    assert result_text(0x41303) == "아직 실행 전"
