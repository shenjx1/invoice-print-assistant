"""开发自测：启动界面并截图（不会真的打印——测试时把打印函数换成空实现）。"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from PySide6.QtCore import QTimer  # noqa: E402
from PySide6.QtGui import QFont  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from app import style  # noqa: E402
from app.config import CONFIG_FILE, app_dir  # noqa: E402
import app.history as history_mod  # noqa: E402
import app.worker as worker_mod  # noqa: E402

# 截图时把真正的打印函数替换成空实现，避免误用纸张
worker_mod.print_sheets = lambda **kwargs: len(kwargs["sheets"])

from app.ui import MainWindow, PreviewDialog, pil_to_pixmap  # noqa: E402
from tools.test_print import make_sample_invoice  # noqa: E402

OUT = ROOT / "build_test"
history_mod.HISTORY_FILE = OUT / "ui_history.json"


def main() -> int:
    CONFIG_FILE.unlink(missing_ok=True)  # 每次都用默认设置截图
    history_mod.HISTORY_FILE.unlink(missing_ok=True)
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    app.setFont(QFont("Microsoft YaHei UI", 9))
    app.setStyleSheet(style.build_qss(app_dir() / "ui"))

    # 造三张不同的发票，演示「一次拖入多个 PDF = 多张独立发票」
    samples = [
        make_sample_invoice(OUT / "inv_2024001.pdf", amount="1280.00", no="01417798"),
        make_sample_invoice(OUT / "inv_2024002.pdf", amount="3560.00", no="01417799"),
        make_sample_invoice(OUT / "inv_2024003.pdf", amount="742.50", no="01417800"),
    ]

    win = MainWindow()
    win.confirm_duplicates = False   # 截图时不要弹重复提示
    win.show()
    preview_dialog = None

    def shot_empty() -> None:
        win.grab().save(str(OUT / "ui_empty.png"))
        win.on_paths_dropped([str(p) for p in samples])
        QTimer.singleShot(700, shot_loaded)

    def shot_loaded() -> None:
        # 选中第二行，预览会切到第 2 张发票（证明逐张发票独立预览）
        win.table.selectRow(1)
        QTimer.singleShot(500, shot_ready)

    def shot_ready() -> None:
        win.grab().save(str(OUT / "ui_loaded.png"))
        # 再截一张「点击预览放大」的大图窗口
        inv = win._preview_target()
        if inv is None:
            win.toggle_settings_panel(True)
            QTimer.singleShot(600, shot_collapsed)
            return
        img, hint, _ = win._render_sheet(inv, high_res=True)
        nonlocal_dialog = PreviewDialog(
            pil_to_pixmap(img), f"拼版预览 · {inv.name}", hint, win,
            pixel_scale=96.0 / 220.0,
        )
        nonlocal_dialog.show()
        globals()["preview_dialog"] = nonlocal_dialog
        QTimer.singleShot(800, shot_preview)

    def shot_preview() -> None:
        dlg = globals().get("preview_dialog")
        if dlg is not None:
            dlg.grab().save(str(OUT / "ui_preview.png"))
            dlg.close()
        win.toggle_settings_panel(True)          # 再截一张收起状态的图
        QTimer.singleShot(600, shot_collapsed)

    def shot_collapsed() -> None:
        win.grab().save(str(OUT / "ui_collapsed.png"))
        shot_mini()

    def shot_mini() -> None:
        """拖放即打印模式：主界面不显示，只有右下角那张小进度卡。"""
        from PySide6.QtGui import QColor, QPainter, QPixmap

        win.close()
        drag_win = MainWindow(headless=True)
        drag_win.confirm_duplicates = False
        drag_win.start_headless([str(samples[0]), str(samples[1])])

        def grab_card() -> None:
            mini = drag_win.mini
            if mini is not None:
                pix = mini.grab()
                canvas = QPixmap(pix.size())
                canvas.fill(QColor("#E7EAF1"))
                painter = QPainter(canvas)
                painter.drawPixmap(0, 0, pix)
                painter.end()
                canvas.save(str(OUT / "ui_mini.png"))
                mini.cancel_countdown()
            drag_win.close()      # closeEvent：停掉打印线程，避免 QThread 警告
            app.quit()

        QTimer.singleShot(1800, grab_card)

    QTimer.singleShot(900, shot_empty)
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
