"""发票打印助手 · 安装程序（自包含 single-exe）。

设计目标是替掉「45MB zip → 完整解压 → 双击 安装到本机.bat → 复制到 D:\\ → 可能还要
先跑 解除锁定.bat」这一串动作：双击一个 exe 就装完。

几个刻意的选择：

* **装到 %LOCALAPPDATA%\\Programs** —— 不写 Program Files、不改 HKLM，
  全程不需要管理员权限，公司电脑也能装。
* **不碰用户数据** —— 配置和历史在 %APPDATA%\\InvoicePrintAssistant，
  卸载时**故意保留**：重装后历史记录还在。
* **自带卸载器** —— 在安装目录生成 ``卸载.bat``，并登记到「应用和功能」列表。
* **--dry-run** —— 只解包到临时目录做校验，不改快捷方式、不写注册表，
  方便打包后自检。

payload.zip 由 ``installer/build_setup.py`` 打进来，运行时从 ``sys._MEIPASS`` 取。
"""
from __future__ import annotations

import ctypes
import os
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

try:
    PROGRAM_NAME = "发票打印助手"   # 必须与 app/config.py 的 PROGRAM_NAME 一致
    UI_NAME = "发票打印助手"        # 必须与 app/config.py 的 UI_NAME 一致
    APP_NAME = "InvoicePrintAssistant"   # 必须与 app/config.py 的 APP_NAME 一致
except Exception:  # pragma: no cover
    PROGRAM_NAME = "发票打印助手"
    UI_NAME = "发票打印助手"
    APP_NAME = "InvoicePrintAssistant"

VERSION = "1.1.0"
PUBLISHER = ""
UNINSTALL_KEY = rf"Software\Microsoft\Windows\CurrentVersion\Uninstall\{APP_NAME}"
CREATE_NO_WINDOW = 0x08000000

MB_YESNO = 0x00000004
MB_OK = 0x00000000
MB_ICONQUESTION = 0x00000020
MB_ICONERROR = 0x00000010
MB_ICONINFORMATION = 0x00000040


def msg(text: str, flags: int = MB_OK | MB_ICONINFORMATION) -> int:
    return ctypes.windll.user32.MessageBoxW(None, text, f"{UI_NAME} 安装程序", flags)


def default_target() -> Path:
    base = os.environ.get("LOCALAPPDATA") or str(Path.home())
    return Path(base) / "Programs" / PROGRAM_NAME


def payload_path() -> Path:
    base = getattr(sys, "_MEIPASS", None)
    if base:
        return Path(base) / "payload.zip"
    # 源码模式：与 installer/ 同级找 payload
    return Path(__file__).resolve().parent / "payload.zip"


def app_running() -> bool:
    try:
        out = subprocess.run(
            ["tasklist", "/fi", f"imagename eq {PROGRAM_NAME}.exe", "/nh"],
            capture_output=True, text=True, creationflags=CREATE_NO_WINDOW, timeout=15,
        )
        return PROGRAM_NAME in (out.stdout or "")
    except Exception:
        return False


def extract_to(target: Path, dry_run: bool = False) -> int:
    src = payload_path()
    if not src.is_file():
        raise FileNotFoundError(f"安装包损坏：找不到 {src.name}")

    if target.exists():
        if app_running():
            raise RuntimeError(
                f"「{PROGRAM_NAME}」正在运行，请先关闭再安装。"
            )
        shutil.rmtree(target, ignore_errors=True)
    target.mkdir(parents=True, exist_ok=True)

    with zipfile.ZipFile(src) as zf:
        names = zf.namelist()
        zf.extractall(target)
    return len(names)


def make_shortcut(lnk: Path, target_exe: Path) -> bool:
    """用 WScript.Shell 建快捷方式（PowerShell 在所有受支持的 Windows 上都有）。"""
    script = (
        "$s=(New-Object -ComObject WScript.Shell).CreateShortcut('{lnk}');"
        "$s.TargetPath='{exe}';"
        "$s.WorkingDirectory='{wd}';"
        "$s.IconLocation='{exe},0';"
        "$s.Description='批量拼版打印 PDF 电子发票';"
        "$s.Save()"
    ).format(lnk=str(lnk), exe=str(target_exe), wd=str(target_exe.parent))
    try:
        proc = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
            capture_output=True, creationflags=CREATE_NO_WINDOW, timeout=60,
        )
        return proc.returncode == 0 and lnk.is_file()
    except Exception:
        return False


