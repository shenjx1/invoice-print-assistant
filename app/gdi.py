"""直接用 ctypes 调用 GDI，完成「打印机 DC → 打印任务 → 贴图」的全过程。

不依赖 pywin32 的打印封装，也不依赖 PIL 的 Windows 扩展，
打包体积更小、行为更可控。
"""
from __future__ import annotations

import ctypes
from ctypes import wintypes

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

    使用 32 位 BGRA 的 DIB：字节序符合 GDI 要求，且每行天然 4 字节对齐。
    """
    if w <= 0 or h <= 0:
        return
    img = image.convert("RGBA")
    data = img.tobytes("raw", "BGRA")

    header = BITMAPINFOHEADER()
    header.biSize = ctypes.sizeof(BITMAPINFOHEADER)
    header.biWidth = img.width
    header.biHeight = -img.height  # 负值表示自上而下的像素顺序
    header.biPlanes = 1
    header.biBitCount = 32
    header.biCompression = 0  # BI_RGB
    header.biSizeImage = len(data)

    gdi32.SetStretchBltMode(hdc_ptr(handle), HALFTONE)
    # 直接把 Python bytes 的缓冲区交给 GDI 读取，省掉一次大块内存拷贝
    bits = ctypes.cast(ctypes.c_char_p(data), ctypes.c_void_p)
    copied = gdi32.StretchDIBits(
        hdc_ptr(handle), x, y, w, h,
        0, 0, img.width, img.height,
        bits, ctypes.byref(header),
        DIB_RGB_COLORS, SRCCOPY,
    )
    if copied <= 0:
        # 不能忽略绘制失败后继续 EndDoc：那会把空白/缺图作业误记成“已打印”。
        raise OSError(f"绘制打印页面失败（错误码 {last_error()}）")
