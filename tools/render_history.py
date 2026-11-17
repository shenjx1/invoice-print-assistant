"""只渲染「历史记录选中一行 + 删除按钮可用」的界面截图，方便确认交互。"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from PySide6.QtWidgets import QApplication  # noqa: E402

from app import style  # noqa: E402
from app.config import CONFIG_FILE, app_dir  # noqa: E402
from app.history import PrintRecord  # noqa: E402
import app.history as history_mod  # noqa: E402

from app.ui import MainWindow  # noqa: E402

OUT = ROOT / "build_test"
history_mod.HISTORY_FILE = OUT / "render_history.json"


def main() -> int:
    OUT.mkdir(exist_ok=True)
    CONFIG_FILE.unlink(missing_ok=True)
    hist = history_mod.History()
    hist.records.clear()
    for i, (no, material, spec, qty, price, when) in enumerate([
        ("46695211", "专项化学用品", "AFCONA-2035", "300", "￥92.66", "2026-11-04 09:12:31"),
        ("99099791", "化学合成材料", "CR10", "2000", "￥23.17", "2026-11-04 09:08:05"),
        ("41041696", "颜料", "—", "4500", "￥6.33", "2026-11-03 16:40:12"),
    ]):
        hist.records.append(PrintRecord(
            when=when, invoice_no=no, material=material, spec=spec, qty=qty,
            unit_price=price, status="已打印" if i < 2 else "打印失败",
        ))
    hist.save()

    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    app.setStyleSheet(style.build_qss(app_dir() / "ui"))

    win = MainWindow()
    win.resize(1140, 820)
    win.show()
    app.processEvents()

    win.history_table.selectRow(1)          # 选中第二行，让「删除选中」变可用
    app.processEvents()
    win.grab().save(str(OUT / "ui_history_selected.png"))
    print("已保存", OUT / "ui_history_selected.png")
    print("删除按钮可用:", win.history_del_btn.isEnabled())
    print("选中行:", win._history_selected_rows())
    win.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
