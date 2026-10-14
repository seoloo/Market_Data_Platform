"""보유 종목 위험 뉴스 감시 — Laya 가 '중대 사건' 후보를 고르고 확인된 것만 알린다.

사용자 2026-10-11 요청.

순수 함수 + 작은 파일. 매매 · 주문과 무관 — 사람이 '정리' 를 누를지 판단하는 재료일 뿐이다.

* 1 차 Laya(choice): 상장폐지 · 펀드 청산 / 해킹 · 사기 / 소송 · 규제 / 실적 충격 / 희석 · 증자 /
  파산 · 부도 / 평범한 소식. '평범' 이 아닌 답이 ``CANDIDATE_MIN`` 이상이면 후보.
* 2 차 Gemini(무료): 후보가 '이미 일어난 중대 사건' 인지 확인한 것만 알림. Gemini 를 못 쓰면
  Laya 만으로 ``ALERT_MIN`` 이상일 때 알림. (2026-10-11 시험: Laya 만으로는 예고 기사를 실적 충격
  88% 로 잘못 보고, 가이던스 하향 급락을 67% 로 낮게 봤다.)
* 영어 제목만 보낸다(사용자: 영어가 더 빠름 · 정확). 같은 기사는 다시 보지 않는다(14 일 기억).
"""

from __future__ import annotations

import hashlib
import json
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from html import escape
from pathlib import Path
from typing import Any

__all__ = [
    "ALERT_MIN",
    "CANDIDATE_MIN",
    "KINDS",
    "RISK_Q",
    "Asset",
    "Headline",
    "SeenStore",
    "alert_text",
    "confirm_prompt",
    "parse_confirm",
    "pick_alerts",
    "state_of",
]

ALERT_MIN = 0.9
"""Gemini 확인을 못 할 때 Laya 만으로 알리는 문턱."""
CANDIDATE_MIN = 0.5
"""Gemini 확인으로 넘길 Laya 후보 문턱 (시험: 가이던스 하향 급락 기사가 0.67)."""
MAX_ALERTS = 6
KEEP_S = 14 * 86_400

KINDS: dict[str, tuple[str, str]] = {
    "delisting": (
        "상장폐지 · 청산 · 거래정지",
        "delisting, ETF or fund closure or liquidation, trading halt or suspension",
    ),
    "hack_fraud": (
        "해킹 · 사기",
        "hack, exploit, theft of funds, fraud, accounting scandal, executives arrested",
    ),
    "legal": (
        "소송 · 규제",
        "major lawsuit, government investigation, regulatory ban, enforcement action, sanctions",
    ),
    "earnings": (
        "실적 충격",
        "a reported large earnings miss, guidance cut or profit warning that already happened",
    ),
    "dilution": (
        "증자 · 희석",
        "large share offering, dilution, convertible notes, reverse split",
    ),
    "bankruptcy": (
        "파산 · 부도",
        "bankruptcy, default, insolvency, liquidity crisis, going-concern warning",
    ),
    "routine": (
        "평범한 소식",
        "ordinary news, previews of upcoming earnings or events, expectations, analyst opinions, "
        "price moves, positive news, or not about this asset",
    ),
}
RISK_Q: dict[str, Any] = {
    "risk": {
        "type": "choice",
        "instructions": "Does this headline report a serious negative event for holders of the "
        "named asset? Pick the closest category.",
        "criteria": {k: v[1] for k, v in KINDS.items()},
    }
}


@dataclass(frozen=True, slots=True)
class Asset:
    key: str
    """보이는 이름 (TQQQ · BTC · Upbit)."""
    name: str
    """Laya 에 줄 영어 이름 (예: 'Bitcoin', 'ProShares UltraPro QQQ (TQQQ)')."""
    query: str
    """구글 뉴스 영어 검색어."""
    yahoo: str = ""
    """야후 뉴스 RSS 기호 (미국 종목만)."""
    group: str = ""
    """Glide · Cruise · 토스 적립 · 수동 보유."""


@dataclass(frozen=True, slots=True)
class Headline:
    asset: Asset
    title: str
    link: str
    published: str

    @property
    def key(self) -> str:
        head = self.title.rsplit(" - ", 1)[0].strip().lower()
        return hashlib.sha1(f"{self.asset.key}|{head}".encode()).hexdigest()[:16]


def state_of(h: Headline) -> str:
    """Laya 에 보낼 영어 문장 — 어떤 자산에 관한 질문인지 붙인다."""
    return f"Asset: {h.asset.name}. Headline: {h.title}"


