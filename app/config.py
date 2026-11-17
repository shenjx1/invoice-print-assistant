"""配置持久化（保存在 ``%APPDATA%\\InvoicePrintAssistant\\config.json``）。"""
from __future__ import annotations

import json
import math
import os
import shutil
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Optional

from .engine import LayoutSpec

# --------------------------------------------------------------------------- #
# 名称：只在这里改，其它模块统一 import，避免两处名字对不上
# --------------------------------------------------------------------------- #
PROGRAM_NAME = "发票打印助手"        # 程序名：exe 文件名、任务栏、右键菜单、「打开方式」
UI_NAME = "发票打印助手"             # 界面里显示的名字：窗口标题、卡片标题、弹窗标题

APP_NAME = "InvoicePrintAssistant"   # %APPDATA% 下的配置目录（纯 ASCII，避免编码问题）

# 旧版留下的配置目录名：首次运行会把里面的设置 / 历史记录搬过来。
# 公开发布版留空；如果你是从自己改名前的老版本升级，把旧目录名填进来即可。
LEGACY_APP_NAMES: tuple[str, ...] = ()


def _legacy_dirs(target: Path) -> list[Path]:
    base = target.parent
    out: list[Path] = []
    for name in LEGACY_APP_NAMES:
        if not name:
            continue
        legacy = base / name
        if legacy.is_dir() and legacy != target:
            out.append(legacy)
    return out


def _migrate_legacy_dir(target: Path) -> None:
    """旧版本的用户数据（设置、历史记录、日志）搬到新目录，只搬一次。"""
    for legacy in _legacy_dirs(target):
        try:
            for name in ("config.json", "history.json"):
                src = legacy / name
                dst = target / name
                if src.is_file() and not dst.exists():
                    shutil.copy2(src, dst)
        except Exception:
            pass    # 搬迁失败不影响使用，程序会用默认设置重新开始


def _merge_legacy_history(target: Path, limit: int = 500) -> None:
    """把旧目录的历史记录并进来。

    改名之后如果又用过旧版（或电脑上同时躺着新旧两个 exe），旧记录会写进旧目录，
    新界面永远看不到。这里在启动时合并一次：以旧文件的修改时间为准，只在它比新文件
    「新」时才合并，并把重复项去掉。
    """
    for legacy in _legacy_dirs(target):
        src = legacy / "history.json"
        dst = target / "history.json"
        if not src.is_file():
            continue
        try:
            if not dst.exists():
                shutil.copy2(src, dst)
                continue
            if src.stat().st_mtime <= dst.stat().st_mtime:
                continue
            old = json.loads(src.read_text("utf-8"))
            new = json.loads(dst.read_text("utf-8"))
            if not isinstance(old, list) or not isinstance(new, list):
                continue
            seen = {
                (r.get("when"), r.get("invoice_no"))
                for r in new
                if isinstance(r, dict)
            }
            merged = list(new) + [
                r for r in old
                if isinstance(r, dict) and (r.get("when"), r.get("invoice_no")) not in seen
            ]
            merged.sort(key=lambda r: str(r.get("when", "")), reverse=True)
            dst.write_text(
                json.dumps(merged[:limit], ensure_ascii=False, indent=1), "utf-8"
            )
        except Exception:
            pass    # 合并失败不影响使用


def _is_writable(d: Path) -> bool:
    """真写一个文件试试 —— 权限问题只有在写的时候才暴露。"""
    try:
        d.mkdir(parents=True, exist_ok=True)
        probe = d / ".write_probe"
        probe.write_text("ok", "utf-8")
        probe.unlink()
        return True
    except Exception:
        return False


_DIR_CACHE: Optional[Path] = None
DIR_FALLBACK_USED = ""      # 非空表示 %APPDATA% 写不进去，退到了这里（界面上会提示）


def app_dir() -> Path:
    """配置/历史/日志的存放目录。

    优先 ``%APPDATA%``；企业环境里漫游配置、重定向或安全策略都可能让它不可写，
    这时依次退到 ``%LOCALAPPDATA%``、``%TEMP%``、程序同级的 ``data`` 目录 ——
    宁可换个地方存，也不能让历史记录悄悄丢掉。
    """
    global _DIR_CACHE, DIR_FALLBACK_USED
    if _DIR_CACHE is not None:
        return _DIR_CACHE

    candidates = []
    for env_name in ("APPDATA", "LOCALAPPDATA", "TEMP"):
        base = os.environ.get(env_name)
        if base:
            candidates.append(Path(base) / APP_NAME)
    exe_root = (
        Path(sys.executable).resolve().parent
        if getattr(sys, "frozen", False)
        else Path(__file__).resolve().parent.parent
    )
    candidates.append(exe_root / "data")

    chosen = candidates[0]
    for d in candidates:
        if _is_writable(d):
            chosen = d
            break

    if chosen != candidates[0]:
        DIR_FALLBACK_USED = str(chosen)

    # 目录是这次新建的，或旧版本目录里有更新的记录 → 搬 / 合并
    _migrate_legacy_dir(chosen)
    _merge_legacy_history(chosen)

    _DIR_CACHE = chosen
    return chosen



CONFIG_FILE = app_dir() / "config.json"


