"""生成程序图标 assets/icon.ico（打包、任务栏、右键菜单都用到）。

图标是**程序化绘制的打印机**，不依赖任何外部素材：
蓝色圆角底 + 白色打印机 + 从进纸口出来的发票页。

小尺寸单独处理：16/24 px 把打印机主体放大一点、去掉状态灯这类细节，
否则 16 px 在标题栏里会糊成一团白块。每个尺寸各画一遍，不靠缩一张大图。

重画：``python tools/make_icon.py``（顺带输出 assets/icon_preview.png 供肉眼检查）。
"""
from __future__ import annotations

import io
import struct
import sys
from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "assets" / "icon.ico"
PREVIEW = ROOT / "assets" / "icon_preview.png"

BASE = 1024          # 所有坐标按 1024 设计
MASTER = 2048        # 先按 2 倍画，再缩小，边缘更干净

BG_TOP = (59, 130, 246)
BG_BOTTOM = (29, 78, 216)
WHITE = (255, 255, 255, 255)
PAPER_LINE = (191, 208, 233, 255)
OUT_PAPER = (206, 222, 245, 255)
SLOT = (23, 55, 138, 255)
LED = (52, 211, 153, 255)

SIZES = [16, 20, 24, 32, 40, 48, 64, 128, 256]
# 尺寸 → 打印机主体的放大倍数：小图放大，大图原样
ZOOM = {16: 1.24, 20: 1.18, 24: 1.12, 32: 1.04}
CORNER = 232         # 蓝色圆角底的圆角半径（1024 空间）


def _scale(v: float, size: int) -> float:
    return v * size / BASE


def _ico_entries(frames: dict[int, Image.Image]) -> bytes:
    """手写 ICO 容器：每个尺寸一帧 PNG。

    PIL 的 ``save(sizes=...)`` 只能从同一张大图缩，做不到「每个尺寸各画一遍」，
    所以这里自己拼 ICONDIR / ICONDIRENTRY（Vista 起支持 PNG 帧）。
    """
    blobs = []
    for size, img in frames.items():
        buf = io.BytesIO()
        img.resize((size, size), Image.LANCZOS).save(buf, format="PNG")
        blobs.append((size, buf.getvalue()))

    head = struct.pack("<HHH", 0, 1, len(blobs))
    offset = len(head) + 16 * len(blobs)
    entries = b""
    for size, data in blobs:
        entries += struct.pack(
            "<BBBBHHII",
            0 if size >= 256 else size,
            0 if size >= 256 else size,
            0, 0, 1, 32, len(data), offset,
        )
        offset += len(data)
    return head + entries + b"".join(data for _, data in blobs)


def render(size: int = MASTER) -> Image.Image:
    """按目标尺寸画一张（含底、含全部细节），返回 RGBA。"""
    zoom = ZOOM.get(size, 1.0)
    cx = cy = BASE / 2

    def z(v: float) -> float:
        return cx + (v - cx) * zoom

    def b(x0, y0, x1, y1):
        # 小尺寸下一条 22/1024 的细线会缩成 0 像素，这里兜底保底 1 px
        x0s, y0s = _scale(z(x0), size), _scale(z(y0), size)
        x1s, y1s = _scale(z(x1), size) - 1, _scale(z(y1), size) - 1
        return [x0s, y0s, max(x1s, x0s + 1), max(y1s, y0s + 1)]

    def r(v: float) -> int:
        return max(1, int(round(_scale(v, size))))

    # 蓝色圆角底（渐变 + 圆角蒙版），不跟着 zoom 变
    grad = Image.new("RGB", (1, size))
    for y in range(size):
        t = y / max(1, size - 1)
        grad.putpixel(
            (0, y),
            tuple(int(BG_TOP[i] + (BG_BOTTOM[i] - BG_TOP[i]) * t) for i in range(3)),
        )
    grad = grad.resize((size, size))
    mask = Image.new("L", (size, size), 0)
    rr = max(2, int(round(_scale(CORNER, size))))
    ImageDraw.Draw(mask).rounded_rectangle(
        [0, 0, size - 1, size - 1], radius=rr, fill=255
    )
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    img.paste(grad, (0, 0), mask)
    d = ImageDraw.Draw(img)

    # 1) 出纸口外面那张纸（打印好的发票，躲在机器下沿后面）
    d.rounded_rectangle(b(318, 590, 706, 900), radius=r(26), fill=OUT_PAPER)

    # 2) 进纸口上方那张纸 + 内容线（16 px 只画两条，免得糊）
    d.rounded_rectangle(b(318, 138, 706, 420), radius=r(24), fill=WHITE)
    if size >= 32:
        lines = ((300, 214), (300, 266), (206, 318))
    elif size >= 24:
        lines = ((300, 220), (300, 280))
    else:
        lines = ()          # 16 px 放不下内容线，画了也是噪点
    for width, y in lines:
        d.rounded_rectangle(b(360, y, 360 + width, y + 22), radius=r(11), fill=PAPER_LINE)

    # 3) 打印机机身（白）
    d.rounded_rectangle(b(150, 350, 874, 774), radius=r(66), fill=WHITE)

    # 4) 机身顶部的进纸口：一条深色横槽，纸从这里进去
    d.rounded_rectangle(b(280, 368, 744, 442), radius=r(24), fill=SLOT)

    # 5) 机身下沿的出纸缝 + 状态灯（小图省掉，省得一像素噪点）
    if size >= 24:
        d.rounded_rectangle(b(318, 742, 706, 768), radius=r(13), fill=OUT_PAPER)
        d.ellipse(b(744, 686, 800, 742), fill=LED)

    return img


def main() -> None:
    OUT.parent.mkdir(parents=True, exist_ok=True)
    frames = {s: render(s) for s in SIZES}
    OUT.write_bytes(_ico_entries(frames))
    frames[256].save(OUT.with_name("icon.png"))

    # 预览：浅色/深色底各排一行，检查小尺寸是否还看得清
    show = [16, 24, 32, 48, 64, 128, 256]
    pad = 16
    cell = 256
    sheet = Image.new(
        "RGB",
        (sum(show) + pad * (len(show) + 1), 2 * (cell + pad) + pad),
        (243, 246, 251),
    )
    for row, bg in enumerate(((243, 246, 251), (32, 38, 52))):
        band_y = row * (cell + 2 * pad)
        band = Image.new("RGB", (sheet.width, cell + pad), bg)
        sheet.paste(band, (0, band_y))
        x = pad
        for s in show:
            sheet.paste(frames[s], (x, band_y + (cell - s) // 2), frames[s])
            x += s + pad
    sheet.save(PREVIEW)

    print(f"图标已生成：{OUT}（{len(SIZES)} 个尺寸）")
    print(f"预览图：{PREVIEW}")


if __name__ == "__main__":
    sys.exit(main())
