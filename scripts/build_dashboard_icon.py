"""SVG 원본의 단순 도형을 ICO로 빌드. 런타임 의존성 없음, 빌드 시 Pillow 필요.

사용: 번들 Python으로 이 파일 실행. SVG/ICO는 저장소에 포함해 설치 없이 UI에서 사용한다.
기존 사진을 편집하지 않고 버전 관리된 벡터 도형을 여러 해상도로 렌더링한다.
"""

from __future__ import annotations

from pathlib import Path
from xml.etree import ElementTree

from PIL import Image, ImageDraw  # type: ignore[import-not-found]


def build(folder: Path) -> None:
    svg = ElementTree.parse(folder / "auto-trading.svg").getroot()
    ns = {"s": "http://www.w3.org/2000/svg"}
    rect = svg.find("s:rect", ns)
    assert rect is not None
    scale = 8
    size = 128 * scale
    image = Image.new("RGBA", (size, size))
    mask = Image.new("L", (size, size))
    box = tuple(float(rect.attrib[k]) * scale for k in ("x", "y", "width", "height"))
    ImageDraw.Draw(mask).rounded_rectangle(
        (box[0], box[1], box[0] + box[2], box[1] + box[3]),
        radius=float(rect.attrib["rx"]) * scale,
        fill=255,
    )
    pixels = image.load()
    assert pixels is not None
    stops = svg.findall("s:defs/s:linearGradient/s:stop", ns)
    colors = [
        tuple(int(stop.attrib["stop-color"][i : i + 2], 16) for i in (1, 3, 5)) for stop in stops
    ]
    assert len(colors) == 2
    for y in range(size):
        for x in range(size):
            weight = min(1.0, max(0.0, (x + y - 8 * scale) / (240 * scale)))
            pixels[x, y] = (
                *(round(a + (b - a) * weight) for a, b in zip(*colors, strict=True)),
                255,
            )
    image.putalpha(mask)
    draw = ImageDraw.Draw(image)
    for line in svg.findall("s:polyline", ns):
        points = [
            tuple(float(v) * scale for v in p.split(",")) for p in line.attrib["points"].split()
        ]
        width = float(line.attrib["stroke-width"]) * scale
        color = line.attrib["stroke"]
        draw.line(points, fill=color, width=round(width), joint="curve")
        for point_x, point_y in points:
            radius = width / 2
            draw.ellipse(
                (point_x - radius, point_y - radius, point_x + radius, point_y + radius),
                fill=color,
            )
    image.resize((256, 256), Image.Resampling.LANCZOS).save(
        folder / "auto-trading.ico",
        format="ICO",
        sizes=[(s, s) for s in (16, 24, 32, 48, 64, 128, 256)],
    )


if __name__ == "__main__":
    build(Path(__file__).resolve().parents[1] / "src" / "auto_trading" / "ui" / "assets")
