"""PDF 解析、N-up 拼版计算与 Windows 打印核心逻辑。

所有拼版计算都与具体单位无关：调用方传入的宽高、边距用同一单位（厘米或设备像素）即可。
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Iterable, List, Optional, Sequence

try:  # PyMuPDF >= 1.24 推荐写法
    import pymupdf as fitz  # type: ignore
except ImportError:  # pragma: no cover
    import fitz  # type: ignore

from PIL import Image

log = logging.getLogger(__name__)

PT_PER_INCH = 72.0
MM_PER_INCH = 25.4

# 纸张尺寸（毫米，纵向）
PAPER_MM = {
    "A4": (210.0, 297.0),
    "A5": (148.0, 210.0),
    "Letter": (215.9, 279.4),
    "A3": (297.0, 420.0),
}

# Windows DEVMODE 纸张代号
PAPER_CODE = {"Letter": 1, "A3": 8, "A4": 9, "A5": 11}

DM_ORIENTATION = 0x00000001
DM_PAPERSIZE = 0x00000002
DM_IN_BUFFER = 0x00000008
DM_OUT_BUFFER = 0x00000002
DMORIENT_PORTRAIT = 1
DMORIENT_LANDSCAPE = 2


# --------------------------------------------------------------------------- #
# 数据结构
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class Rect:
    x: float
    y: float
    w: float
    h: float

    @property
    def right(self) -> float:
        return self.x + self.w

    @property
    def bottom(self) -> float:
        return self.y + self.h


@dataclass(frozen=True)
class LayoutSpec:
    """每张纸的版式参数。"""

    cols: int = 1
    rows: int = 2
    margin_cm: float = 0.51
    gap_cm: float = 0.0
    scale_pct: float = 95.5
    order: str = "row"  # row=先左右后上下（横向）  col=先上下后左右（纵向）
    auto_rotate: bool = False  # 默认不旋转；开启后会在旋转更省空间时把页面转 90°

    @property
    def per_sheet(self) -> int:
        return max(1, self.cols * self.rows)


@dataclass
class PageRef:
    """指向某个 PDF 的某一页。"""

    path: str
    index: int
    size_pt: tuple = (595.276, 841.890)
    label: str = ""

    @property
    def name(self) -> str:
        return self.label or f"{self.path}#{self.index + 1}"


# --------------------------------------------------------------------------- #
# 基础几何
# --------------------------------------------------------------------------- #
def paper_size_mm(paper: str, landscape: bool = False) -> tuple:
    w, h = PAPER_MM.get(paper, PAPER_MM["A4"])
    return (h, w) if landscape else (w, h)


def grid_cells(
    area_w: float,
    area_h: float,
    cols: int,
    rows: int,
    margin: float,
    gap: float = 0.0,
    order: str = "row",
) -> List[Rect]:
    """把整页可用区域切成 cols×rows 个格子，margin/gap 与 area 同单位。"""
    cols = max(1, int(cols))
    rows = max(1, int(rows))
    inner_w = max(area_w - 2 * margin - gap * (cols - 1), 1.0)
    inner_h = max(area_h - 2 * margin - gap * (rows - 1), 1.0)
    cw = inner_w / cols
    ch = inner_h / rows
    cells: List[Rect] = []
    if order == "col":
        iterable = [(c, r) for c in range(cols) for r in range(rows)]
    else:
        iterable = [(c, r) for r in range(rows) for c in range(cols)]
    for c, r in iterable:
        cells.append(Rect(margin + c * (cw + gap), margin + r * (ch + gap), cw, ch))
    return cells


def fit_page(
    page_w: float,
    page_h: float,
    cell: Rect,
    scale: float = 1.0,
    auto_rotate: bool = True,
) -> tuple:
    """等比缩放页面使其居中放入格子，返回 (目标矩形, 旋转角度)。

    scale 表示「页面最大只放到原尺寸的这个比例」（例如 0.955 即 95.5%）；
    若按该比例仍超出格子，则进一步缩小到刚好贴合格子 —— 与 Word 的
    「每张纸放置多页 + 缩放比例」行为一致。
    """
    candidates = [(page_w, page_h, 0)]
    if auto_rotate:
        candidates.append((page_h, page_w, 90))

    best = None
    for w, h, rot in candidates:
        if w <= 0 or h <= 0:
            continue
        factor = min(cell.w / w, cell.h / h, scale)
        if best is None or factor > best[2]:
            best = (w, h, factor, rot)

    if best is None:  # 异常页面尺寸，兜底
        return Rect(cell.x, cell.y, cell.w, cell.h), 0

    w, h, factor, rot = best
    fw, fh = w * factor, h * factor
    x = cell.x + (cell.w - fw) / 2.0
    y = cell.y + (cell.h - fh) / 2.0
    return Rect(x, y, fw, fh), rot


# --------------------------------------------------------------------------- #
# PDF 读取与渲染
# --------------------------------------------------------------------------- #
def scan_pdf(path: str) -> List[PageRef]:
    """读取 PDF 每一页的尺寸，生成 PageRef 列表。"""
    refs: List[PageRef] = []
    base = os.path.basename(path)
    with fitz.open(path) as doc:
        if doc.needs_pass:
            raise ValueError("PDF 已加密，无法打印")
        total = doc.page_count
        for i in range(total):
            r = doc[i].rect
            refs.append(
                PageRef(path=path, index=i, size_pt=(float(r.width), float(r.height)),
                        label=f"{base}#{i + 1}")
            )
    if not refs:
        raise ValueError("PDF 没有可打印的页面")
    return refs


def render_page(path: str, index: int, dpi: float = 300, rotate: int = 0,
                gray: bool = False) -> Image.Image:
    """把某一页渲染成 PIL 图像（按 dpi 输出自然尺寸）。

    ``gray=True`` 输出 8 位灰度：黑白激光打印机本来就要把彩色转灰度，
    先渲染彩色只是白白多花 3 倍内存和 CPU。
    """
    with fitz.open(path) as doc:
        page = doc[index]
        zoom = dpi / PT_PER_INCH
        colorspace = fitz.csGRAY if gray else fitz.csRGB
        pix = page.get_pixmap(
            matrix=fitz.Matrix(zoom, zoom), colorspace=colorspace, alpha=False
        )
        mode = "L" if gray else "RGB"
        img = Image.frombytes(mode, (pix.width, pix.height), pix.samples)
    if rotate:
        img = img.rotate(-rotate, expand=True)
    return img


def compose_sheet(
    refs: Sequence[Optional[PageRef]],
    spec: LayoutSpec,
    paper: str = "A4",
    landscape: bool = False,
    dpi: float = 110,
    render_dpi: float = 200,
    max_width: int = 900,
    show_guides: bool = True,
) -> Image.Image:
    """把若干页面拼成一张纸的图像，用于界面预览。"""
    w_mm, h_mm = paper_size_mm(paper, landscape)
    px_w = max(1, int(round(w_mm / MM_PER_INCH * dpi)))
    px_h = max(1, int(round(h_mm / MM_PER_INCH * dpi)))

    sheet = Image.new("RGB", (px_w, px_h), "white")
    margin = spec.margin_cm / 2.54 * dpi
    gap = spec.gap_cm / 2.54 * dpi
    cells = grid_cells(px_w, px_h, spec.cols, spec.rows, margin, gap, spec.order)

    for cell, ref in zip(cells, list(refs) + [None] * len(cells)):
        if ref is None:
            continue
        pw = ref.size_pt[0] / PT_PER_INCH * dpi
        ph = ref.size_pt[1] / PT_PER_INCH * dpi
        rect, rot = fit_page(pw, ph, cell, spec.scale_pct / 100.0, spec.auto_rotate)
        img = render_page(ref.path, ref.index, dpi=render_dpi, rotate=rot)
        target = (max(1, int(round(rect.w))), max(1, int(round(rect.h))))
        if img.size != target:
            img = img.resize(target, Image.LANCZOS)
        sheet.paste(img, (int(round(rect.x)), int(round(rect.y))))

    if max_width and px_w > max_width:
        ratio = max_width / px_w
        sheet = sheet.resize((max_width, max(1, int(px_h * ratio))), Image.LANCZOS)

    if show_guides:
        _draw_guides(sheet, cells, px_w, px_h, max_width, dpi)
    return sheet


def _draw_guides(sheet: Image.Image, cells: List[Rect], px_w: int, px_h: int,
                 max_width: int, dpi: float) -> None:
    """预览上描一层很淡的纸张/格子辅助线，不影响打印。"""
    from PIL import ImageDraw

    ratio = (sheet.width / px_w) if px_w else 1.0
    draw = ImageDraw.Draw(sheet)
    for cell in cells:
        box = [cell.x * ratio, cell.y * ratio, cell.right * ratio, cell.bottom * ratio]
        if (cell.w * ratio) > 3 and (cell.h * ratio) > 3:
            draw.rectangle(box, outline=(226, 232, 240))
    draw.rectangle([0, 0, sheet.width - 1, sheet.height - 1], outline=(203, 213, 225))


# --------------------------------------------------------------------------- #
# Windows 打印
# --------------------------------------------------------------------------- #
def list_printers() -> tuple:
    """返回 (打印机名列表, 系统默认打印机)。"""
    import win32print

    names: List[str] = []
    try:
        flags = win32print.PRINTER_ENUM_LOCAL | win32print.PRINTER_ENUM_CONNECTIONS
        names = [p["pPrinterName"] for p in win32print.EnumPrinters(flags, None, 2)]
    except Exception:  # pragma: no cover
        log.warning("枚举打印机失败", exc_info=True)
    try:
        default = win32print.GetDefaultPrinter()
    except Exception:
        default = ""
    return names, default


def _build_devmode(printer: str, paper: str, landscape: bool):
    """按设置生成 DEVMODE；失败则返回 None（使用打印机默认设置）。"""
    import win32print

    hprinter = win32print.OpenPrinter(printer)
    try:
        dm = win32print.DocumentProperties(None, hprinter, printer, None, None, 0)
        if dm is None or not hasattr(dm, "Fields"):
            return None
        dm.Orientation = DMORIENT_LANDSCAPE if landscape else DMORIENT_PORTRAIT
        code = PAPER_CODE.get(paper)
        if code:
            dm.PaperSize = code
        dm.Fields = int(dm.Fields) | DM_ORIENTATION | DM_PAPERSIZE
        return win32print.DocumentProperties(
            None, hprinter, printer, dm, dm, DM_IN_BUFFER | DM_OUT_BUFFER
        )
    finally:
        win32print.ClosePrinter(hprinter)


def _create_dc(printer: str, paper: str, landscape: bool):
    import win32gui

    devmode = None
    try:
        devmode = _build_devmode(printer, paper, landscape)
    except Exception:
        log.warning("设置纸张/方向失败，改用打印机默认设置", exc_info=True)
    return win32gui.CreateDC("WINSPOOL", printer, devmode)


def print_sheets(
    printer: str,
    sheets: Sequence[Sequence[Optional[PageRef]]],
    spec: LayoutSpec,
    paper: str = "A4",
    landscape: bool = False,
    copies: int = 1,
    render_dpi: int = 300,
    job_name: str = "发票打印",
    on_page=None,
    output_file: Optional[str] = None,
    engine: str = "auto",
    gray: bool = True,
) -> int:
    """把拼好的版面送到打印机。返回实际输出的纸张数。

    ``sheets`` 每一项是一张纸，内部是按版式顺序排列的 PageRef（None 表示留空）。

    ``engine`` 决定走哪条路线：

    * ``auto``（默认）—— 探测到 SumatraPDF 就走矢量，否则退回 GDI 位图
    * ``sumatrapdf`` —— 强制矢量；找不到引擎直接报错，不静默降级
    * ``raster`` —— 强制位图兼容模式

    矢量路线的收益在体积：同一张 A4，位图要往打印队列送约 26MB，
    矢量 PDF 只有几十 KB，红章和 1pt 细线也不会在重采样里被磨掉。

    ``gray`` 只对位图路线生效：按 8 位灰度渲染并送印，黑白激光打印机
    能把队列数据压到 1/4，彩色激光机可以关掉。

    ``output_file`` 是位图路线的调试出口（例如打到 Microsoft Print to PDF），
    指定时强制走位图，保持原有语义。
    """
    if not printer:
        raise RuntimeError("未找到可用的打印机")
    if not sheets:
        return 0

    if output_file is None:
        from . import printers  # 延迟导入：printers → pdfout → engine 会绕回来

        if printers.resolve_engine(engine) == printers.ENGINE_VECTOR:
            return _print_vector(
                printer, sheets, spec, paper, landscape, copies, job_name, on_page
            )

    return _print_raster(
        printer, sheets, spec, paper, landscape, copies, render_dpi, job_name,
        on_page, output_file, gray,
    )


def _print_vector(
    printer: str,
    sheets: Sequence[Sequence[Optional[PageRef]]],
    spec: LayoutSpec,
    paper: str,
    landscape: bool,
    copies: int,
    job_name: str,
    on_page=None,
) -> int:
    """矢量路线：合成 A4 拼版 PDF → 交给 SumatraPDF 静默打印 → 删临时文件。"""
    import tempfile

    from . import pdfout, printers

    fd, tmp_path = tempfile.mkstemp(prefix="fapiao_", suffix=".pdf")
    os.close(fd)
    try:
        pdfout.compose_to_file(
            sheets, spec, tmp_path, paper, landscape, title=job_name
        )
        printers.print_pdf(tmp_path, printer, copies=copies, job_name=job_name)
        printed = len(sheets) * max(1, int(copies))
        log.info("矢量打印完成：%s → %s，%d 张纸", job_name, printer, printed)
    finally:
        try:
            os.remove(tmp_path)
        except OSError:
            pass

    if on_page:
        try:
            on_page(printed)
        except Exception:  # 进度回调出错不能影响已经完成的打印
            log.warning("打印进度回调失败", exc_info=True)
    return printed


def _print_raster(
    printer: str,
    sheets: Sequence[Sequence[Optional[PageRef]]],
    spec: LayoutSpec,
    paper: str = "A4",
    landscape: bool = False,
    copies: int = 1,
    render_dpi: int = 300,
    job_name: str = "发票打印",
    on_page=None,
    output_file: Optional[str] = None,
    gray: bool = True,
) -> int:
    """位图路线：逐页渲染成位图贴到打印机 DC。零依赖，兼容性最好。"""
    from . import gdi

    copies = max(1, int(copies))
    hdc = _create_dc(printer, paper, landscape)
    printed = 0
    try:
        dpi_x = gdi.get_caps(hdc, gdi.LOGPIXELSX) or 300
        dpi_y = gdi.get_caps(hdc, gdi.LOGPIXELSY) or dpi_x
        page_w = gdi.get_caps(hdc, gdi.HORZRES) or 0
        page_h = gdi.get_caps(hdc, gdi.VERTRES) or 0
        margin = spec.margin_cm / 2.54 * dpi_x
        gap = spec.gap_cm / 2.54 * dpi_x
        cells = grid_cells(page_w, page_h, spec.cols, spec.rows, margin, gap, spec.order)
        log.info("打印 %s：%d×%d 设备像素 @ %ddpi，%d 张纸", printer, page_w, page_h, dpi_x, len(sheets))

        gdi.start_doc(hdc, job_name, output_file)
        try:
            for sheet in sheets:
                for _ in range(copies):
                    gdi.start_page(hdc)
                    for cell, ref in zip(cells, sheet):
                        if ref is not None:
                            _draw_page(hdc, ref, cell, spec, dpi_x, dpi_y, render_dpi, gdi, gray)
                    gdi.end_page(hdc)
                    printed += 1
                if on_page:
                    on_page(printed)
        except Exception:
            gdi.abort_doc(hdc)  # 出错时丢弃未完成的页，避免打一半
            raise
        gdi.end_doc(hdc)
    finally:
        gdi.delete_dc(hdc)
    return printed


def effective_render_dpi(render_dpi, device_dpi) -> int:
    """算出这一页实际使用的渲染 DPI。

    规则：以用户设置为准，但不超过设备分辨率（再高只是插值放大，
    白烧内存和 CPU，纸上不会有任何增益），并限制在 150～600 之间。
    设备 600dpi 时把设置改成 300，渲染就真的是 300。
    """
    device = int(device_dpi) if device_dpi and device_dpi > 0 else 600
    return max(150, min(int(render_dpi or 300), device, 600))


def _draw_page(hdc, ref: PageRef, cell: Rect, spec: LayoutSpec,
               dpi_x: float, dpi_y: float, render_dpi: int, gdi,
               gray: bool = False) -> None:
    page_w = ref.size_pt[0] / PT_PER_INCH * dpi_x
    page_h = ref.size_pt[1] / PT_PER_INCH * dpi_y
    rect, rot = fit_page(page_w, page_h, cell, spec.scale_pct / 100.0, spec.auto_rotate)

    target = (max(1, int(round(rect.w))), max(1, int(round(rect.h))))
    dpi = effective_render_dpi(render_dpi, dpi_x)
    img = render_page(ref.path, ref.index, dpi=dpi, rotate=rot, gray=gray)
    # 一行日志把「设置多少、实际多少、落到设备像素多大」钉死，
    # 出纸清晰度有争议时不用再猜到底是哪一档生效了。
    log.info(
        "渲染 %s 第 %d 页：设置 %ddpi → 实际 %ddpi（设备 %ddpi，%s），源图 %d×%d 像素，贴图 %d×%d",
        os.path.basename(ref.path), ref.index + 1,
        int(render_dpi or 300), dpi, int(dpi_x),
        "灰度" if gray else "彩色",
        img.width, img.height, target[0], target[1],
    )
    if img.size != target:
        img = img.resize(target, Image.LANCZOS)

    gdi.draw_image(hdc, img, int(round(rect.x)), int(round(rect.y)), target[0], target[1])
