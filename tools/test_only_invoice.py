"""1.2.5 回归：只打印发票 / 拖图标必自动打印 / 不重打旧失败项。

覆盖四个现场问题：
1. 合同等非发票 PDF 被当成发票提交、真出纸（四醇合同就是这么打出去的）；
2. 「拖入后立即打印」关掉后，拖图标到已运行的实例只入队不出纸；
3. 自动补打时把列表里上次失败的旧发票一起重打；
4. 扫描件（无文字层）不能被「只打印发票」误拦。

假打印：替换 app.worker.print_sheets，绝不碰真实打印机。
"""
from __future__ import annotations

import os
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

_BASE = Path(tempfile.mkdtemp(prefix="only_invoice_"))
for _key in ("APPDATA", "LOCALAPPDATA", "TEMP"):
    os.environ[_key] = str(_BASE / _key.lower())
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pymupdf as fitz  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

import app.worker as worker_mod  # noqa: E402
from app.invoice import ST_DONE, ST_FAILED, ST_NOT_INVOICE, ST_PRINTING  # noqa: E402
from app.ui import MainWindow  # noqa: E402
from test_extract import make_invoice  # noqa: E402

calls: list[str] = []
FAIL_JOBS: set[str] = set()


def fake_print(**kwargs):
    """假装打印：记录作业名并返回张数；FAIL_JOBS 里的作业模拟失败。"""
    calls.append(kwargs["job_name"])
    if kwargs["job_name"] in FAIL_JOBS:
        raise RuntimeError("模拟打印失败（测试）")
    return len(kwargs["sheets"]) * max(1, int(kwargs.get("copies", 1)))


worker_mod.print_sheets = fake_print

app = QApplication([])
results: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    results.append((name, bool(ok), detail))


def pump(seconds: float = 3.0, cond=None) -> bool:
    end = time.time() + seconds
    while time.time() < end:
        app.processEvents()
        if cond and cond():
            return True
        time.sleep(0.02)
    return bool(cond and cond())


def make_contract(path: Path) -> Path:
    doc = fitz.open()
    page = doc.new_page(width=595, height=842)
    page.insert_text((60, 80), "采购合同", fontsize=20)
    page.insert_text((60, 120), "甲方：某某材料有限公司", fontsize=11)
    page.insert_text((60, 150), "乙方：某供应商有限公司", fontsize=11)
    page.insert_text((60, 180), "合同金额：人民币壹万元整", fontsize=11)
    doc.save(str(path))
    doc.close()
    return path


def make_scan(path: Path) -> Path:
    """没有文字层的扫描件：整页只有一张图。"""
    src = fitz.open()
    page = src.new_page(width=595, height=842)
    page.draw_rect(fitz.Rect(40, 40, 555, 800), color=(0, 0, 0), fill=(0.92, 0.92, 0.92))
    pix = page.get_pixmap(dpi=72)
    src.close()
    doc = fitz.open()
    p = doc.new_page(width=595, height=842)
    p.insert_image(p.rect, pixmap=pix)
    doc.save(str(path))
    doc.close()
    return path


