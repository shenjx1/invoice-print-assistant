"""打印引擎：把矢量 PDF 静默送到打印机。

Windows 本身没有可脚本化的「静默打印 PDF」接口 —— 资源管理器里的「打印」动词
会弹阅读器的对话框。所以矢量路线依赖一个小巧的外部引擎，按优先级自动探测：

1. ``sumatrapdf`` —— SumatraPDF（约 6MB，便携版即可）：``-print-to`` 直打指定
   打印机、``-silent`` 无界面、``-exit-when-done`` 打完退出。矢量输出，几十 KB。
2. ``raster``     —— 程序自带的 GDI 位图路线（:mod:`app.engine` + :mod:`app.gdi`），
   零依赖、一定能用，代价是每张 A4 要往队列送约 26MB。

找不到 SumatraPDF 时 ``auto`` 会安静地退回 ``raster``，不改变原有行为。
把 ``SumatraPDF.exe`` 放到程序目录（或任意 PATH 目录、``%LOCALAPPDATA%\\SumatraPDF``）
就能自动启用矢量路线；也可以用环境变量 ``INVOICE_PRINT_SUMATRA`` 指定完整路径。
"""
from __future__ import annotations

import logging
import os
import shutil
import subprocess
import sys
from typing import List, Optional

log = logging.getLogger(__name__)

ENGINE_AUTO = "auto"
ENGINE_VECTOR = "sumatrapdf"
ENGINE_RASTER = "raster"

ENGINE_LABELS = {
    ENGINE_AUTO: "自动（推荐）",
    ENGINE_VECTOR: "矢量 · SumatraPDF",
    ENGINE_RASTER: "位图 · 兼容模式",
}

_CREATE_NO_WINDOW = 0x08000000
_PRINT_TIMEOUT = 180          # 单张发票的超时；卡纸/离线时不要把线程吊死


def program_dir() -> str:
    """程序所在目录：打包后是 exe 同级，源码运行时是项目根目录。"""
    if getattr(sys, "frozen", False):
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def find_sumatrapdf() -> str:
    """探测 SumatraPDF.exe，返回完整路径；找不到返回空串。"""
    env = os.environ.get("INVOICE_PRINT_SUMATRA", "").strip(" \t\"'")
    if env and os.path.isfile(env):
        return env

    # 程序目录及常见子目录里任何 SumatraPDF*.exe（便携版文件名带版本号）
    for base in (program_dir(), os.path.join(program_dir(), "tools_bin"), os.getcwd()):
        try:
            for name in sorted(os.listdir(base)):
                low = name.lower()
                if low.startswith("sumatrapdf") and low.endswith(".exe"):
                    path = os.path.join(base, name)
                    if os.path.isfile(path):
                        return path
        except OSError:
            continue

    local = os.environ.get("LOCALAPPDATA", "")
    candidates = [
        os.path.join(local, "SumatraPDF", "SumatraPDF.exe") if local else "",
        r"C:\Program Files\SumatraPDF\SumatraPDF.exe",
        r"C:\Program Files (x86)\SumatraPDF\SumatraPDF.exe",
    ]
    for path in candidates:
        if path and os.path.isfile(path):
            return path

    return shutil.which("SumatraPDF") or ""


def resolve_engine(preference: str = ENGINE_AUTO) -> str:
    """把用户偏好解析成实际可用的引擎名。"""
    pref = (preference or ENGINE_AUTO).strip().lower()
    if pref == ENGINE_RASTER:
        return ENGINE_RASTER
    if pref == ENGINE_VECTOR:
        if not find_sumatrapdf():
            raise RuntimeError(
                "已选择「矢量 · SumatraPDF」，但没找到 SumatraPDF.exe。\n"
                "把 SumatraPDF.exe 放进程序目录即可，或改用「自动 / 位图」。"
            )
        return ENGINE_VECTOR
    # auto：能矢量就矢量，否则安静退回位图
    return ENGINE_VECTOR if find_sumatrapdf() else ENGINE_RASTER


def describe(preference: str = ENGINE_AUTO) -> str:
    """给界面提示用的单行说明。"""
    exe = find_sumatrapdf()
    actual = resolve_engine(preference) if preference != ENGINE_VECTOR or exe else ENGINE_RASTER
    if actual == ENGINE_VECTOR:
        return f"矢量打印（SumatraPDF）· {os.path.basename(exe)}"
    if preference == ENGINE_VECTOR:
        return "矢量打印不可用：未找到 SumatraPDF.exe"
    return "位图打印（兼容模式）· 免依赖"


def print_pdf(path: str, printer: str, copies: int = 1, job_name: str = "") -> None:
    """用 SumatraPDF 把 ``path`` 静默送到 ``printer``。

    纸张尺寸与方向由 PDF 页面本身决定（拼版时已按 A4 生成），
    所以这里只传份数，不覆盖用户的打印机默认的 simplex/duplex 设置。
    """
    exe = find_sumatrapdf()
    if not exe:
        raise RuntimeError("未找到 SumatraPDF.exe，无法走矢量打印")
    if not printer:
        raise RuntimeError("未指定打印机")
    if not os.path.isfile(path):
        raise RuntimeError(f"待打印的 PDF 不存在：{path}")

    cmd: List[str] = [exe, "-print-to", printer]
    copies = max(1, int(copies))
    if copies > 1:
        cmd += ["-print-settings", f"copies={copies}"]
    cmd += ["-silent", "-exit-when-done", path]

    log.info("矢量打印：%s → %s（%d 份）", os.path.basename(path), printer, copies)
    try:
        proc = subprocess.run(
            cmd,
            timeout=_PRINT_TIMEOUT,
            creationflags=_CREATE_NO_WINDOW,
            capture_output=True,
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(f"打印超时（{_PRINT_TIMEOUT} 秒）：{os.path.basename(path)}") from exc
    if proc.returncode not in (0, None):
        detail = (proc.stderr or b"").decode("utf-8", "replace").strip()
        raise RuntimeError(
            f"SumatraPDF 返回错误码 {proc.returncode}"
            + (f"：{detail}" if detail else "")
        )


def engine_status() -> str:
    """诊断用：当前可用的引擎与路径。"""
    exe = find_sumatrapdf()
    return f"矢量引擎：{exe or '未找到'}；位图引擎：内置（GDI）"
