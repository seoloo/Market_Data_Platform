"""승인 오비 PNG를 웹 PNG·다중 해상도 ICO로 축소한다.

Pillow는 빌드 환경에만 필요하다. 원본의 검은 배경·색·캐릭터를 보존하며
생성형 편집·배경 제거·자동 크롭 없이 정사각 원본 전체를 리샘플한다.
"""

from __future__ import annotations

import shutil
from pathlib import Path

from PIL import Image  # type: ignore[import-not-found]


def build(folder: Path) -> None:
    with Image.open(folder / "obi-original.png") as source:
        if source.width != source.height:
            raise ValueError("승인된 아이콘 원본은 정사각형이어야 합니다.")
        image = source.convert("RGB")
    image.resize((512, 512), Image.Resampling.LANCZOS).save(folder / "obi.png", optimize=True)
    image.resize((256, 256), Image.Resampling.LANCZOS).save(
        folder / "auto-trading.ico",
        format="ICO",
        sizes=[(s, s) for s in (16, 24, 32, 48, 64, 128, 256)],
    )
    shutil.copyfile(folder / "auto-trading.ico", folder / "obi.ico")


if __name__ == "__main__":
    build(Path(__file__).resolve().parents[1] / "src" / "auto_trading" / "ui" / "assets")
