"""历史打印记录：一条记录 = 一张发票打完（或打失败）后的字段快照。

**只记录票面表面信息**：发票号 / 材料名称 / 规格型号 / 数量 / 单价 / 打印时间 / 结果，
不保存文件、也不记录文件名与路径（用户要求），因此历史记录也不能反查原 PDF。
税率只参与单价折算，不再单独成列（用户要求去掉）。

字段来源见 :mod:`app.extract`；落盘在 ``%APPDATA%\\LehuaPurchasePrintAssistant\\history.json``，
默认保留最近 :data:`DEFAULT_LIMIT` 条，启动时读回，所以关掉程序也不会丢。
旧版本写下的 ``file`` / ``path`` 键在读取时会被忽略，不会报错。
"""
from __future__ import annotations

import datetime
import json
import os
import shutil
import tempfile
from dataclasses import asdict, dataclass, fields
from pathlib import Path
from typing import Any, Dict, List, Optional

from .config import app_dir

DEFAULT_LIMIT = 500
HISTORY_FILE = app_dir() / "history.json"

COLUMNS = ("发票号", "材料名称", "规格型号", "数量", "单价", "打印时间", "结果")


@dataclass
class PrintRecord:
    """一条历史记录 = 只保留票面表面信息，**不保存文件、不记录文件名/路径**。"""

    when: str = ""          # 打印时间
    invoice_no: str = ""    # 发票号后 8 位
    material: str = ""      # 材料名称 = 项目名称（去掉 *xx* 前缀）+ 规格型号
    spec: str = ""          # 规格型号
    qty: str = ""           # 数量
    tax_rate: str = ""      # 税率，如 6%（只用于单价折算与提示，不进表格）
    unit_price: str = ""    # 含税单价 = 票面单价 × (1 + 税率)
    status: str = "已打印"
    note: str = ""          # 提取异常说明，显示在提示里
    formula: str = ""       # 计算过程，显示在提示里

    def cells(self) -> List[str]:
        """表格一行；顺序必须与 :data:`COLUMNS` 一致（税率不占列）。"""
        return [self.invoice_no or "—", self.material or "—", self.spec or "—",
                self.qty or "—", self.unit_price or "—",
                self.when, self.status]

    def tooltip(self) -> str:
        lines: List[str] = []
        if self.formula:
            lines.append(f"单价计算：{self.formula}")
        if self.note:
            lines.append(f"提取备注：{self.note}")
        if not lines:
            lines.append("仅记录票面表面信息（不含文件与路径）")
        return "\n".join(lines)


def _log(text: str) -> None:
    """写不进历史文件时至少留下痕迹：直接追加到同目录的 run.log。

    这里不 import ui.write_log —— 那会形成循环导入，而这条日志必须一定能写。
    """
    try:
        with (app_dir() / "run.log").open("a", encoding="utf-8") as fh:
            stamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            fh.write(f"[{stamp}] [历史记录] {text}\n")
    except Exception:
        pass


class History:
    """记录的读写与容量控制（最新的排在最前面）。"""

    def __init__(self, path: Optional[Path] = None, limit: int = DEFAULT_LIMIT) -> None:
        self.path = Path(path) if path else HISTORY_FILE
        self.limit = max(1, int(limit))
        self.records: List[PrintRecord] = []
        self.last_error = ""      # 最近一次写入/读取失败的原因（界面用来提示）
        self.load()

    # ---------------------------------------------------------------- 存取
    def load(self) -> None:
        self.records = []
        if not self.path.exists():
            return
        try:
            raw = json.loads(self.path.read_text("utf-8"))
        except Exception as exc:
            # 文件坏了也不能让历史凭空消失：备份一份，再把原因写进日志
            self.last_error = f"历史文件读取失败：{exc}"
            try:
                shutil.copy2(self.path, self.path.with_suffix(".bad.json"))
            except Exception:
                pass
            _log(f"{self.path} 解析失败（已备份为 .bad.json）：{exc}")
            return
        if not isinstance(raw, list):
            return
        valid = {f.name for f in fields(PrintRecord)}
        dropped = 0
        for item in raw:
            if isinstance(item, dict):
                try:
                    self.records.append(
                        PrintRecord(**{k: str(v) for k, v in item.items() if k in valid})
                    )
                except Exception:
                    dropped += 1
                    continue
        self.records = self.records[: self.limit]
        if dropped:
            _log(f"读取时跳过 {dropped} 条无法识别的记录")

    def save(self) -> bool:
        """原子写盘：先完整写临时文件，再替换正式文件，避免中途崩溃清空历史。"""
        temp_name = ""
        try:
            payload = [asdict(r) for r in self.records]
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(
                "w", encoding="utf-8", dir=self.path.parent,
                prefix=f"{self.path.name}.", suffix=".tmp", delete=False,
            ) as fh:
                temp_name = fh.name
                json.dump(payload, fh, ensure_ascii=False, indent=1)
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(temp_name, self.path)
            temp_name = ""
            self.last_error = ""
            return True
        except Exception as exc:
            self.last_error = f"历史记录写入失败：{exc}"
            _log(f"写入 {self.path} 失败：{exc}")
            return False
        finally:
            if temp_name:
                try:
                    Path(temp_name).unlink()
                except OSError:
                    pass

    # ---------------------------------------------------------------- 变更
    def add(self, record: PrintRecord) -> bool:
        """追加一条并立刻落盘，返回是否写成功（写失败会在 last_error 里说明原因）。"""
        if not record.when:
            record.when = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self.records.insert(0, record)
        del self.records[self.limit:]
        return self.save()

    def clear(self) -> bool:
        """清空并落盘；写失败时恢复内存里的记录，避免界面与磁盘状态不一致。"""
        previous = list(self.records)
        self.records.clear()
        if self.save():
            return True
        self.records = previous
        return False

    def delete(self, indices: List[int]) -> int:
        """按行号删除若干条（行号 = records 下标，界面里最新的在第 0 行）。

        返回真正删掉的条数；删完立刻落盘，写失败会在 ``last_error`` 里说明。
        """
        targets = sorted({int(i) for i in indices if 0 <= int(i) < len(self.records)},
                         reverse=True)
        if not targets:
            return 0
        for index in targets:
            del self.records[index]
        self.save()
        return len(targets)

    # ---------------------------------------------------------------- 查询
    def __len__(self) -> int:
        return len(self.records)

    def as_rows(self) -> List[Dict[str, Any]]:
        return [{"cells": r.cells(), "tooltip": r.tooltip()} for r in self.records]

    def printed_index(self) -> Dict[str, PrintRecord]:
        """发票号 → 最近一条「已打印」记录（用于判断新文件是否重复打印）。"""
        index: Dict[str, PrintRecord] = {}
        for record in self.records:          # records 是新的在前，所以先到先得
            number = (record.invoice_no or "").strip()
            if not number or number == "—" or number in index:
                continue
            if record.status != "已打印":
                continue
            index[number] = record
        return index

    def export_csv(self, path: Path) -> bool:
        """把记录导成 CSV（UTF-8 BOM，Excel 直接双击不乱码）。"""
        try:
            lines = [",".join(COLUMNS)]
            for r in self.records:
                lines.append(",".join(_csv(c) for c in r.cells()))
            Path(path).write_text("\n".join(lines) + "\n", "utf-8-sig")
            return True
        except Exception:
            return False


def _csv(text: str) -> str:
    t = (text or "").replace('"', '""')
    return f'"{t}"' if ("," in t or '"' in t or "\n" in t) else t
