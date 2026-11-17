"""「拖到图标上」时的重复打印提醒自测。

要验证的是：PDF 直接拖到 exe 图标（headless，不开主界面）时，
发票号与历史记录重复会不会照样弹窗、选择跳过是否真的不打印也不写记录。

打印函数被替换成空实现（返回张数），不会浪费纸。
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from PySide6.QtCore import QTimer  # noqa: E402
from PySide6.QtWidgets import QApplication, QMessageBox  # noqa: E402

from app import style  # noqa: E402
from app.config import CONFIG_FILE, app_dir  # noqa: E402
from app.history import PrintRecord  # noqa: E402
import app.history as history_mod  # noqa: E402
import app.worker as worker_mod  # noqa: E402

worker_mod.print_sheets = lambda **kwargs: len(kwargs["sheets"])

from app.invoice import ST_DONE, ST_SKIPPED  # noqa: E402
from app.ui import MainWindow  # noqa: E402
from tools.test_print import make_sample_invoice  # noqa: E402

OUT = ROOT / "build_test"
HIST = OUT / "dup_history.json"
history_mod.HISTORY_FILE = HIST

results: list = []


def check(title: str, ok: bool, detail: str = "") -> bool:
    print(f"{'OK  ' if ok else 'FAIL'} {title}" + (f"  ({detail})" if detail else ""))
    results.append(ok)
    return ok


def wait_until(app, pred, timeout: float = 15.0) -> bool:
    start = time.time()
    while time.time() - start < timeout:
        app.processEvents()
        if pred():
            return True
        time.sleep(0.05)
    return False


def seed(number: str) -> None:
    """往历史里塞一条「已打印」，模拟这张票以前打过。"""
    hist = history_mod.History()
    hist.records.insert(0, PrintRecord(
        when="2026-11-04 10:00:00", invoice_no=number, material="专项化学用品",
        spec="AFCONA-2035", qty="300", unit_price="￥92.66", status="已打印",
    ))
    hist.save()


def main() -> int:
    OUT.mkdir(exist_ok=True)
    CONFIG_FILE.unlink(missing_ok=True)
    HIST.unlink(missing_ok=True)

    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    app.setStyleSheet(style.build_qss(app_dir() / "ui"))

    pdf = make_sample_invoice(OUT / "dup_same.pdf", amount="1280.00", no="01417898")
    pdf2 = make_sample_invoice(OUT / "dup_other.pdf", amount="99.00", no="01417902")

    # ------------------------------------------------------------ 1 首次不提示
    print("— 场景 1：第一次拖到图标（历史里没有这张票）")
    win = MainWindow(headless=True)
    asked: list = []
    win._ask_duplicates = lambda hits: (asked.append(hits), True)[1]
    win.start_headless([str(pdf)])
    ok = wait_until(app, lambda: any(i.status == ST_DONE for i in win.invoices.values()))
    check("正常打印完成", ok, f"{[i.status for i in win.invoices.values()]}")
    check("没有弹重复提醒", not asked)
    check("历史记录 +1", len(win.history.records) == 1, str(len(win.history.records)))
    if win.mini is not None:
        win.mini.cancel_countdown()
    win.close()
    wait_until(app, lambda: True, 0.3)

    # ------------------------------------------------ 2 重复拖入：提示 + 跳过
    print("\n— 场景 2：同一张票再拖到图标，选择「跳过这些」")
    win2 = MainWindow(headless=True)
    asked2: list = []
    win2._ask_duplicates = lambda hits: (asked2.append(hits), False)[1]
    win2.start_headless([str(pdf)])
    ok2 = wait_until(app, lambda: bool(asked2) and win2.mini is not None
                     and "重复" in win2.mini.detail.text())
    check("拖到图标也会弹重复提醒", bool(asked2), f"提示次数={len(asked2)}")
    check("提醒里带上了重复原因", bool(asked2) and "打印过" in asked2[0][0][1],
          asked2[0][0][1] if asked2 else "—")
    check("选择跳过后状态是「已跳过」",
          any(i.status == ST_SKIPPED for i in win2.invoices.values()),
          f"{[i.status for i in win2.invoices.values()]}")
    check("跳过后没有新增历史记录", len(win2.history.records) == 1,
          str(len(win2.history.records)))
    check("小卡片写明重复跳过张数", ok2,
          win2.mini.detail.text() if win2.mini is not None else "—")
    if win2.mini is not None:
        win2.mini.cancel_countdown()
    win2.close()
    wait_until(app, lambda: True, 0.3)

    # --------------------------------------- 3 重复拖入：提示 + 仍然打印
    print("\n— 场景 3：同一张票再拖，选择「仍然打印」")
    win3 = MainWindow(headless=True)
    asked3: list = []
    win3._ask_duplicates = lambda hits: (asked3.append(hits), True)[1]
    win3.start_headless([str(pdf)])
    ok3 = wait_until(app, lambda: bool(asked3)
                     and all(i.status == ST_DONE for i in win3.invoices.values()))
    check("提示后可以仍然打印", ok3, f"{[i.status for i in win3.invoices.values()]}")
    check("仍然打印会再记一条历史", len(win3.history.records) == 2,
          str(len(win3.history.records)))
    if win3.mini is not None:
        win3.mini.cancel_countdown()
    win3.close()
    wait_until(app, lambda: True, 0.3)

    # ------------------------------------------- 4 同一批次里两个文件同号
    print("\n— 场景 4：同一批次拖入两个发票号相同的文件")
    pdf_dup = make_sample_invoice(OUT / "dup_twin.pdf", amount="1280.00", no="01417898")
    win4 = MainWindow(headless=True)
    asked4: list = []
    win4._ask_duplicates = lambda hits: (asked4.append(hits), False)[1]
    win4.start_headless([str(pdf), str(pdf_dup)])
    wait_until(app, lambda: bool(asked4) and win4.mini is not None
               and "重复" in win4.mini.detail.text())
    check("同批次同号也会提醒", bool(asked4), f"提示次数={len(asked4)}")
    check("提示里说明是同批次重复",
          bool(asked4) and any("本批次" in reason for _, reason in asked4[0]),
          str([r for _, r in asked4[0]]) if asked4 else "—")
    if win4.mini is not None:
        win4.mini.cancel_countdown()
    win4.close()
    wait_until(app, lambda: True, 0.3)

    # ------------------------------------------------- 5 不同发票号不提示
    print("\n— 场景 5：换一张没打过的票")
    win5 = MainWindow(headless=True)
    asked5: list = []
    win5._ask_duplicates = lambda hits: (asked5.append(hits), False)[1]
    win5.start_headless([str(pdf2)])
    ok5 = wait_until(app, lambda: any(i.status == ST_DONE for i in win5.invoices.values()))
    check("新发票号不弹提醒", not asked5)
    check("新发票号正常打印", ok5)
    if win5.mini is not None:
        win5.mini.cancel_countdown()
    win5.close()
    wait_until(app, lambda: True, 0.3)

    # ------------------------------------- 6 真弹窗：确认框确实显示出来了
    print("\n— 场景 6：不替换任何函数，看真实确认框会不会弹出来")
    win6 = MainWindow(headless=True)
    seen: list = []
    poll = QTimer()
    poll.setInterval(100)

    def look_for_dialog() -> None:
        for widget in QApplication.topLevelWidgets():
            if isinstance(widget, QMessageBox) and widget.isVisible():
                seen.append(widget.windowTitle())
                poll.stop()
                widget.reject()          # 相当于点「跳过这些」

    poll.timeout.connect(look_for_dialog)
    poll.start()
    win6.start_headless([str(pdf)])
    ok6 = wait_until(app, lambda: bool(seen), 8.0)
    check("真的弹出了确认框（不只是走逻辑）", ok6, str(seen))
    win6._ask_duplicates = lambda hits: False     # 以防弹窗没被关掉卡住
    wait_until(app, lambda: any(i.status == ST_SKIPPED for i in win6.invoices.values()), 5)
    check("关掉弹窗后按「跳过」处理",
          any(i.status == ST_SKIPPED for i in win6.invoices.values()),
          f"{[i.status for i in win6.invoices.values()]}")
    if win6.mini is not None:
        win6.mini.cancel_countdown()
    win6.close()

    total = len(results)
    print()
    if all(results):
        print(f"全部通过：{total} 项（拖到图标也会提示重复 / 跳过不写记录 / 仍可打印）")
        return 0
    print(f"失败 {results.count(False)} / {total} 项")
    return 1


if __name__ == "__main__":
    sys.exit(main())
