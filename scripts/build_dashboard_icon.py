"""승인 오비 PNG를 웹 PNG·다중 해상도 ICO로 축소한다.

승인된 투명 PNG의 alpha를 유지한다. 배경 제거/재생성/자동 크롭은 하지 않는다.
"""

from __future__ import annotations

import shutil
from pathlib import Path

from PIL import Image  # type: ignore[import-not-found]


def build(folder: Path) -> None:
    with Image.open(folder / "orbit-icon-source.png") as source:
        if source.width != source.height:
            raise ValueError("승인된 아이콘 원본은 정사각형이어야 합니다.")
        image = source.convert("RGBA")
        if image.getpixel((0, 0))[3] != 0:
            raise ValueError("아이콘 원본의 바깥 배경은 투명해야 합니다.")
    image.resize((512, 512), Image.Resampling.LANCZOS).save(folder / "obi.png", optimize=True)
    image.resize((256, 256), Image.Resampling.LANCZOS).save(
        folder / "auto-trading.ico",
        format="ICO",
        sizes=[(s, s) for s in (16, 24, 32, 48, 64, 128, 256)],
    )
    shutil.copyfile(folder / "auto-trading.ico", folder / "obi.ico")
    shutil.copyfile(folder / "auto-trading.ico", folder / "orbit-transparent.ico")


if __name__ == "__main__":
    build(Path(__file__).resolve().parents[1] / "src" / "auto_trading" / "ui" / "assets")
