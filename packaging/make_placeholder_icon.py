# -*- coding: utf-8 -*-
"""生成可替换的「道」文字占位图标。

这不是用户 Logo，也不引用任何第三方图片；用户提供授权素材后，应替换
assets/brand/app_icon.ico（或 logo.png），本脚本仅用于生成占位资源。
"""
from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parent.parent
TARGET = ROOT / "assets" / "brand" / "app_icon.ico"
FONT_CANDIDATES = (
    Path("C:/Windows/Fonts/msyhbd.ttc"),
    Path("C:/Windows/Fonts/msyh.ttc"),
    Path("C:/Windows/Fonts/simhei.ttf"),
)
SIZES = (256, 128, 64, 48, 32, 16)
SCALE = 4


def _font(size: int) -> ImageFont.FreeTypeFont:
    for candidate in FONT_CANDIDATES:
        if candidate.exists():
            return ImageFont.truetype(str(candidate), size)
    return ImageFont.truetype("arial.ttf", size)


def main() -> int:
    canvas = 256 * SCALE
    image = Image.new("RGBA", (canvas, canvas), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    margin = int(canvas * 0.035)
    ring = int(canvas * 0.012)
    # 外圈用深墨色收边，内圈用主题金，保持小尺寸下的辨识度。
    draw.ellipse(
        (margin, margin, canvas - margin, canvas - margin),
        fill=(40, 53, 64, 255),
    )
    inner = margin + ring
    draw.ellipse(
        (inner, inner, canvas - inner, canvas - inner),
        fill=(180, 151, 99, 255),
    )
    highlight = int(canvas * 0.105)
    draw.ellipse(
        (inner + highlight, inner + highlight, canvas - inner - highlight, canvas - inner - highlight),
        fill=(198, 170, 119, 255),
    )
    font = _font(int(canvas * 0.54))
    text = "道"
    box = draw.textbbox((0, 0), text, font=font)
    text_width = box[2] - box[0]
    text_height = box[3] - box[1]
    x = (canvas - text_width) / 2 - box[0]
    y = (canvas - text_height) / 2 - box[1] - canvas * 0.015
    draw.text((x, y), text, font=font, fill=(255, 253, 248, 255))
    TARGET.parent.mkdir(parents=True, exist_ok=True)
    image = image.resize((256, 256), Image.Resampling.LANCZOS)
    frames = [image.resize((size, size), Image.Resampling.LANCZOS) for size in SIZES]
    frames[0].save(
        TARGET,
        format="ICO",
        sizes=[(size, size) for size in SIZES],
        append_images=frames[1:],
    )
    print("占位图标已生成：%s" % TARGET)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
