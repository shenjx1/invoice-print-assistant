"""直接用 ctypes 调用 GDI，完成「打印机 DC → 打印任务 → 贴图」的全过程。

不依赖 pywin32 的打印封装，也不依赖 PIL 的 Windows 扩展，
打包体积更小、行为更可控。
"""
from __future__ import annotations

import ctypes
from ctypes import wintypes

from PIL import Image

# 常用设备参数
HORZRES = 8
VERTRES = 10
LOGPIXELSX = 88
LOGPIXELSY = 90
PHYSICALOFFSETX = 112
PHYSICALOFFSETY = 113

HALFTONE = 4
DIB_RGB_COLORS = 0
SRCCOPY = 0x00CC0020

gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [
        ("biSize", wintypes.DWORD),
        ("biWidth", ctypes.c_long),
        ("biHeight", ctypes.c_long),
        ("biPlanes", wintypes.WORD),
        ("biBitCount", wintypes.WORD),
        ("biCompression", wintypes.DWORD),
        ("biSizeImage", wintypes.DWORD),
        ("biXPelsPerMeter", ctypes.c_long),
        ("biYPelsPerMeter", ctypes.c_long),
        ("biClrUsed", wintypes.DWORD),
        ("biClrImportant", wintypes.DWORD),
    ]


class RGBQUAD(ctypes.Structure):
    _fields_ = [
        ("rgbBlue", ctypes.c_ubyte),
        ("rgbGreen", ctypes.c_ubyte),
        ("rgbRed", ctypes.c_ubyte),
        ("rgbReserved", ctypes.c_ubyte),
    ]


class BITMAPINFO(ctypes.Structure):
    """8 位灰度必须带 256 色调色板，否则 GDI 会按随机颜色解释像素。"""

    _fields_ = [
        ("bmiHeader", BITMAPINFOHEADER),
        ("bmiColors", RGBQUAD * 256),
    ]


def _build_gray_info(width: int, height: int, size: int) -> BITMAPINFO:
    info = BITMAPINFO()
    info.bmiHeader.biSize = ctypes.sizeof(BITMAPINFOHEADER)
    info.bmiHeader.biWidth = width
    info.bmiHeader.biHeight = -height      # 负值 = 自上而下
    info.bmiHeader.biPlanes = 1
    info.bmiHeader.biBitCount = 8
    info.bmiHeader.biCompression = 0       # BI_RGB
    info.bmiHeader.biSizeImage = size
    info.bmiHeader.biClrUsed = 256
    for i in range(256):
        info.bmiColors[i].rgbRed = i
        info.bmiColors[i].rgbGreen = i
        info.bmiColors[i].rgbBlue = i
    return info


class DOCINFOW(ctypes.Structure):
    _fields_ = [
        ("cbSize", ctypes.c_int),
        ("lpszDocName", wintypes.LPCWSTR),
        ("lpszOutput", wintypes.LPCWSTR),
        ("lpszDatatype", wintypes.LPCWSTR),
        ("fwType", wintypes.DWORD),
    ]


# 函数签名（让 64 位句柄/指针被正确传递）
gdi32.GetDeviceCaps.argtypes = [wintypes.HDC, ctypes.c_int]
gdi32.GetDeviceCaps.restype = ctypes.c_int
gdi32.StartDocW.argtypes = [wintypes.HDC, ctypes.POINTER(DOCINFOW)]
gdi32.StartDocW.restype = ctypes.c_int
gdi32.StartPage.argtypes = [wintypes.HDC]
gdi32.StartPage.restype = ctypes.c_int
gdi32.EndPage.argtypes = [wintypes.HDC]
gdi32.EndPage.restype = ctypes.c_int
gdi32.EndDoc.argtypes = [wintypes.HDC]
gdi32.EndDoc.restype = ctypes.c_int
gdi32.AbortDoc.argtypes = [wintypes.HDC]
gdi32.AbortDoc.restype = ctypes.c_int
gdi32.DeleteDC.argtypes = [wintypes.HDC]
gdi32.DeleteDC.restype = wintypes.BOOL
gdi32.SetStretchBltMode.argtypes = [wintypes.HDC, ctypes.c_int]
gdi32.SetStretchBltMode.restype = ctypes.c_int
gdi32.StretchDIBits.argtypes = [
    wintypes.HDC, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
    ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
    ctypes.c_void_p, ctypes.c_void_p, wintypes.UINT, wintypes.DWORD,
]
gdi32.StretchDIBits.restype = ctypes.c_int


def hdc_ptr(handle) -> wintypes.HDC:
    """把 pywin32 返回的 HDC 转成可以被 ctypes 正确使用的句柄。"""
    return wintypes.HDC(int(handle))


def get_caps(handle, index: int) -> int:
    return int(gdi32.GetDeviceCaps(hdc_ptr(handle), index))


def last_error() -> int:
    return ctypes.get_last_error()


