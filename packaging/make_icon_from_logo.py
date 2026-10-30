# -*- coding: utf-8 -*-
"""从用户授权的 assets/brand/logo.png 生成 Windows 多尺寸图标。

只裁剪为正方形并做轻微圆角，不改变原图内容；源图仍保留在 assets/brand。
若 logo.png 不存在，应继续使用 make_placeholder_icon.py 的占位图标。
"""
from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageChops, ImageDraw

ROOT = Path(__file__).resolve().parent.parent
SOURCE = ROOT / "assets" / "brand" / "logo.png"
TARGET = ROOT / "assets" / "brand" / "app_icon.ico"
PREVIEW = ROOT / "assets" / "brand" / "app_icon_preview.png"
SIZES = (256, 128, 64, 48, 32, 16)
BASE = 512
RADIUS_RATIO = 0.16


def _square(image: Image.Image) -> Image.Image:
    width, height = image.size
    side = min(width, height)
    left = (width - side) // 2
    top = (height - side) // 2
    return image.crop((left, top, left + side, top + side))


def main() -> int:
    if not SOURCE.exists():
        print("缺少 Logo 源图：%s" % SOURCE)
        return 1
    image = _square(Image.open(SOURCE).convert("RGBA"))
    image = image.resize((BASE, BASE), Image.Resampling.LANCZOS)

    mask = Image.new("L", (BASE, BASE), 0)
    draw = ImageDraw.Draw(mask)
    radius = int(BASE * RADIUS_RATIO)
    draw.rounded_rectangle((0, 0, BASE - 1, BASE - 1), radius=radius, fill=255)
    image.putalpha(ImageChops.multiply(image.getchannel("A"), mask))

    frames = [
        image.resize((size, size), Image.Resampling.LANCZOS) for size in SIZES
    ]
    TARGET.parent.mkdir(parents=True, exist_ok=True)
    frames[0].save(
        TARGET,
        format="ICO",
        sizes=[(size, size) for size in SIZES],
        append_images=frames[1:],
    )
    frames[0].save(PREVIEW, format="PNG")
    print("图标已生成：%s" % TARGET)
    print("预览：%s" % PREVIEW)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
