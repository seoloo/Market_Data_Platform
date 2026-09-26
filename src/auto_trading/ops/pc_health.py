"""집 PC 운영 점검 — 예약 작업 실패·밀림·비정상 종료를 일일 점검에 한 줄로 (docs/research/84).

지금 PC 한 대가 모든 자동 실행을 맡는다.
사용자 결정(2026-09-26): 수익이 나기 전에는 장비를 사지 않는다.
그래서 PC 가 꺼지거나, 작업이 실패하거나, 전원이 갑자기 끊긴 것을
**다음 점검에서 바로 알 수 있게** 한다.

입력은 PowerShell 이 준 값(작업 목록·이벤트 수)이고 이 모듈은 순수 함수다.
조회는 ``scripts/paper_watchdog.py`` 가 한다.
모든 판정은 **WARN 까지만** 올린다 — 원장 안전(CRITICAL)은 각 원장 점검이 따로 본다.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Any

from auto_trading.ops.paper_watch import Finding

__all__ = ["RESULT_OK", "pc_findings", "result_text"]

_HOUR_MS = 3_600_000
RESULT_OK = {
    0: "성공",
    0x41301: "실행 중",
    0x41303: "아직 실행 전",
}
_ALREADY_RUNNING = 0x800710E0
"""작업이 이미 돌고 있어 새 실행을 건너뜀(MultipleInstances=IgnoreNew). 상주 실행기는 정상이다."""
_RESIDENT = ("AutoTrading_Demo_EW",)
"""한 번 띄우면 계속 도는 상주 실행기 (시간마다 살아 있는지 확인용으로 다시 띄운다)."""
_DAILY_GRACE_MS = 26 * _HOUR_MS


def result_text(code: int) -> str:
    if code in RESULT_OK:
        return RESULT_OK[code]
    if code == _ALREADY_RUNNING:
        return "이미 실행 중"
    return f"실패 코드 0x{code & 0xFFFFFFFF:08X}"


def _ms(text: str | None) -> int | None:
    if not text:
        return None
    try:
        return int(datetime.fromisoformat(text).timestamp() * 1000)
    except ValueError:
        return None


def pc_findings(
    tasks: Sequence[Mapping[str, Any]],
    *,
    now_ms: int,
    unexpected_shutdowns_24h: int,
    last_boot: str | None,
) -> tuple[list[str], list[Finding]]:
    """예약 작업 상태·비정상 종료로 한 줄 요약과 판정.

    ``tasks`` 항목: name · last_run(ISO) · last_result(int) · start_when_available · wake · logon.
    """
    ledger = "PC"
    findings: list[Finding] = []
    if not tasks:
        return [], [Finding("WARN", ledger, "AutoTrading 예약 작업을 찾지 못함")]
    failed = behind = 0
    for t in tasks:
        name = str(t.get("name"))
        code = int(t.get("last_result") or 0)
        ok = code in RESULT_OK or (code == _ALREADY_RUNNING and name in _RESIDENT)
        if not ok:
            failed += 1
            findings.append(Finding("WARN", ledger, f"{name} 마지막 결과 {result_text(code)}"))
        last = _ms(t.get("last_run"))
        daily = name not in _RESIDENT and code != 0x41303
        if daily and last is not None and now_ms - last > _DAILY_GRACE_MS:
            behind += 1
            hours = (now_ms - last) // _HOUR_MS
            findings.append(Finding("WARN", ledger, f"{name} {hours}시간째 실행 안 됨"))
    if unexpected_shutdowns_24h:
        findings.append(
            Finding(
                "WARN",
                ledger,
                f"지난 24시간 비정상 종료 {unexpected_shutdowns_24h}회"
                " (전원 차단·강제 종료) — 문서 84 §2",
            )
        )
    no_catchup = sum(1 for t in tasks if not t.get("start_when_available"))
    interactive = sum(1 for t in tasks if str(t.get("logon")) == "Interactive")
    boot = f" · 마지막 부팅 {last_boot}" if last_boot else ""
    line = (
        f"PC: 작업 {len(tasks)}개 · 실패 {failed} · 밀림 {behind}"
        f" · 비정상 종료 {unexpected_shutdowns_24h}회(24시간){boot}"
    )
    if no_catchup or interactive:
        line += f" · 설정 남음(놓치면 켤 때 실행 꺼짐 {no_catchup} · 로그인 필요 {interactive})"
    return [line], findings
