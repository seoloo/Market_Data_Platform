"""로컬에 보관한 Lucide·코인·공식 기업 아이콘. CDN/계좌 요청 없음."""

from __future__ import annotations

from pathlib import Path

_ROOT = Path(__file__).with_name("assets") / "icons"
_LUCIDE = {
    "home": "house",
    "chart": "chart-no-axes-combined",
    "pie": "chart-pie",
    "wallet": "wallet-cards",
    "gear": "settings-2",
    "help": "circle-question-mark",
    "bell": "bell",
    "leaf": "sprout",
    "eye": "eye",
    "log": "file-clock",
    "sun": "sun",
    "moon": "moon",
    "x": "x",
    "arrow": "chevron-right",
    "coins": "coins",
    "deposit": "circle-dollar-sign",
    "stock": "building",
    "quantum": "atom",
    "fund": "landmark",
}
_SYMBOLS = {
    "BTC": "btc.svg",
    "ETH": "eth.svg",
    "SOL": "sol.svg",
}
_STOCKS = {
    "TQQQ": "chart",
    "SCHD": "leaf",
    "IAUM": "coins",
    "BTQ": "quantum",
    "QS": "stock",
    "WQTM": "quantum",
}
# 예전 정적 내보내기/주소와 원본 보존. 최신 홈은 작은 비트맵 로고를 확대하지 않는다.
_LEGACY = ("tqqq.png", "schd.ico", "btq.ico", "qs.png")
ICON_ASSETS = tuple(
    dict.fromkeys((*[f"{v}.svg" for v in _LUCIDE.values()], *_SYMBOLS.values(), *_LEGACY))
)
ICON_NOTICES = ("LICENSE-lucide", "LICENSE-cryptocurrency.md", "SOURCES.md")
_PATHS = {
    key: (_ROOT / f"{filename}.svg")
    .read_text(encoding="utf-8")
    .partition(">")[2]
    .rsplit("</svg>", 1)[0]
    for key, filename in _LUCIDE.items()
}


def icon(name: str) -> str:
    """검산된 로컬 Lucide path만 inline 표시한다."""
    return f'<svg class="orbit-icon" viewBox="0 0 24 24" aria-hidden="true">{_PATHS[name]}</svg>'


def symbol_icon(symbol: str) -> str:
    """코인은 로고, 주식은 선명한 Lucide 벡터. 공식 기업 로고로 위장하지 않는다."""
    filename = _SYMBOLS.get(symbol)
    if filename is None:
        return icon(_STOCKS.get(symbol, "deposit" if not symbol else "fund"))
    return f'<img src="/assets/icons/{filename}" alt="" aria-hidden="true" width="38" height="38">'
