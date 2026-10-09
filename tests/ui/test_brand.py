"""승인 오비 PNG·다중 해상도 ICO와 호환 SVG를 로컬 배포한다."""

from __future__ import annotations

import struct
from xml.etree import ElementTree

from auto_trading.ui.render import ASSET_DIR


def test_obi_png_is_square_and_local() -> None:
    for name, size in (("obi-original.png", 1254), ("obi.png", 512)):
        body = (ASSET_DIR / name).read_bytes()
        assert body[:8] == b"\x89PNG\r\n\x1a\n"
        assert struct.unpack_from(">II", body, 16) == (size, size)
    assert (ASSET_DIR / "obi.ico").read_bytes() == (ASSET_DIR / "auto-trading.ico").read_bytes()


def test_svg_is_a_self_contained_vector() -> None:
    root = ElementTree.fromstring((ASSET_DIR / "auto-trading.svg").read_bytes())
    assert root.attrib["viewBox"] == "0 0 128 128"
    assert {node.tag.rsplit("}", 1)[-1] for node in root.iter()} <= {
        "svg",
        "title",
        "defs",
        "linearGradient",
        "stop",
        "rect",
        "polyline",
    }
    assert not any(
        "href" in key or key.startswith("on") for node in root.iter() for key in node.attrib
    )


def test_ico_contains_valid_png_frames_for_desktop_and_favicon() -> None:
    body = (ASSET_DIR / "auto-trading.ico").read_bytes()
    assert struct.unpack_from("<HHH", body) == (0, 1, 7)
    sizes: set[int] = set()
    for index in range(7):
        width, height, _, _, _, _, length, offset = struct.unpack_from(
            "<BBBBHHII", body, 6 + 16 * index
        )
        assert width == height
        sizes.add(width or 256)
        assert 6 + 16 * 7 <= offset < offset + length <= len(body)
        assert body[offset : offset + 8] == b"\x89PNG\r\n\x1a\n"
    assert sizes == {16, 24, 32, 48, 64, 128, 256}
