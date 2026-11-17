"""开发自测脚本。

用法：
    python tools/test_print.py                 # 生成示例发票 + 拼版预览图
    python tools/test_print.py --pdf out.pdf   # 只输出拼版预览 PDF 图像
    python tools/test_print.py --print "Microsoft Print to PDF" --output test.pdf
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import pymupdf as fitz  # noqa: E402

from app.engine import (  # noqa: E402
    LayoutSpec,
    PageRef,
    compose_sheet,
    print_sheets,
    scan_pdf,
)

OUT_DIR = ROOT / "build_test"


def make_sample_invoice(path: Path, amount: str = "1280.00", no: str = "01417798") -> Path:
    """生成一张 21×14cm 的仿真发票 PDF（仅用于测试版式）。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    doc = fitz.open()
    page = doc.new_page(width=595.28, height=396.85)  # 21cm × 14cm
    red = (0.72, 0.05, 0.10)

    page.draw_rect(fitz.Rect(12, 12, 583, 385), color=red, width=1.2)
    page.draw_rect(fitz.Rect(12, 46, 583, 46), color=red, width=0.8)

    def text(x, y, s, size=9, color=(0, 0, 0), bold=False):
        # 纯西文用 helv（内置字体缺拉丁字形时会逐字加空格，反而失真）
        font = "helv" if s.isascii() else "china-s"
        try:
            page.insert_text((x, y), s, fontsize=size, color=color, fontname=font)
        except Exception:
            page.insert_text((x, y), s.encode("ascii", "ignore").decode() or "-",
                             fontsize=size, color=color, fontname="helv")

    text(200, 38, "电子发票（增值税专用发票）", 14, red)
    text(360, 38, f"发票号码：253270000000{no}", 8, red)

    # 左上二维码占位
    page.draw_rect(fitz.Rect(24, 56, 96, 128), color=(0.1, 0.1, 0.1), width=0.8)
    for i in range(6):
        for j in range(6):
            if (i + j) % 3 == 0:
                page.draw_rect(fitz.Rect(30 + i * 10, 62 + j * 10, 38 + i * 10, 70 + j * 10),
                               color=None, fill=(0.1, 0.1, 0.1))

    text(110, 70, "购买方名称：某某科技有限公司", 9)
    text(110, 88, "统一社会信用代码：91310115MA1K3XYZ8N", 8)
    text(110, 106, "地址、电话：上海市浦东新区某某路 100 号 021-88886666", 8)
    text(110, 124, "开户行及账号：中国银行某某支行 4523 8899 0011 2233", 8)

    # 明细表
    top = 140
    rows = 6
    page.draw_rect(fitz.Rect(24, top, 571, top + 20 + rows * 16), color=red, width=0.8)
    page.draw_line(fitz.Point(24, top + 20), fitz.Point(571, top + 20), color=red, width=0.8)
    heads = ["项目名称", "规格型号", "单位", "数量", "单价", "金额", "税率/征收率", "税额"]
    xs = [30, 225, 285, 315, 352, 420, 478, 532]
    for x, h in zip(xs, heads):
        text(x, top + 14, h, 7.5)
    for r in range(rows):
        y = top + 36 + r * 16
        page.draw_line(fitz.Point(24, y - 11), fitz.Point(571, y - 11), color=(0.85, 0.85, 0.85), width=0.4)
        if r < 1:
            row_amount = amount
            text(30, y, "*信息技术服务*技术服务费", 8)
            text(230, y, "V1.0", 8)
            text(288, y, "次", 8)
            text(320, y, "1", 8)
            text(356, y, row_amount, 8)
            text(425, y, row_amount, 8)
            text(483, y, "6%", 8)
            text(536, y, f"{float(row_amount) * 0.06:.2f}", 8)

    total_y = top + 20 + rows * 16 + 22
    text(300, total_y, f"合计  {amount}  {float(amount) * 0.06:.2f}", 9, red)
    text(30, total_y, "价税合计（大写）：壹仟贰佰捌拾元整", 9, red)
    text(430, total_y, f"（小写）￥{amount}", 9, red)

    text(30, 370, "收款人：张三          复核：李四          开票人：王五", 8)
    text(430, 370, "开票日期：2026年11月03日", 8)
    page.draw_rect(fitz.Rect(455, 300, 560, 360), color=red, width=1.0)
    text(478, 330, "发票专用章", 9, red)

    doc.save(path)
    doc.close()
    return path


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pdf", default=str(OUT_DIR / "sample_invoice.pdf"))
    ap.add_argument("--print", dest="printer", default="")
    ap.add_argument("--output", default=str(OUT_DIR / "printed.pdf"))
    args = ap.parse_args()

    sample = make_sample_invoice(Path(args.pdf))
    sample2 = make_sample_invoice(
        Path(args.pdf).with_name(Path(args.pdf).stem + "_2.pdf"), "980.00", "01417799"
    )
    refs = scan_pdf(str(sample)) + scan_pdf(str(sample2))
    print(f"示例发票已生成：{sample}（共 {len(refs)} 页，"
          f"{refs[0].size_pt[0]:.1f}×{refs[0].size_pt[1]:.1f} pt）")

    spec = LayoutSpec(cols=1, rows=2, margin_cm=0.51, scale_pct=95.5)
    sheet = compose_sheet(refs, spec, paper="A4", dpi=150, render_dpi=300, max_width=1240)
    png = OUT_DIR / "sheet_preview.png"
    sheet.save(png)
    print(f"拼版预览已输出：{png}  ({sheet.width}×{sheet.height})")

    if args.printer:
        out = Path(args.output)
        if not args.output.lower().endswith(".pdf") or out.parent != OUT_DIR:
            out = OUT_DIR / Path(args.output).name
        printed = print_sheets(
            printer=args.printer,
            sheets=[refs],
            spec=spec,
            paper="A4",
            job_name="invoice test",
            output_file=str(out.resolve()) if "pdf" in args.printer.lower() else None,
        )
        print(f"打印完成，共 {printed} 张纸 → {args.printer}")
        if out.exists():
            print(f"输出文件：{out}  ({out.stat().st_size} 字节)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
