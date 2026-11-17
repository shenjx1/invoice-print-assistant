"""后台打印线程：避免打印时界面卡死。

一个 :class:`PrintBatch` 只承载**一张发票**的纸张 —— 打印时是独立的一条
Windows 打印作业（作业名即 PDF 文件名），因此打印机队列里能逐张发票核对。
"""
from __future__ import annotations

import queue
from dataclasses import dataclass, field
from typing import List, Optional

from PySide6.QtCore import QThread, Signal

from .engine import LayoutSpec, PageRef, print_sheets


@dataclass
class PrintBatch:
    batch_id: int
    invoice_uid: int          # 归属的发票；一张发票一条作业，绝不合并
    name: str                 # 打印作业名（= PDF 文件名）
    printer: str
    sheets: List[List[Optional[PageRef]]]
    spec: LayoutSpec
    paper: str = "A4"
    landscape: bool = False
    copies: int = 1
    render_dpi: int = 300
    page_count: int = field(default=0)  # 该发票总页数，用于回报进度
    printed: int = field(default=0)     # 实际出纸张数（含份数），打完后由工作线程填


class PrintWorker(QThread):
    """串行处理打印任务，保证同一时间只有一份文档在打印。"""

    batch_started = Signal(object)
    batch_finished = Signal(object, bool, str)
    message = Signal(str, str)  # 文本, 级别

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._queue: "queue.Queue" = queue.Queue()
        self._stopping = False

    def submit(self, batch: PrintBatch) -> None:
        self._queue.put(batch)

    def stop(self) -> None:
        self._stopping = True
        self._queue.put(None)

    def pending(self) -> int:
        """队列里还没开打的作业数（正在打的那一条不算）。"""
        return self._queue.qsize()

    # ------------------------------------------------------------------ run
    def run(self) -> None:  # noqa: D401
        while True:
            batch = self._queue.get()
            if batch is None or self._stopping:
                break
            self.batch_started.emit(batch)
            try:
                count = print_sheets(
                    printer=batch.printer,
                    sheets=batch.sheets,
                    spec=batch.spec,
                    paper=batch.paper,
                    landscape=batch.landscape,
                    copies=batch.copies,
                    render_dpi=batch.render_dpi,
                    job_name=batch.name,
                )
                self.message.emit(
                    f"「{batch.name}」已输出 {count} 张纸 → {batch.printer}", "ok"
                )
                batch.printed = count
                self.batch_finished.emit(batch, True, "")
            except Exception as exc:  # 打印失败不能拖垮程序
                self.message.emit(f"「{batch.name}」打印失败：{exc}", "error")
                self.batch_finished.emit(batch, False, str(exc))