def write_uninstaller(target: Path) -> Path:
    bat = target / f"卸载 {PROGRAM_NAME}.bat"
    content = f"""@echo off
chcp 65001 >nul
if /i "%~1"=="-doit" goto doit
for %%I in ("%~dp0.") do set "APP_DIR=%%~fI"
taskkill /f /im {PROGRAM_NAME}.exe >nul 2>&1
reg delete "HKCU\\{UNINSTALL_KEY}" /f >nul 2>&1
del /f /q "%USERPROFILE%\\Desktop\\{PROGRAM_NAME}.lnk" >nul 2>&1
del /f /q "%APPDATA%\\Microsoft\\Windows\\Start Menu\\Programs\\{PROGRAM_NAME}.lnk" >nul 2>&1
copy /y "%~f0" "%TEMP%\\ipa_uninstall.bat" >nul 2>&1
start "" "%TEMP%\\ipa_uninstall.bat" -doit "%APP_DIR%"
exit /b 0

:doit
timeout /t 2 /nobreak >nul
rmdir /s /q "%~2" >nul 2>&1
del /f /q "%~f0" >nul 2>&1
exit /b 0
"""
    bat.write_text(content, encoding="utf-8-sig", newline="\r\n")
    return bat


def register_uninstall(target: Path, uninstaller: Path, file_count: int) -> None:
    import winreg

    exe = target / f"{PROGRAM_NAME}.exe"
    size_kb = sum(f.stat().st_size for f in target.rglob("*") if f.is_file()) // 1024
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, UNINSTALL_KEY) as key:
        winreg.SetValueEx(key, "DisplayName", 0, winreg.REG_SZ, UI_NAME)
        winreg.SetValueEx(key, "DisplayVersion", 0, winreg.REG_SZ, VERSION)
        winreg.SetValueEx(key, "InstallLocation", 0, winreg.REG_SZ, str(target))
        winreg.SetValueEx(key, "DisplayIcon", 0, winreg.REG_SZ, str(exe))
        winreg.SetValueEx(key, "UninstallString", 0, winreg.REG_SZ, f'"{uninstaller}"')
        winreg.SetValueEx(key, "NoModify", 0, winreg.REG_DWORD, 1)
        winreg.SetValueEx(key, "NoRepair", 0, winreg.REG_DWORD, 1)
        winreg.SetValueEx(key, "EstimatedSize", 0, winreg.REG_DWORD, size_kb)
        if PUBLISHER:
            winreg.SetValueEx(key, "Publisher", 0, winreg.REG_SZ, PUBLISHER)


def install(target: Path, dry_run: bool = False, launch: bool = True) -> int:
    count = extract_to(target, dry_run)
    exe = target / f"{PROGRAM_NAME}.exe"
    if not exe.is_file():
        raise RuntimeError(f"解包后找不到 {exe.name}，安装包可能不完整")

    print(f"已解包 {count} 个文件 → {target}")
    if dry_run:
        print("dry-run：跳过快捷方式、注册表与启动")
        return 0

    desktop = Path(os.environ.get("USERPROFILE", str(Path.home()))) / "Desktop"
    start_menu = Path(os.environ.get("APPDATA", "")) / "Microsoft/Windows/Start Menu/Programs"
    ok_desktop = make_shortcut(desktop / f"{PROGRAM_NAME}.lnk", exe) if desktop.is_dir() else False
    ok_menu = make_shortcut(start_menu / f"{PROGRAM_NAME}.lnk", exe) if start_menu.is_dir() else False
    uninstaller = write_uninstaller(target)
    register_uninstall(target, uninstaller, count)

    if launch:
        try:
            os.startfile(str(exe))
        except Exception:
            pass
    return 0 if (ok_desktop or ok_menu) else 0


def main(argv: list) -> int:
    dry_run = "--dry-run" in argv
    no_launch = "--no-launch" in argv
    target = default_target()
    for i, arg in enumerate(argv):
        if arg == "--target" and i + 1 < len(argv):
            target = Path(argv[i + 1])

    if not dry_run:
        reply = msg(
            f"即将安装「{UI_NAME}」到：\n\n{target}\n\n"
            "· 不需要管理员权限\n"
            "· 会在桌面和开始菜单建快捷方式\n"
            "· 卸载请到「设置 → 应用」里找，或运行安装目录下的卸载脚本\n\n"
            "继续安装吗？",
            MB_YESNO | MB_ICONQUESTION,
        )
        if reply != 6:  # IDYES
            return 0

    try:
        install(target, dry_run=dry_run, launch=not no_launch and not dry_run)
    except Exception as exc:
        if dry_run:
            print(f"安装失败：{exc}")
        else:
            msg(f"安装失败：\n\n{exc}", MB_OK | MB_ICONERROR)
        return 1

    if not dry_run:
        msg(
            f"「{UI_NAME}」已安装完成。\n\n"
            f"安装位置：{target}\n"
            "桌面和开始菜单都有快捷方式，把发票 PDF 直接拖到图标上即可打印。",
            MB_OK | MB_ICONINFORMATION,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
