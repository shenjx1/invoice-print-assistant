"""「拖放即打印」时显示的小进度卡片。

不打开主界面，只在屏幕右下角飘一张小卡片：几张发票、出到第几张纸、成功/失败，
打完停 5 秒自动关闭；想细看再点「打开主界面」，那时候才真正打开主窗口。
"""
from __future__ import annotations

from typing import Optional

from PySide6.QtCore import QPoint, QTimer, Qt, Signal
from PySide6.QtGui import QColor, QFont
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QGraphicsDropShadowEffect,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from . import style
from .config import UI_NAME

CLOSE_SECONDS = 5


class MiniProgress(QWidget):
    """右下角的小进度卡片（无边框）。"""

    openMain = Signal()
    closeRequested = Signal()

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(
            parent,
            Qt.Tool | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint,
        )
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WA_ShowWithoutActivating, False)
        self.setWindowTitle(UI_NAME)
        self.setFixedWidth(400)
        self._drag_from: Optional[QPoint] = None
        self._left = CLOSE_SECONDS
        self._countdown = QTimer(self)
        self._countdown.setInterval(1000)
        self._countdown.timeout.connect(self._tick)
        self._build()

    # ------------------------------------------------------------------ UI
    def _build(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(16, 16, 16, 16)   # 给阴影留位置
        outer.setSpacing(0)

        card = QFrame()
        card.setObjectName("Card")
        shadow = QGraphicsDropShadowEffect(self)
        shadow.setBlurRadius(26)
        shadow.setOffset(0, 8)
        shadow.setColor(QColor(15, 23, 42, 55))
        card.setGraphicsEffect(shadow)
        outer.addWidget(card)

        lay = QVBoxLayout(card)
        lay.setContentsMargins(18, 14, 16, 14)
        lay.setSpacing(8)

        head = QHBoxLayout()
        head.setSpacing(8)
        title = QLabel(UI_NAME)
        title.setObjectName("SectionTitle")
        self.state = QLabel("打印中")
        self.state.setStyleSheet(f"color:{style.ACCENT}; font-size:12px; font-weight:600;")
        head.addWidget(title)
        head.addStretch(1)
        head.addWidget(self.state)
        lay.addLayout(head)

        self.headline = QLabel("正在准备 …")
        self.headline.setStyleSheet("font-size:14px; font-weight:600; color:#111827;")
        lay.addWidget(self.headline)

        self.detail = QLabel("")
        self.detail.setObjectName("Muted")
        self.detail.setWordWrap(True)
        lay.addWidget(self.detail)

        self.bar = QProgressBar()
        self.bar.setTextVisible(False)
        self.bar.setFixedHeight(6)
        self.bar.setRange(0, 100)
        self.bar.setValue(0)
        self.bar.setStyleSheet(
            "QProgressBar { background:#EEF1F6; border:none; border-radius:3px; }"
            f"QProgressBar::chunk {{ background:{style.ACCENT}; border-radius:3px; }}"
        )
        lay.addWidget(self.bar)

        foot = QHBoxLayout()
        foot.setSpacing(10)
        self.hint = QLabel("")
        self.hint.setObjectName("Hint")
        foot.addWidget(self.hint, 1)

        self.open_btn = QPushButton("打开主界面")
        self.open_btn.setObjectName("Ghost")
        self.open_btn.setCursor(Qt.PointingHandCursor)
        self.open_btn.clicked.connect(self.openMain.emit)
        self.close_btn = QPushButton("关闭")
        self.close_btn.setObjectName("Ghost")
        self.close_btn.setCursor(Qt.PointingHandCursor)
        self.close_btn.clicked.connect(self._on_close_clicked)
        foot.addWidget(self.open_btn)
        foot.addWidget(self.close_btn)
        lay.addLayout(foot)

    # ------------------------------------------------------------- 文案 / 进度
    def set_progress(self, done: int, total: int, sheets: int, name: str = "") -> None:
        total = max(total, 1)
        percent = int(min(100, round(done * 100 / total)))
        self.bar.setValue(percent)
        self.headline.setText(f"正在打印 {done} / {total} 张发票")
        detail = f"已出纸 {sheets} 张"
        if name:
            detail = f"当前：{name} · " + detail
        self.detail.setText(detail)
        self.hint.setText("关掉这个卡片不影响打印，会继续打完")

    def set_message(self, text: str, level: str = "info") -> None:
        colors = {"ok": style.OK, "warn": style.WARN, "error": style.ERROR, "info": style.MUTED}
        self.detail.setText(text)
        self.detail.setStyleSheet(f"color:{colors.get(level, style.MUTED)};")

    def set_state(self, text: str, color: str = style.ACCENT) -> None:
        self.state.setText(text)
        self.state.setStyleSheet(f"color:{color}; font-size:12px; font-weight:600;")

    # ------------------------------------------------------------------ 完成
    def finish(self, headline: str, detail: str = "", ok: bool = True) -> None:
        self.bar.setValue(100 if ok else self.bar.value())
        self.set_state("完成" if ok else "未打印", style.OK if ok else style.WARN)
        self.headline.setText(headline)
        if detail:
            self.set_message(detail, "ok" if ok else "warn")
        self._left = CLOSE_SECONDS
        self._update_hint()
        self._countdown.start()

    def cancel_countdown(self) -> None:
        self._countdown.stop()
        self.hint.setText("")

    def resign_to_idle(self) -> None:
        """又有新文件转进来：停掉倒计时，回到“打印中”。"""
        self.cancel_countdown()
        self.set_state("打印中", style.ACCENT)
        self.headline.setText("收到新文件 …")

    def _tick(self) -> None:
        self._left -= 1
        if self._left <= 0:
            self._countdown.stop()
            self.closeRequested.emit()
            return
        self._update_hint()

    def _update_hint(self) -> None:
        if self._left > 0 and self._countdown.isActive():
            self.hint.setText(f"{self._left} 秒后自动关闭")

    def _on_close_clicked(self) -> None:
        self._countdown.stop()
        self.closeRequested.emit()

    # ------------------------------------------------------------------ 位置
    def show_at_corner(self) -> None:
        screen = QApplication.primaryScreen()
        if screen is not None:
            rect = screen.availableGeometry()
            self.adjustSize()
            x = rect.right() - self.width() - 8
            y = rect.bottom() - self.height() + 8
            self.move(max(rect.left(), x), max(rect.top(), y))
        self.show()
        self.raise_()

    # ------------------------------------------------------ 无边框窗口拖动
    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.LeftButton:
            self._drag_from = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        if self._drag_from is not None and event.buttons() & Qt.LeftButton:
            self.move(event.globalPosition().toPoint() - self._drag_from)
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        self._drag_from = None
        super().mouseReleaseEvent(event)

    def keyPressEvent(self, event) -> None:  # noqa: N802
        if event.key() == Qt.Key_Escape:
            self._on_close_clicked()
            return
        super().keyPressEvent(event)
