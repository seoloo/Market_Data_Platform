"""입금 나누기 안내 — 달마다 넣을 돈(합계)을 Deneb · Vega 로 나눠 알리는 글 (시안 A, 계산만).

언제 쓰나: 매수 당일 아침 금액 묻기 · 텔레그램 금액 답장 (``scripts/toss_accumulate.py``).
읽고 쓰는 것: 없음 — 돈을 옮기거나 주문하지 않는다. 금액만 계산해 알리고, 입금은 사용자가 직접 한다.

[이유] 사용자 2026-10-15 "달마다 얼마씩 넣을지 지금처럼 알림을 주되, 그 금액으로 분배를 하려면
       얼마로 각각 입금해야하는지 알려주면 좋겠어" → 시안 A(목표 비율대로 단순히 나누기).

* Deneb 몫은 그달 Deneb 매수 금액이 된다(토스).
* Vega 몫은 업비트에 넣을 원화다 — 2026-10-19 사용자 "altair는 정리하고 vega를 업비트로 옮기도록
  하자 · deneb:vega = 90:10" (그 전: 80 · 10 · 10, Vega · Altair 는 바이낸스).
"""

from __future__ import annotations

__all__ = ["MIX", "ask_text", "default_total", "split", "split_text", "vega_line"]

MIX: tuple[tuple[str, float], ...] = (("Deneb", 0.90), ("Vega", 0.10))
"""목표 비율 (사용자 2026-10-19 "deneb:vega = 90:10"). 합 1. Altair 는 정리(빠짐)."""
UNIT = 1_000
"""원 단위 반올림 — 나머지는 Deneb 에 붙인다(합계가 정확히 맞게)."""
_WHERE = {
    "Deneb": ("🌟", "토스 증권", "오늘 밤 이 금액으로 매수해요"),
    "Vega": ("🌌", "업비트 원화", "넣어 두면 다음 09:05 Vega 실행이 입금으로 알아채요"),
}
"""곳 → (아이콘, 어디에 넣나, 한 줄 안내)."""


def _weight(name: str) -> float:
    """[쓰임] 그곳의 목표 비율 (예: Deneb 0.9)."""
    return dict(MIX)[name]


def split(total_krw: float) -> dict[str, float]:
    """[쓰임] 합계 → 곳마다 원화 {Deneb: …, Vega: …}.
    [이유] Deneb 이 아닌 곳은 1,000 원 단위로 내리고 남는 돈은 Deneb 으로 — 합계가 정확히 맞게."""
    amounts: dict[str, float] = {}
    for name, weight in MIX:
        if name != "Deneb":
            amounts[name] = float(int(total_krw * weight // UNIT) * UNIT)
    amounts["Deneb"] = float(total_krw - sum(amounts.values()))
    return {name: amounts[name] for name, _ in MIX}


def default_total(deneb_krw: float) -> float:
    """[쓰임] 답이 없을 때의 합계 — 기본 Deneb 금액(설정)을 Deneb 비율로 나눈 값."""
    return deneb_krw / _weight("Deneb")


def vega_line(deneb_krw: float) -> str:
    """[쓰임] Deneb 금액 → 같은 비율로 업비트(Vega)에 넣을 원화 한 줄.
    [이유] 사용자 2026-10-19 'deneb 입금 알림오면 … 얼마 입금해야하는지도 알려줘'
           (같은 날 바이낸스 80 : 20 → 업비트 90 : 10)."""
    vega_krw = split(default_total(deneb_krw))["Vega"]
    deneb_percent = _weight("Deneb") * 100
    return (
        f"🌌 업비트(Vega)엔 {vega_krw:,.0f}원 — Deneb : Vega = {deneb_percent:.0f}"
        f" : {100 - deneb_percent:.0f}"
    )


def _mix_label() -> str:
    """[쓰임] 'Deneb 90 · Vega 10'."""
    return " · ".join(f"{name} {weight * 100:.0f}" for name, weight in MIX)


def ask_text(
    today: str, deneb_default_krw: float, extra: str = "", usdt_krw: float | None = None
) -> str:
    """[쓰임] 매수 당일 아침 — 이번 달 넣을 돈(합계)을 묻는 글. ``extra`` = 맨 아래 줄(브레이크 등).

    ``usdt_krw`` 는 옛 바이낸스 때 쓰던 칸 — 지금은 쓰지 않는다(부르는 쪽 호환)."""
    from auto_trading.notify.style import RULE, sub

    total = default_total(deneb_default_krw)
    return "\n".join(
        [
            f"💰 {today[:4]}년 {int(today[5:7])}월 입금 안내 · 목표 {_mix_label()}",
            RULE,
            "이번 달 두 곳에 넣을 돈(합계)을 알려 주세요",
            sub("예시 답: 100만  (0 = 이번 달 쉬기)"),
            sub(f"답이 없으면 합계 {total:,.0f}원 기준"),
            sub(f"그때는 Deneb {deneb_default_krw:,.0f}원만 삽니다"),
            sub(vega_line(deneb_default_krw)),
            sub(f"🌟 Deneb 은 오늘({today}) 밤 그 몫으로 매수해요"),
            *(["", extra] if extra else []),
        ]
    )


def split_text(month: str, total_krw: float, usdt_krw: float | None) -> str:
    """[쓰임] 답을 받은 뒤 — 곳마다 얼마를 어디에 넣을지 글.

    ``usdt_krw`` 는 바이낸스로 보내던 때(~ 2026-10-19) 의 USDT 환산용 — 지금은 안 쓴다."""
    from auto_trading.notify.style import RULE, sub

    year, month_number = month[:4], int(month[5:7])
    lines = [f"💰 {year}년 {month_number}월 입금 {total_krw:,.0f}원 → 이렇게 넣어 주세요", RULE]
    for name, krw in split(total_krw).items():
        icon, where, note = _WHERE[name]
        lines.append(f"{icon} {name} · {where}  {krw:,.0f}원")
        if note:
            lines.append(sub(note))
    lines += ["", "✏️ 금액을 바꾸려면 오늘 21:00 전에 다시 보내 주세요"]
    return "\n".join(lines)