def start_doc(handle, job_name: str, output_file: str | None = None) -> int:
    info = DOCINFOW()
    info.cbSize = ctypes.sizeof(DOCINFOW)
    info.lpszDocName = job_name
    info.lpszOutput = output_file
    info.lpszDatatype = None
    info.fwType = 0
    job = gdi32.StartDocW(hdc_ptr(handle), ctypes.byref(info))
    if job <= 0:
        raise OSError(f"无法开始打印任务（错误码 {last_error()}）")
    return job


def start_page(handle) -> None:
    if gdi32.StartPage(hdc_ptr(handle)) <= 0:
        raise OSError(f"无法开始新的一页（错误码 {last_error()}）")


def end_page(handle) -> None:
    if gdi32.EndPage(hdc_ptr(handle)) <= 0:
        raise OSError(f"无法结束当前页（错误码 {last_error()}）")


def end_doc(handle) -> None:
    if gdi32.EndDoc(hdc_ptr(handle)) <= 0:
        raise OSError(f"无法结束打印任务（错误码 {last_error()}）")


def abort_doc(handle) -> None:
    try:
        gdi32.AbortDoc(hdc_ptr(handle))
    except Exception:
        pass


def delete_dc(handle) -> None:
    try:
        gdi32.DeleteDC(hdc_ptr(handle))
    except Exception:
        pass


def draw_image(handle, image, x: int, y: int, w: int, h: int) -> None:
    """把 PIL 图像拉伸贴到打印机 DC 的指定矩形（左上角为原点）。

    灰度图（mode="L"）走 8 位 DIB，每像素 1 字节；彩色走 24 位 BGR。
    黑白激光打印机的发票场景下，8 位能把送进打印队列的数据压到 1/4，
    而且驱动本来就要把彩色转灰度，彩色渲染纯属白烧 CPU 和内存。
    两者都要求每行按 4 字节对齐，宽度不足时补零。
    """
    if w <= 0 or h <= 0:
        return
    if image.mode == "L":
        data, info, sw, sh = gray_dib(image)
    else:
        data, info, sw, sh = color_dib(image)
    _blit(handle, data, info, sw, sh, x, y, w, h)


def gray_dib(image: Image.Image):
    """灰度 → 8 位 DIB 载荷，返回 (字节, BITMAPINFO, 宽, 高)。

    8 位 DIB 必须带 256 级调色板，否则 GDI 会把像素当随机颜色解释；
    每行必须 4 字节对齐，宽度不是 4 的倍数时补白列（补白会被拉伸忽略）。
    """
    img = image if image.mode == "L" else image.convert("L")
    stride = (img.width + 3) & ~3
    if stride != img.width:
        padded = Image.new("L", (stride, img.height), 255)
        padded.paste(img, (0, 0))
        data = padded.tobytes()
    else:
        data = img.tobytes()
    return data, _build_gray_info(img.width, img.height, len(data)), img.width, img.height


def color_dib(image: Image.Image):
    """彩色 → 24 位 BGR DIB 载荷，返回 (字节, BITMAPINFOHEADER, 宽, 高)。

    发票没有透明通道，丢掉 alpha 让送进打印队列的数据少四分之一
    （一张 A4 @300dpi 约 33MB → 25MB），出纸速度直接受益。
    每行同样按 4 字节对齐：宽度×3 不是 4 的倍数时补零。
    """
    img = image.convert("RGB")
    width, height = img.size
    stride = (width * 3 + 3) & ~3
    data = img.tobytes("raw", "BGR")
    if stride != width * 3:
        row = width * 3
        filler = b"\x00" * (stride - row)
        data = b"".join(data[i * row:(i + 1) * row] + filler for i in range(height))

    header = BITMAPINFOHEADER()
    header.biSize = ctypes.sizeof(BITMAPINFOHEADER)
    header.biWidth = width
    header.biHeight = -height  # 负值表示自上而下的像素顺序
    header.biPlanes = 1
    header.biBitCount = 24
    header.biCompression = 0  # BI_RGB
    header.biSizeImage = len(data)
    return data, header, width, height


def _blit(handle, data: bytes, info, sw: int, sh: int,
          x: int, y: int, w: int, h: int) -> None:
    """把一份 DIB 载荷拉伸贴到 DC 上（灰度/彩色共用）。"""
    gdi32.SetStretchBltMode(hdc_ptr(handle), HALFTONE)
    # 直接把 Python bytes 的缓冲区交给 GDI 读取，省掉一次大块内存拷贝
    bits = ctypes.cast(ctypes.c_char_p(data), ctypes.c_void_p)
    copied = gdi32.StretchDIBits(
        hdc_ptr(handle), x, y, w, h,
        0, 0, sw, sh,
        bits, ctypes.byref(info),
        DIB_RGB_COLORS, SRCCOPY,
    )
    if copied <= 0:
        # 不能忽略绘制失败后继续 EndDoc：那会把空白/缺图作业误记成“已打印”。
        raise OSError(f"绘制打印页面失败（错误码 {last_error()}）")
