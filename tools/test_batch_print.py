"""端到端验证：多张发票逐张独立打印（输出到 Microsoft Print to PDF，不耗纸）。

验证点：
1) 每个 PDF 单独一条打印作业、单独一个输出文件；
2) 每张纸里只出现本发票的页面；
3) 3 页发票按页序拆成 2 张纸，最后一张剩余格留白。
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import pymupdf as fitz  # noqa: E402

from app.engine import LayoutSpec, print_sheets  # noqa: E402
from app.invoice import PAD_REPEAT, plan_sheets, read_invoice  # noqa: E402

OUT = ROOT / "build_test"
PRINTER = "Microsoft Print to PDF"
SPEC = LayoutSpec(cols=1, rows=2, margin_cm=0.51, scale_pct=95.5)


def main() -> int:
    files = [OUT / "sample_invoice.pdf", OUT / "sample_invoice_2.pdf",
             OUT / "sample_invoice_3.pdf"]
    missing = [f.name for f in files if not f.exists()]
    if missing:
        print("缺少样本：", missing, "先跑 tools/test_print.py 和 tools/test_flow.py")
        return 2

    # Windows 会把「最近用过的打印机」设成默认，跑完必须改回去，
    # 否则用户下一次打印会莫名其妙跑到 Microsoft Print to PDF
    original_default = ""
    try:
        import win32print
        original_default = win32print.GetDefaultPrinter()
    except Exception:
        win32print = None

    try:
        return _run(files)
    finally:
        if original_default and win32print is not None:
            try:
                if win32print.GetDefaultPrinter() != original_default:
                    win32print.SetDefaultPrinter(original_default)
                    print(f"（已把默认打印机改回：{original_default}）")
            except Exception as exc:
                print(f"（默认打印机没能改回 {original_default}：{exc}）")


def _run(files) -> int:
    for i, path in enumerate(files, 1):
        inv = read_invoice(i, path)
        if not inv.refs:
            print(f"解析失败 {path.name}: {inv.error}")
            return 1
        sheets = plan_sheets(inv, SPEC, PAD_REPEAT)
        target = OUT / f"batch_invoice_{i}.pdf"
        target.unlink(missing_ok=True)
        count = print_sheets(
            printer=PRINTER,
            sheets=sheets,
            spec=SPEC,
            paper="A4",
            job_name=inv.path.stem,
            output_file=str(target),
        )
        doc = fitz.open(str(target)) if target.exists() else None
        pages = doc.page_count if doc else 0
        print(f"[{i}] {path.name}: {inv.page_count} 页 → 计划 {len(sheets)} 张 "
              f"→ 实际输出 {count} 张 / PDF {pages} 页")
        if doc:
            doc[0].get_pixmap(matrix=fitz.Matrix(1.0, 1.0)).save(str(OUT / f"batch_invoice_{i}.png"))
            doc.close()
        if count != len(sheets) or pages != len(sheets):
            print("  ✗ 张数不一致")
            return 1
    print("端到端通过：每张发票独立作业、独立文件、张数正确")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
