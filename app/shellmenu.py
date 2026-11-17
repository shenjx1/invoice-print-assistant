"""Windows 资源管理器集成：右键菜单「使用发票打印助手打印」+「打开方式」。

全部写在 ``HKCU\\Software\\Classes`` 下：

* **右键菜单** —— ``SystemFileAssociations\\.pdf\\shell\\LehuaPurchasePrintAssistant``，
  选中一个或多个 PDF 右键即可看到；多选时 Windows 会把全部文件一起传进来
  （``MultiSelectModel=Player``，上限 100 个）。
* **打开方式** —— ``Applications\\发票打印助手.exe`` + ``.pdf\\OpenWithProgids``，
  微信/浏览器里「打开方式 → 选择其他应用」能直接看到本程序。

旧版本（早期叫 InvoicePrint）写下的注册表项会在启动时自动清掉，
避免右键菜单里同时出现新旧两条。

为什么用 HKCU：不需要管理员权限、不影响别的用户、卸载时删掉即可；
也**不会**抢走 PDF 的默认关联（那需要用户在系统设置里自己确认）。
"""
from __future__ import annotations

import ctypes
import sys
from pathlib import Path
from typing import Iterable, List, Optional

import winreg

from .config import PROGRAM_NAME

BASE_DIR = Path(__file__).resolve().parent.parent
EXE_NAME = f"{PROGRAM_NAME}.exe"
APP_TITLE = PROGRAM_NAME
MENU_TEXT = f"使用{PROGRAM_NAME}打印"

VERB = "PdfInvoicePrintAssistant"
PROGID = "PdfInvoicePrint.pdf"

# 旧版本留下的键（升级后自动清理，不然右键里会有两条一模一样的菜单）
LEGACY_VERBS = ("InvoicePrintAssistant",)
LEGACY_PROGIDS = ("InvoicePrint.pdf",)
LEGACY_APPS = ("InvoicePrint.exe",)

CLS_ROOT = r"Software\Classes"

HKCU = winreg.HKEY_CURRENT_USER

# SHCNE_ASSOCCHANGED / SHCNF_IDLIST —— 通知资源管理器刷新关联，右键菜单立即生效
_SHCNE_ASSOCCHANGED = 0x08000000


# --------------------------------------------------------------------------- #
# 路径 / 命令行
# --------------------------------------------------------------------------- #
def _icon_file() -> Optional[Path]:
    for base in (BASE_DIR, Path(getattr(sys, "_MEIPASS", BASE_DIR))):
        icon = base / "assets" / "icon.ico"
        if icon.exists():
            return icon
    return None


def icon_path() -> str:
    """注册表里的图标值。

    打包后直接取 exe 自身（``"…\\发票打印助手.exe",0``）—— 单文件 exe 运行时会解包到临时目录，
    那里的 ico 路径程序一退出就失效了，不能写进注册表。
    """
    if getattr(sys, "frozen", False):
        return f'"{sys.executable}",0'
    icon = _icon_file()
    return f'"{icon}"' if icon else ""


def launcher_command() -> str:
    """注册表里要写的命令行（``%1`` 由资源管理器替换成被点击的文件）。

    * 打包后：``"…\\发票打印助手.exe" "%1"``
    * 源码运行：``"…\\pythonw.exe" "…\\main.py" "%1"``（用 pythonw 避免弹黑框）
    """
    if getattr(sys, "frozen", False):
        return f'"{sys.executable}" "%1"'

    exe = Path(sys.executable)
    pyw = exe.with_name("pythonw.exe")
    runner = pyw if pyw.exists() else exe
    main_py = BASE_DIR / "main.py"
    return f'"{runner}" "{main_py}" "%1"'


def app_id() -> str:
    """「打开方式」列表里显示的程序名（取自 exe 文件名，便于系统识别）。"""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).name
    return EXE_NAME


def paths_from_argv(argv: Iterable[str]) -> List[str]:
    """从命令行参数里挑出 PDF 文件 / 文件夹（右键菜单、拖到 exe 图标都走这里）。"""
    out: List[str] = []
    for raw in argv:
        text = (raw or "").strip().strip('"')
        if not text or text.startswith("-"):
            continue
        path = Path(text)
        if path.suffix.lower() == ".pdf":
            if path.is_file():
                out.append(str(path.absolute()))
        elif path.is_dir():
            out.append(str(path.absolute()))
    return out


# --------------------------------------------------------------------------- #
# 注册表小工具
# --------------------------------------------------------------------------- #
def _set_sz(key, name: str, value: str) -> None:
    winreg.SetValueEx(key, name, 0, winreg.REG_SZ, value)


def _create(path: str):
    return winreg.CreateKeyEx(HKCU, path, 0, winreg.KEY_WRITE)


def _delete_tree(path: str) -> None:
    """递归删除一个键（winreg 没有现成的递归删除）。"""
    try:
        with winreg.OpenKey(HKCU, path, 0, winreg.KEY_READ | winreg.KEY_WRITE) as key:
            while True:
                try:
                    name = winreg.EnumKey(key, 0)
                except OSError:
                    break
                _delete_tree(f"{path}\\{name}")
    except FileNotFoundError:
        return
    except OSError:
        return
    try:
        winreg.DeleteKey(HKCU, path)
    except OSError:
        pass


