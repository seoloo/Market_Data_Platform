"""UI 시험 공통 — 설정 화면이 진짜 PowerShell(Windows 예약)을 부르지 않게."""

from __future__ import annotations

import pytest

from auto_trading.ops import task_setup


@pytest.fixture(autouse=True)
def _no_real_powershell(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake(script: str) -> str:
        if "Get-ScheduledTask" in script and "Register" not in script:
            return "[]"
        raise AssertionError("시험에서 예약을 바꾸려 함")

    monkeypatch.setattr(task_setup, "_powershell", fake)
