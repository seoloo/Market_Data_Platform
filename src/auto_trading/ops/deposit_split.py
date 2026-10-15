"""달마다 넣을 돈(합계)을 Deneb · Vega · Altair(옛 AG4S2)으로 나눠 알리기 — 시안 A.

사용자 2026-10-15 요청.

사용자: "달마다 얼마씩 넣을지 지금처럼 알림을 주되, 그 금액으로 분배를 하려면 얼마로 각각
입금해야하는지 알려주면 좋겠어" → 시안 A(목표 비율대로 단순히 나누기) 선택.

순수 함수만. 돈을 옮기거나 주문하지 않는다 — 금액만 계산해 알린다. 입금은 사용자가 직접 한다.
Deneb 몫은 그달 Deneb 매수 금액이 된다(토스).
Vega · Altair 몫은 바이낸스 현물 · USDⓈ-M 선물로 보낼 원화다.
"""

from __future__ import annotations

from collections.abc import Mapping

__all__ = ["MIX", "ask_text", "default_total", "split", "split_text"]

MIX: tuple[tuple[str, float], ...] = (("Deneb", 0.80), ("Vega", 0.10), ("Altair", 0.10))
"""목표 비율 (사용자 2026-10-15 "80 10 10"). 합 1.

Altair = 옛 AG4S2 (사용자 2026-10-15 이름)."""
UNIT = 1_000
"""원 단위 반올림 — 나머지는 Deneb 에 붙인다(합계가 정확히 맞게)."""
_WHERE = {
    "Deneb": ("🌟", "토스 증권", "오늘 밤 이 금액으로 매수해요"),
    "Vega": ("🌌", "바이낸스 현물", ""),
    "Altair": ("☄️", "바이낸스 선물(USDⓈ-M)", "현물로 보내면 자동 이체 (선물 몫 500 USDT 부터)"),
}


def _weight(name: str) -> float:
    return dict(MIX)[name]


def split(total_krw: float) -> dict[str, float]:
    """합계 → 곳마다 원화. Deneb 이 아닌 곳은 1,000 원 단위로 내리고 남는 돈은 Deneb 으로."""
    out: dict[str, float] = {}
    for name, w in MIX:
        if name != "Deneb":
            out[name] = float(int(total_krw * w // UNIT) * UNIT)
    out["Deneb"] = float(total_krw - sum(out.values()))
    return {name: out[name] for name, _ in MIX}


def default_total(glide_krw: float) -> float:
    """답이 없을 때의 합계 — 기본 Deneb 금액(설정)을 Deneb 비율로 나눈 값."""
    return glide_krw / _weight("Deneb")


def _mix_label() -> str:
    return " · ".join(f"{n} {w * 100:.0f}" for n, w in MIX)


def ask_text(today: str, glide_default_krw: float) -> str:
    """매수 당일 아침 — 이번 달 넣을 돈(합계)을 묻는다."""
    from auto_trading.notify.style import RULE, sub

    total = default_total(glide_default_krw)
    return "\n".join(
        [
            f"💰 {today[:4]}년 {int(today[5:7])}월 입금 안내 · 목표 {_mix_label()}",
            RULE,
            "이번 달 세 곳에 넣을 돈(합계)을 알려 주세요",
            sub("예시 답: 100만  (0 = 이번 달 쉬기)"),
            sub(f"답이 없으면 합계 {total:,.0f}원 기준"),
            sub(f"그때는 Deneb {glide_default_krw:,.0f}원만 삽니다"),
            sub(f"🌟 Deneb 은 오늘({today}) 밤 그 몫으로 매수해요"),
        ]
    )


def split_text(month: str, total_krw: float, usdt_krw: float | None) -> str:
    """답을 받은 뒤 — 곳마다 얼마를 어디에 넣을지.

    ``usdt_krw`` = 업비트 USDT 원화 값(없으면 생략)."""
    from auto_trading.notify.style import RULE, sub

    y, m = month[:4], int(month[5:7])
    parts = split(total_krw)
    lines = [f"💰 {y}년 {m}월 입금 {total_krw:,.0f}원 → 이렇게 넣어 주세요", RULE]
    usdt: Mapping[str, float] = {}
    if usdt_krw:
        usdt = {n: v / usdt_krw for n, v in parts.items() if n != "Deneb"}
    for name, krw in parts.items():
        icon, where, note = _WHERE[name]
        tail = f" ≈ {usdt[name]:,.0f} USDT" if name in usdt else ""
        lines.append(f"{icon} {name} · {where}  {krw:,.0f}원{tail}")
        if note:
            lines.append(sub(note))
    coin = sum(v for n, v in parts.items() if n != "Deneb")
    if coin > 0:
        lines += ["", RULE, "🧭 바이낸스로 보내는 법"]
        lines.append(sub(f"업비트에서 USDT {coin:,.0f}원어치 사서 바이낸스 현물로 보내기"))
        lines.append(sub("Vega · Altair 몫을 모두 현물로 보내면 비율대로 자동으로 나눠요"))
        if usdt_krw:
            lines.append(sub(f"USDT 값: 업비트 {usdt_krw:,.0f}원 기준"))
    lines += ["", "✏️ 금액을 바꾸려면 오늘 21:00 전에 다시 보내 주세요"]
    return "\n".join(lines)
