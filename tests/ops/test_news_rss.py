"""무료 RSS·Atom 뉴스 파싱 (문서 115 2 단계 준비)."""

from __future__ import annotations

from auto_trading.ops.news_rss import dedupe, parse_feed

RSS = """<?xml version="1.0"?>
<rss version="2.0"><channel><title>Feed</title>
<item><title>Nvidia leads &amp; Nasdaq rises</title><link>https://x.test/a</link>
<pubDate>Fri, 02 Oct 2026 20:00:00 GMT</pubDate>
<description>&lt;p&gt;Chips rally&lt;/p&gt;</description></item>
<item><title>Fed holds rates</title><link>https://x.test/b</link></item>
<item><title></title><link>https://x.test/empty</link></item>
</channel></rss>"""

ATOM = """<?xml version="1.0"?>
<feed xmlns="http://www.w3.org/2005/Atom"><title>A</title>
<entry><title>Powell speaks</title><link href="https://y.test/c"/><updated>2026-10-02T18:00:00Z</updated>
<summary>Remarks on inflation</summary></entry>
</feed>"""


def test_parse_rss_items_and_strip_html() -> None:
    items = parse_feed(RSS, "cnbc", "2026-10-03T00:00:00+00:00")
    assert [i.title for i in items] == ["Nvidia leads & Nasdaq rises", "Fed holds rates"]
    assert items[0].summary == "Chips rally"
    assert items[0].published.startswith("Fri, 02 Oct 2026")
    assert items[0].fetched_at == "2026-10-03T00:00:00+00:00"


def test_parse_atom_entries_with_href_links() -> None:
    items = parse_feed(ATOM, "fed", "t")
    assert items[0].title == "Powell speaks"
    assert items[0].link == "https://y.test/c"
    assert items[0].summary == "Remarks on inflation"


def test_broken_xml_gives_empty_list_and_dedupe_by_link() -> None:
    assert parse_feed("<rss><channel>", "x", "t") == []
    a = parse_feed(RSS, "s1", "t")
    b = parse_feed(RSS, "s2", "t")
    assert len(dedupe(a + b)) == 2
