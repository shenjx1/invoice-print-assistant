"""历史记录「选取删除」自测：多选、勾选按钮状态、确认后落盘、取消不动数据。

用的是临时 history 文件，不会碰真实记录。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from PySide6.QtCore import QItemSelectionModel  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from app import style  # noqa: E402
from app.config import CONFIG_FILE, app_dir  # noqa: E402
from app.history import PrintRecord  # noqa: E402
import app.history as history_mod  # noqa: E402

from app.ui import MainWindow  # noqa: E402

OUT = ROOT / "build_test"
HIST = OUT / "delete_history.json"
history_mod.HISTORY_FILE = HIST

results: list = []


def check(title: str, ok: bool, detail: str = "") -> bool:
    print(f"{'OK  ' if ok else 'FAIL'} {title}" + (f"  ({detail})" if detail else ""))
    results.append(ok)
    return ok


def seed(numbers) -> None:
    hist = history_mod.History()
    hist.records.clear()
    for i, number in enumerate(numbers):
        hist.records.append(PrintRecord(
            when=f"2026-11-04 09:0{i}:00", invoice_no=number, material="颜料",
            spec="CR10", qty="100", unit_price="￥20.50", status="已打印",
        ))
    hist.save()


def main() -> int:
    OUT.mkdir(exist_ok=True)
    CONFIG_FILE.unlink(missing_ok=True)

    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    app.setStyleSheet(style.build_qss(app_dir() / "ui"))

    seed(["11111111", "22222222", "33333333", "44444444"])
    win = MainWindow()
    table = win.history_table

    check("表格行数 = 记录数", table.rowCount() == 4, str(table.rowCount()))
    check("没选中时「删除选中」不可点", not win.history_del_btn.isEnabled())
    check("表格可以多选",
          table.selectionMode() == table.SelectionMode.ExtendedSelection,
          str(table.selectionMode()))

    # 选两行（模拟 Ctrl 点选）
    table.selectRow(0)
    app.processEvents()
    check("选中一行后按钮变可点", win.history_del_btn.isEnabled())

    model = table.selectionModel()
    model.select(table.model().index(2, 0),
                 QItemSelectionModel.Select | QItemSelectionModel.Rows)
    app.processEvents()
    picked = win._history_selected_rows()
    check("多选能取到两个行号", picked == [0, 2], str(picked))

    # 取消：数据不动
    win._confirm_delete = lambda count, preview: False
    win._delete_selected_history()
    app.processEvents()
    check("取消后记录不变", len(win.history.records) == 4,
          str(len(win.history.records)))
    check("取消后表格仍 4 行", table.rowCount() == 4, str(table.rowCount()))

    # 确认：删掉第 0、2 行（发票号 11111111 与 33333333）
    win._confirm_delete = lambda count, preview: True
    win._delete_selected_history()
    app.processEvents()
    left = [r.invoice_no for r in win.history.records]
    check("只删掉选中的两条", left == ["22222222", "44444444"], str(left))
    check("表格同步刷新", table.rowCount() == 2, str(table.rowCount()))
    check("条数提示同步", "共 2 条" in win.history_hint.text(), win.history_hint.text())

    on_disk = json.loads(HIST.read_text("utf-8"))
    check("删除结果已落盘",
          [r["invoice_no"] for r in on_disk] == ["22222222", "44444444"],
          str([r["invoice_no"] for r in on_disk]))

    # 重新打开（模拟下次启动）仍然是删后的结果
    win.close()
    win2 = MainWindow()
    check("重开程序仍是删后的记录",
          [r.invoice_no for r in win2.history.records] == ["22222222", "44444444"],
          str([r.invoice_no for r in win2.history.records]))

    # 全选删除 -> 空
    win2.history_table.selectAll()
    app.processEvents()
    win2._confirm_delete = lambda count, preview: True
    win2._delete_selected_history()
    app.processEvents()
    check("全选删除后记录为空", len(win2.history.records) == 0)
    check("空记录时按钮回到不可点", not win2.history_del_btn.isEnabled())
    win2.close()

    total = len(results)
    print()
    if all(results):
        print(f"全部通过：{total} 项（多选 / 按钮状态 / 取消不动 / 删除落盘 / 重开保持）")
        return 0
    print(f"失败 {results.count(False)} / {total} 项")
    return 1


if __name__ == "__main__":
    sys.exit(main())
