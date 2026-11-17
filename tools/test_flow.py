"""开发自测：验证「一次拖入多个 PDF → 每个文件一张独立发票」的完整逻辑。

不真的调用打印机：把 worker 里的 print_sheets 换成记录调用的假函数，
于是每次「提交」都被记进 CALLS，可以精确断言「提交了几条作业、每条作业里是谁的页」。
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import pymupdf as fitz  # noqa: E402

from app import style  # noqa: E402
from app.config import CONFIG_FILE, app_dir  # noqa: E402
import app.history as history_mod  # noqa: E402
import app.worker as worker_mod  # noqa: E402

CALLS = []


def fake_print_sheets(**kwargs):
    CALLS.append(kwargs)
    return len(kwargs["sheets"])


worker_mod.print_sheets = fake_print_sheets

from PySide6.QtCore import QMimeData, QUrl  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from app.ui import MainWindow, PreviewDialog, pil_to_pixmap  # noqa: E402
from tools.test_print import make_sample_invoice  # noqa: E402

OUT = ROOT / "build_test"
F1 = OUT / "sample_invoice.pdf"
F2 = OUT / "sample_invoice_2.pdf"

# 历史记录写到临时文件，别污染真实数据
history_mod.HISTORY_FILE = OUT / "test_history.json"
history_mod.HISTORY_FILE.unlink(missing_ok=True)


def make_pdf(path: Path, pages: int) -> Path:
    """造一个多页 PDF（模拟「发票 + 销货清单」）。"""
    doc = fitz.open()
    for i in range(pages):
        page = doc.new_page(width=595.28, height=396.85)
        page.insert_text((40, 60), f"page {i + 1}", fontsize=20)
    doc.save(str(path))
    doc.close()
    return path


def pump(app, seconds: float) -> None:
    end = time.time() + seconds
    while time.time() < end:
        app.processEvents()
        time.sleep(0.02)


def owners(sheet) -> list:
    """看一张纸里放的是哪些文件（用于判断有没有串台）。"""
    return [Path(ref.path).name if ref else None for ref in sheet]


def main() -> int:
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    app.setStyleSheet(style.build_qss(app_dir() / "ui"))

    F3 = make_pdf(OUT / "sample_invoice_3.pdf", 3)   # 3 页 = 需要 2 张纸
    bad = OUT / "broken.pdf"
    bad.write_bytes(b"not a pdf at all")
    make_sample_invoice(F1, "1280.00", "01417798")
    make_sample_invoice(F2, "3560.00", "01417799")

    fails = []
    CONFIG_FILE.unlink(missing_ok=True)   # 用默认设置跑，避免上次截图留下的收起状态
    win = MainWindow()
    win.layout_combo.setCurrentIndex(win.layout_combo.findData("1x2"))
    win.auto_print_check.setChecked(True)
    win.confirm_duplicates = False   # 前 8 个场景会重复拖入同样的文件，先关掉弹窗
    # 本套用例测的是排版与作业拆分，F3 是纯文本假页（不是发票），
    # 关掉「只打印发票」才不会把 F3 拦掉；发票判定由 test_only_invoice 覆盖
    win.only_invoice_check.setChecked(False)
    win.show()

    # ---------------------------------------------------------------- 场景 1
    # 一次拖入 3 个单页 PDF：应提交 3 条独立作业，每条只有自己那一页（重复补齐本发票）
    CALLS.clear()
    win.on_paths_dropped([str(F1), str(F2), str(F3)])
    pump(app, 2.5)
    if len(CALLS) != 3:
        fails.append(f"场景1 作业数={len(CALLS)}，期望 3 条（一个 PDF 一条）")
    else:
        for call, path in zip(CALLS, (F1, F2, F3)):
            if Path(call["job_name"] + ".pdf").name != path.name:
                fails.append(f"场景1 作业名={call['job_name']}，与文件 {path.name} 不对应")
        s1 = CALLS[2]["sheets"]  # 第三个文件是 3 页的
        if len(s1) != 2:  # F3 是 3 页 1×2 → 2 张纸
            fails.append(f"场景1 三页发票张数={len(s1)}，期望 2")
        else:
            names = {n for sheet in s1 for n in owners(sheet) if n}
            if names != {F3.name}:
                fails.append(f"场景1 三页发票里混入了别的文件：{names}")
            if s1[-1][1] is not None:
                fails.append("场景1 最后一张纸的剩余格子应留白")
        first = CALLS[0]["sheets"][0]
        if owners(first) != [F1.name, F1.name]:
            fails.append(f"场景1 单页发票未按联次补齐：{owners(first)}")

    status_col = [win.table.item(r, 3).text() for r in range(win.table.rowCount())]
    if status_col != ["已打印"] * 3:
        fails.append(f"场景1 状态={status_col}，期望三行都「已打印」")
    if win.table.rowCount() != 3:
        fails.append(f"场景1 表格行数={win.table.rowCount()}，期望 3（一张发票一行）")

    # ---------------------------------------------------------------- 场景 2
    # 夹一个坏文件：只有它失败，其余照常打印
    win.clear_queue()
    CALLS.clear()
    win.on_paths_dropped([str(F1), str(bad), str(F2)])
    pump(app, 2.5)
    if len(CALLS) != 2:
        fails.append(f"场景2 作业数={len(CALLS)}，期望 2（坏文件不产生作业）")
    status_col = [win.table.item(r, 3).text() for r in range(win.table.rowCount())]
    if status_col != ["已打印", "读取失败", "已打印"]:
        fails.append(f"场景2 状态={status_col}，期望 已打印/读取失败/已打印")

    # ---------------------------------------------------------------- 场景 3
    # 关掉自动打印：只入队不输出，点「开始打印」后每张发票仍是一条独立作业
    win.clear_queue()
    win.auto_print_check.setChecked(False)
    CALLS.clear()
    win.on_paths_dropped([str(F1), str(F2)])
    pump(app, 1.0)
    if CALLS:
        fails.append(f"场景3 关闭立即打印后不应自动提交，实际 {len(CALLS)} 条")
    status_col = [win.table.item(r, 3).text() for r in range(win.table.rowCount())]
    if status_col != ["待打印", "待打印"]:
        fails.append(f"场景3 状态={status_col}，期望两行「待打印」")
    win.print_pending()
    pump(app, 2.5)
    if len(CALLS) != 2:
        fails.append(f"场景3 点开始打印后作业数={len(CALLS)}，期望 2 条")
    status_col = [win.table.item(r, 3).text() for r in range(win.table.rowCount())]
    if status_col != ["已打印", "已打印"]:
        fails.append(f"场景3 状态={status_col}，期望两行「已打印」")

    # ---------------------------------------------------------------- 场景 4
    # 空位留白：单页发票 → 一张纸只放一份，下半留空
    win.clear_queue()
    win.auto_print_check.setChecked(True)
    win.pad_combo.setCurrentIndex(win.pad_combo.findData("blank"))
    CALLS.clear()
    win.on_paths_dropped([str(F1)])
    pump(app, 2.0)
    if not CALLS:
        fails.append("场景4 未提交")
    elif owners(CALLS[0]["sheets"][0]) != [F1.name, None]:
        fails.append(f"场景4 留白模式={owners(CALLS[0]['sheets'][0])}，期望 [文件, None]")

    # ---------------------------------------------------------------- 场景 5
    # 1×1 版式：多文件 → 每个文件 1 页 1 张纸，各自独立
    win.clear_queue()
    win.pad_combo.setCurrentIndex(win.pad_combo.findData("repeat"))
    win.layout_combo.setCurrentIndex(win.layout_combo.findData("1x1"))
    CALLS.clear()
    win.on_paths_dropped([str(F1), str(F2)])
    pump(app, 2.5)
    if len(CALLS) != 2 or any(len(c["sheets"]) != 1 for c in CALLS):
        fails.append(f"场景5 期望 2 条作业各 1 张纸，实际 {[len(c['sheets']) for c in CALLS]}")

    # ---------------------------------------------------------------- 场景 6
    # 历史记录：每打印一张发票入一条，字段来自该 PDF
    win.clear_queue()
    win.layout_combo.setCurrentIndex(win.layout_combo.findData("1x2"))
    win.pad_combo.setCurrentIndex(win.pad_combo.findData("repeat"))
    win.history.clear()
    win.refresh_history()
    CALLS.clear()
    win.on_paths_dropped([str(F1), str(F2)])
    pump(app, 2.5)
    if len(win.history.records) != 2:
        fails.append(f"场景6 历史条数={len(win.history.records)}，期望 2")
    else:
        rec = win.history.records[0]          # 最新一条 = F2
        if rec.invoice_no != "01417799":
            fails.append(f"场景6 发票号={rec.invoice_no!r}，期望 01417799")
        if rec.material != "技术服务费 V1.0":
            fails.append(f"场景6 材料名称={rec.material!r}，期望 技术服务费 V1.0")
        if rec.spec != "V1.0":
            fails.append(f"场景6 规格型号={rec.spec!r}，期望 V1.0")
        if rec.qty != "1":
            fails.append(f"场景6 数量={rec.qty!r}，期望 1")
        if rec.tax_rate != "6%":
            fails.append(f"场景6 税率={rec.tax_rate!r}，期望 6%")
        # 含税单价 = 票面单价 3560.00 × (1 + 6%) = 3773.60
        if rec.unit_price != "￥3,773.60":
            fails.append(f"场景6 单价={rec.unit_price!r}，期望 ￥3,773.60")
        if "(1 + 6%)" not in rec.formula:
            fails.append(f"场景6 计算过程={rec.formula!r}，期望含 (1 + 6%)")
        if win.history_table.columnCount() != len(history_mod.COLUMNS):
            fails.append("场景6 历史表格列数与 COLUMNS 不一致")
        if rec.status != "已打印":
            fails.append(f"场景6 结果={rec.status!r}，期望 已打印")
        if win.history_table.rowCount() != 2:
            fails.append(f"场景6 表格行数={win.history_table.rowCount()}，期望 2")
        if win.history_table.item(0, 0).text() != "01417799":
            fails.append("场景6 表格首行不是最新记录")
        if hasattr(rec, "file") or hasattr(rec, "path"):
            fails.append("场景6 历史记录不应再保存文件/路径")
        if "文件" in history_mod.COLUMNS:
            fails.append("场景6 历史表格不应再有「文件」列")
        if "税率" in history_mod.COLUMNS:
            fails.append("场景6 历史表格不应再有「税率」列（税率只参与单价折算）")
        if len(rec.cells()) != len(history_mod.COLUMNS):
            fails.append(f"场景6 cells() 有 {len(rec.cells())} 列，"
                         f"COLUMNS 有 {len(history_mod.COLUMNS)} 列，对不上")
    # 落盘 + 重载
    reloaded = history_mod.History()
    if len(reloaded) != 2:
        fails.append(f"场景6 历史落盘后条数={len(reloaded)}，期望 2")

    # ---------------------------------------------------------------- 场景 7
    # 设置面板折叠/展开：宽度必须是渐变的（不是瞬移），且能回到原宽度
    widths = []
    win.toggle_settings_panel(True)
    end = time.time() + 0.6
    while time.time() < end:
        app.processEvents()
        widths.append(win.settings_wrap.maximumWidth())
        time.sleep(0.02)
    if len(set(widths)) < 3:
        fails.append(f"场景7 收起过程没有渐变：{sorted(set(widths))}")
    if win.settings_wrap.maximumWidth() != 0:
        fails.append(f"场景7 收起后宽度={win.settings_wrap.maximumWidth()}，期望 0")
    if win.settings_card.isVisible():
        fails.append("场景7 收起后设置卡片仍可见")
    if win.collapse_btn.text() != "›":
        fails.append(f"场景7 收起后按钮={win.collapse_btn.text()!r}，期望 ›")
    win.toggle_settings_panel(False)
    pump(app, 0.8)
    if win.settings_wrap.maximumWidth() != win.PANEL_WIDTH:
        fails.append(f"场景7 展开后宽度={win.settings_wrap.maximumWidth()}，期望 {win.PANEL_WIDTH}")
    if not win.settings_card.isVisible():
        fails.append("场景7 展开后设置卡片未显示")
    if not win.layout_combo.isVisible():
        fails.append("场景7 展开后版式下拉框不可见")

    # ---------------------------------------------------------------- 场景 8
    # 记录很多时：表格自身滚动，卡片高度不变
    base_height = win.history_table.parentWidget().height()
    for i in range(40):
        win.history.add(history_mod.PrintRecord(
            when=f"2026-11-03 10:{i:02d}:00", invoice_no=f"0000{i:04d}",
            material="信息技术服务", spec="V1.0", qty="1", unit_price="￥100.00",
            status="已打印"))
    win.refresh_history()
    pump(app, 0.2)
    scroll = win.history_table.verticalScrollBar()
    if scroll.maximum() <= 0:
        fails.append("场景8 记录较多时应有垂直滚动条")
    if win.history_table.parentWidget().height() != base_height:
        fails.append("场景8 记录变多后历史卡片高度被撑开")
    if win.history_table.rowCount() != 42:
        fails.append(f"场景8 行数={win.history_table.rowCount()}，期望 42")

    # ---------------------------------------------------------------- 场景 9
    # 重复发票号：拖入同一张已打印过的发票 → 弹提示；选「跳过」则不入队、不记录
    win.clear_queue()
    win.history.clear()
    win.refresh_history()
    win.confirm_duplicates = True
    asked = []

    def ask_skip(hits):
        asked.append(len(hits))
        return False

    win._ask_duplicates = ask_skip
    CALLS.clear()
    win.on_paths_dropped([str(F1)])
    pump(app, 2.0)
    win.clear_queue()
    win.on_paths_dropped([str(F1)])
    pump(app, 0.6)
    if not asked:
        fails.append("场景9 重复发票号应弹窗确认")
    skipped = [i for i in win.invoices.values() if i.status == "已跳过"]
    if len(skipped) != 1:
        fails.append(f"场景9 跳过状态数量={len(skipped)}，期望 1")
    if len(win.history.records) != 1:
        fails.append(f"场景9 跳过后历史条数={len(win.history.records)}，期望 1")
    if len(CALLS) != 1:
        fails.append(f"场景9 跳过后不应再提交打印，实际 {len(CALLS)} 次")

    # 选「仍然打印」→ 正常入队并记录
    win.clear_queue()
    win._ask_duplicates = lambda hits: True
    win.on_paths_dropped([str(F1)])
    pump(app, 2.0)
    if len(win.history.records) != 2:
        fails.append(f"场景9 强制打印后历史条数={len(win.history.records)}，期望 2")
    win.confirm_duplicates = False

    # ---------------------------------------------------------------- 场景 10
    # 点击预览放大：高清渲染的图必须明显大于缩略图
    inv = win._preview_target()
    if inv is None:
        fails.append("场景10 没有可预览的发票")
    else:
        low, _, _ = win._render_sheet(inv, high_res=False)
        high, hint, _ = win._render_sheet(inv, high_res=True)
        if low is None or high is None:
            fails.append("场景10 预览渲染失败")
        elif high.width < low.width * 1.5:
            fails.append(f"场景10 高清图宽度={high.width}，应远大于缩略图 {low.width}")
        if not hint:
            fails.append("场景10 大图缺少说明文字")
        if high is not None:
            dlg = PreviewDialog(pil_to_pixmap(high), "测试", hint, win)
            dlg._set_zoom(0.5)
            if dlg.zoom_label.text() != "50%":
                fails.append(f"场景10 缩放标签={dlg.zoom_label.text()!r}，期望 50%")
            dlg._set_zoom(9.0)   # 超上限应被夹住
            if dlg._zoom > dlg.MAX_ZOOM:
                fails.append("场景10 缩放上限未生效")
            dlg.fit_btn.click()
            if dlg._zoom <= 0:
                fails.append("场景10 适应窗口后缩放异常")
            dlg.close()

    # ---------------------------------------------------------------- 场景 11
    # 剪贴板导入：微信/资源管理器「复制文件」后 Ctrl+V，免去先存桌面
    win.clear_queue()
    mime = QMimeData()
    mime.setUrls([QUrl.fromLocalFile(str(F1))])
    QApplication.clipboard().setMimeData(mime)
    win.paste_from_clipboard()
    pump(app, 2.0)
    if len(win.invoices) != 1:
        fails.append(f"场景11 剪贴板导入后发票数={len(win.invoices)}，期望 1")
    elif win.history.records[0].invoice_no != "01417798":
        fails.append(f"场景11 剪贴板导入发票号={win.history.records[0].invoice_no!r}")
    QApplication.clipboard().clear()

    win.close()
    if fails:
        print("失败：")
        for f in fails:
            print("  -", f)
        return 1
    print("全部通过：多文件独立成票 / 独立作业 / 逐行状态 / 补位与留白 / "
          "历史记录字段（不含文件）/ 面板折叠动画 / 长列表滚动 / "
          "重复发票号提醒 / 预览高清渲染 逻辑正常")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
