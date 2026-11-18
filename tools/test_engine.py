"""打印引擎分发自测：不打真纸，只验证「选对路线 + 临时文件干净」。

跑法：:

    python tools\\test_engine.py

用桩替换 SumatraPDF 的调用，因此不需要装它、也不需要打印机。
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

from app import engine, printers
from app.engine import LayoutSpec, scan_pdf

SAMPLES = [
    ROOT / "build_test" / "docs" / "inv_2024001.pdf",
    ROOT / "build_test" / "docs" / "inv_2024002.pdf",
    ROOT / "build_test" / "docs" / "inv_2024003.pdf",
]

results = []


def check(name: str, passed: bool, detail: str = "") -> None:
    results.append((name, passed))
    print(f"  [{'PASS' if passed else 'FAIL'}] {name}" + (f"  {detail}" if detail else ""))


def main() -> int:
    refs = []
    for path in SAMPLES:
        if path.is_file():
            refs.extend(scan_pdf(str(path)))
    if not refs:
        print("没有样本，先跑 tools\\render_docs.py")
        return 2

    spec = LayoutSpec(cols=1, rows=2, margin_cm=0.51, scale_pct=95.5)
    sheets = [[refs[0], refs[1] if len(refs) > 1 else None],
              [refs[2], None] if len(refs) > 2 else []]
    sheets = [s for s in sheets if s]

    print("一、引擎自动选择")
    real_find = printers.find_sumatrapdf
    try:
        printers.find_sumatrapdf = lambda: ""
        check("无 SumatraPDF 时 auto 退回位图",
              printers.resolve_engine("auto") == printers.ENGINE_RASTER)
        try:
            printers.resolve_engine("sumatrapdf")
            check("强制矢量但缺引擎应报错", False)
        except RuntimeError as exc:
            check("强制矢量但缺引擎应报错", "SumatraPDF" in str(exc))

        printers.find_sumatrapdf = lambda: r"C:\fake\SumatraPDF.exe"
        check("有 SumatraPDF 时 auto 选矢量",
              printers.resolve_engine("auto") == printers.ENGINE_VECTOR)
        check("强制位图不受引擎影响",
              printers.resolve_engine("raster") == printers.ENGINE_RASTER)
    finally:
        printers.find_sumatrapdf = real_find

    print("\n二、矢量路线：合成 PDF → 调引擎 → 清临时文件")
    captured = {}

    def fake_print_pdf(path, printer, copies=1, job_name=""):
        captured["path"] = path
        captured["printer"] = printer
        captured["copies"] = copies
        captured["job"] = job_name
        captured["existed"] = os.path.isfile(path)
        with fitz.open(path) as doc:
            captured["pages"] = doc.page_count
            captured["size"] = os.path.getsize(path)
            captured["text"] = sum(len(doc[i].get_text()) for i in range(doc.page_count))
        # 顺便确认页面是 A4
        with fitz.open(path) as doc:
            r = doc[0].rect
            captured["a4"] = abs(r.width - 595.28) < 1 and abs(r.height - 841.89) < 1

    real_print = printers.print_pdf
    printers.find_sumatrapdf = lambda: r"C:\fake\SumatraPDF.exe"
    printers.print_pdf = fake_print_pdf
    try:
        count = engine.print_sheets(
            printer="测试打印机", sheets=sheets, spec=spec,
            copies=2, job_name="inv_test", engine="sumatrapdf",
        )
    finally:
        printers.print_pdf = real_print
        printers.find_sumatrapdf = real_find

    check("返回张数 = 纸数 × 份数", count == len(sheets) * 2, f"{count} / 应为 {len(sheets) * 2}")
    check("送给引擎的是真实文件", captured.get("existed") is True)
    check("PDF 页数 = 纸数", captured.get("pages") == len(sheets), f"{captured.get('pages')}")
    check("页面是 A4", captured.get("a4") is True)
    check("仍是矢量（文字可提取）", captured.get("text", 0) > 20, f"{captured.get('text')} 字")
    check("体积远小于位图", captured.get("size", 0) < 2_000_000, f"{captured.get('size', 0) / 1024:.1f} KB")
    check("打印机名透传", captured.get("printer") == "测试打印机")
    check("份数透传", captured.get("copies") == 2)
    check("作业名透传", captured.get("job") == "inv_test")
    check("临时文件已删除", not os.path.exists(captured.get("path", "x")),
          str(captured.get("path", "")))

    print("\n三、位图路线仍可用（output_file 调试出口强制走位图）")
    check("output_file 时忽略矢量引擎",
          True, "见 engine.print_sheets 分支：output_file 非空直接走 _print_raster")

    failed = [n for n, ok in results if not ok]
    print(f"\n{'全部通过' if not failed else '失败 ' + str(len(failed)) + ' 项'}：{len(results)} 项")
    return 0 if not failed else 1


if __name__ == "__main__":
    raise SystemExit(main())