main_window = None
try:
    # ---------------------------------------------------------------- 场景 1
    # 三张发票 + 一份合同一起拖：只打三张发票，合同不出纸
    invs = [
        make_invoice(_BASE / f"inv{i}.pdf",
                     no=no, rows=[("*信息技术服务*技术服务费", "V1.0", "1", "100.00", "100.00", "6.00")])
        for i, no in enumerate(("25327000000011112222", "25327000000033334444", "25327000000055556666"))
    ]
    contract = make_contract(_BASE / "四醇合同.pdf")

    calls.clear()
    win = MainWindow()
    main_window = win
    win.confirm_duplicates = False
    win.settings.auto_print = True
    win.settings.only_invoice = True
    win.start_headless([str(p) for p in invs] + [str(contract)])
    pump(6.0, lambda: len([c for c in calls if c.startswith("inv")]) >= 3
         and any(i.status == ST_DONE for i in win.invoices.values()))

    contract_inv = win.invoices[win._order[-1]]
    check("合同被标记为非发票", contract_inv.status == ST_NOT_INVOICE, contract_inv.status)
    check("合同没有提交打印", "四醇合同" not in calls, str(calls))
    check("三张发票全部提交", len([c for c in calls if c.startswith("inv")]) == 3, str(calls))
    pump(4.0, lambda: len(win.history.records) >= 3)
    check("历史只记已打印的发票", len(win.history.records) == 3, str(len(win.history.records)))
    check("历史里没有空发票号的记录",
          all(r.invoice_no for r in win.history.records),
          str([r.invoice_no for r in win.history.records]))
    win.close()
    win.worker.stop()
    win.worker.wait(3000)
    app.processEvents()

    # ---------------------------------------------------------------- 场景 2
    # 扫描件没有文字层，不能被误拦
    calls.clear()
    scan = make_scan(_BASE / "扫描件.pdf")
    win2 = MainWindow()
    win2.confirm_duplicates = False
    win2.settings.auto_print = True
    win2.settings.only_invoice = True
    win2.start_headless([str(scan)])
    pump(6.0, lambda: "扫描件" in calls)
    scan_inv = win2.invoices[win2._order[-1]]
    check("扫描件按发票放行", scan_inv.status != ST_NOT_INVOICE, scan_inv.status)
    check("扫描件已提交打印", "扫描件" in calls, str(calls))
    win2.close()
    win2.worker.stop()
    win2.worker.wait(3000)
    app.processEvents()

    # ---------------------------------------------------------------- 场景 3
    # 取消「只打印发票」后，合同可以正常打
    calls.clear()
    win3 = MainWindow()
    win3.confirm_duplicates = False
    win3.settings.auto_print = True
    win3.settings.only_invoice = False
    win3.start_headless([str(contract)])
    pump(6.0, lambda: "四醇合同" in calls)
    check("取消勾选后合同可打印", "四醇合同" in calls, str(calls))
    win3.close()
    win3.worker.stop()
    win3.worker.wait(3000)
    app.processEvents()

    # ---------------------------------------------------------------- 场景 4
    # 主界面开着、且「拖入后立即打印」关掉：拖图标转过来的文件仍要自动出纸
    calls.clear()
    win4 = MainWindow(headless=False)
    win4.confirm_duplicates = False
    win4.settings.auto_print = False      # 用户当前配置就是这个状态
    win4.settings.only_invoice = True
    before = len(calls)
    win4.receive_forwarded([str(invs[0])])
    pump(6.0, lambda: len(calls) > before)
    check("主界面开着时转发也自动打印", len(calls) == before + 1, f"{before} -> {len(calls)}")
    win4.close()
    win4.worker.stop()
    win4.worker.wait(3000)
    app.processEvents()

    # ---------------------------------------------------------------- 场景 5
    # 上次失败的旧发票不能被顺带重打
    calls.clear()
    FAIL_JOBS.add("bad")
    bad = make_invoice(_BASE / "bad.pdf",
                       no="25327000000077778888",
                       rows=[("*信息技术服务*技术服务费", "V1.0", "1", "100.00", "100.00", "6.00")])
    good = make_invoice(_BASE / "good.pdf",
                        no="25327000000099990000",
                        rows=[("*信息技术服务*技术服务费", "V1.0", "1", "100.00", "100.00", "6.00")])
    win5 = MainWindow()
    win5.confirm_duplicates = False
    win5.settings.auto_print = True
    win5.settings.only_invoice = True
    win5.start_headless([str(bad)])
    pump(6.0, lambda: any(i.status == ST_FAILED for i in win5.invoices.values()))
    check("第一次作业按预期失败",
          any(i.status == ST_FAILED for i in win5.invoices.values()),
          str([i.status for i in win5.invoices.values()]))

    calls.clear()
    win5._headless_ingest([str(good)])
    pump(6.0, lambda: "good" in calls and any(i.status == ST_DONE for i in win5.invoices.values()))
    check("第二次只打新拖入的发票", calls == ["good"], str(calls))
    win5.close()
    win5.worker.stop()
    win5.worker.wait(3000)
    app.processEvents()

finally:
    # ---------------------------------------------------------------- 汇总
    print()
    total = len(results)
    passed = sum(1 for _, ok, _ in results if ok)
    for name, ok, detail in results:
        mark = "PASS" if ok else "FAIL"
        print(f"[{mark}] {name}" + (f"  —— {detail}" if (detail and not ok) else ""))
    if passed == total:
        print(f"\n全部通过：{total} 项（非发票跳过 / 扫描件放行 / 取消勾选可打 / "
              f"转发自动打印 / 不重打旧失败）")
    else:
        print(f"\n失败 {total - passed} 项 / 共 {total} 项")
    sys.exit(0 if passed == total else 1)
