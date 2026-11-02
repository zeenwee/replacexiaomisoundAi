#!/usr/bin/env python3
"""生成「小智控制台」的 App 图标。

    ./xiaogpt-env/bin/python tools/make_icon.py

产出：
    console/assets/icon.icns   （PyInstaller spec 里引用这个）

画法：深蓝→青的竖向渐变圆角方块 + 白色声波条。
macOS 的图标规范要求图形缩到画布的 ~82%（四周留白），这里按这个来。
"""

from __future__ import annotations

import math
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT_DIR = ROOT / "console" / "assets"
BASE = OUT_DIR / "icon_1024.png"
ICONSET = OUT_DIR / "icon.iconset"
ICNS = OUT_DIR / "icon.icns"

SIZES = [
    (16, 1), (32, 2), (32, 1), (64, 2), (128, 2), (256, 2),
    (256, 1), (512, 2), (512, 1), (1024, 1),
]

# 声波条：(中心的 x 比例, 粗细比例, 半高比例)
BARS = [
    (0.345, 0.046, 0.085),
    (0.415, 0.046, 0.155),
    (0.485, 0.046, 0.225),
    (0.555, 0.046, 0.150),
    (0.625, 0.046, 0.082),
]


def draw(bleed: bool = False, out: Path | None = None) -> None:
    """画图标。

    bleed=False（默认）：macOS .app 用的圆角版，四周留白。
    bleed=True：PWA / 添加到主屏用的满幅版 —— 安卓会自己套形状，
    再带圆角和留白就会显得特别小。
    """
    import Quartz
    from Quartz import (
        CGColorCreateGenericRGB,
        CGColorSpaceCreateDeviceRGB,
        CGContextAddArc,
        CGContextAddLineToPoint,
        CGContextBeginPath,
        CGContextClosePath,
        CGContextClip,
        CGContextFillRect,
        CGContextMoveToPoint,
        CGContextSetFillColorWithColor,
        CGContextSetLineCap,
        CGContextSetLineWidth,
        CGContextSetStrokeColorWithColor,
        CGContextStrokePath,
        CGBitmapContextCreate,
        CGBitmapContextCreateImage,
        CGImageDestinationCreateWithURL,
        CGImageDestinationAddImage,
        CGImageDestinationFinalize,
        CGImageGetHeight,
        CGImageGetWidth,
        CGRectMake,
    )
    from CoreFoundation import (
        CFURLCreateWithFileSystemPath,
        kCFAllocatorDefault,
        kCFURLPOSIXPathStyle,
    )

    size = 1024
    cs = CGColorSpaceCreateDeviceRGB()
    info = Quartz.kCGImageAlphaPremultipliedLast | Quartz.kCGBitmapByteOrder32Big
    ctx = CGBitmapContextCreate(None, size, size, 8, 0, cs, info)
    if ctx is None:
        raise RuntimeError("建不了位图上下文")

    # macOS 图标留白：图形只占画布 ~83%；出血版满幅
    inset = 0 if bleed else size * 0.085
    side = size - inset * 2
    radius = side * 0.225

    # ---- 圆角遮罩（必须先裁剪，再画背景）----
    x0, y0 = inset, inset
    x1, y1 = inset + side, inset + side
    r = radius
    CGContextBeginPath(ctx)
    CGContextMoveToPoint(ctx, x0 + r, y0)
    CGContextAddLineToPoint(ctx, x1 - r, y0)
    CGContextAddArc(ctx, x1 - r, y0 + r, r, -math.pi / 2, 0, 0)
    CGContextAddLineToPoint(ctx, x1, y1 - r)
    CGContextAddArc(ctx, x1 - r, y1 - r, r, 0, math.pi / 2, 0)
    CGContextAddLineToPoint(ctx, x0 + r, y1)
    CGContextAddArc(ctx, x0 + r, y1 - r, r, math.pi / 2, math.pi, 0)
    CGContextAddLineToPoint(ctx, x0, y0 + r)
    CGContextAddArc(ctx, x0 + r, y0 + r, r, math.pi, 1.5 * math.pi, 0)
    CGContextClosePath(ctx)
    if not bleed:
        # 出血版不裁剪 —— 安卓会自己套形状，满幅才好看
        CGContextClip(ctx)

    # ---- 背景渐变（逐条色带，不依赖 CGGradient）----
    # CG 坐标系 y 往上，最后写 PNG 时会上下翻转 —— 这里按「顶深底青」调。
    steps = 256
    for i in range(steps):
        t = i / (steps - 1)
        col = CGColorCreateGenericRGB(
            0.10 + (0.09 - 0.10) * t,
            0.62 + (0.11 - 0.62) * t,
            0.74 + (0.26 - 0.74) * t,
            1.0,
        )
        CGContextSetFillColorWithColor(ctx, col)
        y = size * i / steps
        CGContextFillRect(ctx, CGRectMake(0, y, size, size / steps + 1.5))

    # ---- 声波条 ----
    white = CGColorCreateGenericRGB(1.0, 1.0, 1.0, 0.97)
    CGContextSetStrokeColorWithColor(ctx, white)
    CGContextSetLineCap(ctx, 1)  # kCGLineCapRound
    cx_mid = size * 0.5
    cy_mid = size * 0.5
    for x, w, h in BARS:
        bar_w = size * w
        cx = size * x
        half = size * h
        CGContextSetLineWidth(ctx, bar_w)
        CGContextMoveToPoint(ctx, cx, cy_mid - half)
        CGContextAddLineToPoint(ctx, cx, cy_mid + half)
        CGContextStrokePath(ctx)

    # ---- 存 PNG ----
    target = out or BASE
    target.parent.mkdir(parents=True, exist_ok=True)
    cgimg = CGBitmapContextCreateImage(ctx)
    url = CFURLCreateWithFileSystemPath(
        kCFAllocatorDefault, str(target), kCFURLPOSIXPathStyle, False
    )
    dest = CGImageDestinationCreateWithURL(url, "public.png", 1, None)
    if dest is None:
        raise RuntimeError("建不了图片目标")
    CGImageDestinationAddImage(dest, cgimg, None)
    if not CGImageDestinationFinalize(dest):
        raise RuntimeError("PNG 写不出来")
    print(f"✓ {target}  ({CGImageGetWidth(cgimg)}x{CGImageGetHeight(cgimg)})")


