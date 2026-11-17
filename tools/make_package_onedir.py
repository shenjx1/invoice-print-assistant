"""把文件夹版（onedir）打包成可以直接发给同事的 zip。

用法：
    python tools/make_package_onedir.py

产物：``dist_onedir/发票打印助手.zip``，解压后是一个同名文件夹，
里面有 exe、_internal 运行库、使用说明、安装脚本。
"""
from __future__ import annotations

import os
import shutil
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

from app.config import PROGRAM_NAME  # noqa: E402
from make_package import normalize_bat, normalize_txt  # noqa: E402

DIST = Path(os.environ.get("ONEDIR_DIST") or (ROOT / "dist_onedir"))
PKG = ROOT / "package"
TXT = "使用说明.txt"
BAT_UNLOCK = "解除锁定.bat"
BAT_INSTALL = "安装到本机.bat"


def build() -> int:
    app_dir = DIST / PROGRAM_NAME
    exe = app_dir / f"{PROGRAM_NAME}.exe"
    if not exe.is_file():
        print(f"[x] 找不到 {exe}，先跑 build_onedir.spec 打包")
        return 1
    if not (app_dir / "_internal").is_dir():
        print("[x] 缺少 _internal 目录，这不是完整的文件夹版")
        return 1

    normalize_txt(PKG / TXT)
    normalize_bat(PKG / BAT_UNLOCK)
    normalize_bat(PKG / BAT_INSTALL)

    for name in (TXT, BAT_UNLOCK, BAT_INSTALL):
        shutil.copy2(PKG / name, app_dir / name)

    zip_path = DIST / f"{PROGRAM_NAME}.zip"
    zip_path.unlink(missing_ok=True)

    count = 0
    total = 0
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
        for path in sorted(app_dir.rglob("*")):
            if path.is_file():
                arc = f"{PROGRAM_NAME}/{path.relative_to(app_dir).as_posix()}"
                zf.write(path, arcname=arc)
                count += 1
                total += path.stat().st_size

    with zipfile.ZipFile(zip_path) as zf:
        bad = zf.testzip()
        if bad:
            print(f"[x] zip 校验失败：{bad}")
            return 1
        names = zf.namelist()
        print(f"\n[ok] {zip_path}  ({zip_path.stat().st_size / 1024 / 1024:.1f} MB)")
        print(f"     原始 {total / 1024 / 1024:.1f} MB / {count} 个文件")
        for must in (f"{PROGRAM_NAME}/{PROGRAM_NAME}.exe", f"{PROGRAM_NAME}/{TXT}",
                     f"{PROGRAM_NAME}/{BAT_INSTALL}"):
            print(f"     {'OK ' if must in names else '缺失'} {must}")
    return 0


if __name__ == "__main__":
    sys.exit(build())
