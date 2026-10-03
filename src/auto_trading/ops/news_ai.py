"""뉴스·AI 판단 기록 (문서 115 2 단계) — 질문 만들기·답 검사는 순수 함수, 호출은 스크립트.

AI(ChatGPT)에게 **그날 모은 뉴스 제목만** 주고 "주도 섹터·주도주·증시가 더 오를 여지" 를 묻는다.
기계 규칙(113)의 주도주와 나란히 기록해 몇 달 뒤 채점한다. 매매에는 쓰지 않는다.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from typing import Any

MAX_HEADLINES = 150
MAX_SUMMARY = 120
ROOM = ("up", "neutral", "down")

SYSTEM = (
    "You are a disciplined US equity market analyst. Use ONLY the headlines provided below, "
    "which were collected at the stated time. Do not use or assume knowledge of any events after "
    "that time. Pick tickers ONLY from the provided S&P 500 universe. Answer in strict JSON with "
    'keys: "leading_sectors" (list of up to 3 GICS sector names), "leaders" (list of up to 10 '
    'objects {"symbol": ticker, "reason": short reason in Korean}), "market_room" (one of "up", '
    '"neutral", "down" — whether US stocks still have room to rise over the next 1-3 months), '
    '"confidence" (0 to 1), "risks" (up to 5 short strings in Korean), "summary" (2-3 sentences '
    "in Korean)."
)


def build_messages(
    report: Mapping[str, Any], items: Sequence[Mapping[str, Any]], universe: Sequence[str]
) -> list[dict[str, str]]:
    """보고서(기계 규칙 결과)·그날 뉴스·유니버스로 대화 메시지를 만든다."""
    fetched = max((str(i.get("fetched_at", "")) for i in items), default="")
    lines = []
    for it in items[:MAX_HEADLINES]:
        summary = str(it.get("summary", ""))[:MAX_SUMMARY]
        lines.append(
            f"- [{it.get('source')}] {it.get('title')}" + (f" — {summary}" if summary else "")
        )
    mech = ", ".join(str(x.get("symbol")) for x in report.get("leaders") or [])
    sectors = ", ".join(str(s) for s in report.get("leading_sectors") or [])
    user = (
        f"Headlines collected at: {fetched}\n"
        f"Last close date: {report.get('date')}\n"
        f"Mechanical rule (momentum ranking) leaders: {mech}\n"
        f"Mechanical rule leading sectors: {sectors}\n"
        f"S&P 500 above 200-day average: {report.get('spy_above_200')}; "
        f"share of S&P 500 stocks above 50-day average: {report.get('breadth_above_50ma')}\n"
        f"S&P 500 universe: {' '.join(universe)}\n\n"
        "Headlines:\n" + "\n".join(lines)
    )
    return [{"role": "system", "content": SYSTEM}, {"role": "user", "content": user}]


def parse_reply(text: str, universe: Sequence[str]) -> dict[str, Any]:
    """AI 답(JSON) 검사·정리. 유니버스 밖 종목은 따로 표시하고 뺀다. 틀린 형식이면 ValueError."""
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError("AI 답이 JSON 이 아니다") from exc
    if not isinstance(data, dict):
        raise ValueError("AI 답이 객체가 아니다")
    allowed = set(universe)
    leaders, outside = [], []
    for x in data.get("leaders") or []:
        if not isinstance(x, dict):
            continue
        sym = str(x.get("symbol", "")).upper().strip()
        if sym in allowed:
            leaders.append({"symbol": sym, "reason": str(x.get("reason", ""))[:200]})
        elif sym:
            outside.append(sym)
    room = str(data.get("market_room", "")).lower()
    if room not in ROOM:
        raise ValueError("market_room 이 up·neutral·down 이 아니다")
    try:
        confidence = min(1.0, max(0.0, float(data.get("confidence", 0))))
    except (TypeError, ValueError):
        confidence = 0.0
    return {
        "leading_sectors": [str(s) for s in (data.get("leading_sectors") or [])][:3],
        "leaders": leaders[:10],
        "outside_universe": outside,
        "market_room": room,
        "confidence": round(confidence, 3),
        "risks": [str(r)[:200] for r in (data.get("risks") or [])][:5],
        "summary": str(data.get("summary", ""))[:600],
    }


def compare(mechanical: Sequence[str], ai: Sequence[str]) -> dict[str, Any]:
    """기계 규칙과 AI 주도주의 겹침."""
    m, a = set(mechanical), set(ai)
    return {"both": sorted(m & a), "only_mechanical": sorted(m - a), "only_ai": sorted(a - m)}
