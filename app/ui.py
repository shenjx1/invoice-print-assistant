"""主窗口与自定义控件。"""
from __future__ import annotations

import datetime
import os
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from PIL import Image
from PySide6.QtCore import (
    QAbstractAnimation,
    QEasingCurve,
    QEvent,
    QPropertyAnimation,
    QRectF,
    QSize,
    Qt,
    QTimer,
    Signal,
)
from PySide6.QtGui import (
    QBrush,
    QColor,
    QFont,
    QGuiApplication,
    QImage,
    QKeySequence,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
    QShortcut,
)
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QDoubleSpinBox,
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from . import printers, shellmenu, style
from .config import CONFIG_FILE, UI_NAME, Settings, app_dir, desktop_dir
from .engine import LayoutSpec, compose_sheet, list_printers
from .extract import extract_fields
from .history import COLUMNS, History, PrintRecord
from .invoice import (
    PAD_BLANK,
    PAD_REPEAT,
    ST_DONE,
    ST_FAILED,
    ST_NOT_INVOICE,
    ST_PENDING,
    ST_PRINTING,
    ST_SKIPPED,
    ST_UNREADABLE,
    Invoice,
    plan_sheets,
    read_invoice,
    sheet_count,
)
from .worker import PrintBatch, PrintWorker

LOG_FILE = app_dir() / "run.log"


def write_log(text: str) -> None:
    try:
        with LOG_FILE.open("a", encoding="utf-8") as fh:
            stamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            fh.write(f"[{stamp}] {text}\n")
    except Exception:
        pass


# 历史记录表格的对齐：文字列靠左，数字/时间/结果居中
LEFT_COLUMNS = (1, 2)
STATUS_COLUMN = COLUMNS.index("结果")


def _cell_align(col: int):
    return (Qt.AlignLeft if col in LEFT_COLUMNS else Qt.AlignHCenter) | Qt.AlignVCenter


def pil_to_pixmap(img: Image.Image) -> QPixmap:
    img = img.convert("RGB")
    data = img.tobytes("raw", "RGB")
    qimg = QImage(data, img.width, img.height, img.width * 3, QImage.Format_RGB888).copy()
    return QPixmap.fromImage(qimg)


def clipboard_paths() -> List[str]:
    """读取剪贴板里的文件路径（微信 / 资源管理器「复制文件」走 CF_HDROP）。

    这条路让「微信收到发票 → 先存桌面 → 再拖进来」变成「复制 → Ctrl+V」。
    """
    paths: List[str] = []
    try:
        mime = QGuiApplication.clipboard().mimeData()
        if mime is not None and mime.hasUrls():
            paths = [u.toLocalFile() for u in mime.urls() if u.isLocalFile()]
    except Exception:
        paths = []
    if paths:
        return [p for p in paths if p]

    # 兜底：有些程序只往 CF_HDROP 写，Qt 的 mimeData 读不到
    try:
        import win32clipboard
        import win32con

        win32clipboard.OpenClipboard()
        try:
            if win32clipboard.IsClipboardFormatAvailable(win32con.CF_HDROP):
                paths = [str(p) for p in win32clipboard.GetClipboardData(win32con.CF_HDROP)]
        finally:
            win32clipboard.CloseClipboard()
    except Exception:
        paths = []
    return [p for p in paths if p]


# --------------------------------------------------------------------------- #
# 拖放区
# --------------------------------------------------------------------------- #
class DropZone(QFrame):
    filesDropped = Signal(list)
    pickRequested = Signal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setAcceptDrops(True)
        self.setCursor(Qt.PointingHandCursor)
        self.setMinimumSize(QSize(360, 220))
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self._drag = False
        self._hover = False

    # ---------------------------------------------------------- 拖放事件
    def dragEnterEvent(self, event) -> None:  # noqa: N802
        if self._has_pdf(event.mimeData()):
            event.acceptProposedAction()
            self._drag = True
            self.update()
        else:
            event.ignore()

    def dragMoveEvent(self, event) -> None:  # noqa: N802
        if self._has_pdf(event.mimeData()):
            event.acceptProposedAction()

    def dragLeaveEvent(self, event) -> None:  # noqa: N802
        self._drag = False
        self.update()

    def dropEvent(self, event) -> None:  # noqa: N802
        self._drag = False
        self.update()
        paths = []
        for url in event.mimeData().urls():
            local = url.toLocalFile()
            if local:
                paths.append(local)
        if paths:
            event.acceptProposedAction()
            self.filesDropped.emit(paths)

    @staticmethod
    def _has_pdf(mime) -> bool:
        if not mime.hasUrls():
            return False
        for url in mime.urls():
            p = url.toLocalFile()
            if p and (p.lower().endswith(".pdf") or os.path.isdir(p)):
                return True
        return False

    # ---------------------------------------------------------- 鼠标
    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.LeftButton:
            self.pickRequested.emit()

    def enterEvent(self, event) -> None:  # noqa: N802
        self._hover = True
        self.update()

    def leaveEvent(self, event) -> None:  # noqa: N802
        self._hover = False
        self.update()

    # ---------------------------------------------------------- 绘制
    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        rect = QRectF(self.rect()).adjusted(1.5, 1.5, -1.5, -1.5)
        path = QPainterPath()
        path.addRoundedRect(rect, 14, 14)

        if self._drag:
            grad = QLinearGradient(rect.topLeft(), rect.bottomRight())
            grad.setColorAt(0.0, QColor("#EEF4FF"))
            grad.setColorAt(1.0, QColor("#DEEAFF"))
            painter.fillPath(path, QBrush(grad))
            pen = QPen(QColor(style.ACCENT), 2.0, Qt.DashLine)
        else:
            grad = QLinearGradient(rect.topLeft(), rect.bottomRight())
            grad.setColorAt(0.0, QColor("#FFFFFF"))
            grad.setColorAt(1.0, QColor("#FAFBFD"))
            painter.fillPath(path, QBrush(grad))
            color = "#A8B8D8" if self._hover else "#C9D3E4"
            pen = QPen(QColor(color), 1.6, Qt.DashLine)
        pen.setDashPattern([6, 4])
        painter.setPen(pen)
        painter.drawPath(path)

        # 文档图标
        icon_w, icon_h = 54.0, 66.0
        cx = rect.center().x()
        top = rect.top() + rect.height() * 0.18
        icon = QRectF(cx - icon_w / 2, top, icon_w, icon_h)
        painter.setPen(QPen(QColor("#B9C6DC"), 1.4))
        painter.setBrush(QBrush(QColor("#FFFFFF")))
        painter.drawRoundedRect(icon, 7, 7)
        fold = QPainterPath()
        fold.moveTo(icon.right() - 16, icon.top())
        fold.lineTo(icon.right(), icon.top() + 16)
        fold.lineTo(icon.right() - 16, icon.top() + 16)
        fold.closeSubpath()
        painter.setPen(Qt.NoPen)
        painter.setBrush(QBrush(QColor("#E7EDF7")))
        painter.drawPath(fold)

        painter.setPen(QPen(QColor(style.ACCENT)))
        f = QFont(self.font())
        f.setPixelSize(13)
        f.setBold(True)
        painter.setFont(f)
        painter.drawText(
            QRectF(icon.left(), icon.top() + 24, icon.width(), 20),
            Qt.AlignHCenter | Qt.AlignVCenter,
            "PDF",
        )
        painter.setPen(QPen(QColor("#D7E1F1")))
        for i in range(3):
            y = icon.top() + 44 + i * 6
            painter.drawLine(int(icon.left() + 10), int(y), int(icon.right() - 10), int(y))

        # 文案
        title_font = QFont(self.font())
        title_font.setPixelSize(16)
        title_font.setBold(True)
        painter.setFont(title_font)
        painter.setPen(QPen(QColor("#111827")))
        painter.drawText(
            QRectF(rect.left(), icon.bottom() + 12, rect.width(), 26),
            Qt.AlignHCenter | Qt.AlignVCenter,
            "把发票 PDF 拖到这里",
        )

        sub_font = QFont(self.font())
        sub_font.setPixelSize(12)
        painter.setFont(sub_font)
        painter.setPen(QPen(QColor(style.MUTED)))
        painter.drawText(
            QRectF(rect.left(), icon.bottom() + 38, rect.width(), 46),
            Qt.AlignHCenter | Qt.AlignTop,
            "支持一次拖入多个 PDF / 文件夹\n每个文件独立成一张发票、独立打印\n也可以单击选择文件",
        )


# --------------------------------------------------------------------------- #
# 版面预览
# --------------------------------------------------------------------------- #
class SheetPreview(QWidget):
    """拼版预览。单击（或双击）图片会发出 clicked 信号，由主窗口打开大图。"""

    clicked = Signal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._pixmap: Optional[QPixmap] = None
        self._hint = ""
        self.setMinimumSize(QSize(280, 300))
        self.setCursor(Qt.ArrowCursor)
        self.setToolTip("")

    def set_sheet(self, img: Optional[Image.Image], hint: str = "") -> None:
        self._pixmap = pil_to_pixmap(img) if img is not None else None
        self._hint = hint
        has_sheet = self._pixmap is not None and not self._pixmap.isNull()
        self.setCursor(Qt.PointingHandCursor if has_sheet else Qt.ArrowCursor)
        self.setToolTip("点击放大查看" if has_sheet else "")
        self.update()

    def pixmap(self) -> Optional[QPixmap]:
        return self._pixmap

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        if (
            self._pixmap is not None
            and not self._pixmap.isNull()
            and event.button() == Qt.LeftButton
        ):
            self.clicked.emit()
        super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event) -> None:  # noqa: N802
        # 双击也放大（部分用户习惯双击），避免再触发一次放大
        if self._pixmap is not None and not self._pixmap.isNull():
            event.accept()
            return
        super().mouseDoubleClickEvent(event)

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.fillRect(self.rect(), QColor("#FFFFFF"))

        area = QRectF(self.rect()).adjusted(14, 14, -14, -14)
        if self._pixmap is None or self._pixmap.isNull():
            pen = QPen(QColor("#D7DEEA"), 1.3, Qt.DashLine)
            pen.setDashPattern([5, 4])
            painter.setPen(pen)
            painter.setBrush(Qt.NoBrush)
            painter.drawRoundedRect(area, 10, 10)
            painter.setPen(QPen(QColor("#9AA3B2")))
            f = QFont(self.font())
            f.setPixelSize(13)
            painter.setFont(f)
            painter.drawText(area, Qt.AlignCenter, self._hint or "没有待打印的页面")
            return

        pm = self._pixmap
        hint_h = 22.0 if self._hint else 0.0
        img_area = QRectF(area.left(), area.top(), area.width(), max(40.0, area.height() - hint_h))
        ratio = min(img_area.width() / pm.width(), img_area.height() / pm.height())
        w = pm.width() * ratio
        h = pm.height() * ratio
        target = QRectF(img_area.center().x() - w / 2, img_area.center().y() - h / 2, w, h)

        shadow = target.adjusted(3, 4, 3, 6)
        painter.setPen(Qt.NoPen)
        painter.setBrush(QBrush(QColor(15, 23, 42, 22)))
        painter.drawRoundedRect(shadow, 4, 4)

        painter.drawPixmap(target, pm, QRectF(pm.rect()))
        painter.setPen(QPen(QColor("#CBD5E1"), 1))
        painter.setBrush(Qt.NoBrush)
        painter.drawRect(target.adjusted(-0.5, -0.5, 0.5, 0.5))

        if self._hint:
            painter.setPen(QPen(QColor(style.MUTED)))
            f = QFont(self.font())
            f.setPixelSize(11)
            painter.setFont(f)
            painter.drawText(
                QRectF(area.left(), area.bottom() - 2, area.width(), 18),
                Qt.AlignHCenter | Qt.AlignBottom,
                self._hint,
            )


