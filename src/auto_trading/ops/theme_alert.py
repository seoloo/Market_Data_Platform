"""주간 미래 주도 테마 알림 (문서 148) — 순수 함수. 매매하지 않는 정보 도구다.

지난 7 일 동안 모은 뉴스 **제목만**(번호를 붙여) AI 에게 주고, 앞으로 6~24 달 주도할 테마와 종목을
묻는다. 근거는 반드시 그 **뉴스 번호**로 대게 하고, 코드가 번호를 실제 제목으로 바꿔 붙인다 —
AI 가 지어낸 근거가 알림에 실리지 않게. 종목별 사실(추세·3 달 수익·주도주 목록 포함 여부)은
AI 가 아니라 우리 자료로 코드가 붙인다. 성적은 문서 147 장부로 따로 쌓인다.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from typing import Any

__all__ = [
    "MARKETS",
    "build_messages",
    "diff_weeks",
    "format_market",
    "number_headlines",
    "parse_weekly",
]

MARKETS = {"kr": "한국 유가증권시장", "us": "미국 S&P 500"}
MAX_HEADLINES = 200
MAX_THEMES = 3
MAX_PICKS = 5

SYSTEM = (
    "너는 주식 시장 뉴스를 읽고 **앞으로 6~24 달** 동안 시장을 이끌 산업·테마를 판단하는 분석가다. "
    "주어진 뉴스 제목만 근거로 쓴다. 가격·차트 정보는 없다. 근거마다 뉴스 번호(예: K12, U7)를 "
    "반드시 단다 — 번호 없는 주장은 쓰지 않는다. 종목은 주어진 종목군의 코드만 쓴다. "
    "이미 많이 알려져 크게 오른 테마일 가능성이 있으면 위험에 적는다. "
    "투자 권유가 아니라 판단 기록이다. "
    "한국어로, JSON 하나로만 답한다."
)

SCHEMA = (
    '{"kr": {"themes": [{"name": "테마", "why": "왜 앞으로 주도할지 2~3 문장", '
    '"evidence": ["K3", "K17"], "confidence": 0.0~1.0}], '
    '"picks": [{"symbol": "005930", "theme": "테마", "why": "이유 1~2 문장", "evidence": ["K3"]}], '
    '"risks": ["위험 1 문장"]}, "us": {같은 모양, 근거는 U 번호}, '
    '"summary": "이번 주 한 줄 판단"}'
)


def number_headlines(
    items: Sequence[Mapping[str, Any]], prefix: str, limit: int = MAX_HEADLINES
) -> list[dict[str, str]]:
    """제목 중복을 빼고 최근 것부터 ``limit`` 개에 ``K1``·``U1`` 같은 번호를 붙인다."""
    seen: set[str] = set()
    out: list[dict[str, str]] = []
    ordered = sorted(items, key=lambda x: str(x.get("fetched_at", "")), reverse=True)
    for item in ordered:
        title = str(item.get("title", "")).strip()
        key = title.split(" - ")[0].strip().lower()
        if not title or key in seen:
            continue
        seen.add(key)
        out.append(
            {"id": f"{prefix}{len(out) + 1}", "title": title, "link": str(item.get("link", ""))}
        )
        if len(out) >= limit:
            break
    return out


def build_messages(
    week: str,
    headlines: Mapping[str, Sequence[Mapping[str, str]]],
    universes: Mapping[str, Mapping[str, str]],
) -> list[dict[str, str]]:
    """시스템 · 사용자 메시지. 뉴스 제목과 종목군(코드:이름)만 — 가격·주도주 목록은 주지 않는다."""
    parts = [
        f"이번 주: {week}",
        f"답 형식: {SCHEMA}",
        f"테마는 시장마다 최대 {MAX_THEMES} 개, 종목은 최대 {MAX_PICKS} 개.",
    ]
    for market, label in MARKETS.items():
        lines = "\n".join(f"[{h['id']}] {h['title']}" for h in headlines.get(market, []))
        names = ", ".join(f"{c}:{n}" for c, n in sorted(universes.get(market, {}).items()))
        parts.append(f"\n## {label} 뉴스 제목 (지난 7 일)\n{lines or '(없음)'}")
        parts.append(f"\n## {label} 종목군 (코드:이름)\n{names}")
    return [{"role": "system", "content": SYSTEM}, {"role": "user", "content": "\n".join(parts)}]


def _ids(raw: Any, valid: set[str]) -> list[str]:
    return [str(x) for x in raw if str(x) in valid] if isinstance(raw, list) else []


def parse_weekly(
    text: str,
    universes: Mapping[str, Mapping[str, str]],
    headlines: Mapping[str, Sequence[Mapping[str, str]]],
) -> dict[str, Any]:
    """AI 답 검사. 종목군 밖 코드·없는 뉴스 번호·근거 없는 항목은 빼고 뺀 개수를 센다."""
    try:
        body = json.loads(text)
    except ValueError as exc:
        raise ValueError(f"JSON 아님: {exc}") from None
    if not isinstance(body, dict):
        raise ValueError("JSON 객체가 아님")
    out: dict[str, Any] = {"summary": str(body.get("summary", ""))[:300], "dropped": 0}
    for market in MARKETS:
        raw = body.get(market)
        part: dict[str, Any] = raw if isinstance(raw, dict) else {}
        valid = {h["id"] for h in headlines.get(market, [])}
        uni = universes.get(market, {})
        themes, picks = [], []
        for t in (part.get("themes") or [])[:MAX_THEMES]:
            ev = _ids(t.get("evidence"), valid) if isinstance(t, dict) else []
            if not ev:
                out["dropped"] += 1
                continue
            try:
                conf = min(max(float(t.get("confidence", 0)), 0.0), 1.0)
            except (TypeError, ValueError):
                conf = 0.0
            themes.append(
                {
                    "name": str(t.get("name", ""))[:40],
                    "why": str(t.get("why", ""))[:300],
                    "evidence": ev[:3],
                    "confidence": conf,
                }
            )
        for p in (part.get("picks") or [])[:MAX_PICKS]:
            sym = str(p.get("symbol", "")).strip() if isinstance(p, dict) else ""
            ev = _ids(p.get("evidence"), valid) if isinstance(p, dict) else []
            if sym not in uni or not ev:
                out["dropped"] += 1
                continue
            picks.append(
                {
                    "symbol": sym,
                    "name": uni[sym],
                    "theme": str(p.get("theme", ""))[:40],
                    "why": str(p.get("why", ""))[:200],
                    "evidence": ev[:2],
                }
            )
        risks = [str(r)[:150] for r in (part.get("risks") or [])[:3] if str(r).strip()]
        out[market] = {"themes": themes, "picks": picks, "risks": risks}
    return out


def diff_weeks(
    this: Mapping[str, Any], last: Mapping[str, Any] | None, market: str
) -> dict[str, list[str]]:
    """지난주 알림과 견줘 새로 나온·빠진 테마와 종목."""
    now = this.get(market) or {}
    before = (last or {}).get(market) or {}
    t_now = [t["name"] for t in now.get("themes", [])]
    t_before = [t["name"] for t in before.get("themes", [])]
    p_now = [p["symbol"] for p in now.get("picks", [])]
    p_before = [p["symbol"] for p in before.get("picks", [])]
    return {
        "new_themes": [t for t in t_now if t not in t_before],
        "gone_themes": [t for t in t_before if t not in t_now],
        "new_picks": [p for p in p_now if p not in p_before],
        "gone_picks": [p for p in p_before if p not in p_now],
    }


def _pct(x: float | None) -> str:
    return "—" if x is None else f"{x * 100:+.1f}%"


def format_market(
    market: str,
    judged: Mapping[str, Any],
    headlines: Sequence[Mapping[str, str]],
    facts: Mapping[str, Mapping[str, Any]],
    diff: Mapping[str, Sequence[str]] | None,
    week: str,
) -> str:
    """텔레그램 한 통(시장 하나). 근거는 실제 뉴스 제목, 사실은 우리 자료."""
    title_of = {h["id"]: h["title"] for h in headlines}
    part = judged.get(market) or {}
    lines = [f"🔭 {MARKETS[market]} · 미래 주도 테마 ({week})", ""]
    if not part.get("themes") and not part.get("picks"):
        lines.append("이번 주는 근거가 붙은 판단이 없다.")
    for i, t in enumerate(part.get("themes", []), 1):
        lines.append(f"{i}. {t['name']} (확신 {t['confidence']:.0%})")
        lines.append(f"   왜: {t['why']}")
        for e in t["evidence"]:
            lines.append(f"   근거 [{e}] {title_of.get(e, '')[:90]}")
    if part.get("picks"):
        lines += ["", "주목 종목 — 우리 자료 확인(AI 아님)"]
        for p in part["picks"]:
            f = facts.get(p["symbol"], {})
            t_up = f.get("trend")
            trend = "추세 위" if t_up is True else "추세 아래" if t_up is False else "추세 ?"
            tags = [trend, f"3 달 {_pct(f.get('ret_3m'))}"]
            tags += [str(x) for x in f.get("in_lists", [])]
            lines.append(f"• {p['name']} ({p['symbol']}) — {p['theme']}")
            lines.append(f"   {p['why']}")
            lines.append(f"   [{' · '.join(tags)}]")
    if part.get("risks"):
        lines += ["", "위험: " + " / ".join(part["risks"])]
    if diff and any(diff.values()):
        change = []
        if diff.get("new_themes"):
            change.append("새 테마 " + ", ".join(diff["new_themes"]))
        if diff.get("gone_themes"):
            change.append("빠진 테마 " + ", ".join(diff["gone_themes"]))
        if diff.get("new_picks"):
            change.append("새 종목 " + ", ".join(diff["new_picks"]))
        lines += ["", "지난주 대비: " + " · ".join(change)]
    lines += ["", "참고용 정보 — 검증된 매매 신호 아님. AI 판단의 성적은 문서 147 장부로 기록 중."]
    return "\n".join(lines)
