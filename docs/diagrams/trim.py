"""스크린샷 아래쪽 흰 여백을 잘라 낸다. 사용: python trim.py <png 경로> [아래 여백 px]"""

import sys

from PIL import Image, ImageChops

path = sys.argv[1]
bottom_margin_px = int(sys.argv[2]) if len(sys.argv) > 2 else 40

image = Image.open(path).convert("RGB")
white = Image.new("RGB", image.size, (255, 255, 255))
content_box = ImageChops.difference(image, white).getbbox()
if content_box is not None:
    content_bottom = min(image.height, content_box[3] + bottom_margin_px)
    image.crop((0, 0, image.width, content_bottom)).save(path, optimize=True)
print(path, image.width, "x", content_bottom if content_box else image.height)