# --------------------------------------------------------------------------- #
# 放大查看
# --------------------------------------------------------------------------- #
class PreviewDialog(QDialog):
    """拼版预览的大图窗口：适应窗口 / 100% / 加减缩放，Ctrl+滚轮也能缩放。"""

    MIN_ZOOM = 0.1
    MAX_ZOOM = 5.0

    def __init__(self, pixmap: QPixmap, title: str, hint: str = "",
                 parent=None, pixel_scale: float = 1.0) -> None:
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setObjectName("PreviewDialog")
        # 状态先就位：resize 会触发 resizeEvent，那里要读 _src / _fit_zoom
        self._src = pixmap
        self._zoom = 1.0
        self._fit_zoom = 1.0
        # pixel_scale = 96 / 渲染 dpi：让「100%」等于屏幕上的实际纸张大小，
        # 而不是高分辨率位图的原始像素（220dpi 图直接 100% 会大得离谱）
        self._base = max(0.05, pixel_scale)
        self.resize(1040, 820)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(12, 10, 12, 10)
        lay.setSpacing(8)

        self.label = QLabel()
        self.label.setAlignment(Qt.AlignCenter)
        self.label.setStyleSheet("background: transparent;")

        self.scroll = QScrollArea()
        self.scroll.setWidget(self.label)
        self.scroll.setWidgetResizable(False)
        self.scroll.setAlignment(Qt.AlignCenter)
        self.scroll.setFrameShape(QFrame.NoFrame)
        self.scroll.setStyleSheet("QScrollArea { background: #F4F6FA; border-radius: 8px; }")
        self.scroll.viewport().installEventFilter(self)
        lay.addWidget(self.scroll, 1)

        foot = QHBoxLayout()
        foot.setSpacing(8)
        self.hint_label = QLabel(hint or "拖动滚动条查看细节")
        self.hint_label.setObjectName("Muted")
        foot.addWidget(self.hint_label)
        foot.addStretch(1)

        self.zoom_label = QLabel("100%")
        self.zoom_label.setObjectName("Muted")
        self.zoom_label.setMinimumWidth(48)
        self.zoom_label.setAlignment(Qt.AlignCenter)
        foot.addWidget(self.zoom_label)

        self.fit_btn = QPushButton("适应窗口")
        self.out_btn = QPushButton("缩小")
        self.in_btn = QPushButton("放大")
        self.actual_btn = QPushButton("100%")
        for btn in (self.out_btn, self.in_btn, self.actual_btn, self.fit_btn):
            btn.setObjectName("Ghost")
            foot.addWidget(btn)
        self.fit_btn.setDefault(True)

        lay.addLayout(foot)

        self.out_btn.clicked.connect(lambda: self._step(1 / 1.25))
        self.in_btn.clicked.connect(lambda: self._step(1.25))
        self.actual_btn.clicked.connect(lambda: self._set_zoom(self._base))
        self.fit_btn.clicked.connect(self._fit)

        self.setToolTip("Ctrl+滚轮缩放 · 双击切换「适应窗口 / 100%」· Esc 关闭")
        QTimer.singleShot(0, self._fit)

    # ------------------------------------------------------------ 缩放
    def _viewport_size(self) -> Tuple[int, int]:
        size = self.scroll.viewport().size()
        return max(40, size.width()), max(40, size.height())

    def _fit(self) -> None:
        if self._src is None or self._src.isNull():
            return
        vw, vh = self._viewport_size()
        self._fit_zoom = min(vw / self._src.width(), vh / self._src.height())
        self._set_zoom(self._fit_zoom)

    def _step(self, factor: float) -> None:
        self._set_zoom(self._zoom * factor)

    def _set_zoom(self, zoom: float) -> None:
        if self._src is None or self._src.isNull():
            return
        zoom = max(self.MIN_ZOOM, min(self.MAX_ZOOM, zoom))
        self._zoom = zoom
        w = max(1, int(round(self._src.width() * zoom)))
        h = max(1, int(round(self._src.height() * zoom)))
        pm = self._src.scaled(QSize(w, h), Qt.KeepAspectRatio, Qt.SmoothTransformation)
        self.label.setPixmap(pm)
        self.label.resize(pm.size())
        self.zoom_label.setText(f"{int(round(zoom / self._base * 100))}%")

    def wheelEvent(self, event) -> None:  # noqa: N802
        if event.modifiers() & Qt.ControlModifier:
            self._step(1.15 if event.angleDelta().y() > 0 else 1 / 1.15)
            event.accept()
            return
        super().wheelEvent(event)

    def eventFilter(self, obj, event):  # noqa: N802
        if obj is self.scroll.viewport() and event.type() == QEvent.Type.Wheel:
            if event.modifiers() & Qt.ControlModifier:
                self._step(1.15 if event.angleDelta().y() > 0 else 1 / 1.15)
                return True
        return super().eventFilter(obj, event)

    def mouseDoubleClickEvent(self, event) -> None:  # noqa: N802
        if abs(self._zoom - self._fit_zoom) < 0.02:
            self._set_zoom(self._base)
        else:
            self._fit()
        event.accept()

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        if not hasattr(self, "scroll"):
            return  # 构造期间 resize 先于子控件创建
        # 窗口大小变了：如果当前正好是「适应窗口」，跟着重新适配
        if self._src is not None and abs(self._zoom - self._fit_zoom) < 0.02:
            self._fit()


