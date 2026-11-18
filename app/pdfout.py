"""矢量拼版：把 PDF 页面按版式合成到整页纸上，全程不栅格化。

这是对原来「把每页渲染成位图，再 StretchDIBits 贴到打印机 DC」的替代路线。
位图路线一张 A4 @300dpi 要往打印队列塞约 26MB（32 位 BGRA），而矢量 PDF
只有几十 KB：送得快、红章和 1pt 细线也不会在重采样里被磨掉。

注意：本模块只负责**生成**矢量 PDF，怎么把它送到打印机由 :mod:`app.printers`
决定（SumatraPDF / 系统 PDF 阅读器 / 存盘后手动打）。
"""
from __future__ import annotations

import os
from typing import Dict, List, Optional, Sequence

try:  # PyMuPDF >= 1.24 推荐写法
    import pymupdf as fitz  # type: ignore
except ImportError:  # pragma: no cover
    import fitz  # type: ignore

from .engine import LayoutSpec, PageRef, fit_page, grid_cells, paper_size_mm

PT_PER_MM = 72.0 / 25.4


def paper_size_pt(paper: str, landscape: bool = False) -> tuple:
    """纸张尺寸（PostScript 点，1pt = 1/72 英寸）。"""
    w_mm, h_mm = paper_size_mm(paper, landscape)
    return w_mm * PT_PER_MM, h_mm * PT_PER_MM


class _DocCache:
    """同一次拼版里复用源文档句柄 —— 一张发票多页时不必反复开关文件。"""

    def __init__(self) -> None:
        self._docs: Dict[str, "fitz.Document"] = {}

    def get(self, path: str) -> "fitz.Document":
        doc = self._docs.get(path)
        if doc is None:
            doc = fitz.open(path)
            if doc.needs_pass:
                doc.close()
                raise ValueError(f"PDF 已加密，无法打印：{os.path.basename(path)}")
            self._docs[path] = doc
        return doc

    def close(self) -> None:
        for doc in self._docs.values():
            try:
                doc.close()
            except Exception:
                pass
        self._docs.clear()

    def __enter__(self) -> "_DocCache":
        return self

    def __exit__(self, *exc_info) -> None:
        self.close()


def compose_sheet_pdf(
    sheets: Sequence[Sequence[Optional[PageRef]]],
    spec: LayoutSpec,
    paper: str = "A4",
    landscape: bool = False,
    title: str = "",
) -> "fitz.Document":
    """把若干张纸的版式合成一个**矢量** PDF，返回已打开的 fitz 文档。

    ``sheets`` 的结构与 :func:`app.engine.print_sheets` 完全一致：
    外层是「第几张纸」，内层是按版式顺序排列的页面引用，``None`` 表示留白。

    调用方负责 ``doc.close()``；也可以用 :func:`compose_to_file` 直接落盘。
    """
    w_pt, h_pt = paper_size_pt(paper, landscape)
    margin = spec.margin_cm * 10.0 * PT_PER_MM
    gap = spec.gap_cm * 10.0 * PT_PER_MM

    out = fitz.open()
    try:
        with _DocCache() as cache:
            for sheet in sheets:
                page = out.new_page(width=w_pt, height=h_pt)
                cells = grid_cells(
                    w_pt, h_pt, spec.cols, spec.rows, margin, gap, spec.order
                )
                for cell, ref in zip(cells, list(sheet) + [None] * len(cells)):
                    if ref is None:
                        continue
                    src = cache.get(ref.path)
                    pw, ph = ref.size_pt
                    rect, rot = fit_page(
                        pw, ph, cell, spec.scale_pct / 100.0, spec.auto_rotate
                    )
                    try:
                        page.show_pdf_page(
                            fitz.Rect(rect.x, rect.y, rect.right, rect.bottom),
                            src,
                            ref.index,
                            rotate=rot,
                        )
                    except Exception as exc:
                        raise RuntimeError(
                            f"拼版失败：{os.path.basename(ref.path)} "
                            f"第 {ref.index + 1} 页（{exc}）"
                        ) from exc

        if out.page_count == 0:
            raise ValueError("没有可拼版的页面")

        if title:
            try:
                out.set_metadata({"title": title, "producer": "发票打印助手"})
            except Exception:
                pass
        return out
    except Exception:
        out.close()
        raise


def compose_to_file(
    sheets: Sequence[Sequence[Optional[PageRef]]],
    spec: LayoutSpec,
    path: str,
    paper: str = "A4",
    landscape: bool = False,
    title: str = "",
) -> str:
    """合成矢量 PDF 并保存到 ``path``，返回路径。"""
    doc = compose_sheet_pdf(sheets, spec, paper, landscape, title)
    try:
        doc.save(path, garbage=3, deflate=True)
    finally:
        doc.close()
    return path


def compose_to_bytes(
    sheets: Sequence[Sequence[Optional[PageRef]]],
    spec: LayoutSpec,
    paper: str = "A4",
    landscape: bool = False,
    title: str = "",
) -> bytes:
    """合成矢量 PDF 并直接返回字节流（测试 / 内存中转用）。"""
    doc = compose_sheet_pdf(sheets, spec, paper, landscape, title)
    try:
        return doc.tobytes(garbage=3, deflate=True)
    finally:
        doc.close()
