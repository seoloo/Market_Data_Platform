"""UI 시험 공통 — 설정 화면이 진짜 PowerShell(Windows 예약)을 부르지 않게."""

from __future__ import annotations

import pytest

import auto_trading.ops.phone_link as phone_link
import auto_trading.ops.task_setup as task_setup


@pytest.fixture(autouse=True)
def _no_real_powershell(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake(script: str) -> str:
        if "Get-ScheduledTask" in script and "Register" not in script:
            return "[]"
        raise AssertionError("시험에서 예약을 바꾸려 함")

    monkeypatch.setattr(task_setup, "_powershell", fake)
    monkeypatch.setattr(phone_link, "find_exe", lambda: None)  # 진짜 tailscale 도 부르지 않게