def pick_alerts(
    items: Sequence[Headline],
    choices: Sequence[tuple[str, float] | None],
    min_prob: float = ALERT_MIN,
) -> list[tuple[Headline, str, float]]:
    """(기사, 종류, 확률) — '평범' 이 아니고 확률이 ``min_prob`` 이상인 것만.

    확률 높은 순 최대 6 개.

    Gemini 확인이 있으면 ``CANDIDATE_MIN`` 으로 후보를 넓게 고르고,
    없으면 ``ALERT_MIN`` 으로 바로 알린다."""
    out = []
    for it, ch in zip(items, choices, strict=False):
        if ch is None:
            continue
        label, prob = ch
        if label in KINDS and label != "routine" and prob >= min_prob:
            out.append((it, label, prob))
    out.sort(key=lambda x: -x[2])
    return out[:MAX_ALERTS]


def confirm_prompt(cands: Sequence[tuple[Headline, str, float]]) -> tuple[str, str]:
    """Gemini 2 차 확인 (후보가 있을 때만, 무료 키)."""
    system = (
        "너는 보유 자산 위험 뉴스 판정가다. 각 제목이 그 자산을 가진 사람에게 큰 손실을 줄 수 있는 "
        "'이미 일어난' 중대 사건(상장폐지 · 청산 · 해킹 · 사기 · 큰 소송/규제 · "
        "실적 충격 · 대규모 증자 · "
        "파산)인지 판단한다. 예고 · 전망 · 분석 · 의견 · 작은 등락 · 다른 자산 이야기는 아니다. "
        "제목에 없는 사실을 지어내지 않는다. 반드시 JSON 하나만 답한다: "
        '{"items": [{"id": "R1", "serious": true, "why": "한국어 한 줄 요약(60자 안)"}]}'
    )
    user = "\n".join(
        f"R{k}. 자산: {h.asset.name} | 제목: {h.title}" for k, (h, _l, _p) in enumerate(cands, 1)
    )
    return system, user


def parse_confirm(answer: Mapping[str, Any] | None, n: int) -> dict[int, str]:
    """확인된 후보 번호(1 부터) → 한 줄 요약. 형식이 틀리면 빈 값."""
    out: dict[int, str] = {}
    if not isinstance(answer, Mapping):
        return out
    for row in answer.get("items") or []:
        if not isinstance(row, Mapping) or row.get("serious") is not True:
            continue
        rid = str(row.get("id") or "")
        if rid.startswith("R") and rid[1:].isdigit() and 1 <= int(rid[1:]) <= n:
            out[int(rid[1:])] = str(row.get("why") or "").strip()[:80]
    return out


def alert_text(
    alerts: Sequence[tuple[Headline, str, float]], whys: Mapping[int, str] | None = None
) -> str:
    """투자 봇 HTML 알림 (바깥 글은 이스케이프). ``whys`` = 알림 번호(1 부터) → Gemini 한 줄."""
    whys = whys or {}
    head = "AI 1 차 + Gemini 확인" if whys else "AI 판단"
    lines = [f"⚠️ <b>보유 종목 위험 뉴스</b> ({head} · 확인 필요)", ""]
    for k, (h, label, prob) in enumerate(alerts, 1):
        grp = f" · {escape(h.asset.group)}" if h.asset.group else ""
        lines.append(
            f"🔴 <b>{escape(h.asset.key)}</b>{grp} — {escape(KINDS[label][0])} (AI {prob:.0%})"
        )
        if whys.get(k):
            lines.append(f"   {escape(whys[k])}")
        link = f'<a href="{escape(h.link, quote=True)}">기사</a>' if h.link else ""
        lines.append(f"   {escape(h.title[:120])} {link}")
    lines += [
        "",
        "※ 매매는 바뀌지 않아요. 확인 뒤 필요하면 '정리' 버튼으로 직접 판단하세요.",
        "※ 제목만 읽은 판단이라 틀릴 수 있어요.",
    ]
    return "\n".join(lines)


class SeenStore:
    """이미 본 기사 (14 일). ``data/risk_news/seen.json``."""

    def __init__(self, path: Path) -> None:
        self.path = path
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            raw = {}
        self.seen: dict[str, float] = {str(k): float(v) for k, v in dict(raw).items()}

    def new(self, items: Sequence[Headline]) -> list[Headline]:
        out, keys = [], set()
        for it in items:
            if it.key not in self.seen and it.key not in keys:
                keys.add(it.key)
                out.append(it)
        return out

    def mark(self, items: Sequence[Headline], now: float | None = None) -> None:
        t = time.time() if now is None else now
        for it in items:
            self.seen[it.key] = t
        self.seen = {k: v for k, v in self.seen.items() if t - v < KEEP_S}

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.seen), encoding="utf-8")
        tmp.replace(self.path)


def log_rows(
    items: Sequence[Headline], choices: Sequence[tuple[str, float] | None], at: str
) -> list[Mapping[str, Any]]:
    return [
        {
            "at": at,
            "asset": it.asset.key,
            "title": it.title,
            "link": it.link,
            "label": ch[0] if ch else None,
            "prob": round(ch[1], 4) if ch else None,
        }
        for it, ch in zip(items, choices, strict=False)
    ]