def _read_default(path: str) -> str:
    try:
        with winreg.OpenKey(HKCU, path, 0, winreg.KEY_READ) as key:
            value, _kind = winreg.QueryValueEx(key, "")
            return str(value)
    except OSError:
        return ""


def refresh_shell() -> None:
    """让资源管理器立刻重读关联（不然要重启才看到新菜单）。"""
    try:
        ctypes.windll.shell32.SHChangeNotify(_SHCNE_ASSOCCHANGED, 0, None, None)
    except Exception:
        pass


def cleanup_legacy(root: str = CLS_ROOT) -> bool:
    """清掉改名前的旧菜单项（InvoicePrint）。返回是否删了东西。"""
    removed = False
    for verb in LEGACY_VERBS:
        key = rf"{root}\SystemFileAssociations\.pdf\shell\{verb}"
        if _read_default(key + r"\command"):
            removed = True
        _delete_tree(key)
    for name in LEGACY_APPS:
        key = rf"{root}\Applications\{name}"
        if _read_default(key + r"\shell\open\command"):
            removed = True
        _delete_tree(key)
    for progid in LEGACY_PROGIDS:
        key = rf"{root}\{progid}"
        if _read_default(key + r"\shell\open\command"):
            removed = True
        _delete_tree(key)
        try:
            with winreg.OpenKey(
                HKCU, rf"{root}\.pdf\OpenWithProgids", 0, winreg.KEY_READ | winreg.KEY_WRITE
            ) as key:
                winreg.DeleteValue(key, progid)
                removed = True
        except OSError:
            pass
    if removed:
        refresh_shell()
    return removed


# --------------------------------------------------------------------------- #
# 安装 / 卸载
# --------------------------------------------------------------------------- #
def register(root: str = CLS_ROOT) -> bool:
    """把右键菜单 + 打开方式写进当前用户注册表。返回是否成功。"""
    try:
        cleanup_legacy(root)
        cmd = launcher_command()
        icon = icon_path()
        exe_name = app_id()

        # 1) 右键菜单：只挂在 .pdf 上，不写进 HKCR 全局
        verb = rf"{root}\SystemFileAssociations\.pdf\shell\{VERB}"
        with _create(verb) as key:
            _set_sz(key, "MUIVerb", MENU_TEXT)
            _set_sz(key, "MultiSelectModel", "Player")   # 允许多选（最多 100 个文件）
            if icon:
                _set_sz(key, "Icon", icon)
        with _create(verb + r"\command") as key:
            _set_sz(key, "", cmd)

        # 2) 打开方式：Applications 下的应用条目
        app_key = rf"{root}\Applications\{exe_name}"
        with _create(app_key) as key:
            _set_sz(key, "FriendlyAppName", APP_TITLE)
        with _create(app_key + r"\DefaultIcon") as key:
            _set_sz(key, "", icon or "")
        with _create(app_key + r"\shell\open\command") as key:
            _set_sz(key, "", cmd)
        with _create(app_key + r"\SupportedTypes") as key:
            _set_sz(key, ".pdf", "")

        # 3) 打开方式：给 .pdf 一个候选 ProgID
        prog = rf"{root}\{PROGID}"
        with _create(prog) as key:
            _set_sz(key, "", "PDF 文档")
            _set_sz(key, "FriendlyTypeName", "PDF 文档")
        with _create(prog + r"\DefaultIcon") as key:
            _set_sz(key, "", icon or "")
        with _create(prog + r"\shell\open\command") as key:
            _set_sz(key, "", cmd)
        with _create(rf"{root}\.pdf\OpenWithProgids") as key:
            winreg.SetValueEx(key, PROGID, 0, winreg.REG_NONE, b"")

        refresh_shell()
        return True
    except OSError:
        return False


def unregister(root: str = CLS_ROOT) -> bool:
    """删掉 register() 写下的全部内容（不改用户的默认打开方式）。"""
    try:
        _delete_tree(rf"{root}\SystemFileAssociations\.pdf\shell\{VERB}")
        _delete_tree(rf"{root}\Applications\{app_id()}")
        _delete_tree(rf"{root}\{PROGID}")
        try:
            with winreg.OpenKey(
                HKCU, rf"{root}\.pdf\OpenWithProgids", 0, winreg.KEY_READ | winreg.KEY_WRITE
            ) as key:
                winreg.DeleteValue(key, PROGID)
        except OSError:
            pass
        refresh_shell()
        return True
    except OSError:
        return False


# --------------------------------------------------------------------------- #
# 查询
# --------------------------------------------------------------------------- #
def registered_command(root: str = CLS_ROOT) -> str:
    return _read_default(rf"{root}\SystemFileAssociations\.pdf\shell\{VERB}\command")


def has_menu(root: str = CLS_ROOT) -> bool:
    """右键菜单这个 verb 是否存在（不校验路径）。"""
    return bool(registered_command(root))


def is_registered(root: str = CLS_ROOT) -> bool:
    """菜单存在，且指向当前这个 exe / 源码入口。"""
    return registered_command(root) == launcher_command()


def set_registered(flag: bool, root: str = CLS_ROOT) -> bool:
    return register(root) if flag else unregister(root)
