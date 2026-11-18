"""把文件夹版打成「一个 exe 的安装程序」。

用法：:

    python installer\\build_setup.py            # 用现有 dist 产物
    python installer\\build_setup.py --rebuild  # 先用 build_onedir.spec 重新打包，再建安装程序

产物：``dist_installer\\发票打印助手_安装程序.exe``（约 45MB，双击即装，免管理员）。

它做的三件事：
1. 把 ``dist_onedir\\发票打印助手``（或 ``dist\\发票打印助手``）压成 payload.zip；
2. 用 PyInstaller 把 ``installer/setup_main.py`` + payload 打成一个 windowed 单文件 exe；
3. 校验程序名与 ``app/config.py`` 一致 —— 改名字时不会两边对不上。
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
INSTALLER_DIR = ROOT / "installer"
PAYLOAD = INSTALLER_DIR / "payload.zip"
SETUP_NAME = "发票打印助手_安装程序"

DIST_CANDIDATES = [
    ROOT / "dist_onedir" / "发票打印助手",
    ROOT / "dist" / "发票打印助手",
]


def read_config_names() -> dict:
    """从 app/config.py 里读出程序名，避免安装程序与实际程序脱节。"""
    text = (ROOT / "app" / "config.py").read_text("utf-8")
    names = {}
    for key in ("PROGRAM_NAME", "UI_NAME", "APP_NAME"):
        m = re.search(rf'^{key}\s*=\s*"([^"]+)"', text, re.M)
        if m:
            names[key] = m.group(1)
    return names


def check_setup_names(expected: dict) -> list:
    """安装程序里写死的名字必须与 config.py 一致。"""
    text = (INSTALLER_DIR / "setup_main.py").read_text("utf-8")
    problems = []
    for key, value in expected.items():
        m = re.search(rf'^\s*{key}\s*=\s*"([^"]*)"', text, re.M)
        if not m:
            problems.append(f"setup_main.py 缺少 {key}")
        elif m.group(1) != value:
            problems.append(f"{key} 不一致：setup_main.py={m.group(1)!r}，config.py={value!r}")
    return problems


def find_app_dir() -> Path:
    for path in DIST_CANDIDATES:
        if (path / "发票打印助手.exe").is_file() and (path / "_internal").is_dir():
            return path
    raise SystemExit(
        "找不到打包好的文件夹版。先跑：\n"
        "    python -m PyInstaller --noconfirm --clean build_onedir.spec\n"
        "或者加 --rebuild 让本脚本代跑。"
    )


def make_payload(app_dir: Path) -> int:
    """把文件夹版压进 payload.zip（根目录就是 exe 所在层）。"""
    if PAYLOAD.is_file():
        PAYLOAD.unlink()
    total = 0
    with zipfile.ZipFile(PAYLOAD, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
        for path in sorted(app_dir.rglob("*")):
            if path.is_file():
                zf.write(path, path.relative_to(app_dir))
                total += 1
        zf.writestr("使用说明.txt", READ_ME)
    return total


READ_ME = """发票打印助手 —— 使用说明

1. 把 PDF 发票拖到桌面快捷方式（或 exe）图标上，自动拼版并打印。
2. 想先看效果，直接双击打开，拖文件进窗口，点「打印待打」。
3. 拼版参数默认：A4 纵向、每张纸上下两张、页边距 0.51cm、缩放 95.5%。

加速（可选）
    把 SumatraPDF.exe（约 6MB，便携版即可）放进本目录，程序会自动改走矢量打印：
    每张纸送进打印队列的数据从约 30MB 降到几 KB，出纸更快、红章更保真。
    不装也能正常用，只是走位图兼容模式。

数据位置
    配置、打印历史、运行日志：%APPDATA%\\InvoicePrintAssistant
    卸载不会删除这个目录，重装后历史记录还在。

卸载
    设置 → 应用 → 发票打印助手，或运行本目录下的「卸载 发票打印助手.bat」。
"""


def rebuild_app() -> None:
    cmd = [sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean",
           str(ROOT / "build_onedir.spec")]
    print("重新打包文件夹版 …")
    subprocess.run(cmd, cwd=str(ROOT), check=True)


def build_setup_exe() -> Path:
    dist = ROOT / "dist_installer"
    work = ROOT / "build_installer"
    cmd = [
        sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean",
        "--onefile", "--windowed",
        "--name", SETUP_NAME,
        "--icon", str(ROOT / "assets" / "icon.ico"),
        "--add-data", f"{PAYLOAD};.",
        "--distpath", str(dist),
        "--workpath", str(work),
        "--specpath", str(work),
        "--exclude-module", "PySide6",
        "--exclude-module", "PyQt5",
        "--exclude-module", "PyQt6",
        "--exclude-module", "PIL",
        "--exclude-module", "numpy",
        str(INSTALLER_DIR / "setup_main.py"),
    ]
    print("构建安装程序 …")
    subprocess.run(cmd, cwd=str(ROOT), check=True)
    exe = dist / f"{SETUP_NAME}.exe"
    if not exe.is_file():
        raise SystemExit("构建失败：没有生成安装程序 exe")
    return exe


def main(argv: list) -> int:
    expected = read_config_names()
    problems = check_setup_names(expected)
    if problems:
        print("名字校验失败：")
        for p in problems:
            print("  -", p)
        return 1
    print(f"名字校验通过：{expected.get('PROGRAM_NAME')}")

    if "--rebuild" in argv:
        rebuild_app()

    app_dir = find_app_dir()
    print(f"源目录：{app_dir}")
    count = make_payload(app_dir)
    print(f"payload.zip：{count} 个文件，{PAYLOAD.stat().st_size / 1048576:.1f} MB")

    exe = build_setup_exe()
    print(f"\n完成：{exe}（{exe.stat().st_size / 1048576:.1f} MB）")
    print("自检（不改系统）：")
    print(f"    {exe} --dry-run --target %TEMP%\\ipa_selftest")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
