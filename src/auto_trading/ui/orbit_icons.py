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
    "TQQQ": "tqqq.png",
    "SCHD": "schd.ico",
    "BTQ": "btq.ico",
    "QS": "qs.png",
}
ICON_ASSETS = tuple(dict.fromkeys((*[f"{v}.svg" for v in _LUCIDE.values()], *_SYMBOLS.values())))
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
    """기업 로고는 종목 식별용. 미등록 종목은 중립 펀드 아이콘."""
    filename = _SYMBOLS.get(symbol)
    if filename is None:
        return icon("quantum" if symbol == "WQTM" else "deposit" if not symbol else "fund")
    return f'<img src="/assets/icons/{filename}" alt="" aria-hidden="true" width="38" height="38">'
