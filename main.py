"""发票打印助手 —— 程序入口。

用法 / 启动方式：
* **双击图标**：打开主界面（正常窗口）。
* **把 PDF 拖到图标上**（可多选、也可以是文件夹）：**不打开主界面**，
  后台直接解析、排到默认打印机，只在屏幕右下角飘一张小进度卡，打完自动退出。
* **右键「使用发票打印助手打印」**：和拖图标完全一样（走的就是「带文件启动」这条路）。
* 带文件启动时如果程序已经在跑，不会开新窗口，而是把文件转给那个实例。

同一个用户只允许一个实例（命名管道 ``LehuaPurchasePrintAssistant.single``），
避免两个进程同时写历史记录。
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtGui import QFont, QIcon  # noqa: E402
from PySide6.QtNetwork import QLocalServer, QLocalSocket  # noqa: E402
from PySide6.QtWidgets import QApplication, QMessageBox  # noqa: E402

from app import config, shellmenu, style  # noqa: E402
from app.config import PROGRAM_NAME, UI_NAME, app_dir  # noqa: E402
from app.ui import MainWindow, write_log  # noqa: E402

# 单实例管道名。环境变量只给自动化测试用（让测试进程连自己的管道，
# 不会连到用户正在跑的实例上），正常启动不设置，行为不变。
SERVER_NAME = os.environ.get("INVOICE_SINGLE_NAME") or "InvoicePrintAssistant.single"


# --------------------------------------------------------------------------- #
# 单实例：第二个进程把参数转发给已开的实例
# --------------------------------------------------------------------------- #
def forward_to_running(paths, show: bool = False) -> bool:
    """把文件（或「把主界面调出来」的请求）交给已开的实例。

    **只有对方回执确认才算交出去**。没有回执就自己接管：对方可能正在退出、
    或卡住了，这种情况下盲目转发等于把文件扔进黑洞（不打印、也没历史记录）。
    ``show=True`` 表示这次是「双击图标」——已有实例要么弹主界面，要么
    从小进度卡切回主界面，而不是又开一个进程。
    """
    sock = QLocalSocket()
    sock.connectToServer(SERVER_NAME)
    if not sock.waitForConnected(400):
        return False
    payload = {"files": list(paths), "show": bool(show)}
    sock.write(json.dumps(payload, ensure_ascii=False).encode("utf-8"))
    sock.flush()
    sock.waitForBytesWritten(500)
    ack = b""
    if sock.waitForReadyRead(1200):
        ack = bytes(sock.readAll())
    sock.disconnectFromServer()
    return ack.strip() == b"ok"


def serve_single_instance(window: MainWindow) -> QLocalServer:
    QLocalServer.removeServer(SERVER_NAME)   # 上次异常退出残留的管道
    server = QLocalServer(window)
    if not server.listen(SERVER_NAME):
        return server

    def activate() -> None:
        """把用户注意力引到当前实例上（无界面模式下就抬起小卡片）。"""
        if window.headless:
            if window.mini is not None:
                window.mini.show_at_corner()
        else:
            window.showNormal()
            window.raise_()
            window.activateWindow()

    def on_new_connection() -> None:
        conn = server.nextPendingConnection()
        if conn is None:
            return
        buffer = {"raw": b"", "done": False}

        def handle() -> None:
            if buffer["done"]:
                return
            buffer["raw"] += bytes(conn.readAll())
            text = buffer["raw"].decode("utf-8", "ignore")
            if not text.strip():
                return
            try:
                payload = json.loads(text)
            except Exception:
                return      # 还没收全，等下一次 readyRead
            buffer["done"] = True
            if isinstance(payload, dict):
                paths = shellmenu.paths_from_argv(payload.get("files") or [])
                show = bool(payload.get("show"))
            else:
                paths = shellmenu.paths_from_argv(payload) if isinstance(payload, list) else []
                show = False
            if show:
                # 双击图标时又开了一次：把当前实例的界面调到前台，
                # 无界面实例（拖图标打印后还没退）则切回主界面
                window.show_main_ui()
            else:
                activate()
            if paths:
                if window.accepting_forwarded():
                    # 先回执再干活：对方收到 ok 才会安心退出，不会自己再开一个实例
                    conn.write(b"ok")
                    conn.flush()
                    conn.waitForBytesWritten(300)
                    write_log(f"另一个实例转来 {len(paths)} 个文件，接着打")
                    window.receive_forwarded(paths)
                else:
                    write_log("本实例正在退出，拒绝接收转来的文件（交给新进程处理）")
            else:
                conn.write(b"ok")
                conn.flush()
                conn.waitForBytesWritten(300)
            conn.disconnectFromServer()

        conn.readyRead.connect(handle)
        conn.disconnected.connect(conn.deleteLater)
        if conn.bytesAvailable():
            handle()

    server.newConnection.connect(on_new_connection)
    return server


def ensure_shell_menu(window: MainWindow) -> None:
    """设置里开着「右键菜单」就保证注册表是最新的（exe 换位置后会自动重写）。

    顺便把改名前的旧菜单项清掉，右键里不会同时出现新旧两条。
    """
    if shellmenu.cleanup_legacy():
        write_log("已清理旧版留下的右键菜单 / 打开方式条目")
    if window.settings.shell_menu and not shellmenu.is_registered():
        if shellmenu.register():
            write_log(f"已注册资源管理器右键菜单：{shellmenu.MENU_TEXT}")
    # 让左侧面板的勾选状态与注册表实际内容一致
    if not window.headless:
        window._sync_shell_checkbox()


def main() -> int:
    QApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
    )
    app = QApplication(sys.argv)
    app.setApplicationName(PROGRAM_NAME)
    app.setApplicationDisplayName(UI_NAME)
    app.setStyle("Fusion")
    app.setFont(QFont("Microsoft YaHei UI", 9))
    app.setStyleSheet(style.build_qss(app_dir() / "ui"))

    icon_path = BASE_DIR / "assets" / "icon.ico"
    if icon_path.exists():
        app.setWindowIcon(QIcon(str(icon_path)))

    # 有参数 = 拖到图标上 / 右键菜单 / 命令行；没有任何参数 = 双击图标
    raw_args = list(sys.argv[1:])
    paths = shellmenu.paths_from_argv(raw_args)
    dropped = bool(raw_args)

    if dropped and not paths:
        # 拖进来的不是 PDF：提示一句就退出，不打开主界面
        names = "、".join(Path(a).name or a for a in raw_args[:4])
        box = QMessageBox()
        box.setIcon(QMessageBox.Information)
        box.setWindowTitle(UI_NAME)
        box.setText("只能打印 PDF 发票")
        box.setInformativeText(
            f"拖进来的不是 PDF（或文件夹里没有 PDF）：\n{names}"
        )
        box.exec()
        return 0

    if forward_to_running(paths, show=not dropped):
        return 0    # 已有实例在跑，文件（或「开界面」的请求）已经交给它了

    headless = bool(paths)
    window = MainWindow(headless=headless)
    if headless:
        # 拖放即打印：不开主界面，只显示右下角的小进度卡，打完自动退出
        app.setQuitOnLastWindowClosed(False)
        window.start_headless(paths)
    else:
        window.show()

    server = serve_single_instance(window)   # 保持引用，别被 GC 掉
    window._single_server = server
    ensure_shell_menu(window)

    # 排查「打完没有历史记录」时，这两行是第一现场
    write_log(f"启动完成：数据目录 {app_dir()}")
    write_log(
        f"历史记录已载入 {len(window.history.records)} 条（{window.history.path}）"
    )
    if config.DIR_FALLBACK_USED:
        write_log(
            f"警告：%APPDATA% 下写不进文件，数据已改存到 {config.DIR_FALLBACK_USED}"
        )
        window._toast(
            f"默认的数据目录不可写，历史记录改存到 {config.DIR_FALLBACK_USED}", "warn"
        )

    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
