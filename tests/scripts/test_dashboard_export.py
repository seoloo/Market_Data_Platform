"""오프라인 HTML도 테마·페이지·홈 바로가기 사이의 연결을 보존한다."""

from __future__ import annotations

from html.parser import HTMLParser
from pathlib import Path

import pytest

from auto_trading.ui.render import PAGES
from scripts import dashboard


class _Links(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.links: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "a":
            self.links.extend(value for name, value in attrs if name == "href" and value)


def test_export_both_themes_without_broken_internal_links(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "source"
    source.mkdir()
    monkeypatch.setattr(dashboard, "REPO", source)
    folder = tmp_path / "export"
    assert dashboard.export(folder) == 0
    assert len(list(folder.glob("*.html"))) == 2 * len(PAGES)
    for path in folder.glob("*.html"):
        html = path.read_text(encoding="utf-8")
        theme = "dark" if path.stem.endswith("-dark") else "light"
        assert f'data-theme="{theme}"' in html
        assert 'src="assets/auto-trading.svg"' in html
        assert 'href="assets/auto-trading.ico?v=2"' in html
        links = _Links()
        links.feed(html)
        for href in links.links:
            if href.startswith("#") or "://" in href:
                continue
            assert not href.startswith("/") and "?theme=" not in href
            assert (folder / href.partition("#")[0]).exists(), (path.name, href)
    assert not (source / "data").exists()
    assert (folder / "assets" / "auto-trading.svg").exists()
    assert (folder / "assets" / "auto-trading.ico").read_bytes().startswith(b"\x00\x00\x01\x00")
