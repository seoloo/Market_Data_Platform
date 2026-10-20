"""매일 안전 점검 — 실주문 쪽이 '모르는 사이' 바뀌지 않았는지 (I/O 없음, 판단만).

사용자 2026-10-16 "1 -> 3 -> 2 이렇게 진행해줘" 의 1번. 같은 날 문제들(시험이 실계좌 Vega
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


@dataclass(frozen=True, slots=True)
class Finding:
    level: str
    """warn (⚠️ 확인 필요) · info (ℹ️ 바뀐 것)."""
    text: str


def state_changes(prev: Mapping[str, str], now: Mapping[str, str], label: str) -> list[Finding]:
    """어제 기록과 다른 값 (처음이면 없음)."""
    if not prev:
        return []
    out = []
    for k in sorted(set(prev) | set(now)):
        a, b = prev.get(k, "-"), now.get(k, "-")
        if a != b:
            out.append(Finding("info", f"{label} {k}: {a} → {b}"))
    return out


def key_issues(r: Mapping[str, object]) -> list[Finding]:
    """바이낸스 ``/sapi/v1/account/apiRestrictions`` → 위험한 권한."""
    out = []
    if r.get("enableWithdrawals"):
        out.append(Finding("warn", "바이낸스 키에 출금 권한이 켜져 있어요 — 바로 끄세요"))
    if not r.get("ipRestrict"):
        out.append(Finding("warn", "바이낸스 키 IP 제한이 풀려 있어요"))
    return out


def futures_issues(positions: Mapping[str, Decimal]) -> list[Finding]:
    out = []
    if positions:
        held = ", ".join(f"{s} {q}" for s, q in sorted(positions.items()))
        out.append(Finding("warn", f"Altair 은퇴 뒤인데 바이낸스 선물 포지션이 있어요: {held}"))
    return out


def task_issues(states: Mapping[str, str]) -> list[Finding]:
    """예약 이름 → 상태(Ready · Running · Disabled …)."""
    out = []
    on = {n for n, s in states.items() if s != "Disabled"}
    for old, new in DUPLICATE_TASKS:
        if old in on and new in on:
            out.append(Finding("warn", f"예약 중복: {old} 와 {new} 가 둘 다 켜져 있어요"))
    for name in REQUIRED_TASKS:
        if name not in on:
            out.append(Finding("warn", f"꼭 필요한 예약이 꺼져 있거나 없어요: {name}"))
    return out


def vega_issues(expected: Mapping[str, str], actual: Mapping[str, Decimal]) -> list[Finding]:
    """Vega 장부(마지막 실행 뒤 '있어야 할' 잔고)보다 코인이 적으면 장부 밖 매도."""
    out = []
    for coin, exp in expected.items():
        if coin in ("USDT", "KRW"):  # 현금은 입출금으로 늘고 줄어 비교하지 않는다
            continue
        want, got = Decimal(str(exp)), actual.get(coin, Decimal(0))
        if want > 0 and got < want * (1 - COIN_TOLERANCE):
            out.append(
                Finding(
                    "warn",
                    f"Vega {coin} 가 장부보다 적어요 ({got:f} < {want:f}) — 장부 밖에서 팔렸나요?",
                )
            )
    return out


def holding_issues(
    label: str, ledger: Mapping[str, Decimal], holdings: Mapping[str, Decimal]
) -> list[Finding]:
    """장부 수량이 토스 실제 보유보다 많으면 (앱에서 팔았거나 장부 오류)."""
    out = []
    for sym, q in sorted(ledger.items()):
        if q > 0 and holdings.get(sym, Decimal(0)) < q:
            out.append(
                Finding(
                    "warn",
                    f"{label} {sym} 장부 {q:f} > 토스 보유 {holdings.get(sym, Decimal(0)):f}",
                )
            )
    return out


def report_text(findings: list[Finding], failed: list[str]) -> str | None:
    """보낼 글 (경고 · 바뀐 것 · 확인 실패가 없으면 None)."""
    warns = [f.text for f in findings if f.level == "warn"]
    infos = [f.text for f in findings if f.level == "info"]
    if not (warns or infos or failed):
        return None
    lines = ["🛡️ 매일 안전 점검"]
    if warns:
        lines += ["", "⚠️ 확인 필요", *(f"• {w}" for w in warns)]
    if infos:
        lines += ["", "ℹ️ 어제와 달라진 것", *(f"• {i}" for i in infos)]
    if failed:
        lines += ["", "❔ 확인 못 한 것", *(f"• {x}" for x in failed)]
    return "\n".join(lines)
