"""💹 월간 미장 주도주 추천 · 매도 신호 (I/O 없음).

사용자 (2026-10-17): 맛코 분석 → 미장(S&P 500 + 나스닥 100) 주도주 추천 →
"한 달에 한번만 … 월말에 추천".

* 고르는 법 = 연구 113 기본(261 ``KEEP_BASE``): 1 · 3 · 6 달 수익 순위 점수 → 주도 섹터 3 →
  점수 상위 10. S&P 500 이 200 일선 아래면 새로 추천하지 않는다(113 · 237).
  시가총액 · 실적은 카드의 참고 정보일 뿐이다.
* 매도 신호 = 258 의 ARM: 산 뒤(산 날 포함) 종가로 추세가 한 번 확인된 종목만 — 추세 꺼짐
  (20 · 60 · 120 일 부호 합 ≤ 0) 또는 산 뒤 최고 종가 −35%. −3% 손절은 :mod:`stop_guard` 가
  따로 자동으로 한다.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from auto_trading.ops.lt_paper import LOOKS, leaders, spy_ok, trend_off, trend_up

__all__ = [
    "PEAK_STOP",
    "Pick",
    "Reco",
    "is_month_end",
    "reco_text",
    "recommend",
    "sell_signal",
    "sell_text",
]

Closes = Sequence[float | None]
PEAK_STOP = 0.35
SPY_SMA = 200
SECTOR_KO = {
    "Information Technology": "IT",
    "Health Care": "헬스케어",
    "Energy": "에너지",
    "Financials": "금융",
    "Industrials": "산업재",
    "Consumer Discretionary": "임의소비재",
    "Consumer Staples": "필수소비재",
    "Communication Services": "통신 서비스",
    "Materials": "소재",
    "Utilities": "유틸리티",
    "Real Estate": "부동산",
}


@dataclass(frozen=True, slots=True)
class Pick:
    symbol: str
    sector: str
    score: float
    rets: tuple[float, float, float]
    """1 · 3 · 6 달(21 · 63 · 126 거래일) 수익."""
    trend: bool


@dataclass(frozen=True, slots=True)
class Reco:
    day: str
    market_ok: bool
    spy_gap: float
    """S&P 500(SPY) 종가 ÷ 200 일선 − 1."""
    sectors: list[str]
    picks: list[Pick]


def _ret(xs: Closes, t: int, k: int) -> float:
    a, b = xs[t], xs[t - k] if t - k >= 0 else None
    return a / b - 1 if a is not None and b is not None and b > 0 else 0.0


def recommend(
    days: Sequence[str],
    closes: Mapping[str, Closes],
    sectors: Mapping[str, str],
    spy: Closes,
    t: int,
) -> Reco:
    """``t`` (월말) 종가 기준 추천. 추세가 아닌 주도주는 목록에 남기되 ``trend`` 가 거짓."""
    lead, top = leaders(closes, sectors, t)
    window = [v for v in spy[t - SPY_SMA + 1 : t + 1] if v is not None]
    now = spy[t]
    gap = now / (sum(window) / len(window)) - 1 if now and len(window) == SPY_SMA else 0.0
    picks = [
        Pick(
            s,
            sectors.get(s, "Unknown"),
            sc,
            (
                _ret(closes[s], t, LOOKS[0]),
                _ret(closes[s], t, LOOKS[1]),
                _ret(closes[s], t, LOOKS[2]),
            ),
            trend_up(closes[s], t),
        )
        for s, sc in lead
    ]
    return Reco(days[t], spy_ok(spy, t), gap, list(top), picks)


def is_month_end(day: str, next_session: str) -> bool:
    """``day`` 다음 정규장이 다른 달이면 ``day`` 가 그달 마지막 거래일."""
    return day[:7] != next_session[:7]


def sell_signal(xs: Closes, i0: int, t: int) -> tuple[str, float] | None:
    """산 날 ``i0`` 부터 ``t`` 까지 종가로 — 추세가 한 번 확인됐으면 오늘 추세 꺼짐(``trend``) ·
    산 뒤 최고 종가 −35%(``peak35``). (이유, 최고 종가) 또는 None."""
    if t < i0 or xs[t] is None:
        return None
    armed = any(trend_up(xs, k) for k in range(i0, t + 1) if xs[k] is not None)
    if not armed:
        return None
    peak = max(v for v in xs[i0 : t + 1] if v is not None)
    now = xs[t]
    assert now is not None
    if now <= (1 - PEAK_STOP) * peak:
        return "peak35", peak
    if trend_off(xs, t):
        return "trend", peak
    return None


def _usd(v: float) -> str:
    return f"${v:,.2f}"


def _pct(v: float) -> str:
    return f"{v:+.0%}"


def reco_text(
    reco: Reco,
    month: str,
    *,
    detail: Mapping[str, Sequence[str]] | None = None,
    names: Mapping[str, str] | None = None,
    held: Sequence[str] = (),
    dropped: Sequence[str] = (),
    show: int = 3,
) -> str:
    """월간 카드 글. ``detail`` = 종목 → 덧붙일 줄(시총 · 실적 · 💹 수량)."""
    detail = detail or {}
    names = names or {}
    head = [
        f"💹 [미장 주도주 · {month}]  {reco.day[5:].replace('-', '/')} 종가 기준",
        "━━━━━━━━━━━━",
    ]
    sec = " · ".join(SECTOR_KO.get(s, s) for s in reco.sectors)
    if not reco.market_ok:
        return "\n".join(
            [
                *head,
                f"🧭 시장: S&P500 200일선 아래 ({reco.spy_gap:+.1%}) ⛔ 이번 달은 새로 사지 않아요",
                "   (연구 113·237: 시장이 약할 때 사면 크게 잃었음)",
                f"🔥 참고 — 주도 섹터: {sec}",
                "📦 들고 있는 종목은 각자 매도 규칙대로 지켜봐요",
            ]
        )
    lines = [
        *head,
        f"🧭 시장: S&P500 200일선 위 ({reco.spy_gap:+.1%}) ✅ 새 매수 가능",
        f"🔥 주도 섹터: {sec}",
        "",
        "⭐ 이번 달 주도주 (점수 순)",
    ]
    marks = ["1️⃣", "2️⃣", "3️⃣", "4️⃣", "5️⃣", "6️⃣", "7️⃣", "8️⃣", "9️⃣", "🔟"]
    for i, p in enumerate(reco.picks[:show]):
        nm = names.get(p.symbol, "")
        lines.append(
            f"{marks[i]} {p.symbol}{' ' + nm if nm and nm != p.symbol else ''}"
            f" · {SECTOR_KO.get(p.sector, p.sector)}{'' if p.trend else ' · ⚠ 추세 아님'}"
        )
        lines.append(f"   └ 1달 {_pct(p.rets[0])} · 3달 {_pct(p.rets[1])} · 6달 {_pct(p.rets[2])}")
        lines.extend(f"   └ {x}" for x in detail.get(p.symbol, []))
    if len(reco.picks) > show:
        lines.append(f"   … {show + 1} ~ {len(reco.picks)}위는 [📋 전부 보기]")
    lines.append("")
    lines.append(f"📦 들고 있는 것: {', '.join(held) if held else '없음'}")
    lines.append(f"🧹 이번 달 목록에서 빠진 것: {', '.join(dropped) if dropped else '없음'}")
    lines.append("")
    lines.append("💬 연구 113·261 근거 (2011~2026 연 15% · 생존 편향 있음) — 수익 보장 아님")
    return "\n".join(lines)


def sell_text(
    name: str, qty: float, avg: float, last: float, peak: float, reason: str, fx: float
) -> str:
    gain = (last - avg) * qty
    won = gain * fx
    why = (
        "📉 추세 꺾임 — 20 · 60 · 120일 수익 중 2개 이상이 마이너스"
        if reason == "trend"
        else f"📉 고점 −35% — 산 뒤 최고 {_usd(peak)} → 지금 {_usd(last)}"
    )
    sign = "+" if gain >= 0 else "-"
    return "\n".join(
        [
            f"💹 [매도 신호] {name} · {qty:g}주",
            "━━━━━━━━━━━━",
            why,
            f"   └ 산 값 {_usd(avg)} → 지금 {_usd(last)} ({last / avg - 1:+.1%},"
            f" 약 {sign}${abs(gain):,.0f} · {sign}{abs(won) / 10_000:,.1f}만 원)",
            f"   └ 산 뒤 최고 {_usd(peak)} (지금 {last / peak - 1:+.1%})",
            "연구 규칙: 다음 정규장에서 파는 것 (113·258) — −3% 손절은 따로 자동",
        ]
    )
