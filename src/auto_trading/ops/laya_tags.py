"""Laya 기사 분류 — 시장 브리핑 분위기 · 관계없는 기사 거르기, 주간 테마 기사 수.

사용자 2026-10-11 요청.

순수 함수만. 질문은 영어(사용자: 영어가 더 빠르고 정확), 보이는 이름은 한국어.
정보용 — 매매 · 주문과 무관하다.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

__all__ = [
    "BRIEF_Q",
    "DROP_MIN",
    "THEMES",
    "THEME_Q",
    "brief_line",
    "brief_tally",
    "theme_counts",
    "theme_lines",
]

DROP_MIN = 0.85
"""브리핑에서 '관계없음' 이 이 확률 이상일 때만 기사를 뺀다 (중요한 기사를 잘못 빼지 않게)."""

BRIEF_Q: dict[str, Any] = {
    "relevant": {
        "type": "choice",
        "instructions": "Is this headline relevant to the US stock market today?",
        "criteria": {
            "market": "about US stocks, the economy, interest rates, the Fed, or large listed "
            "companies",
            "other": "not relevant to US stocks (sports, lifestyle, local news, unrelated topics)",
        },
    },
    "impact": {
        "type": "choice",
        "instructions": "Is this headline good, bad or neutral for US stock prices?",
        "criteria": {
            "good": "positive for US stocks",
            "bad": "negative for US stocks",
            "neutral": "mixed, unclear, or no price impact",
        },
    },
}
_MOOD = {"good": ("🟢", "호재"), "bad": ("🔴", "악재"), "neutral": ("⚪", "중립")}


def brief_tally(
    labels: Sequence[tuple[tuple[str, float] | None, tuple[str, float] | None]],
) -> tuple[list[bool], dict[str, Any]]:
    """기사마다 (관계, 영향) 답 → (남길지, 요약). 답이 없는 기사는 남긴다."""
    keep: list[bool] = []
    count = {"good": 0, "bad": 0, "neutral": 0}
    dropped = 0
    for rel, imp in labels:
        if rel is not None and rel[0] == "other" and rel[1] >= DROP_MIN:
            keep.append(False)
            dropped += 1
            continue
        keep.append(True)
        if imp is not None and imp[0] in count:
            count[imp[0]] += 1
    judged = sum(count.values())
    return keep, {**count, "judged": judged, "dropped": dropped}


def brief_line(t: Mapping[str, Any]) -> str:
    """브리핑 텔레그램 한 줄 (판단이 없으면 빈 문자열)."""
    if not t or not t.get("judged"):
        return ""
    parts = " · ".join(f"{_MOOD[k][0]} {_MOOD[k][1]} {t[k]}" for k in ("good", "bad", "neutral"))
    drop = f" · 관계없는 기사 {t['dropped']}건 뺌" if t.get("dropped") else ""
    return f"🧪 Laya 기사 분위기 ({t['judged']}건): {parts}{drop}"


THEMES: dict[str, tuple[str, str]] = {
    "ai_semis": ("AI · 반도체", "artificial intelligence, AI models, chips, semiconductors, GPUs"),
    "cloud_dc": ("클라우드 · 데이터센터", "cloud computing, data centers, software infrastructure"),
    "quantum": ("양자컴퓨터", "quantum computing"),
    "ev_battery": ("전기차 · 배터리", "electric vehicles, batteries, autonomous driving"),
    "energy": ("에너지 · 원자력 · 전력", "oil, gas, nuclear, power utilities, renewables, grid"),
    "bio": ("바이오 · 헬스케어", "biotech, pharma, healthcare, medical devices, weight-loss drugs"),
    "finance": (
        "금융 · 핀테크",
        "specific banks, insurers, payment or fintech companies, brokers",
    ),
    "crypto": ("코인 · 블록체인", "bitcoin, crypto, blockchain, stablecoins"),
    "defense_space": ("방산 · 우주", "defense, military, aerospace, space, drones"),
    "consumer": (
        "소비 · 유통",
        "retail, consumer goods, restaurants, travel, media, entertainment",
    ),
    "macro": ("거시 · 정책", "the Fed, inflation, jobs, tariffs, government policy, the economy"),
    "market": ("시장 전반", "general stock market wrap, index moves, futures, investing tips"),
    "other": ("기타", "none of the above"),
}
UNCOUNTED = frozenset({"market", "other"})
"""세지 않는 칸 — 시장 전체 소식 · 기타는 테마가 아니다."""
THEME_Q: dict[str, Any] = {
    "theme": {
        "type": "choice",
        "instructions": "Which investment theme is this news headline mainly about?",
        "criteria": {k: v[1] for k, v in THEMES.items()},
    }
}


def theme_counts(
    labels: Sequence[tuple[str, float] | None], min_prob: float = 0.5
) -> dict[str, int]:
    """테마 → 기사 수 (확률 ``min_prob`` 미만 · 시장 전반 · 기타 · 답 없음은 세지 않는다)."""
    out: dict[str, int] = {}
    for lab in labels:
        if lab is None or lab[0] not in THEMES or lab[0] in UNCOUNTED or lab[1] < min_prob:
            continue
        out[lab[0]] = out.get(lab[0], 0) + 1
    return out


def theme_lines(
    now: Mapping[str, int], last: Mapping[str, int] | None, total: int, top: int = 6
) -> list[str]:
    """주간 테마 메시지에 붙일 줄 — 기사 수 많은 순, 지난주 대비 증감."""
    if not now:
        return []
    lines = [f"🧪 Laya 테마별 기사 수 (미국 기사 {total}건 분류)"]
    for k, n in sorted(now.items(), key=lambda x: -x[1])[:top]:
        diff = ""
        if last is not None:
            d = n - int(last.get(k, 0))
            diff = f" (지난주 대비 {'▲' if d > 0 else '▼' if d < 0 else '＝'}{abs(d) if d else ''})"
        lines.append(f"• {THEMES[k][0]} {n}건{diff}")
    lines.append("※ 기사 수는 관심의 크기일 뿐, 수익과 무관해요")
    return lines