def desktop_dir() -> Path:
    """真正的「桌面」目录。

    不能用 ``Path.home() / "Desktop"``：桌面被挪到别的盘（本机是 ``D:\\桌面``）
    或者是中文名时，那个路径根本不存在，导出对话框会落在空目录里。
    """
    try:
        import ctypes

        buf = ctypes.create_unicode_buffer(260)
        # CSIDL_DESKTOPDIRECTORY = 0x0010
        if ctypes.windll.shell32.SHGetFolderPathW(None, 0x0010, None, 0, buf) == 0:
            path = Path(buf.value) if buf.value else None
            if path is not None and path.is_dir():
                return path
    except Exception:
        pass
    for name in ("Desktop", "桌面"):
        candidate = Path.home() / name
        if candidate.is_dir():
            return candidate
    return Path.home()


@dataclass
class Settings:
    printer: str = ""          # 空字符串 = 使用系统默认打印机
    paper: str = "A4"
    landscape: bool = False
    render_dpi: int = 300
    copies: int = 1
    auto_print: bool = True    # 拖入后立即打印（逐文件独立提交）
    only_invoice: bool = True  # 只打印发票：合同/报价单等非发票 PDF 跳过，不出纸
    pad_mode: str = "repeat"   # 空位处理：repeat=用本发票补齐联次  blank=留白
    panel_collapsed: bool = False  # 左侧设置面板是否收起
    shell_menu: bool = True    # 在资源管理器右键菜单里显示「使用发票打印助手打印」（写 HKCU）
    spec: LayoutSpec = field(default_factory=LayoutSpec)

    # ---------------------------------------------------------------- 存取
    @classmethod
    def load(cls) -> "Settings":
        """读取并逐项校验配置；任何损坏字段都回退默认值，不能拖垮启动。"""
        try:
            raw = json.loads(CONFIG_FILE.read_text("utf-8"))
        except Exception:
            return cls()
        if not isinstance(raw, dict):
            return cls()

        defaults = cls()
        spec_raw = raw.get("spec", {})
        if not isinstance(spec_raw, dict):
            spec_raw = {}
        spec_defaults = LayoutSpec()

        def text(value, fallback: str) -> str:
            return value if isinstance(value, str) else fallback

        def boolean(value, fallback: bool) -> bool:
            return value if isinstance(value, bool) else fallback

        def integer(value, fallback: int, low: int, high: int) -> int:
            if isinstance(value, bool):
                return fallback
            try:
                if isinstance(value, float) and not value.is_integer():
                    return fallback
                number = int(value)
            except (TypeError, ValueError, OverflowError):
                return fallback
            return number if low <= number <= high else fallback

        def decimal(value, fallback: float, low: float, high: float) -> float:
            if isinstance(value, bool):
                return fallback
            try:
                number = float(value)
            except (TypeError, ValueError, OverflowError):
                return fallback
            if not math.isfinite(number) or not low <= number <= high:
                return fallback
            return number

        paper = text(raw.get("paper"), defaults.paper)
        if paper not in ("A4", "A5", "Letter", "A3"):
            paper = defaults.paper
        pad_mode = text(raw.get("pad_mode"), defaults.pad_mode)
        if pad_mode not in ("repeat", "blank"):
            pad_mode = defaults.pad_mode

        cols = integer(spec_raw.get("cols"), spec_defaults.cols, 1, 4)
        rows = integer(spec_raw.get("rows"), spec_defaults.rows, 1, 4)
        if (cols, rows) not in {(1, 1), (1, 2), (1, 3), (2, 2), (2, 3), (2, 4)}:
            cols, rows = spec_defaults.cols, spec_defaults.rows
        order = text(spec_raw.get("order"), spec_defaults.order)
        if order not in ("row", "col"):
            order = spec_defaults.order

        try:
            return cls(
                printer=text(raw.get("printer"), defaults.printer),
                paper=paper,
                landscape=boolean(raw.get("landscape"), defaults.landscape),
                render_dpi=integer(raw.get("render_dpi"), defaults.render_dpi, 150, 600),
                copies=integer(raw.get("copies"), defaults.copies, 1, 99),
                auto_print=boolean(raw.get("auto_print"), defaults.auto_print),
                only_invoice=boolean(raw.get("only_invoice"), defaults.only_invoice),
                pad_mode=pad_mode,
                panel_collapsed=boolean(raw.get("panel_collapsed"), defaults.panel_collapsed),
                shell_menu=boolean(raw.get("shell_menu"), defaults.shell_menu),
                spec=LayoutSpec(
                    cols=cols,
                    rows=rows,
                    margin_cm=decimal(spec_raw.get("margin_cm"), spec_defaults.margin_cm, 0.0, 5.0),
                    gap_cm=decimal(spec_raw.get("gap_cm"), spec_defaults.gap_cm, 0.0, 5.0),
                    scale_pct=decimal(spec_raw.get("scale_pct"), spec_defaults.scale_pct, 30.0, 100.0),
                    order=order,
                    auto_rotate=boolean(spec_raw.get("auto_rotate"), spec_defaults.auto_rotate),
                ),
            )
        except Exception:
            return cls()

    def save(self) -> None:
        try:
            CONFIG_FILE.write_text(
                json.dumps(asdict(self), ensure_ascii=False, indent=2), "utf-8"
            )
        except Exception:
            pass

    # ---------------------------------------------------------------- 便捷
    @property
    def per_sheet(self) -> int:
        return self.spec.per_sheet
