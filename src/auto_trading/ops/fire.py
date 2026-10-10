"""파이어 진행 보고 — 매달 1 일, 실거래 장부 합계가 목표에 얼마나 왔나 (사용자 2026-10-10). 순수.

* 목표: 지금은 **1 억 원**(사용자 2026-10-10 "지금 현재 목표는 1억으로"). 바꾸려면 ``TARGET_KRW``.
* 평가: 대시보드 실거래 홈과 같은 장부 + 공개 시세(Glide · Cruise · 토스 적립).
  연금 · ISA 는 빠짐(장부 없음).
* 도달 예상은 **가정한 연수익 셋**으로 단순 복리 계산한 것이다. 예측이 아니다.
  보통 12% 는 Glide(195 · 196)와 김프 뺀 Cruise(200, 세후 16%)를 반반 섞은 대략값을 낮게 잡은 것.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date

TARGET_KRW = 100_000_000
SCENARIOS: tuple[tuple[str, str, float], ...] = (
    ("🟢", "좋을 때", 0.20),
    ("⚪", "보통", 0.12),
    ("🔴", "나쁠 때", 0.04),
)
MAX_MONTHS = 600


def months_to_target(value: float, monthly: float, annual: float, target: float) -> int | None:
    """매달 말 ``monthly`` 를 넣고 연 ``annual`` 로 불어날 때 목표까지 달 수 (못 닿으면 None)."""
    if value >= target:
        return 0
    r = (1 + annual) ** (1 / 12) - 1
    v = value
    for n in range(1, MAX_MONTHS + 1):
        v = v * (1 + r) + monthly
        if v >= target:
            return n
    return None


def monthly_rate(deposits: Sequence[tuple[str, float]], today: date) -> float:
    """지금까지 넣은 돈 ÷ 첫 입금부터 지난 달 수(최소 1). 넣은 기록이 없으면 0."""
    dated = [(d, x) for d, x in deposits if x > 0]
    if not dated:
        return 0.0
    first = date.fromisoformat(min(d for d, _ in dated)[:10])
    months = max(1.0, (today - first).days / 30.44)
    return sum(x for _, x in dated) / months


def _add_months(d: date, n: int) -> str:
    y, m = divmod(d.month - 1 + n, 12)
    return f"{d.year + y}-{m + 1:02d}"


def _span(n: int) -> str:
    y, m = divmod(n, 12)
    parts = ([f"{y}년"] if y else []) + ([f"{m}개월"] if m or not y else [])
    return " ".join(parts)


@dataclass(frozen=True, slots=True)
class Snapshot:
    deposits: float
    value: float


def report_text(
    today: date,
    now: Snapshot,
    monthly: float,
    last: Snapshot | None,
    target: float = TARGET_KRW,
) -> str:
    from auto_trading.notify.style import RULE, sub

    pct = now.value / target
    filled = min(10, int(pct * 10))
    bar = "▓" * filled + "░" * (10 - filled)
    pl = now.value - now.deposits
    pl_pct = f" ({pl / now.deposits:+.1%})" if now.deposits > 0 else ""
    lines = [
        f"🎯 [파이어 진행] {today:%Y-%m-%d}",
        RULE,
        f"목표 {target / 1e8:g}억 원",
        f"지금 평가 {now.value:,.0f}원",
        sub(f"입금 {now.deposits:,.0f}원 · 손익 {pl:+,.0f}원{pl_pct}"),
        f"진행 {bar} {pct:.1%}",
    ]
    if last is not None:
        grow = now.value - last.value
        added = now.deposits - last.deposits
        lines.append(
            sub(f"지난 보고보다 {grow:+,.0f}원 (입금 {added:+,.0f} · 시장 {grow - added:+,.0f})")
        )
    lines += ["", RULE]
    if monthly <= 0:
        lines.append("입금 기록이 아직 없어 도달 예상을 계산하지 못했어요")
    else:
        lines.append(f"이 속도(월 {monthly:,.0f}원 입금)면 목표까지")
        for icon, label, annual in SCENARIOS:
            n = months_to_target(now.value, monthly, annual, target)
            when = "50년 넘음" if n is None else f"{_add_months(today, n)} · {_span(n)}"
            lines.append(sub(f"{icon} {label}(연 {annual:.0%}) {when}"))
    lines += [
        "",
        "※ 세금 전 · 실거래 장부(Glide · Cruise · 토스 적립)만 · 연금 · ISA 제외",
        "※ 도달 시점은 가정한 연수익으로 낸 계산이지 예측이 아님",
    ]
    return "\n".join(lines)