def make_iconset() -> None:
    if ICONSET.exists():
        subprocess.run(["rm", "-rf", str(ICONSET)], check=False)
    ICONSET.mkdir(parents=True, exist_ok=True)
    for px, scale in SIZES:
        out = ICONSET / (
            f"icon_{px}x{px}.png" if scale == 1 else f"icon_{px}x{px}@{scale}x.png"
        )
        subprocess.run(
            ["sips", "-z", str(px), str(px), str(BASE), "--out", str(out)],
            check=True,
            capture_output=True,
        )
    subprocess.run(
        ["iconutil", "-c", "icns", str(ICONSET), "-o", str(ICNS)], check=True
    )
    print(f"✓ {ICNS}")


def make_pwa_icons() -> None:
    """给浏览器版「添加到主屏」用的图标（满幅，安卓自己套形状）。"""
    web = ROOT / "console" / "web"
    web.mkdir(parents=True, exist_ok=True)
    src = web / "icon-1024.png"
    draw(bleed=True, out=src)
    for size in (512, 192):
        subprocess.run(
            ["sips", "-z", str(size), str(size), str(src),
             "--out", str(web / f"icon-{size}.png")],
            check=True,
            capture_output=True,
        )
    print(f"✓ {web}/icon-192.png / icon-512.png")


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    draw()
    make_iconset()
    make_pwa_icons()
    print("打包时引用：icon='console/assets/icon.icns'")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
