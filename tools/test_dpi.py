"""打印清晰度与送印位深的回归测试。

链路：``render_dpi`` 设置 → 按设备能力算出实际渲染 DPI → PyMuPDF 渲染
→ 缩放到版式算出的落纸矩形 → DIB 贴到打印机 DC。

把三个最容易回退的点钉成断言：

1. 设备 600dpi 时把设置改成 300，实际渲染必须真的是 300
   （写成 ``max(render_dpi, min(device, 600))`` 就会把 300 顶回 600，设置等于无效）；
2. 8 位灰度 / 24 位彩色 DIB 的位深与「每行 4 字节对齐」后的数据长度；
3. 端到端打到 Microsoft Print to PDF，页面仍是 A4、有内容，日志里记录的实际 DPI 是 300。

不碰真实打印机：只有第 3 项会调系统 PDF 驱动，产物落在 ``build_test/``。
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import pymupdf as fitz  # noqa: E402
from PIL import Image  # noqa: E402

from app import engine, gdi  # noqa: E402

OUT = ROOT / "build_test"
SRC = OUT / "sample_invoice.pdf"      # 测试自己生成，不依赖任何外部文件


def make_source() -> Path:
    """造一张 A4 的「发票样张」：有文字、表格线、二维码方块，渲染出来才有内容。"""
    OUT.mkdir(parents=True, exist_ok=True)
    doc = fitz.open()
    page = doc.new_page(width=595.276, height=841.89)   # A4，单位 pt
    page.insert_text((60, 80), "电子发票（增值税专用发票）", fontsize=20, fontname="china-s")
    page.insert_text((60, 120), "发票号码：26372000005546862316", fontsize=11, fontname="china-s")
    page.insert_text((60, 140), "开票日期：2026年11月09日", fontsize=11, fontname="china-s")
    page.draw_rect(fitz.Rect(50, 170, 545, 700), color=(0, 0, 0), width=1)
    for i in range(1, 6):
        y = 170 + i * 88
        page.draw_line(fitz.Point(50, y), fitz.Point(545, y), color=(0, 0, 0), width=0.8)
    for i in range(1, 4):
        x = 50 + i * 124
        page.draw_line(fitz.Point(x, 170), fitz.Point(x, 700), color=(0, 0, 0), width=0.8)
    page.insert_text((70, 210), "项目名称  规格型号  数量  单价  金额", fontsize=10, fontname="china-s")
    page.insert_text((70, 300), "天然硫酸钡 1250目  782  21.681419929035  16954.87", fontsize=9, fontname="china-s")
    page.insert_text((70, 390), "价税合计（大写）壹万玖仟壹佰伍拾玖圆整", fontsize=10, fontname="china-s")
    page.draw_rect(fitz.Rect(430, 740, 500, 810), color=(0, 0, 0), width=1)   # 模拟红章/二维码块
    doc.save(str(SRC))
    doc.close()
    return SRC


passed = 0
failed: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    global passed
    if cond:
        passed += 1
    else:
        failed.append(f"{name}{('：' + detail) if detail else ''}")


# --------------------------------------------------------------------------- #
# 1. 实际渲染 DPI：设置值优先，但不能超过设备能力
# --------------------------------------------------------------------------- #
def test_effective_dpi() -> None:
    eff = engine.effective_render_dpi
    check("设备 600 / 设置 300 → 300（设置必须真的生效）", eff(300, 600) == 300, f"得到 {eff(300, 600)}")
    check("设备 600 / 设置 600 → 600", eff(600, 600) == 600)
    check("设备 600 / 设置 200 → 200", eff(200, 600) == 200)
    check("设备 203（传真机）/ 设置 300 → 203（不超设备）", eff(300, 203) == 203, f"得到 {eff(300, 203)}")
    check("设备未知（0）/ 设置 300 → 300", eff(300, 0) == 300)
    check("设备未知 / 设置为空 → 300（默认）", eff(None, 0) == 300, f"得到 {eff(None, 0)}")
    check("设置 120 → 抬到下限 150", eff(120, 600) == 150, f"得到 {eff(120, 600)}")
    check("设置 900 → 压到上限 600", eff(900, 600) == 600, f"得到 {eff(900, 600)}")


# --------------------------------------------------------------------------- #
# 2. DIB 载荷：位深正确、每行 4 字节对齐
# --------------------------------------------------------------------------- #
def test_dib_payload() -> None:
    # 宽度 999：8 位时 stride 1000；24 位时 2997 → 补到 3000
    gray = Image.new("L", (999, 50), 255)
    data, info, w, h = gdi.gray_dib(gray)
    check("灰度位深 8", info.bmiHeader.biBitCount == 8, str(info.bmiHeader.biBitCount))
    check("灰度 stride = 1000（999 → 4 字节对齐）", info.bmiHeader.biSizeImage == 1000 * 50,
          str(info.bmiHeader.biSizeImage))
    check("灰度字节数一致", len(data) == 1000 * 50, str(len(data)))
    check("灰度调色板 256 级", info.bmiHeader.biClrUsed == 256, str(info.bmiHeader.biClrUsed))
    check("灰度高度取负（自上而下）", info.bmiHeader.biHeight == -50, str(info.bmiHeader.biHeight))
    check("灰度尺寸回传一致", (w, h) == (999, 50), f"{(w, h)}")

    color = Image.new("RGB", (999, 50), (255, 0, 0))
    data, header, w, h = gdi.color_dib(color)
    check("彩色位深 24（不再带 alpha）", header.biBitCount == 24, str(header.biBitCount))
    check("彩色 stride = 3000（2997 → 4 字节对齐）", header.biSizeImage == 3000 * 50,
          str(header.biSizeImage))
    check("彩色字节数一致", len(data) == 3000 * 50, str(len(data)))
    check("彩色第一像素是 BGR 蓝=0/绿=0/红=255", data[:3] == b"\x00\x00\xff", str(data[:3]))

    # 不用补位的情况：宽度 1000 → 3000 已是 4 的倍数
    data2, header2, _, _ = gdi.color_dib(Image.new("RGB", (1000, 10)))
    check("彩色无需补位时长度 = 3000×10", header2.biSizeImage == 3000 * 10, str(header2.biSizeImage))

    # 24 位相对 32 位省 1/4
    rgb24 = header.biSizeImage
    rgba32 = 999 * 4 * 50
    check("24 位比 32 位省约 25%", 0.24 < 1 - rgb24 / rgba32 < 0.26, f"{1 - rgb24 / rgba32:.3f}")


# --------------------------------------------------------------------------- #
# 3. 渲染尺寸：A4 在 300dpi 下的像素数
# --------------------------------------------------------------------------- #
def test_render_size() -> None:
    if not SRC.is_file():
        check("找到测试发票", False, str(SRC))
        return
    with fitz.open(str(SRC)) as doc:
        page_pt = doc[0].rect
    expect_w = round(page_pt.width / 72.0 * 300)
    expect_h = round(page_pt.height / 72.0 * 300)

    img = engine.render_page(str(SRC), 0, dpi=300, gray=True)
    check("300dpi 渲染宽度符合纸张尺寸", abs(img.width - expect_w) <= 1,
          f"{img.width} vs {expect_w}")
    check("300dpi 渲染高度符合纸张尺寸", abs(img.height - expect_h) <= 1,
          f"{img.height} vs {expect_h}")
    check("灰度渲染输出 L 模式", img.mode == "L", img.mode)

    img300 = engine.render_page(str(SRC), 0, dpi=300, gray=False)
    check("彩色渲染输出 RGB 模式", img300.mode == "RGB", img300.mode)
    check("彩色与灰度像素尺寸一致", img300.size == (img.width, img.height),
          f"{img300.size} vs {img.size}")


# --------------------------------------------------------------------------- #
# 4. 端到端：打到 PDF，页面仍是 A4、有内容、日志记为 300dpi
# --------------------------------------------------------------------------- #
def test_end_to_end() -> int:
    if not SRC.is_file():
        check("端到端：找到测试发票", False, str(SRC))
        return 0
    OUT.mkdir(parents=True, exist_ok=True)
    target = OUT / "dpi_check.pdf"
    target.unlink(missing_ok=True)

    spec = engine.LayoutSpec(
        cols=1, rows=2, margin_cm=0.51, gap_cm=0.0,
        scale_pct=95.5, order="row", auto_rotate=False,
    )
    refs = engine.scan_pdf(str(SRC))
    sheets = [[refs[0], refs[0]]]

    records: list[str] = []

    class _Grab(logging.Handler):
        def emit(self, record):
            records.append(record.getMessage())

    handler = _Grab()
    engine.log.addHandler(handler)
    engine.log.setLevel(logging.INFO)
    try:
        count = engine.print_sheets(
            "Microsoft Print to PDF", sheets, spec, paper="A4",
            render_dpi=300, job_name="300dpi 验证", output_file=str(target),
            gray=True,
        )
    finally:
        engine.log.removeHandler(handler)

    check("端到端：输出 1 张纸", count == 1, str(count))
    check("端到端：目标 PDF 已生成", target.is_file(), str(target))
    if not target.is_file():
        return 0

    rendered = [r for r in records if r.startswith("渲染 ")]
    check("端到端：有逐页渲染日志", bool(rendered), str(records[:3]))
    if rendered:
        check("端到端：日志里的实际 DPI 是 300", "实际 300dpi" in rendered[0], rendered[0])
        check("端到端：设置值也是 300", "设置 300dpi" in rendered[0], rendered[0])

    with fitz.open(str(target)) as doc:
        page = doc[0]
        w_mm = page.rect.width / 72.0 * 25.4
        h_mm = page.rect.height / 72.0 * 25.4
        check("端到端：页面是 A4 纵向", abs(w_mm - 210) < 2 and abs(h_mm - 297) < 2,
              f"{w_mm:.1f}×{h_mm:.1f}mm")
        pix = page.get_pixmap(matrix=fitz.Matrix(1, 1))
        dark = sum(1 for b in pix.samples if b < 200)
        check("端到端：页面有内容（不是空白页）", dark > 1000, f"暗像素 {dark}")
        pix.save(str(OUT / "dpi_check.png"))
    return count


def main() -> int:
    make_source()
    test_effective_dpi()
    test_dib_payload()
    test_render_size()
    test_end_to_end()

    if failed:
        print(f"失败 {len(failed)} 项（通过 {passed} 项）：")
        for item in failed:
            print("  ×", item)
        return 1
    print(f"全部通过：{passed} 项（渲染 DPI 判定 / 8+24 位 DIB 对齐 / A4@300dpi 尺寸 / 端到端出纸）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
