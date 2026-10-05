"""국장 주도주·테마 그림자 기록 (문서 143) — AI 질문 만들기는 순수 함수, 호출은 스크립트.

AI 에게 **그날 모은 한국 경제 뉴스 제목과 유가증권시장 종목군(코드·이름)만** 주고
"주도 테마·주도주·KOSPI 가 더 오를 여지" 를 묻는다. 기계 규칙(141 가격 · 142 수급)의 목록과
가격 지표는 일부러 주지 않는다 — 베끼면 비교가 안 된다(115 에서 실제로 베꼈다).
매매에는 쓰지 않는다.
답 검사는 미국판과 같은 :func:`auto_trading.ops.news_ai.parse_reply` 를 쓴다(코드가 종목군 안인지).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

MAX_HEADLINES = 150
MAX_SUMMARY = 120

SYSTEM = (
    "당신은 규율 있는 한국 주식시장 분석가다. 아래에 주어진 뉴스 제목만 쓴다. "
    "적힌 수집 시각 뒤의 일은 알지 못하는 것으로 하고, 그 뒤 사건을 짐작해 쓰지 않는다. "
    "종목은 반드시 주어진 유가증권시장 종목군(6 자리 코드)에서만 고른다. "
    "엄격한 JSON 으로만 답한다. 키: "
    '"leading_sectors" (지금 주도하는 테마·섹터 최대 3 개, 한국어 짧은 이름), '
    '"leaders" (최대 10 개, {"symbol": 6 자리 코드, "reason": 한국어 짧은 이유}), '
    '"market_room" ("up" · "neutral" · "down" 중 하나 — 앞으로 1~3 달 KOSPI 가 더 오를 여지), '
    '"confidence" (0~1), "risks" (한국어 짧은 문장 최대 5 개), "summary" (한국어 2~3 문장).'
)


def build_messages(
    day: str,
    items: Sequence[Mapping[str, Any]],
    universe: Mapping[str, str],
) -> list[dict[str, str]]:
    """``universe`` = {코드: 종목명}. 뉴스는 최대 150 건, 요약은 120 자까지."""
    fetched = max((str(i.get("fetched_at", "")) for i in items), default="")
    lines = []
    for it in items[:MAX_HEADLINES]:
        summary = str(it.get("summary", ""))[:MAX_SUMMARY]
        lines.append(
            f"- [{it.get('source')}] {it.get('title')}" + (f" — {summary}" if summary else "")
        )
    names = " · ".join(f"{code} {name}" for code, name in sorted(universe.items()))
    user = (
        f"뉴스 수집 시각: {fetched}\n"
        f"마지막 한국 증시 종가 날짜: {day}\n"
        f"유가증권시장 종목군 (거래대금 상위 {len(universe)}): {names}\n\n"
        "뉴스 제목:\n" + "\n".join(lines)
    )
    return [{"role": "system", "content": SYSTEM}, {"role": "user", "content": user}]
