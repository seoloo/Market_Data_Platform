"""포트폴리오 보고서 — 연구 카드 공통 틀.

카드 한 장 = 연구 하나(또는 한 번에 판정한 묶음). 칸 이름 없이 두 문단(가설·방법 / 결과·교훈)과 핵심 숫자 한 줄.
숫자와 판정은 각 연구 문서(docs/research)가 기준이다.
"""

from __future__ import annotations

from dataclasses import dataclass

VERDICTS = {
    "pass": ("통과", "#1f8a5b"),
    "hist": ("역사적 지지", "#3aa57a"),
    "design": ("설계 결정", "#6b5bd6"),
    "unclear": ("판단 불가", "#d48a1c"),
    "nodata": ("자료 부족", "#3b7dd8"),
    "reject": ("기각", "#8a8f98"),
}

FAMILIES = {
    "trend": "코인 추세 보유와 레버리지",
    "short": "단기 방향 예측 (분 · 시간 · 하루)",
    "pattern": "차트 패턴 · 보조지표 · 엘리엇",
    "relative": "숏 · 롱숏 · 상대 강도 (코인)",
    "carry": "캐리 · 차익 · 시장조성 · 그리드",
    "event": "자금 흐름 · 이벤트 · 반등",
    "stock": "주식 종목 선택 · 주도주 · 테마",
    "index": "지수 적립 · 레버리지 설계",
    "alloc": "자산 배분",
    "ml": "머신러닝 · LLM 판독",
}

MARKETS = ("코인", "금", "미국 주식", "국내 주식", "지수 · ETF", "여러 자산")
HORIZONS = ("분 · 시간", "며칠", "몇 주", "몇 달")


@dataclass(frozen=True, slots=True)
class Card:
    key: str
    name: str
    fam: str
    verdict: str
    market: str
    horizon: str
    date: str
    p1: str
    p2: str
    fig: str


def C(
    key: str,
    name: str,
    fam: str,
    verdict: str,
    market: str,
    horizon: str,
    date: str,
    p1: str,
    p2: str,
    fig: str,
) -> Card:
    assert fam in FAMILIES, fam
    assert verdict in VERDICTS, verdict
    assert market in MARKETS, market
    assert horizon in HORIZONS, horizon
    return Card(key, name, fam, verdict, market, horizon, date, p1, p2, fig)
