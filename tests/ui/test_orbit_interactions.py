"""팝업 닫기·체결 대기 조건·로컬 그림의 안전/보존 계약."""

from __future__ import annotations

from html.parser import HTMLParser
from pathlib import Path
from xml.etree import ElementTree

import pytest

from auto_trading.ui.live_home import Book, Quotes
from auto_trading.ui.orbit_home import layout, render_live
from auto_trading.ui.orbit_icons import ICON_ASSETS, ICON_NOTICES, icon, symbol_icon
from auto_trading.ui.server import _ASSETS

ASSETS = Path(__file__).resolve().parents[2] / "src/auto_trading/ui/assets/icons"


class _Tags(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.tags: list[tuple[str, dict[str, str | None]]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.tags.append((tag, dict(attrs)))


def test_all_popups_have_fixed_header_close_and_matching_targets() -> None:
    parser = _Tags()
    parser.feed(layout(render_live([], Quotes(), 0), "light"))
    dialogs = {a["id"] for t, a in parser.tags if t == "section" and a.get("popover") == "auto"}
    assert dialogs == {
        "orbit-guide",
        "orbit-accounts",
        "orbit-settings",
        "orbit-status",
        "orbit-all-trades",
    }
    closers = [
        a for t, a in parser.tags if t == "button" and a.get("popovertargetaction") == "hide"
    ]
    assert {a["popovertarget"] for a in closers} == dialogs
    assert all(
        str(a.get("aria-label", "")).endswith("닫기") and a.get("type") == "button" for a in closers
    )
    for t, a in parser.tags:
        if t == "button" and "popovertarget" in a:
            assert a["popovertarget"] in dialogs
    assert "<script" not in layout("", "light") and "<form" not in layout("", "light")


@pytest.mark.parametrize("pending", [(), ("BTC 지정가 체결 확인",)])
def test_pending_banner_only_when_records_exist(pending: tuple[str, ...]) -> None:
    book = Book("cruise", "Cruise", "업비트", 100, 100, (), pending=pending)
    html = render_live([book], Quotes(), 0)
    assert ('class="orbit-alert"' in html) == bool(pending)
    assert ("먼저 확인해 주세요" in html) == bool(pending)
    if pending:
        assert "체결 확인 대기 1건" in html


def test_hiding_pending_banner_does_not_hide_data_or_risk_warnings() -> None:
    book = Book("cruise", "Cruise", "업비트", 100, 100, (), notes=("낙폭 정지 중",))
    html = render_live([book], Quotes(errors=("시세 조회 실패",)), 0)
    assert 'class="orbit-alert"' not in html
    assert 'class="orbit-data-note"' in html
    assert "시세 조회 실패" in html and "매수 정지 기록을 확인하세요" in html
    assert "아직 실거래 장부가 없습니다" in render_live([], Quotes(), 0)


@pytest.mark.parametrize("theme,expected", [("light", "moon"), ("dark", "sun")])
def test_theme_toggle_and_shared_mascot(theme: str, expected: str) -> None:
    html = layout(render_live([], Quotes(), 0), theme)
    tools = html.split('class="orbit-tools"', 1)[1].split("</div>", 1)[0]
    assert icon(expected) in tools and icon("gear") not in tools
    assert html.count('class="orbit-hero-cat"') == 1
    assert 'class="orbit-hero-dark-cat"' not in html


def test_icon_files_safe_local_and_exactly_whitelisted() -> None:
    for name in ICON_ASSETS:
        path = ASSETS / name
        assert path.is_file() and path.stat().st_size > 50
        assert _ASSETS[f"/assets/icons/{name}"][0] == f"icons/{name}"
        if name.endswith(".svg"):
            root = ElementTree.fromstring(path.read_text(encoding="utf-8"))
            for node in root.iter():
                assert node.tag.rsplit("}", 1)[-1] not in {
                    "script",
                    "foreignObject",
                    "image",
                    "use",
                    "a",
                }
                assert not any(key.lower().startswith("on") or "href" in key for key in node.attrib)
            assert "url(" not in path.read_text(encoding="utf-8")
        else:
            assert path.read_bytes().startswith(
                b"\x89PNG" if name.endswith(".png") else b"\x00\x00\x01\x00"
            )
    assert all((ASSETS / name).is_file() for name in ICON_NOTICES)
    for request_path in (
        "/assets/icons/../server.py",
        "/assets/icons/LICENSE-lucide",
        "/assets/icons/unknown.svg",
    ):
        assert request_path not in _ASSETS


@pytest.mark.parametrize("symbol", ["BTC", "ETH", "SOL", "TQQQ", "SCHD", "BTQ", "QS"])
def test_known_instruments_use_local_logo_not_letter(symbol: str) -> None:
    assert 'src="/assets/icons/' in symbol_icon(symbol)
    assert 'aria-hidden="true"' in symbol_icon(symbol)


def test_unknown_instrument_never_becomes_asset_path() -> None:
    assert "<img" not in symbol_icon('../../.env" onerror="bad')
    assert icon("fund") == symbol_icon("unknown")
