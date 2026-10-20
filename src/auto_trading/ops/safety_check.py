"""매일 안전 점검 — 실주문 쪽이 '모르는 사이' 바뀌지 않았는지 판단만 한다 (조회 · 파일 없음).

언제 쓰나: 텔레그램 봇(2 분마다)이 매일 09:20 KST 뒤 한 번 ``scripts/safety_check.py`` 로 부른다.
읽고 쓰는 것: 없음 — 조회 결과를 받아 '발견(Finding)' 목록과 보낼 글만 만든다.

[이유] 사용자 2026-10-16 "1 -> 3 -> 2 이렇게 진행해줘" 의 1번. 같은 날 문제들(시험이 실계좌 Vega
       코인을 팖, 업비트 주문 스위치 없음, 옛 · 새 예약 중복)이 모두 사람이 우연히 발견한 것이라,
       하루 한 번 자동으로 본다.

* 주문 스위치(.env) — 어제와 달라졌으면 알림.
* 바이낸스 키 권한 — 출금이 켜졌거나 IP 제한이 풀렸으면 경고.
* 바이낸스 선물 — 포지션이 있음 (Altair 는 2026-10-19 은퇴 — 선물을 쓰는 실행기가 없다).
* 예약 — 같은 일을 하는 옛 · 새 예약이 함께 켜짐, 꼭 필요한 예약이 꺼짐.
* 장부 vs 실제 잔고 — Vega(업비트, 2026-10-19 부터) 코인이 장부보다 적음(장부 밖 매도),
  Deneb · 💹 개별주 장부가 토스 보유보다 많음.
* 그룹 정지 — 어제와 달라졌을 때만 알림(일부러 멈춘 Deneb 을 매일 말하지 않게).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal

__all__ = [
    "DUPLICATE_TASKS",
    "REQUIRED_TASKS",
    "Finding",
    "futures_issues",
    "holding_issues",
    "key_issues",
    "report_text",
    "state_changes",
    "task_issues",
    "vega_issues",
]

DUPLICATE_TASKS = (
    ("AutoTrading_Binance_Cruise", "AutoTrading_Vega"),
    ("AutoTrading_Upbit_T2_Live", "AutoTrading_Vega"),  # 10-19 Vega 가 다시 업비트 — 옛 업비트 예약
    ("AutoTrading_Upbit_T2_Weekly", "AutoTrading_Vega_Weekly"),
    ("AutoTrading_Toss_M3_Chat", "AutoTrading_Deneb_Chat"),
    ("AutoTrading_Toss_M3_Retry", "AutoTrading_Deneb_Retry"),
)
"""(옛 이름, 새 이름) — 둘 다 켜져 있으면 같은 일을 두 번 한다 (2026-10-15 이름 변경)."""
REQUIRED_TASKS = ("AutoTrading_Telegram_Bot",)
"""꺼지면 개별주 손절 · 정리 이어 하기 · 알림이 멈추는 예약."""
COIN_TOLERANCE = Decimal("0.01")
"""Vega 코인이 장부보다 이만큼(1%) 넘게 적으면 경고 — 수수료 · 먼지는 넘지 않는다."""
CASH_CURRENCIES = ("USDT", "KRW")
"""현금 — 입출금으로 늘고 줄어서 장부와 비교하지 않는다."""


@dataclass(frozen=True, slots=True)
class Finding:
    """점검에서 찾은 것 한 줄."""

    level: str
    """warn (⚠️ 확인 필요) · info (ℹ️ 바뀐 것)."""
    text: str


def state_changes(prev: Mapping[str, str], now: Mapping[str, str], label: str) -> list[Finding]:
    """[쓰임] 어제 기록과 달라진 값마다 'ℹ️ 바뀐 것' 한 줄 (스위치 · 정지 상태).
    [이유] 처음(어제 기록 없음)이면 아무것도 말하지 않는다 —
           첫날 모든 값이 '바뀜' 으로 오지 않게."""
    if not prev:
        return []
    findings = []
    for name in sorted(set(prev) | set(now)):
        before = prev.get(name, "-")
        after = now.get(name, "-")
        if before != after:
            findings.append(Finding("info", f"{label} {name}: {before} → {after}"))
    return findings


def key_issues(restrictions: Mapping[str, object]) -> list[Finding]:
    """[쓰임] 바이낸스 키 권한(``/sapi/v1/account/apiRestrictions``) → 위험한 권한 경고."""
    findings = []
    if restrictions.get("enableWithdrawals"):
        findings.append(Finding("warn", "바이낸스 키에 출금 권한이 켜져 있어요 — 바로 끄세요"))
    if not restrictions.get("ipRestrict"):
        findings.append(Finding("warn", "바이낸스 키 IP 제한이 풀려 있어요"))
    return findings


def futures_issues(positions: Mapping[str, Decimal]) -> list[Finding]:
    """[쓰임] 바이낸스 선물에 포지션이 남아 있으면 경고 {종목: 수량}.
    [이유] Altair 은퇴(2026-10-19) 뒤에는 선물을 쓰는 실행기가 없어,
           포지션이 있으면 사람이 봐야 한다."""
    if not positions:
        return []
    held_parts = []
    for symbol, qty in sorted(positions.items()):
        held_parts.append(f"{symbol} {qty}")
    held = ", ".join(held_parts)
    return [Finding("warn", f"Altair 은퇴 뒤인데 바이낸스 선물 포지션이 있어요: {held}")]


def task_issues(states: Mapping[str, str]) -> list[Finding]:
    """[쓰임] 예약 {이름: 상태(Ready · Running · Disabled …)} → 중복 · 꺼진 필수 예약 경고."""
    findings = []
    turned_on = set()
    for name, state in states.items():
        if state != "Disabled":
            turned_on.add(name)
    for old_name, new_name in DUPLICATE_TASKS:
        if old_name in turned_on and new_name in turned_on:
            findings.append(
                Finding("warn", f"예약 중복: {old_name} 와 {new_name} 가 둘 다 켜져 있어요")
            )
    for name in REQUIRED_TASKS:
        if name not in turned_on:
            findings.append(Finding("warn", f"꼭 필요한 예약이 꺼져 있거나 없어요: {name}"))
    return findings


def vega_issues(expected: Mapping[str, str], actual: Mapping[str, Decimal]) -> list[Finding]:
    """[쓰임] Vega 장부의 '있어야 할' 코인(마지막 실행 뒤)보다 실제 코인이 1% 넘게 적으면 경고.
    [이유] 장부 밖에서 팔린 코인(10-16 시험이 실계좌 코인을 판 일)을 다음 날 바로 알아채려고."""
    findings = []
    for coin, expected_text in expected.items():
        if coin in CASH_CURRENCIES:
            continue
        expected_qty = Decimal(str(expected_text))
        actual_qty = actual.get(coin, Decimal(0))
        if expected_qty > 0 and actual_qty < expected_qty * (1 - COIN_TOLERANCE):
            text = (
                f"Vega {coin} 가 장부보다 적어요 ({actual_qty:f} < {expected_qty:f})"
                " — 장부 밖에서 팔렸나요?"
            )
            findings.append(Finding("warn", text))
    return findings


def holding_issues(
    label: str, ledger: Mapping[str, Decimal], holdings: Mapping[str, Decimal]
) -> list[Finding]:
    """[쓰임] 장부 수량이 토스 실제 보유보다 많은 종목마다 경고 (앱에서 팔았거나 장부 오류).
    [이유] 장부가 실제보다 많으면 실행기가 없는 주식을 팔려 하거나 비중을 틀리게 잡는다."""
    findings = []
    for symbol, ledger_qty in sorted(ledger.items()):
        held_qty = holdings.get(symbol, Decimal(0))
        if ledger_qty > 0 and held_qty < ledger_qty:
            text = f"{label} {symbol} 장부 {ledger_qty:f} > 토스 보유 {held_qty:f}"
            findings.append(Finding("warn", text))
    return findings


def report_text(findings: list[Finding], failed: list[str]) -> str | None:
    """[쓰임] 텔레그램으로 보낼 글. 경고 · 바뀐 것 · 확인 실패가 없으면 None (보내지 않음)."""
    warnings = [finding.text for finding in findings if finding.level == "warn"]
    infos = [finding.text for finding in findings if finding.level == "info"]
    if not (warnings or infos or failed):
        return None
    lines = ["🛡️ 매일 안전 점검"]
    if warnings:
        lines += ["", "⚠️ 확인 필요", *(f"• {text}" for text in warnings)]
    if infos:
        lines += ["", "ℹ️ 어제와 달라진 것", *(f"• {text}" for text in infos)]
    if failed:
        lines += ["", "❔ 확인 못 한 것", *(f"• {text}" for text in failed)]
    return "\n".join(lines)
