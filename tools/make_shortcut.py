"""生成 Windows 快捷方式（.lnk），不依赖 COM。

用法：:

    python tools/make_shortcut.py <目标exe> <快捷方式路径> [工作目录]

只写最小可用结构：LinkInfo（卷 + 本地路径，ANSI + Unicode 双份）、
WorkingDir、IconLocation。资源管理器、任务栏、拖放都认。
"""
from __future__ import annotations

import struct
import sys
from pathlib import Path

CLSID_SHELL_LINK = bytes(
    [0x01, 0x14, 0x02, 0x00, 0x00, 0x00, 0x00, 0x00,
     0xC0, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x46]
)

HAS_LINK_INFO = 0x00000002
HAS_WORKING_DIR = 0x00000010
HAS_ICON_LOCATION = 0x00000040
IS_UNICODE = 0x00000080


def _string_data(text: str) -> bytes:
    """StringData：先 2 字节字符数，再 UTF-16LE 内容（不含结尾 0）。"""
    return struct.pack("<H", len(text)) + text.encode("utf-16-le")


def build_lnk(target: Path, workdir: Path, icon: Path | None = None) -> bytes:
    target_s = str(target)
    workdir_s = str(workdir)
    icon_s = f"{icon or target},0"

    flags = HAS_LINK_INFO | HAS_WORKING_DIR | HAS_ICON_LOCATION | IS_UNICODE
    header = struct.pack("<I", 0x4C) + CLSID_SHELL_LINK
    header += struct.pack("<I", flags)
    header += struct.pack("<I", 0x00000020)          # FILE_ATTRIBUTE_ARCHIVE
    header += struct.pack("<Q", 0) * 3               # 创建/访问/修改时间
    header += struct.pack("<I", 0)                   # 文件大小
    header += struct.pack("<i", 0)                   # 图标序号
    header += struct.pack("<I", 1)                   # SW_SHOWNORMAL
    header += struct.pack("<H", 0)                   # 热键
    header += struct.pack("<H", 0)                   # 保留
    header += struct.pack("<I", 0)                   # 保留
    header += struct.pack("<I", 0)                   # 保留
    assert len(header) == 0x4C

    # ---- VolumeID：本地固定盘，卷标留空
    volume_id = struct.pack("<IIII", 17, 3, 0, 0x10) + b"\x00"

    local_ansi = target_s.encode("mbcs") + b"\x00"
    local_wide = target_s.encode("utf-16-le") + b"\x00\x00"
    suffix_ansi = b"\x00"
    suffix_wide = b"\x00\x00"

    vol_off = 0x24
    local_off = vol_off + len(volume_id)
    suffix_off = local_off + len(local_ansi)
    local_wide_off = suffix_off + len(suffix_ansi)
    suffix_wide_off = local_wide_off + len(local_wide)
    link_info_size = suffix_wide_off + len(suffix_wide)

    link_info = struct.pack(
        "<IIIIIIIII",
        link_info_size, 0x24, 0x1,
        vol_off, local_off, 0, suffix_off, local_wide_off, suffix_wide_off,
    )
    link_info += volume_id + local_ansi + suffix_ansi + local_wide + suffix_wide
    assert len(link_info) == link_info_size

    strings = _string_data(workdir_s) + _string_data(icon_s)
    terminal = struct.pack("<I", 0)                  # ExtraData 终止块
    return header + link_info + strings + terminal


def create(target: Path, lnk: Path, workdir: Path | None = None, icon: Path | None = None) -> Path:
    lnk.parent.mkdir(parents=True, exist_ok=True)
    lnk.write_bytes(build_lnk(target, workdir or target.parent, icon))
    return lnk


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print(__doc__)
        raise SystemExit(2)
    tgt = Path(sys.argv[1])
    out = Path(sys.argv[2])
    wd = Path(sys.argv[3]) if len(sys.argv) > 3 else tgt.parent
    create(tgt, out, wd)
    print(f"已生成 {out}（{out.stat().st_size} 字节）-> {tgt}")