# --------------------------------------------------------------------------- #
# 主窗口
# --------------------------------------------------------------------------- #
class MainWindow(QMainWindow):
    def __init__(self, headless: bool = False) -> None:
        super().__init__()
        # headless=True：拖放即打印模式，主界面不显示，只在右下角飘一张小进度卡
        self.headless = headless
        self.mini = None
        self.dialog_parent = self          # 弹窗（重复提醒）挂哪个窗口上
        self.settings: Settings = Settings.load()
        self.worker = PrintWorker(self)
        self.history: History = History()
        self.invoices: Dict[int, Invoice] = {}     # uid -> 发票
        self._order: List[int] = []                # 表格里的显示顺序
        self._rows: Dict[int, int] = {}            # uid -> 表格行号
        self._preview_uid: Optional[int] = None
        self._uid_seq = 0
        self._batch_seq = 0
        self._default_printer = ""
        self._busy = False
        self._mini_done = False
        self._quit_pending = False
        self._close_when_idle = False     # 关窗时还在打印：打完再退
        self._close_attempts = 0          # 连点两次 × 才允许强制退出
        self._shutting_down = False      # 关闭流程已开始：不再接收别的实例转来的文件
        self._last_message = ""
        self.confirm_duplicates = True   # 发票号重复时是否弹窗确认（自测里可关掉）
        self._preview_timer = QTimer(self)
        self._preview_timer.setSingleShot(True)
        self._preview_timer.setInterval(90)
        self._preview_timer.timeout.connect(self.refresh_preview)
        self._panel_anim: Optional[QPropertyAnimation] = None

        self.setWindowTitle(UI_NAME)
        self.resize(1140, 820)
        self._build_ui()
        self._bind_events()
        self.worker.start()  # 打印队列线程常驻
        self.refresh_printers()
        self._sync_widgets_from_settings()
        self._sync_shell_checkbox()
        self.refresh_preview()
        self._update_hint()
        self._apply_panel_state(animate=False)
        self.refresh_history()

    # ------------------------------------------------------------------ UI
    def _build_ui(self) -> None:
        root = QWidget()
        root.setObjectName("Root")
        self.setCentralWidget(root)
        outer = QVBoxLayout(root)
        outer.setContentsMargins(16, 14, 16, 10)
        outer.setSpacing(12)

        outer.addWidget(self._build_header())

        body = QHBoxLayout()
        body.setSpacing(10)
        body.addWidget(self._build_settings_panel_wrap(), 0)
        body.addWidget(self._build_collapse_bar(), 0)
        body.addLayout(self._build_main_area(), 1)
        outer.addLayout(body, 1)

        outer.addWidget(self._build_history(), 0)

        self.status_label = QLabel("准备就绪")
        self.status_label.setObjectName("Muted")
        holder = QWidget()
        holder_lay = QHBoxLayout(holder)
        holder_lay.setContentsMargins(10, 3, 10, 5)
        holder_lay.setSpacing(0)
        holder_lay.addWidget(self.status_label)
        self.statusBar().addWidget(holder, 1)
        self.statusBar().setSizeGripEnabled(False)
        self.statusBar().setMinimumHeight(30)
        self.statusBar().setStyleSheet(
            "QStatusBar { background: transparent; border-top: 1px solid "
            f"{style.BORDER}; }}"
        )

    # ---------------------------------------------------------- 顶部
    def _build_header(self) -> QWidget:
        card = QFrame()
        card.setObjectName("Card")
        lay = QHBoxLayout(card)
        lay.setContentsMargins(20, 14, 18, 14)
        lay.setSpacing(12)

        title_box = QVBoxLayout()
        title_box.setSpacing(2)
        title = QLabel(UI_NAME)
        title.setObjectName("Title")
        subtitle = QLabel("多个 PDF 一起拖入 · 每个文件独立成一张发票 · 按「每张纸 1×2」拼版后送打印机")
        subtitle.setObjectName("Subtitle")
        title_box.addWidget(title)
        title_box.addWidget(subtitle)
        lay.addLayout(title_box)
        lay.addStretch(1)

        printer_label = QLabel("打印机")
        printer_label.setObjectName("Field")
        self.printer_combo = QComboBox()
        self.printer_combo.setMinimumWidth(300)
        self.refresh_btn = QPushButton("刷新")
        self.refresh_btn.setObjectName("Ghost")
        self.refresh_btn.setFixedWidth(62)

        lay.addWidget(printer_label)
        lay.addWidget(self.printer_combo)
        lay.addWidget(self.refresh_btn)
        return card

    # ---------------------------------------------------------- 左侧设置
    def _build_settings(self) -> QWidget:
        card = QFrame()
        card.setObjectName("Card")
        card.setFixedWidth(292)
        lay = QVBoxLayout(card)
        lay.setContentsMargins(16, 16, 16, 16)
        lay.setSpacing(12)

        title = QLabel("打印设置")
        title.setObjectName("SectionTitle")
        lay.addWidget(title)

        grid = QGridLayout()
        grid.setHorizontalSpacing(10)
        grid.setVerticalSpacing(9)
        grid.setColumnStretch(1, 1)

        def add_row(r: int, text: str, widget: QWidget) -> None:
            label = QLabel(text)
            label.setObjectName("Field")
            widget.setMinimumHeight(30)
            grid.addWidget(label, r, 0)
            grid.addWidget(widget, r, 1)

        self.layout_combo = QComboBox()
        self.layout_combo.addItem("1 × 2  上下两页", "1x2")
        self.layout_combo.addItem("1 × 1  单页", "1x1")
        self.layout_combo.addItem("2 × 1  左右两页", "2x1")
        self.layout_combo.addItem("2 × 2  四页", "2x2")

        self.margin_spin = QDoubleSpinBox()
        self.margin_spin.setRange(0.0, 4.0)
        self.margin_spin.setSingleStep(0.05)
        self.margin_spin.setDecimals(2)
        self.margin_spin.setSuffix(" cm")

        self.scale_spin = QDoubleSpinBox()
        self.scale_spin.setRange(40.0, 100.0)
        self.scale_spin.setSingleStep(0.5)
        self.scale_spin.setDecimals(1)
        self.scale_spin.setSuffix(" %")
        self.scale_spin.setToolTip("先把发票等比缩放到格子大小，再按此比例微调")

        self.paper_combo = QComboBox()
        for name in ("A4", "A5", "Letter", "A3"):
            self.paper_combo.addItem(name, name)

        self.orient_combo = QComboBox()
        self.orient_combo.addItem("纵向", False)
        self.orient_combo.addItem("横向", True)

        self.copies_spin = QSpinBox()
        self.copies_spin.setRange(1, 99)

        self.dpi_combo = QComboBox()
        for dpi in (200, 300, 600):
            self.dpi_combo.addItem(f"{dpi} dpi", dpi)

        self.engine_combo = QComboBox()
        for value, label in printers.ENGINE_LABELS.items():
            self.engine_combo.addItem(label, value)

        self.gray_check = QCheckBox("灰度送印（黑白激光机更快）")
        self.gray_check.setToolTip(
            "按 8 位灰度渲染并送印。黑白激光打印机本来就要把彩色转灰度，\n"
            "勾上后送进队列的数据少约 3/4，出纸更快；彩色激光机请取消勾选。"
        )

        add_row(0, "每张纸放置", self.layout_combo)
        add_row(1, "页边距", self.margin_spin)
        add_row(2, "缩放比例", self.scale_spin)
        add_row(3, "纸张", self.paper_combo)
        add_row(4, "方向", self.orient_combo)
        add_row(5, "份数", self.copies_spin)
        add_row(6, "打印清晰度", self.dpi_combo)
        add_row(7, "打印引擎", self.engine_combo)
        add_row(8, "送印色彩", self.gray_check)
        lay.addLayout(grid)

        self.engine_hint = QLabel("")
        self.engine_hint.setObjectName("Hint")
        self.engine_hint.setWordWrap(True)
        lay.addWidget(self.engine_hint)
        self._update_engine_hint()

        tip = QLabel("1×2 + 页边距 0.51cm + 缩放 95.5% 即为标准发票拼版参数；\n每个 PDF 独立成版、独立一张打印作业")
        tip.setObjectName("Hint")
        tip.setWordWrap(True)
        lay.addWidget(tip)

        line = QFrame()
        line.setFixedHeight(1)
        line.setStyleSheet(f"background:{style.BORDER};")
        lay.addWidget(line)

        self.auto_print_check = QCheckBox("拖入后立即打印")
        self.auto_print_check.setToolTip(
            "开启后每个 PDF 解析完就独立提交打印；关闭后先入队，点「开始打印」再逐张输出"
        )
        self.only_invoice_check = QCheckBox("只打印发票")
        self.only_invoice_check.setToolTip(
            "勾选后：内容不像发票的 PDF（合同、报价单等）只入队、不出纸，\n"
            "避免误打印；扫描件没有文字层，仍按发票正常打印。\n"
            "拖到图标或右键菜单启动时同样生效。"
        )
        self.pad_combo = QComboBox()
        self.pad_combo.addItem("重复本发票（联次）", PAD_REPEAT)
        self.pad_combo.addItem("留空", PAD_BLANK)
        self.pad_combo.setToolTip(
            "只有「整张发票的页数装得下一张纸」时才涉及空位：\n"
            "重复本发票 = 用这张发票自己的页面补满（单页发票 1×2 即上下两份）；\n"
            "留空 = 剩余格子留白。两种都不会混入其他 PDF 的页面。"
        )
        pad_row = QHBoxLayout()
        pad_row.setSpacing(8)
        pad_label = QLabel("空位处理")
        pad_label.setObjectName("Field")
        pad_row.addWidget(pad_label)
        pad_row.addWidget(self.pad_combo, 1)
        lay.addWidget(self.auto_print_check)
        lay.addWidget(self.only_invoice_check)
        lay.addLayout(pad_row)

        line2 = QFrame()
        line2.setFixedHeight(1)
        line2.setStyleSheet(f"background:{style.BORDER};")
        lay.addWidget(line2)

        self.shell_check = QCheckBox("右键菜单打印")
        self.shell_check.setToolTip(
            "勾选后：在资源管理器里选中 PDF（可多选）→ 右键 →「使用发票打印助手打印」，\n"
            "直接送进本程序排队打印，也可以当 PDF 的「打开方式」用。\n"
            "只写当前用户注册表（HKCU），不需要管理员权限；取消勾选即彻底移除。"
        )
        lay.addWidget(self.shell_check)

        self.shell_hint = QLabel("")
        self.shell_hint.setObjectName("Hint")
        self.shell_hint.setWordWrap(True)
        lay.addWidget(self.shell_hint)

        lay.addStretch(1)

        links = QHBoxLayout()
        links.setSpacing(14)
        self.log_btn = QPushButton("打开运行日志")
        self.log_btn.setObjectName("Link")
        links.addWidget(self.log_btn)

        # 历史记录到底存哪儿了 —— 出问题时用户能一眼看到，不用猜
        self.data_btn = QPushButton("数据文件夹")
        self.data_btn.setObjectName("Link")
        self.data_btn.setToolTip("配置、打印历史、运行日志都在这里")
        links.addWidget(self.data_btn)
        links.addStretch(1)
        lay.addLayout(links)

        # 面板右上角的收起按钮（卡片头部右侧会再放一个箭头）
        return card

    # ---------------------------------------------------------- 可折叠的设置面板
    def _build_settings_panel_wrap(self) -> QWidget:
        """把设置卡片包一层，靠改 wrapper 的 maximumWidth 做折叠动画。"""
        wrap = QWidget()
        wrap.setObjectName("PanelWrap")
        wrap.setMinimumWidth(0)
        wrap.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Expanding)

        lay = QVBoxLayout(wrap)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        lay.setSizeConstraint(QVBoxLayout.SetNoConstraint)

        self.settings_card = self._build_settings()
        self.settings_card.setFixedWidth(292)
        lay.addWidget(self.settings_card)

        self.PANEL_WIDTH = 292
        wrap.setMaximumWidth(self.PANEL_WIDTH)
        self.settings_wrap = wrap
        return wrap

    def _build_collapse_bar(self) -> QWidget:
        """细长的竖条把手：收起时它留在原处，点一下就能把面板拉回来。"""
        bar = QWidget()
        bar.setObjectName("CollapseBar")
        bar.setFixedWidth(20)
        lay = QVBoxLayout(bar)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        lay.addStretch(1)

        self.collapse_btn = QToolButton()
        self.collapse_btn.setObjectName("Collapse")
        self.collapse_btn.setText("‹")
        self.collapse_btn.setCursor(Qt.PointingHandCursor)
        self.collapse_btn.setFixedSize(20, 96)
        self.collapse_btn.setToolTip("收起打印设置（Ctrl+B）")
        self.collapse_btn.clicked.connect(lambda: self.toggle_settings_panel())
        lay.addWidget(self.collapse_btn, 0, Qt.AlignHCenter)
        lay.addStretch(1)
        return bar

    def toggle_settings_panel(self, collapsed: Optional[bool] = None) -> None:
        """收起 / 展开左侧设置面板（带动画）。"""
        target = (not self.settings.panel_collapsed) if collapsed is None else bool(collapsed)
        self.settings.panel_collapsed = target
        self.settings.save()

        if not target:
            self.settings_card.show()   # 先显示，再让宽度长出来
        self._animate_panel(0 if target else self.PANEL_WIDTH, animate=True)

    def _apply_panel_state(self, animate: bool = False) -> None:
        """按当前设置恢复面板状态（启动时用，不播动画）。"""
        collapsed = self.settings.panel_collapsed
        if collapsed:
            self.settings_card.hide()
        else:
            self.settings_card.show()
        self.settings_wrap.setMaximumWidth(0 if collapsed else self.PANEL_WIDTH)
        self.collapse_btn.setText("›" if collapsed else "‹")
        self.collapse_btn.setToolTip(
            "展开打印设置（Ctrl+B）" if collapsed else "收起打印设置（Ctrl+B）"
        )

    def _animate_panel(self, target_width: int, animate: bool = True) -> None:
        start = self.settings_wrap.maximumWidth()
        if start == target_width:
            self._on_panel_anim_done()
            return
        if self._panel_anim is None:
            self._panel_anim = QPropertyAnimation(self.settings_wrap, b"maximumWidth", self)
            self._panel_anim.setDuration(190)
            self._panel_anim.setEasingCurve(QEasingCurve.OutCubic)
            self._panel_anim.finished.connect(self._on_panel_anim_done)
        self._panel_anim.stop()
        self._panel_anim.setStartValue(start)
        self._panel_anim.setEndValue(target_width)
        if animate:
            self._panel_anim.start()
        else:
            self.settings_wrap.setMaximumWidth(target_width)
            self._on_panel_anim_done()
        collapsed = target_width == 0
        self.collapse_btn.setText("›" if collapsed else "‹")
        self.collapse_btn.setToolTip(
            "展开打印设置（Ctrl+B）" if collapsed else "收起打印设置（Ctrl+B）"
        )

    def _on_panel_anim_done(self) -> None:
        collapsed = self.settings_wrap.maximumWidth() == 0
        self.settings_card.setVisible(not collapsed)

    # ---------------------------------------------------------- 右侧主体
    def _build_main_area(self) -> QVBoxLayout:
        area = QVBoxLayout()
        area.setSpacing(12)

        top = QHBoxLayout()
        top.setSpacing(12)
        self.drop_zone = DropZone()
        self.preview_card, self.preview = self._wrap_preview()
        top.addWidget(self.drop_zone, 5)
        top.addWidget(self.preview_card, 4)
        area.addLayout(top, 3)

        area.addWidget(self._build_tasks(), 2)
        return area

    def _wrap_preview(self) -> Tuple[QWidget, SheetPreview]:
        card = QFrame()
        card.setObjectName("Card")
        lay = QVBoxLayout(card)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)

        head = QHBoxLayout()
        head.setContentsMargins(16, 12, 14, 0)
        self.preview_title = QLabel("拼版预览")
        self.preview_title.setObjectName("SectionTitle")
        head.addWidget(self.preview_title)
        head.addStretch(1)
        self.preview_hint = QLabel("点击图片可放大")
        self.preview_hint.setObjectName("Muted")
        head.addWidget(self.preview_hint)
        lay.addLayout(head)

        preview = SheetPreview()
        lay.addWidget(preview, 1)
        return card, preview

    def _build_tasks(self) -> QWidget:
        card = QFrame()
        card.setObjectName("Card")
        card.setMinimumHeight(210)
        lay = QVBoxLayout(card)
        lay.setContentsMargins(16, 12, 16, 12)
        lay.setSpacing(8)

        head = QHBoxLayout()
        head.setSpacing(8)
        title = QLabel("发票列表")
        title.setObjectName("SectionTitle")
        self.queue_hint = QLabel("")
        self.queue_hint.setObjectName("Muted")
        head.addWidget(title)
        head.addWidget(self.queue_hint)
        head.addStretch(1)

        self.paste_btn = QPushButton("粘贴文件")
        self.paste_btn.setObjectName("Ghost")
        self.paste_btn.setToolTip(
            "从微信或资源管理器「复制文件」后点这里（或按 Ctrl+V），不必先另存到桌面"
        )
        head.addWidget(self.paste_btn)

        self.print_btn = QPushButton("开始打印")
        self.print_btn.setObjectName("Primary")
        self.print_btn.setEnabled(False)
        self.clear_btn = QPushButton("清空")
        self.clear_btn.setObjectName("Ghost")
        head.addWidget(self.print_btn)
        head.addWidget(self.clear_btn)
        lay.addLayout(head)

        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(["发票文件", "页数", "张数", "状态", "时间"])
        self.table.verticalHeader().setVisible(False)
        self.table.setShowGrid(False)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setFocusPolicy(Qt.NoFocus)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.Stretch)
        for col in (1, 2, 3, 4):
            header.setSectionResizeMode(col, QHeaderView.Fixed)
        self.table.setColumnWidth(1, 58)
        self.table.setColumnWidth(2, 58)
        self.table.setColumnWidth(3, 96)
        self.table.setColumnWidth(4, 92)
        header.setHighlightSections(False)
        header.setDefaultAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        for col in (1, 2, 3, 4):
            item = self.table.horizontalHeaderItem(col)
            if item is not None:
                item.setTextAlignment(Qt.AlignCenter)
        self.table.verticalHeader().setDefaultSectionSize(31)
        lay.addWidget(self.table, 1)
        return card

    # ---------------------------------------------------------- 历史打印记录
    def _build_history(self) -> QWidget:
        card = QFrame()
        card.setObjectName("Card")
        card.setFixedHeight(208)
        lay = QVBoxLayout(card)
        lay.setContentsMargins(16, 10, 16, 10)
        lay.setSpacing(6)

        head = QHBoxLayout()
        head.setSpacing(8)
        title = QLabel("历史打印记录")
        title.setObjectName("SectionTitle")
        self.history_hint = QLabel("")
        self.history_hint.setObjectName("Muted")
        head.addWidget(title)
        head.addWidget(self.history_hint)
        head.addStretch(1)

        self.export_btn = QPushButton("导出 CSV")
        self.export_btn.setObjectName("Ghost")
        self.export_btn.setEnabled(False)
        self.history_del_btn = QPushButton("删除选中")
        self.history_del_btn.setObjectName("Ghost")
        self.history_del_btn.setEnabled(False)
        self.history_del_btn.setToolTip("选中一行或多行后删除（也可以按 Delete 键）")
        self.history_clear_btn = QPushButton("清空记录")
        self.history_clear_btn.setObjectName("Ghost")
        self.history_clear_btn.setEnabled(False)
        head.addWidget(self.export_btn)
        head.addWidget(self.history_del_btn)
        head.addWidget(self.history_clear_btn)
        lay.addLayout(head)

        self.history_table = QTableWidget(0, len(COLUMNS))
        self.history_table.setHorizontalHeaderLabels(list(COLUMNS))
        self.history_table.verticalHeader().setVisible(False)
        self.history_table.setShowGrid(False)
        self.history_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.history_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.history_table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.history_table.setFocusPolicy(Qt.ClickFocus)   # 点了能获得焦点，Delete 才生效
        self.history_table.setVerticalScrollMode(QAbstractItemView.ScrollPerPixel)
        self.history_table.setHorizontalScrollMode(QAbstractItemView.ScrollPerPixel)
        self.history_table.setWordWrap(False)
        self.history_table.verticalHeader().setDefaultSectionSize(27)

        header = self.history_table.horizontalHeader()
        for col in range(len(COLUMNS)):
            header.setSectionResizeMode(col, QHeaderView.Fixed)
        header.setStretchLastSection(True)   # 多出来的宽度给「结果」列
        for col, width in ((0, 110), (1, 220), (2, 150), (3, 70),
                           (4, 120), (5, 178), (6, 90)):
            self.history_table.setColumnWidth(col, width)
        header.setHighlightSections(False)
        header.setDefaultAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        for col in range(len(COLUMNS)):
            item = self.history_table.horizontalHeaderItem(col)
            if item is not None:
                item.setTextAlignment(_cell_align(col))

        self.history_table.setToolTip(
            "历史记录只保留票面表面信息，不保存文件与文件名；\n"
            "选中一行或多行后点「删除选中」（或按 Delete）可以删掉"
        )
        lay.addWidget(self.history_table, 1)
        return card

    # ---------------------------------------------------------- 历史记录：读写
    def refresh_history(self) -> None:
        """按 self.history.records 重建表格（最新的在最上面）。"""
        rows = self.history.records
        self.history_table.setUpdatesEnabled(False)
        self.history_table.setRowCount(0)
        for rec in rows:
            row = self.history_table.rowCount()
            self.history_table.insertRow(row)
            for col, text in enumerate(rec.cells()):
                item = QTableWidgetItem(text)
                item.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable)
                item.setTextAlignment(_cell_align(col))
                item.setToolTip(rec.tooltip())
                if col == STATUS_COLUMN:
                    item.setForeground(QColor(style.OK if rec.status == "已打印" else style.ERROR))
                self.history_table.setItem(row, col, item)
        self.history_table.setUpdatesEnabled(True)

        total = len(rows)
        if self.history.last_error:
            self.history_hint.setText(f"共 {total} 条 · {self.history.last_error}")
            self.history_hint.setStyleSheet(f"color:{style.ERROR};")
        else:
            self.history_hint.setText(
                f"共 {total} 条 · 仅记录票面信息（不保存文件）" if total else "还没有记录"
            )
            self.history_hint.setStyleSheet("")
        self.history_hint.setToolTip(f"记录文件：{self.history.path}\n（只记录票面信息，不保存文件）")
        self.export_btn.setEnabled(bool(total))
        self.history_clear_btn.setEnabled(bool(total))
        self.history_table.clearSelection()      # 重建后行号已变，别留着旧选择
        self._sync_history_buttons()

    def _record_history(self, inv: Invoice, ok: bool) -> None:
        """一张发票打完（或失败）后，把提取到的字段追加成一条历史记录。"""
        fields = inv.fields
        row = fields.as_row() if fields is not None else {}
        rec = PrintRecord(
            when=datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            invoice_no=row.get("invoice_no", "—"),
            material=row.get("material", "—"),
            spec=row.get("spec", "—"),
            qty=row.get("qty", "—"),
            tax_rate=row.get("tax_rate", "—"),
            unit_price=row.get("unit_price", "—"),
            status="已打印" if ok else "打印失败",
            note=(getattr(fields, "note", "") if fields is not None else "未提取字段"),
            formula=(getattr(fields, "formula_text", "") if fields is not None else ""),
        )
        saved = self.history.add(rec)
        self.refresh_history()
        write_log(
            f"历史记录 +1（发票号 {rec.invoice_no}，{rec.status}，共 {len(self.history.records)} 条）"
            + ("" if saved else f" —— 但写入失败：{self.history.last_error}")
        )
        if not saved:
            # 写不进去必须让用户当场看到，否则就成了「打了却没有记录」
            self._toast(
                f"{self.history.last_error}（记录暂存在内存，关掉程序会丢）", "error"
            )

    def clear_history(self) -> None:
        if not self.history.records:
            return
        answer = QMessageBox.question(self, "清空历史记录", "确定清空全部历史打印记录吗？")
        if answer != QMessageBox.Yes:
            return
        if self.history.clear():
            self.refresh_history()
            self._toast("历史记录已清空", "info")
        else:
            write_log(f"清空历史失败：{self.history.last_error}")
            self.refresh_history()
            self._toast(f"清空历史失败：{self.history.last_error}", "error")

    def _history_selected_rows(self) -> List[int]:
        """历史表格里选中的行号（可按住 Ctrl / Shift 多选）。"""
        model = self.history_table.selectionModel()
        if model is None:
            return []
        return sorted({index.row() for index in model.selectedRows()})

    def _sync_history_buttons(self) -> None:
        self.history_del_btn.setEnabled(bool(self._history_selected_rows()))

    def _confirm_delete(self, count: int, preview: str) -> bool:
        """删除前的二次确认（单独抽出来，自测里可以直接替换掉）。"""
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Warning)
        box.setWindowTitle("删除历史记录")
        box.setText(f"确定删除选中的 {count} 条记录吗？")
        box.setInformativeText(
            f"发票号：{preview}\n只删本地记录，已经打印出来的纸质发票不受影响。"
        )
        yes_btn = box.addButton("删除", QMessageBox.DestructiveRole)
        cancel_btn = box.addButton("取消", QMessageBox.RejectRole)
        box.setDefaultButton(cancel_btn)
        box.exec()
        return box.clickedButton() is yes_btn

    def _delete_selected_history(self) -> None:
        """删除选中的历史记录（先确认，删完立刻落盘）。"""
        rows = self._history_selected_rows()
        if not rows:
            self._toast("先在历史记录里选中要删的行（Ctrl / Shift 可多选）", "info")
            return
        numbers = [self.history.records[i].invoice_no or "—"
                   for i in rows if i < len(self.history.records)]
        preview = "、".join(numbers[:6]) + ("…" if len(numbers) > 6 else "")
        if not self._confirm_delete(len(rows), preview):
            return
        removed = self.history.delete(rows)
        self.refresh_history()
        write_log(f"删除历史记录 {removed} 条（发票号 {preview}）")
        if self.history.last_error:
            self._toast(self.history.last_error, "error")
        else:
            self._toast(f"已删除 {removed} 条历史记录", "ok")

    def export_history(self) -> None:
        if not self.history.records:
            return
        default = desktop_dir() / "发票打印记录.csv"
        path, _ = QFileDialog.getSaveFileName(self, "导出历史记录", str(default), "CSV 文件 (*.csv)")
        if not path:
            return
        if self.history.export_csv(Path(path)):
            self._toast(f"已导出到 {path}", "ok")
        else:
            self._toast("导出失败，请换个位置再试", "error")

    # ---------------------------------------------------------- 事件绑定
    def _bind_events(self) -> None:
        self.drop_zone.filesDropped.connect(self.on_paths_dropped)
        self.drop_zone.pickRequested.connect(self.pick_files)
        self.paste_btn.clicked.connect(self.paste_from_clipboard)
        self.preview.clicked.connect(self.open_preview_dialog)
        self.refresh_btn.clicked.connect(self.refresh_printers)
        self.printer_combo.currentIndexChanged.connect(self._on_printer_changed)
        self.log_btn.clicked.connect(self._open_log)
        self.data_btn.clicked.connect(self._open_data_dir)
        self.data_btn.setToolTip(f"配置、打印历史、运行日志都在这个文件夹：\n{self.history.path.parent}")

        for widget in (self.margin_spin, self.scale_spin, self.copies_spin):
            widget.valueChanged.connect(self._on_settings_changed)
        for combo in (self.layout_combo, self.paper_combo, self.orient_combo, self.dpi_combo):
            combo.currentIndexChanged.connect(self._on_settings_changed)
        self.engine_combo.currentIndexChanged.connect(self._on_engine_changed)
        self.auto_print_check.toggled.connect(self._on_settings_changed)
        self.gray_check.toggled.connect(self._on_settings_changed)
        self.only_invoice_check.toggled.connect(self._on_only_invoice_toggled)
        self.pad_combo.currentIndexChanged.connect(self._on_settings_changed)
        self.shell_check.toggled.connect(self._on_shell_toggled)

        self.print_btn.clicked.connect(self.print_pending)
        self.clear_btn.clicked.connect(self.clear_queue)
        self.table.itemSelectionChanged.connect(self._on_row_selected)

        self.worker.batch_started.connect(self._on_batch_started)
        self.worker.batch_finished.connect(self._on_batch_finished)
        self.worker.message.connect(self._on_worker_message)

        self.history_clear_btn.clicked.connect(self.clear_history)
        self.export_btn.clicked.connect(self.export_history)
        self.history_del_btn.clicked.connect(self._delete_selected_history)
        self.history_table.itemSelectionChanged.connect(self._sync_history_buttons)

        # Delete 键删除选中记录（焦点在历史表格里时生效）
        del_key = QShortcut(QKeySequence.Delete, self.history_table)
        del_key.setContext(Qt.WidgetWithChildrenShortcut)
        del_key.activated.connect(self._delete_selected_history)
        self._history_del_shortcut = del_key

        shortcut = QShortcut(QKeySequence("Ctrl+B"), self)
        shortcut.activated.connect(lambda: self.toggle_settings_panel())

        paste = QShortcut(QKeySequence("Ctrl+V"), self)
        paste.activated.connect(self.paste_from_clipboard)

    # ------------------------------------------------------------------ 设置
    def _sync_widgets_from_settings(self) -> None:
        s = self.settings
        spec = s.spec
        widgets = [self.layout_combo, self.paper_combo, self.orient_combo, self.dpi_combo,
                   self.engine_combo, self.margin_spin, self.scale_spin, self.copies_spin]
        for w in widgets:
            w.blockSignals(True)

        idx = self.layout_combo.findData(f"{spec.cols}x{spec.rows}")
        self.layout_combo.setCurrentIndex(idx if idx >= 0 else 0)
        m = self.paper_combo.findData(s.paper)
        self.paper_combo.setCurrentIndex(m if m >= 0 else 0)
        self.orient_combo.setCurrentIndex(1 if s.landscape else 0)
        d = self.dpi_combo.findData(s.render_dpi)
        self.dpi_combo.setCurrentIndex(d if d >= 0 else 1)
        e = self.engine_combo.findData(s.print_engine)
        self.engine_combo.setCurrentIndex(e if e >= 0 else 0)
        self.gray_check.blockSignals(True)
        self.gray_check.setChecked(s.gray)
        self.gray_check.blockSignals(False)
        self.margin_spin.setValue(spec.margin_cm)
        self.scale_spin.setValue(spec.scale_pct)
        self.copies_spin.setValue(s.copies)

        self.auto_print_check.blockSignals(True)
        self.only_invoice_check.blockSignals(True)
        self.pad_combo.blockSignals(True)
        self.auto_print_check.setChecked(s.auto_print)
        self.only_invoice_check.setChecked(s.only_invoice)
        p = self.pad_combo.findData(s.pad_mode)
        self.pad_combo.setCurrentIndex(p if p >= 0 else 0)
        self.auto_print_check.blockSignals(False)
        self.only_invoice_check.blockSignals(False)
        self.pad_combo.blockSignals(False)

        for w in widgets:
            w.blockSignals(False)
        self._update_engine_hint()

    # ---------------------------------------------------- 资源管理器右键菜单
    def _sync_shell_checkbox(self) -> None:
        """复选框以注册表实际内容为准（用户可能手动清理过）。"""
        self.shell_check.blockSignals(True)
        self.shell_check.setChecked(shellmenu.has_menu())
        self.shell_check.blockSignals(False)
        self._update_shell_hint()

    def _update_shell_hint(self) -> None:
        if not shellmenu.has_menu():
            text = "未加入：勾选后右键 PDF 即可直接打印"
        elif shellmenu.is_registered():
            text = "已加入：右键 PDF → 使用发票打印助手打印"
        else:
            text = "已加入，但程序换了位置，下次启动自动更新"
        self.shell_hint.setText(text)

    def _on_shell_toggled(self, checked: bool) -> None:
        if not shellmenu.set_registered(bool(checked)):
            self.shell_check.blockSignals(True)
            self.shell_check.setChecked(not checked)
            self.shell_check.blockSignals(False)
            self._toast("写入注册表失败，右键菜单未改变（可能被安全软件拦截）", "error")
            return
        self.settings.shell_menu = bool(checked)
        self.settings.save()
        if checked:
            self._toast("已加入右键菜单：选中 PDF 右键 →「使用发票打印助手打印」", "ok")
        else:
            self._toast("已从右键菜单和「打开方式」里移除", "info")
        self._update_shell_hint()

    def _layout_value(self) -> Tuple[int, int]:
        data = str(self.layout_combo.currentData() or "1x2")
        try:
            cols, rows = data.split("x")
            return max(1, int(cols)), max(1, int(rows))
        except Exception:
            return 1, 2

    def _on_only_invoice_toggled(self, checked: bool) -> None:
        """切换「只打印发票」：列表里已入队的非发票要跟着改状态，不用重新拖。"""
        self.settings.only_invoice = bool(checked)
        self.settings.save()
        changed = 0
        for uid in self._order:
            inv = self.invoices.get(uid)
            if inv is None or inv.fields is None or inv.fields.is_invoice:
                continue
            if checked and inv.status == ST_PENDING:
                inv.status = ST_NOT_INVOICE
                inv.error = "内容不像发票，已跳过"
                changed += 1
                self._refresh_row(inv)
            elif not checked and inv.status == ST_NOT_INVOICE:
                inv.status = ST_PENDING
                inv.error = ""
                changed += 1
                self._refresh_row(inv)
        self._update_print_button()
        if changed:
            self._toast(
                "已按新设置更新列表里的非发票"
                + ("（现在可以打印）" if not checked else "（现在会跳过）"),
                "info",
            )

    def _on_settings_changed(self) -> None:
        cols, rows = self._layout_value()
        spec = LayoutSpec(
            cols=cols,
            rows=rows,
            margin_cm=self.margin_spin.value(),
            gap_cm=self.settings.spec.gap_cm,
            scale_pct=self.scale_spin.value(),
            order=self.settings.spec.order,
            auto_rotate=self.settings.spec.auto_rotate,
        )
        self.settings.spec = spec
        self.settings.paper = self.paper_combo.currentData()
        self.settings.landscape = bool(self.orient_combo.currentData())
        self.settings.copies = self.copies_spin.value()
        self.settings.render_dpi = self.dpi_combo.currentData()
        self.settings.print_engine = self.engine_combo.currentData() or printers.ENGINE_AUTO
        self.settings.gray = self.gray_check.isChecked()
        self.settings.auto_print = self.auto_print_check.isChecked()
        self.settings.only_invoice = self.only_invoice_check.isChecked()
        self.settings.pad_mode = self.pad_combo.currentData() or PAD_REPEAT
        self.settings.save()
        self._refresh_sheet_counts()
        self._update_print_button()
        self._preview_timer.start()

    def _on_printer_changed(self) -> None:
        self.settings.printer = self.printer_combo.currentData() or ""
        self.settings.save()
        self._update_hint()

    def _update_hint(self) -> None:
        target = self.current_printer()
        suffix = "" if self.settings.printer else "（系统默认）"
        self.status_label.setText(f"当前输出：{target or '未找到打印机'}{suffix}")

    def _update_engine_hint(self) -> None:
        """把当前引擎的实际可用状态写在下拉框下面，别让用户猜为什么没变快。"""
        preference = self.engine_combo.currentData() or printers.ENGINE_AUTO
        self.engine_hint.setText(printers.describe(preference))
        tip = (
            "矢量：拼好的版面是 PDF，直接交给 SumatraPDF 静默打印，"
            "每张纸几 KB，红章与细线保真。\n"
            "位图：无需任何外部程序，兼容性最好，但每张纸要往打印队列送约 30MB。\n\n"
            "启用矢量：把 SumatraPDF.exe 放到程序目录即可（约 6MB，便携版无需安装）。"
        )
        self.engine_hint.setToolTip(tip)
        self.engine_combo.setToolTip(tip)

    def _on_engine_changed(self) -> None:
        self.settings.print_engine = self.engine_combo.currentData() or printers.ENGINE_AUTO
        self.settings.save()
        self._update_engine_hint()
        self.status_label.setToolTip(f"配置文件：{CONFIG_FILE}")

    # ------------------------------------------------------------------ 打印机
    def refresh_printers(self) -> None:
        names, default = list_printers()
        self._default_printer = default
        self.printer_combo.blockSignals(True)
        self.printer_combo.clear()
        self.printer_combo.addItem(f"系统默认 · {default}" if default else "系统默认打印机", "")
        for name in names:
            self.printer_combo.addItem(name, name)
        idx = self.printer_combo.findData(self.settings.printer)
        self.printer_combo.setCurrentIndex(idx if idx >= 0 else 0)
        self.printer_combo.blockSignals(False)
        self._update_hint()

    def current_printer(self) -> str:
        return self.printer_combo.currentData() or self._default_printer

    # ------------------------------------------------------------------ 预览
    def _preview_target(self) -> Optional[Invoice]:
        """当前要预览哪张发票：优先取用户选中的行，否则取第一张还没打完的。"""
        if self._preview_uid is not None:
            inv = self.invoices.get(self._preview_uid)
            if inv is not None and inv.refs:
                return inv
        for uid in self._order:
            inv = self.invoices.get(uid)
            if inv is not None and inv.refs and inv.status in (ST_PENDING, ST_PRINTING):
                return inv
        for uid in reversed(self._order):
            inv = self.invoices.get(uid)
            if inv is not None and inv.refs:
                return inv
        return None

    def refresh_preview(self) -> None:
        if self.headless:
            return      # 拖放即打印时主界面没显示，没必要渲染预览图（省 CPU）
        inv = self._preview_target()
        self.preview_hint.setVisible(inv is not None)
        if inv is None:
            self.preview_title.setText("拼版预览")
            self.preview.set_sheet(None, "没有待打印的发票")
            return

        self.preview_title.setText(f"拼版预览 · {inv.name}")
        try:
            img, hint, _ = self._render_sheet(inv, high_res=False)
            if img is None:
                self.preview.set_sheet(None, "没有可打印的页面")
                return
            self.preview.set_sheet(img, hint)
        except Exception as exc:  # 预览失败不影响打印
            write_log(f"预览失败 {inv.name}：{exc}")
            self.preview.set_sheet(None, "预览失败")

    def _render_sheet(self, inv: Invoice, high_res: bool = False):
        """渲染这张发票的第 1 张纸；返回 (PIL 图像, 说明, 纸张总数)。"""
        spec = self.settings.spec
        sheets = plan_sheets(inv, spec, self.settings.pad_mode)
        if not sheets:
            return None, "", 0
        img = compose_sheet(
            sheets[0],
            spec,
            paper=self.settings.paper,
            landscape=self.settings.landscape,
            dpi=220 if high_res else 110,
            render_dpi=300 if high_res else 160,
            max_width=2000 if high_res else 520,
        )
        hint = f"第 1 / {len(sheets)} 张纸 · {inv.page_count} 页 · 每张 {spec.cols}×{spec.rows}"
        return img, hint, len(sheets)

    def open_preview_dialog(self) -> None:
        """点击预览图 → 打开可缩放的大图窗口。"""
        inv = self._preview_target()
        if inv is None:
            self._toast("还没有可以预览的发票", "info")
            return

        pixmap = self.preview.pixmap()
        hint = ""
        try:  # 大图按更高分辨率重新渲染，放大后小字也清楚
            img, hint, _ = self._render_sheet(inv, high_res=True)
            if img is not None:
                pixmap = pil_to_pixmap(img)
        except Exception as exc:
            write_log(f"高清预览失败 {inv.name}：{exc}")
            hint = "高清渲染失败，已用缩略图放大"

        if pixmap is None or pixmap.isNull():
            self._toast("预览还没准备好，请稍后再试", "warn")
            return
        PreviewDialog(
            pixmap, f"拼版预览 · {inv.name}", hint, self, pixel_scale=96.0 / 220.0
        ).exec()

    def _on_row_selected(self) -> None:
        rows = self.table.selectionModel().selectedRows() if self.table.selectionModel() else []
        if not rows:
            return
        row = rows[0].row()
        for uid, r in self._rows.items():
            if r == row:
                self._preview_uid = uid
                self._preview_timer.start()
                break

    # ------------------------------------------------------------------ 收文件
    def pick_files(self) -> None:
        files, _ = QFileDialog.getOpenFileNames(
            self, "选择发票 PDF（可多选）", "", "PDF 文件 (*.pdf)"
        )
        if files:
            self.on_paths_dropped(files)

    def paste_from_clipboard(self) -> None:
        """把微信/资源管理器里「复制」的文件直接粘进来，省掉"先存桌面再拖"这一步。"""
        paths = clipboard_paths()
        targets = [
            p for p in paths
            if Path(p).suffix.lower() == ".pdf" or Path(p).is_dir()
        ]
        if not targets:
            self._toast("剪贴板里没有 PDF：在微信里右键文件 → 复制，再回到这里按 Ctrl+V", "warn")
            return
        write_log(f"从剪贴板导入 {len(targets)} 个文件")
        self._toast(f"已从剪贴板读取 {len(targets)} 个文件", "ok")
        self.on_paths_dropped(targets)

    def _duplicate_hits(self, invoices: List[Invoice]) -> List[Tuple[Invoice, str]]:
        """找出与历史、当前待打/在打印任务或本批次前一张同号的发票。"""
        index = self.history.printed_index()
        incoming_uids = {inv.uid for inv in invoices}
        active: Dict[str, str] = {}
        for old in self.invoices.values():
            if old.uid in incoming_uids or old.status not in (ST_PENDING, ST_PRINTING):
                continue
            number = (getattr(old.fields, "invoice_no", "") or "").strip() if old.fields else ""
            if number and number != "—":
                active[number] = old.status
        hits: List[Tuple[Invoice, str]] = []
        seen: Dict[str, Invoice] = {}
        for inv in invoices:
            fields = inv.fields
            number = (getattr(fields, "invoice_no", "") or "").strip() if fields else ""
            if not number or number == "—":
                continue
            if number in seen:
                hits.append((inv, "与本批次里的另一个文件相同"))
                continue
            seen[number] = inv
            if number in active:
                hits.append((inv, f"当前列表已有同号发票（{active[number]}）"))
                continue
            record = index.get(number)
            if record is not None:
                hits.append((inv, f"已于 {record.when} 打印过"))
        return hits

    def _ask_duplicates(self, hits: List[Tuple[Invoice, str]]) -> bool:
        """弹窗确认：True = 仍然打印，False = 跳过这些重复发票。"""
        lines = []
        for inv, reason in hits:
            number = getattr(inv.fields, "invoice_no", "") if inv.fields else ""
            lines.append(f"· 发票号 {number or '未知'}（{reason}）")
        box = QMessageBox(self.dialog_parent)
        box.setIcon(QMessageBox.Warning)
        box.setWindowTitle("发票可能已打印过")
        box.setText(f"有 {len(hits)} 个文件的发票号（后 8 位）与已有记录重复：")
        box.setInformativeText("\n".join(lines) + "\n\n确认不是重复报销的话，可以仍然打印。")
        keep_btn = box.addButton("仍然打印", QMessageBox.AcceptRole)
        skip_btn = box.addButton("跳过这些", QMessageBox.RejectRole)
        box.setDefaultButton(skip_btn)
        box.exec()
        return box.clickedButton() is keep_btn

    def on_paths_dropped(self, paths: List[str]) -> None:
        """一次拖入 N 个 PDF = 收下 N 张发票，逐个解析、逐个排队。"""
        targets: List[Path] = []
        seen: set = set()
        from_subdir = 0

        def take(path: Path, top_level: bool) -> None:
            nonlocal from_subdir
            try:
                key = str(path.resolve()).lower()
            except OSError:
                key = str(path).lower()
            if key in seen:
                return
            seen.add(key)
            if not top_level:
                from_subdir += 1
            targets.append(path)

        for p in paths:
            path = Path(p)
            if path.is_dir():
                # 递归子文件夹：整批票据常常分好类放在子目录里
                for child in sorted(path.rglob("*")):
                    if child.is_file() and child.suffix.lower() == ".pdf":
                        take(child, child.parent == path)
            elif path.suffix.lower() == ".pdf":
                take(path, True)
        if not targets:
            self._toast("没有找到 PDF 文件", "warn")
            return
        if from_subdir:
            write_log(f"拖入的文件夹里含子目录，共找到 {len(targets)} 个 PDF（{from_subdir} 个在子文件夹）")

        invoices: List[Invoice] = []
        failed = 0
        not_invoice = 0
        for path in targets:
            inv = self._register(path)
            if inv is None:
                failed += 1
                continue
            if inv.status == ST_NOT_INVOICE:
                not_invoice += 1
                continue
            invoices.append(inv)

        if not invoices:
            if not_invoice:
                self._toast(
                    f"{not_invoice} 个文件不是发票，已跳过（设置里可取消「只打印发票」）",
                    "warn",
                )
            else:
                self._toast(f"没有可打印的发票（{failed} 个文件读取失败）", "error")
            self._preview_timer.start()
            self._update_print_button()
            return

        # 发票号重复提醒：可能在重复报销，先问一句再打印
        skipped = 0
        hits = self._duplicate_hits(invoices) if self.confirm_duplicates else []
        if hits:
            numbers = "、".join(
                (getattr(inv.fields, "invoice_no", "") if inv.fields else "") or "?"
                for inv, _ in hits
            )
            write_log(f"发票号重复，等待确认：{numbers}")
            if not self._ask_duplicates(hits):
                skip_uids = {inv.uid for inv, _ in hits}
                for inv, _ in hits:
                    inv.status = ST_SKIPPED
                    inv.error = "发票号与已有记录重复，已跳过"
                    self._refresh_row(inv)
                skipped = len(skip_uids)
                invoices = [i for i in invoices if i.uid not in skip_uids]
                write_log(f"用户选择跳过 {skipped} 张重复发票")

        if not invoices:
            self._toast(f"{skipped} 张发票因重复已跳过", "info")
            self._preview_timer.start()
            self._update_print_button()
            return

        pages = sum(inv.page_count for inv in invoices)
        sheets = sum(inv.total_sheets for inv in invoices)
        summary = f"收到 {len(invoices)} 张发票（{pages} 页 / {sheets} 张纸）"
        if from_subdir:
            summary += f"，含子文件夹 {from_subdir} 个"
        if skipped:
            summary += f"，跳过 {skipped} 张重复"
        if failed:
            summary += f"，{failed} 个文件读取失败"
        if not_invoice:
            summary += f"，跳过非发票 {not_invoice} 个"

        if self.settings.auto_print:
            submitted = 0
            for inv in invoices:  # 逐张发票独立提交，互不影响
                if self._submit_invoice(inv):
                    submitted += 1
            if submitted:
                self._toast(summary + f"，已提交 {submitted} 条打印作业", "ok")
        else:
            self._toast(summary + "，点「开始打印」逐张输出", "ok")

        if self._preview_uid is None and invoices:
            self._preview_uid = invoices[0].uid
        self._preview_timer.start()
        self._update_print_button()

    def _copies(self) -> int:
        """份数：一张发票要打几份（≥1）。"""
        return max(1, int(self.settings.copies or 1))

    def _register(self, path: Path) -> Optional[Invoice]:
        """解析单个 PDF 并登记成一张发票；失败只影响这一行。"""
        self._uid_seq += 1
        inv = read_invoice(self._uid_seq, path)
        inv.total_sheets = sheet_count(inv, self.settings.spec) * self._copies()
        inv.time = datetime.datetime.now().strftime("%H:%M:%S")
        self.invoices[inv.uid] = inv
        self._order.append(inv.uid)
        self._add_row(inv)

        if inv.status == ST_UNREADABLE:
            write_log(f"读取失败 {path.name}: {inv.error}")
            self._toast(f"{path.name}：{inv.error}", "error")
            return None

        # 历史记录用的字段（材料名称 / 规格型号 / 数量 / 税率 / 含税单价 / 发票号）
        try:
            inv.fields = extract_fields(str(path))
            f = inv.fields
            write_log(f"字段提取 {path.name}：{f.material_full or '—'}（项目名称 "
                      f"{f.material or '—'} / 规格 {f.spec or '—'}）/ "
                      f"数量 {f.qty_text} / 税率 {f.tax_rate_text} / "
                      f"含税单价 {f.unit_price_text} / 发票号 {f.invoice_no or '—'}"
                      + (f"（{f.formula_text}）" if f.formula_text else "")
                      + (f"（{f.note}）" if f.note else ""))
        except Exception as exc:  # 提取失败不影响打印
            inv.fields = None
            write_log(f"字段提取失败 {path.name}: {exc}")

        # 只打印发票：合同、报价单这类没有发票专用字样的 PDF 只入队，不出纸。
        # 判定放在提取之后 —— 要看正文才能知道是不是发票。
        if (self.settings.only_invoice and inv.fields is not None
                and not inv.fields.is_invoice):
            inv.status = ST_NOT_INVOICE
            inv.error = "内容不像发票，已跳过"
            write_log(f"跳过非发票 {path.name}：未找到发票号码/价税合计等发票字样")
            self._refresh_row(inv)

        write_log(f"解析完成 {path.name}：{inv.page_count} 页 → {inv.total_sheets} 张纸")
        return inv

    # ------------------------------------------------------------------ 队列
    def _submit_invoice(self, inv: Invoice) -> bool:
        """把**一张发票**提交成一条独立的打印作业。"""
        if not inv.refs:
            return False
        # 非发票、重复跳过、已打完的都不再提交 —— 自动补打时会把整批交给这里
        if inv.status not in (ST_PENDING, ST_FAILED):
            return False
        printer = self.current_printer()
        if not printer:
            self._toast("没有可用的打印机，请先在系统中安装打印机", "error")
            inv.status = ST_FAILED
            inv.error = "没有可用的打印机"
            self._refresh_row(inv)
            # 失败也要留一条记录：否则用户会以为「点了但什么都没发生」
            self._record_history(inv, False)
            return False

        copies = self._copies()
        sheets = plan_sheets(inv, self.settings.spec, self.settings.pad_mode)
        inv.total_sheets = len(sheets) * copies
        inv.printed_sheets = 0
        inv.status = ST_PRINTING
        self._refresh_row(inv)

        self._batch_seq += 1
        batch = PrintBatch(
            batch_id=self._batch_seq,
            invoice_uid=inv.uid,
            name=inv.path.stem,  # 作业名 = 文件名，打印机队列里可逐张核对
            printer=printer,
            sheets=sheets,
            spec=self.settings.spec,
            paper=self.settings.paper,
            landscape=self.settings.landscape,
            copies=self.settings.copies,
            render_dpi=self.settings.render_dpi,
            engine=self.settings.print_engine,
            gray=self.settings.gray,
            page_count=inv.page_count,
        )
        self.worker.submit(batch)
        write_log(f"提交打印 {inv.name}：{len(sheets)} 张纸 × {self.settings.copies} 份 → {printer}")
        return True

    def print_pending(self) -> None:
        """把列表里所有「待打印 / 上次失败」的发票逐张提交。"""
        todo = [
            self.invoices[uid] for uid in self._order
            if self.invoices.get(uid) and self.invoices[uid].refs
            and self.invoices[uid].status in (ST_PENDING, ST_FAILED)
        ]
        if not todo:
            self._toast("没有待打印的发票", "info")
            return
        submitted = [inv for inv in todo if self._submit_invoice(inv)]
        if submitted:
            sheets = sum(inv.total_sheets for inv in submitted)
            self._toast(f"已提交 {len(submitted)} 张发票（{sheets} 张纸），逐张输出中 …", "ok")
        self._update_print_button()

    def clear_queue(self) -> None:
        if self._still_printing():
            # 完成回调靠 invoices[uid] 找回发票并写历史；中途清掉就会出纸却漏记。
            self._toast("还有发票正在打印或排队，请打完后再清空列表", "warn")
            return
        self.invoices.clear()
        self._order.clear()
        self._rows.clear()
        self._preview_uid = None
        self.table.setRowCount(0)
        self._update_print_button()
        self.refresh_preview()

    # ------------------------------------------------------------------ 表格
    STATUS_COLORS = {
        ST_DONE: style.OK,
        ST_PRINTING: style.ACCENT,
        ST_FAILED: style.ERROR,
        ST_UNREADABLE: style.ERROR,
        ST_PENDING: style.MUTED,
        ST_SKIPPED: style.WARN,
        ST_NOT_INVOICE: style.WARN,
    }

    def _add_row(self, inv: Invoice) -> int:
        """一张发票占一行，行号记在 _rows 里，后续状态更新只动这一行。"""
        row = self.table.rowCount()
        self.table.insertRow(row)
        for col in range(5):
            item = QTableWidgetItem("")
            item.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable)
            self.table.setItem(row, col, item)
        self.table.item(row, 0).setText(inv.name)
        self.table.item(row, 0).setToolTip(str(inv.path))
        self._rows[inv.uid] = row
        self.table.scrollToBottom()
        self._refresh_row(inv)
        return row

    def _refresh_row(self, inv: Invoice) -> None:
        row = self._rows.get(inv.uid)
        if row is None or row >= self.table.rowCount():
            return
        pages = self.table.item(row, 1)
        sheets = self.table.item(row, 2)
        status = self.table.item(row, 3)
        stamp = self.table.item(row, 4)
        if None in (pages, sheets, status, stamp):
            return

        pages.setText(str(inv.page_count) if inv.page_count else "—")
        pages.setTextAlignment(Qt.AlignCenter)
        sheets.setText(str(inv.total_sheets) if inv.total_sheets else "—")
        sheets.setTextAlignment(Qt.AlignCenter)
        base = sheet_count(inv, self.settings.spec) if inv.refs else 0
        copies = self._copies()
        sheets.setToolTip(
            f"{base} 张版 × {copies} 份 = {inv.total_sheets} 张纸"
            if base and copies > 1 else ""
        )
        status.setText(inv.status)
        status.setTextAlignment(Qt.AlignCenter)
        status.setForeground(QColor(self.STATUS_COLORS.get(inv.status, style.TEXT)))
        status.setToolTip(inv.error or "")
        stamp.setText(inv.time or "—")
        stamp.setTextAlignment(Qt.AlignCenter)
        self._update_print_button()

    def _refresh_sheet_counts(self) -> None:
        """版式/纸张变了，重算每张发票的张数。"""
        for uid in self._order:
            inv = self.invoices.get(uid)
            if inv is None or not inv.refs:
                continue
            inv.total_sheets = sheet_count(inv, self.settings.spec) * self._copies()
            self._refresh_row(inv)

    def _update_print_button(self) -> None:
        pending = [
            self.invoices[uid] for uid in self._order
            if self.invoices.get(uid) and self.invoices[uid].refs
            and self.invoices[uid].status in (ST_PENDING, ST_FAILED)
        ]
        self.print_btn.setEnabled(bool(pending))
        self.print_btn.setText(f"开始打印（{len(pending)} 张）" if len(pending) > 1 else "开始打印")
        total = len(self.invoices)
        if total:
            done = sum(1 for i in self.invoices.values() if i.status == ST_DONE)
            self.queue_hint.setText(f"共 {total} 张发票 · 已完成 {done} 张")
        else:
            self.queue_hint.setText("")

    # ------------------------------------------------------------------ 打印回调
    def _on_batch_started(self, batch: PrintBatch) -> None:
        self._busy = True
        self._update_print_button()
        self.status_label.setText(f"正在打印：{batch.name}（{len(batch.sheets)} 张纸）")
        if self.mini is not None:
            self._headless_sync(name=batch.name)

    def _on_batch_finished(self, batch: PrintBatch, ok: bool, error: str) -> None:
        self._busy = False
        inv = self.invoices.get(batch.invoice_uid)
        if inv is not None:
            if ok:
                inv.status = ST_DONE
                # 出纸张数 = 拼版张数 × 份数（以工作线程回报的实际数量为准）
                inv.printed_sheets = (getattr(batch, "printed", 0)
                                      or len(batch.sheets) * max(1, batch.copies))
                inv.time = datetime.datetime.now().strftime("%H:%M:%S")
                inv.error = ""
                write_log(f"打印完成 {inv.name}：{inv.printed_sheets} 张纸"
                          f"（{len(batch.sheets)} 张版 × {max(1, batch.copies)} 份）")
            else:
                inv.status = ST_FAILED
                inv.error = error
                inv.time = datetime.datetime.now().strftime("%H:%M:%S")
                write_log(f"打印失败 {inv.name}: {error}")
            self._refresh_row(inv)
            self._record_history(inv, ok)   # 打完即入历史记录（含失败）
        self.status_label.setText("准备就绪" if ok else f"打印失败：{error}")
        self._update_print_button()
        self._preview_timer.start()
        if self.headless:
            self._headless_sync()
        # 关窗口时还有活没干完：等这一批打完再真正退出（见 closeEvent）
        if self._close_when_idle and not self._still_printing():
            self._close_when_idle = False
            QTimer.singleShot(60, self.close)

    def _on_worker_message(self, text: str, level: str) -> None:
        self._toast(text, level)

    def _toast(self, text: str, level: str = "info") -> None:
        colors = {"ok": style.OK, "warn": style.WARN, "error": style.ERROR, "info": style.MUTED}
        self.status_label.setStyleSheet(f"color:{colors.get(level, style.MUTED)};")
        self.status_label.setText(text)
        write_log(text)
        self._last_message = text
        if self.headless and self.mini is not None:
            self.mini.set_message(text, level)

    # ------------------------------------------------ 拖放即打印（不开主界面）
    def start_headless(self, paths: List[str]) -> None:
        """拖到 exe 图标 / 右键菜单启动：主界面不显示，只打印 + 右下角小进度卡。"""
        from .mini import MiniProgress

        self.headless = True
        self.mini = MiniProgress()
        self.dialog_parent = self.mini          # 重复提醒挂在小卡片上，别飘到屏幕中间
        self.mini.openMain.connect(self._enter_full_ui)
        self.mini.closeRequested.connect(self._quit_headless)
        self.mini.show_at_corner()
        write_log(f"拖放即打印：收到 {len(paths)} 个路径，主界面不显示")
        QTimer.singleShot(120, lambda: self._headless_ingest(paths))

    def _headless_ingest(self, paths: List[str]) -> None:
        self._mini_done = False
        before = set(self._order)
        self.on_paths_dropped(paths)
        # 拖放这个动作本身就意味着"现在就打"，不受「拖入后立即打印」影响。
        # 而且**只打本次新拖入的**：列表里上次失败或待打的旧发票不能被顺带重打。
        self.print_invoices(
            [self.invoices[uid] for uid in self._order if uid not in before]
        )
        self._headless_sync()

    def print_invoices(self, invoices: List[Invoice]) -> int:
        """提交指定的一批发票（拖放/右键自动补打），不波及列表里的其它记录。"""
        todo = [
            inv for inv in invoices
            if inv.refs and inv.status in (ST_PENDING, ST_FAILED)
        ]
        if not todo:
            return 0
        submitted = [inv for inv in todo if self._submit_invoice(inv)]
        if submitted:
            sheets = sum(inv.total_sheets for inv in submitted)
            self._toast(f"已提交 {len(submitted)} 张发票（{sheets} 张纸），逐张输出中 …", "ok")
        self._update_print_button()
        return len(submitted)

    def _has_pending(self) -> bool:
        return any(inv.status == ST_PENDING and inv.refs for inv in self.invoices.values())

    def _headless_sync(self, name: str = "") -> None:
        """把小卡片的进度/结果刷新到「当前批次」的真实状态。"""
        if self.mini is None:
            # 卡片已被关掉，但还有活没干完：干完就退出，别留一个没有窗口的进程
            if self._quit_pending and not self._still_printing():
                self._quit_headless()
            return
        invs = [self.invoices[uid] for uid in self._order if uid in self.invoices]
        total = len(invs)
        finished = [
            i for i in invs
            if i.status in (ST_DONE, ST_FAILED, ST_SKIPPED, ST_UNREADABLE, ST_NOT_INVOICE)
        ]
        sheets = sum(i.printed_sheets for i in invs)

        if total == 0:
            if not self._mini_done:
                self._mini_done = True
                self.mini.finish(
                    "没有可打印的发票",
                    self._last_message or "拖进来的不是 PDF，或文件夹里没有 PDF",
                    ok=False,
                )
            return

        if len(finished) < total:
            self.mini.set_progress(len(finished), total, sheets, name)
            return

        if self._mini_done:
            return
        self._mini_done = True

        done = [i for i in invs if i.status == ST_DONE]
        failed = [i for i in invs if i.status == ST_FAILED]
        skipped = [i for i in invs if i.status == ST_SKIPPED]
        unreadable = [i for i in invs if i.status == ST_UNREADABLE]
        not_invoice = [i for i in invs if i.status == ST_NOT_INVOICE]
        parts = [f"成功 {len(done)} 张"]
        if failed:
            parts.append(f"失败 {len(failed)} 张")
        if skipped:
            parts.append(f"重复跳过 {len(skipped)} 张")
        if not_invoice:
            parts.append(f"非发票跳过 {len(not_invoice)} 个")
        if unreadable:
            parts.append(f"读取失败 {len(unreadable)} 个")
        headline = (
            f"打印完成 · 共出纸 {sheets} 张" if done
            else ("已跳过非发票，没有出纸" if not_invoice and not failed else "没有打印成功")
        )
        self.mini.finish(headline, " · ".join(parts),
                         ok=bool(done) or (bool(not_invoice) and not failed))
        if self._quit_pending:
            QTimer.singleShot(200, self._quit_headless)

    def receive_forwarded(self, paths: List[str]) -> None:
        """另一个实例（右键/拖放）把文件转过来时，接着打。"""
        if self.mini is not None:
            self.mini.resign_to_idle()
        self._mini_done = False
        before = set(self._order)
        self.on_paths_dropped(paths)
        # 主界面开着时也要自动出纸：这条路径的语义是"拖上去就开始打"，
        # 不能取决于「拖入后立即打印」那个开关，更不能重打列表里的旧发票。
        self.print_invoices(
            [self.invoices[uid] for uid in self._order if uid not in before]
        )
        if self.headless:
            self._headless_sync()

    def accepting_forwarded(self) -> bool:
        """还能不能接别人的文件 —— 正在退出的实例必须先说「不」。

        否则新进程把文件一交就退出，文件跟着进程一起没了：
        打印没出纸、历史也没记录，用户只会看到「拖上去没反应」。
        """
        return not self._shutting_down

    def _enter_full_ui(self) -> None:
        """小卡片上点「打开主界面」：切回正常窗口，不再自动退出。"""
        if self.mini is not None:
            self.mini.cancel_countdown()
            self.mini.close()
            self.mini = None
        self.headless = False
        self.dialog_parent = self
        self._quit_pending = False
        # 无界面模式关掉了「关最后一个窗口就退出」，切回主界面必须恢复，
        # 否则关掉窗口后进程会一直挂在那里（还占着单实例管道）
        app = QApplication.instance()
        if app is not None:
            app.setQuitOnLastWindowClosed(True)
        self.show()
        self.raise_()
        self.activateWindow()
        self._preview_timer.start()
        write_log("从小进度卡切到主界面")

    def show_main_ui(self) -> None:
        """把界面调到前台（另一次双击图标 / 再打开一次程序时走这里）。"""
        if self.headless:
            self._enter_full_ui()
        else:
            self.showNormal()
            self.raise_()
            self.activateWindow()

    def _quit_headless(self) -> None:
        """打完（或用户点关闭）就退出进程，后台不留窗。"""
        if self._still_printing() or self._has_pending():
            # 还在打：先把卡片收起来，剩下的打完再退出（不半路撕掉打印作业）
            self._quit_pending = True
            if self.mini is not None:
                self.mini.hide()
            write_log("关闭进度卡：剩余任务打完后自动退出")
            return
        write_log("拖放即打印结束，退出程序")
        if self.mini is not None:
            self.mini.cancel_countdown()
            self.mini.close()
            self.mini = None
        self.close()                      # 触发 closeEvent：停打印线程
        app = QApplication.instance()
        if app is not None:
            app.quit()

    def _open_log(self) -> None:
        if LOG_FILE.exists():
            try:
                os.startfile(str(LOG_FILE))  # noqa: S606
            except Exception:
                pass

    def _open_data_dir(self) -> None:
        """打开配置/历史/日志所在目录 —— 排查「记录不见了」的第一现场。"""
        d = self.history.path.parent
        try:
            d.mkdir(parents=True, exist_ok=True)
            os.startfile(str(d))  # noqa: S606
        except Exception:
            self._toast(f"数据目录：{d}", "info")

    # ------------------------------------------------------------------ 关闭
    def _still_printing(self) -> bool:
        """还有作业在打或者在排队（含正在提交、尚未 started 的那条）。"""
        try:
            queued = self.worker.pending()
        except Exception:
            queued = 0
        return bool(self._busy or queued > 0)

    def closeEvent(self, event) -> None:  # noqa: N802
        # 打印线程不能中途撕掉：QThread 在跑就销毁会直接崩（该发票还会丢历史记录）
        if self._still_printing():
            self._close_attempts += 1
            if self._close_attempts >= 2:
                box = QMessageBox(self)
                box.setIcon(QMessageBox.Warning)
                box.setWindowTitle("仍在打印")
                box.setText("打印还没完成，现在退出会中断打印，这一张也不会记进历史。")
                box.setInformativeText("建议点「继续等待」，打完会自动退出。")
                wait_btn = box.addButton("继续等待", QMessageBox.RejectRole)
                quit_btn = box.addButton("立即退出", QMessageBox.DestructiveRole)
                box.setDefaultButton(wait_btn)
                box.exec()
                if box.clickedButton() is not quit_btn:
                    event.ignore()
                    self._close_attempts = 1
                    return
                write_log("用户选择在打印未完成时立即退出")
            else:
                self._close_when_idle = True
                self._toast("还有发票正在打印，打完会自动退出", "info")
                write_log("关闭窗口时仍有打印任务：改为打完再退出")
                event.ignore()
                return

        self._shutting_down = True       # 让转发方知道「这个实例不能再接活」
        self._close_when_idle = False
        self.settings.save()
        self.worker.stop()
        if not self.worker.wait(3000):
            # 强退时线程还活着：直接结束进程，别让 Qt 在析构时报
            # "QThread: Destroyed while thread is still running" 然后崩溃
            write_log("打印线程 3 秒内未停止，强制结束进程")
            event.accept()
            os._exit(0)
        super().closeEvent(event)
