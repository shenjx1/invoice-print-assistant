"""把 dist 里的 exe 连同说明文档打成一个可以直接发给同事的 zip。

用法：
    python tools/make_package.py

做四件事（可重复运行，不会把已经转好码的文件转坏）：

1. 把 ``package/使用说明.txt`` 统一成 UTF-8 + BOM + CRLF（记事本双击才不乱码）；
2. 把 ``package/解除锁定.bat`` 统一成 GBK + CRLF（cmd 默认代码页 936，存 UTF-8 会乱码）；
3. 从 ``dist`` 复制最新的 exe 进来；
4. 打成 ``dist/发票打印助手.zip``，最外层套一个同名文件夹，解压不会散落一地。

zip 用 Python 的 zipfile 写，中文文件名会带 UTF-8 标志位，
Windows 资源管理器、7-Zip、WinRAR 解压都不会变成方块字。
"""

from __future__ import annotations

import shutil
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.config import PROGRAM_NAME  # noqa: E402
PKG = ROOT / "package"
DIST = ROOT / "dist"

TXT = "使用说明.txt"
BAT = "解除锁定.bat"


def normalize_txt(path: Path) -> None:
    raw = path.read_bytes()
    text = raw.decode("utf-8-sig")          # 已经有 BOM 也能正常读
    path.write_bytes(b"\xef\xbb\xbf" + text.replace("\r\n", "\n").replace("\n", "\r\n").encode("utf-8"))


def normalize_bat(path: Path) -> None:
    raw = path.read_bytes()
    try:
        text = raw.decode("utf-8")          # 源码里是 UTF-8
        if "\ufffd" in text:
            raise UnicodeDecodeError("utf-8", raw, 0, 1, "replacement char")
    except UnicodeDecodeError:
        text = raw.decode("gbk")            # 已经是 GBK 了，重写一遍只为统一换行
    path.write_bytes(text.replace("\r\n", "\n").replace("\n", "\r\n").encode("gbk"))


def build() -> int:
    exe = DIST / f"{PROGRAM_NAME}.exe"
    if not exe.is_file():
        print(f"[x] 找不到 {exe}，先跑 build_exe.bat 打包")
        return 1

    for name in (TXT, BAT):
        if not (PKG / name).is_file():
            print(f"[x] package 目录里缺少 {name}")
            return 1

    normalize_txt(PKG / TXT)
    normalize_bat(PKG / BAT)

    target = PKG / exe.name
    if not target.is_file() or target.stat().st_size != exe.stat().st_size:
        shutil.copy2(exe, target)
        print(f"    复制 exe：{exe.name}（{exe.stat().st_size / 1024 / 1024:.1f} MB）")
    else:
        print(f"    exe 已是最新：{exe.name}")

    files = [exe.name, TXT, BAT]
    zip_path = DIST / f"{PROGRAM_NAME}.zip"
    if zip_path.exists():
        zip_path.unlink()

    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
        for name in files:
            zf.write(PKG / name, arcname=f"{PROGRAM_NAME}/{name}")

    with zipfile.ZipFile(zip_path) as zf:
        bad = zf.testzip()
        if bad:
            print(f"[x] zip 校验失败：{bad}")
            return 1
        print(f"\n[ok] {zip_path}  （{zip_path.stat().st_size / 1024 / 1024:.1f} MB）")
        for info in zf.infolist():
            flag = "UTF-8" if info.flag_bits & 0x800 else "ASCII"
            print(f"     {info.filename:<50} {info.file_size:>12,} 字节  [{flag}]")
    return 0


if __name__ == "__main__":
    sys.exit(build())
