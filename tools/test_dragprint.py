"""「拖放即打印」自测：不显示主界面、小进度卡、多文件/文件夹/非 PDF 的处理。

打印函数被替换成空实现（返回张数），不会浪费纸。
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from PySide6.QtWidgets import QApplication  # noqa: E402

from app import style  # noqa: E402
from app.config import CONFIG_FILE, app_dir  # noqa: E402
import app.history as history_mod  # noqa: E402
import app.worker as worker_mod  # noqa: E402

# 关键：打印线程里用的是 worker 模块里的 print_sheets，替换掉就不会真出纸
worker_mod.print_sheets = lambda **kwargs: len(kwargs["sheets"])

from app.invoice import ST_DONE  # noqa: E402
from app.ui import MainWindow  # noqa: E402
from tools.test_print import make_sample_invoice  # noqa: E402

OUT = ROOT / "build_test"
history_mod.HISTORY_FILE = OUT / "drag_history.json"

passed: list = []


def check(title: str, ok: bool, detail: str = "") -> bool:
    print(f"{'OK  ' if ok else 'FAIL'} {title}" + (f"  ({detail})" if detail else ""))
    passed.append(ok)
    return ok


def wait_until(app, pred, timeout: float = 20.0) -> bool:
    start = time.time()
    while time.time() - start < timeout:
        app.processEvents()
        if pred():
            return True
        time.sleep(0.05)
    return False


def finished(win: MainWindow) -> bool:
    return bool(win.invoices) and all(
        inv.status == ST_DONE for inv in win.invoices.values()
    )


def main() -> int:
    OUT.mkdir(exist_ok=True)
    CONFIG_FILE.unlink(missing_ok=True)
    history_mod.HISTORY_FILE.unlink(missing_ok=True)

    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    app.setStyleSheet(style.build_qss(app_dir() / "ui"))

    pdf_a = make_sample_invoice(OUT / "drag_a.pdf", amount="1280.00", no="01417898")
    pdf_b = make_sample_invoice(OUT / "drag_b.pdf", amount="3560.00", no="01417899")

    # ---------------------------------------------------------------- 场景 1
    print("— 场景 1：拖两个 PDF 到图标上")
    win = MainWindow(headless=True)
    win.start_headless([str(pdf_a), str(pdf_b)])
    ok = wait_until(app, lambda: finished(win) and win.mini is not None
                    and "打印完成" in win.mini.headline.text())
    check("主界面始终没有显示（拖放即打印）", not win.isVisible())
    check("右下角小进度卡显示出来了", win.mini is not None and win.mini.isVisible())
    check("两张发票各自打印完成（一个 PDF = 一条作业）", ok,
          f"{[i.status for i in win.invoices.values()]}")
    check("历史记录自动写入 2 条", len(win.history.records) == 2,
          str(len(win.history.records)))
    if win.mini is not None:
        check("卡片标题包含出纸张数", "共出纸 2 张" in win.mini.headline.text(),
              win.mini.headline.text())
    if win.mini is not None:
        win.mini.cancel_countdown()
    win.close()

    # ---------------------------------------------------------------- 场景 2
    print("\n— 场景 2：拖一个文件夹（里面 1 个 PDF + 1 个 docx）")
    folder = OUT / "drag_folder"
    folder.mkdir(exist_ok=True)
    for old in folder.glob("*"):
        old.unlink()
    make_sample_invoice(folder / "含税发票.pdf", amount="742.50", no="01417900")
    (folder / "说明.docx").write_bytes(b"not a pdf")

    win2 = MainWindow(headless=True)
    win2.start_headless([str(folder)])
    ok2 = wait_until(app, lambda: finished(win2) and win2.mini is not None
                     and "打印完成" in win2.mini.headline.text())
    check("文件夹里的 PDF 被收下并打印", ok2,
          f"{[i.name for i in win2.invoices.values()]}")
    check("docx 没有被当成发票", len(win2.invoices) == 1, str(len(win2.invoices)))
    if win2.mini is not None:
        win2.mini.cancel_countdown()
    win2.close()

    # ---------------------------------------------------------------- 场景 3
    print("\n— 场景 3：拖进来一个非 PDF")
    docx = OUT / "说明.docx"
    docx.write_bytes(b"docx")
    win3 = MainWindow(headless=True)
    win3.start_headless([str(docx)])
    ok3 = wait_until(app, lambda: win3.mini is not None
                     and "没有可打印的发票" in win3.mini.headline.text())
    check("给出「没有可打印的发票」而不是静默退出", ok3,
          win3.mini.headline.text() if win3.mini else "")
    if win3.mini is not None:
        win3.mini.cancel_countdown()
    win3.close()

    # ---------------------------------------------------------------- 场景 4
    print("\n— 场景 4：小卡片上点「打开主界面」")
    pdf_c = make_sample_invoice(OUT / "drag_c.pdf", amount="99.00", no="01417901")
    win4 = MainWindow(headless=True)
    win4.confirm_duplicates = False
    win4.start_headless([str(pdf_c)])
    wait_until(app, lambda: finished(win4) and win4.mini is not None
               and "打印完成" in win4.mini.headline.text())
    if win4.mini is not None:
        win4.mini.openMain.emit()
    app.processEvents()
    check("切换后主界面显示出来", win4.isVisible())
    check("切换后不再自动退出（headless 关闭）", win4.headless is False)
    win4.close()

    total = len(passed)
    print()
    if all(passed):
        print(f"全部通过：{total} 项（拖放即打印 / 小卡片 / 多文件 / 文件夹 / 非 PDF / 切回主界面）")
        return 0
    print(f"失败 {passed.count(False)} / {total} 项")
    return 1


if __name__ == "__main__":
    sys.exit(main())
