"""发票任务模型：一个 PDF 文件 = 一张发票 = 一个独立任务。

这一层的存在只为了一件事 —— 让「文件」和「纸张」彻底解耦：

* 解析是**逐文件**的，一个文件对应一个 :class:`Invoice`，各自记录页数、状态、错误；
* 排版是**逐发票**的，:func:`plan_sheets` 只会拿**这一张发票自己的页**去填格子，
  物理上永远不会把另一个 PDF 的页面塞进来；
* 打印是**逐发票**的，每张发票提交给打印机时是**一条独立的打印作业**，
  在 Windows 打印队列里能看到 N 条作业（文件名就是 PDF 名），而不是混成一坨。

因此「一次拖入 10 个 PDF」= 10 张发票、10 条独立作业，互不干扰：失败一张不影响其他，
重复拖入同一个文件也是两张发票（两次打印），符合财务上"一份文件一次凭证"的直觉。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, List, Optional

from .engine import LayoutSpec, PageRef, scan_pdf

# 空位处理方式
PAD_REPEAT = "repeat"   # 用本发票自己的页面循环补齐（单页发票 → 上下两份联次）
PAD_BLANK = "blank"     # 空位留白

# 状态
ST_PENDING = "待打印"
ST_PRINTING = "打印中"
ST_DONE = "已打印"
ST_FAILED = "打印失败"
ST_UNREADABLE = "读取失败"
ST_SKIPPED = "已跳过"      # 发票号与已有记录重复，用户选择不打印
ST_NOT_INVOICE = "非发票"  # 内容不是发票（合同/报价单等），按「只打印发票」跳过


@dataclass
class Invoice:
    """一张发票（= 一个 PDF 文件）的全部状态。"""

    uid: int
    path: Path
    refs: List[PageRef] = field(default_factory=list)
    status: str = ST_PENDING
    printed_sheets: int = 0
    total_sheets: int = 0
    error: str = ""
    time: str = ""
    fields: Optional[Any] = None   # app.extract.InvoiceFields：历史记录用的字段快照

    # ---------------------------------------------------------------- 只读属性
    @property
    def name(self) -> str:
        return self.path.name

    @property
    def page_count(self) -> int:
        return len(self.refs)

    @property
    def ok(self) -> bool:
        return self.status in (ST_PENDING, ST_PRINTING, ST_DONE)

    def describe(self) -> str:
        return f"{self.name}（{self.page_count} 页 → {self.total_sheets} 张纸）"


def read_invoice(uid: int, path: Path) -> Invoice:
    """解析单个 PDF 并生成发票对象；解析失败的原样写进 status/error，不抛出。"""
    inv = Invoice(uid=uid, path=path)
    try:
        inv.refs = scan_pdf(str(path))
    except Exception as exc:  # 损坏 / 加密 / 空文件 —— 只影响这一张发票
        inv.status = ST_UNREADABLE
        inv.error = str(exc)
    return inv


def plan_sheets(inv: Invoice, spec: LayoutSpec, pad_mode: str = PAD_REPEAT) -> List[List[Optional[PageRef]]]:
    """把**一张发票**排成若干张纸。

    规则（保证归属不串台）：

    * 页数 ≤ 每张纸格数：一张纸放得下，剩余格子用**本发票自己的页**循环补齐
      （单页发票 1×2 → 上下两份同一张发票，即截图里的「页面 1,1」）；
      选了留白就空着，绝不引入别的发票；
    * 页数 > 每张纸格数：按页序装满整张，最后一张的剩余格子留白。

    返回值的每一项是一张纸的格子内容，``None`` 表示该格留空。
    """
    per = max(1, spec.per_sheet)
    pages = list(inv.refs)
    if not pages:
        return []

    if len(pages) <= per:
        cell: List[Optional[PageRef]] = list(pages)
        if pad_mode == PAD_REPEAT:
            while len(cell) < per:  # 只循环本发票的页
                cell.append(pages[len(cell) % len(pages)])
        return [cell + [None] * (per - len(cell))]

    sheets: List[List[Optional[PageRef]]] = [
        list(pages[i:i + per]) for i in range(0, len(pages), per)
    ]
    sheets[-1] += [None] * (per - len(sheets[-1]))
    return sheets


def sheet_count(inv: Invoice, spec: LayoutSpec) -> int:
    """预估张数（与 plan_sheets 的纸张数一致，用于表格展示）。"""
    per = max(1, spec.per_sheet)
    if not inv.refs:
        return 0
    return max(1, -(-len(inv.refs) // per))  # 向上取整
