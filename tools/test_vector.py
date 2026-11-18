"""矢量拼版自测：合成 A4 拼版 PDF，验证「还是矢量」和体积下降。

跑法：:

    python tools\\test_vector.py

不需要打印机。会给每个样本生成 ``build_test/vector/<名字>.pdf`` 与同名 PNG 预览。
判定通过的三条：

1. 输出 PDF 里**文字仍可提取** —— 位图路线做不到，说明字体没被栅格化；
2. 体积比同版面的位图小一个数量级以上；
3. 页数与纸张数一致。
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

try:
    import pymupdf as fitz  # type: ignore
except ImportError:  # pragma: no cover
    import fitz  # type: ignore

from app import pdfout
from app.engine import LayoutSpec, scan_pdf

OUT_DIR = ROOT / "build_test" / "vector"
SAMPLES = [
    ROOT / "build_test" / "docs" / "inv_2024001.pdf",
    ROOT / "build_test" / "docs" / "inv_2024002.pdf",
    ROOT / "build_test" / "docs" / "inv_2024003.pdf",
]

# 一张 A4 @300dpi / 32 位 BGRA 的送队列体积，用来对比
RASTER_BYTES_PER_SHEET = int(297 / 25.4 * 300) * int(210 / 25.4 * 300) * 4


def _plan(refs, per_sheet=2):
    sheets, current = [], []
    for ref in refs:
        current.append(ref)
        if len(current) == per_sheet:
            sheets.append(current)
            current = []
    if current:
        while len(current) < per_sheet:
            current.append(None)
        sheets.append(current)
    return sheets


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    spec = LayoutSpec(cols=1, rows=2, margin_cm=0.51, scale_pct=95.5)

    refs = []
    for path in SAMPLES:
        if not path.is_file():
            print(f"跳过（不存在）：{path}")
            continue
        refs.extend(scan_pdf(str(path)))
    if not refs:
        print("没有可用样本，先跑一次 tools\\render_docs.py 生成测试发票")
        return 2

    sheets = _plan(refs, spec.per_sheet)
    print(f"输入 {len(refs)} 页 → {len(sheets)} 张纸（{spec.cols}×{spec.rows}）")

    target = OUT_DIR / "composed.pdf"
    pdfout.compose_to_file(sheets, spec, str(target), "A4", False, title="矢量拼版自测")
    size = target.stat().st_size
    per_sheet = size / max(1, len(sheets))

    with fitz.open(str(target)) as doc:
        pages = doc.page_count
        text = "".join(doc[i].get_text() for i in range(pages)).strip()
        images = sum(len(doc[i].get_images(full=True)) for i in range(pages))
        first = doc[0].rect

    print(f"输出：{target}")
    print(f"  页数        {pages}（应为 {len(sheets)}）")
    print(f"  页面尺寸    {first.width:.1f} × {first.height:.1f} pt（A4 = 595.3 × 841.9）")
    print(f"  文件体积    {size / 1024:.1f} KB（每张纸 {per_sheet / 1024:.1f} KB）")
    print(f"  页面内图片  {images} 个")
    print(f"  可提取文字  {len(text)} 字：{text[:60].replace(chr(10), ' ')}…")

    # 预览图，肉眼核对拼版位置
    with fitz.open(str(target)) as doc:
        for i in range(doc.page_count):
            png = OUT_DIR / f"composed_p{i + 1}.png"
            doc[i].get_pixmap(matrix=fitz.Matrix(150 / 72, 150 / 72)).save(str(png))
            print(f"  预览        {png}")

    checks = [
        ("文字未栅格化", len(text) > 20),
        ("页数匹配", pages == len(sheets)),
        ("体积远小于位图", per_sheet < RASTER_BYTES_PER_SHEET * 0.05),
        ("A4 尺寸正确", abs(first.width - 595.28) < 1 and abs(first.height - 841.89) < 1),
    ]
    print()
    ok = True
    for name, passed in checks:
        print(f"  [{'PASS' if passed else 'FAIL'}] {name}")
        ok = ok and passed

    ratio = RASTER_BYTES_PER_SHEET / per_sheet if per_sheet else 0
    print(f"\n每张纸：位图约 {RASTER_BYTES_PER_SHEET / 1048576:.1f} MB → 矢量 {per_sheet / 1024:.1f} KB"
          f"（约 1/{ratio:.0f}）")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
