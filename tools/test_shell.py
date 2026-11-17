"""资源管理器集成自测：右键菜单 / 打开方式 的注册表读写 + 命令行参数解析。

默认只在测试用的注册表根（HKCU\\Software\\InvoicePrintTest）下增删，
不会碰真实的 .pdf 关联；加 ``--real`` 才在真实位置注册一次再删掉。
"""
from __future__ import annotations

import sys
import winreg
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import shellmenu  # noqa: E402

TEST_ROOT = r"Software\InvoicePrintTest\Classes"
VERB_PATH = rf"{TEST_ROOT}\SystemFileAssociations\.pdf\shell\{shellmenu.VERB}"
PROGID_PATH = rf"{TEST_ROOT}\{shellmenu.PROGID}"
APP_PATH = rf"{TEST_ROOT}\Applications\{shellmenu.app_id()}"


def check(title: str, ok: bool, detail: str = "") -> bool:
    print(f"{'OK  ' if ok else 'FAIL'} {title}" + (f"  ({detail})" if detail else ""))
    return ok


def main() -> int:
    passed = []

    # 1) 命令行：右键菜单多选、带引号、文件夹、非 PDF、不存在的文件
    import tempfile

    tmp = Path(tempfile.mkdtemp(prefix="inv_shell_"))
    pdf_a = tmp / "2024年11月 服务费.pdf"
    pdf_b = tmp / "a.PDF"
    folder = tmp / "发票文件夹"
    folder.mkdir()
    docx = tmp / "说明.docx"
    for f in (pdf_a, pdf_b, docx):
        f.write_bytes(b"%PDF-1.4\n%%EOF\n")

    argv = [
        f'"{pdf_a}"',
        str(pdf_b),
        str(folder),
        str(docx),
        str(tmp / "不存在.pdf"),
        "--flag",
    ]
    paths = shellmenu.paths_from_argv(argv)
    passed.append(check(
        "命令行挑出 PDF / 文件夹并去掉引号（跳过 docx、不存在的文件、开关）",
        paths == [str(pdf_a), str(pdf_b), str(folder)],
        str(paths),
    ))
    passed.append(check("命令行空参数不报错", shellmenu.paths_from_argv([]) == []))

    # 2) 启动命令：带引号、带 %1
    cmd = shellmenu.launcher_command()
    passed.append(check(
        "启动命令行包含 %1 且路径带引号",
        '"%1"' in cmd and cmd.count('"') >= 4,
        cmd,
    ))

    # 3) 注册 → 查询 → 卸载（测试根下）
    shellmenu._delete_tree(TEST_ROOT)
    passed.append(check("注册返回成功", shellmenu.register(TEST_ROOT) is True))
    passed.append(check(
        f"菜单名称为「{shellmenu.MENU_TEXT}」",
        winreg.QueryValueEx(winreg.OpenKey(winreg.HKEY_CURRENT_USER, VERB_PATH), "MUIVerb")[0]
        == shellmenu.MENU_TEXT,
    ))
    passed.append(check(
        "允许一次选中多个 PDF（MultiSelectModel=Player）",
        winreg.QueryValueEx(
            winreg.OpenKey(winreg.HKEY_CURRENT_USER, VERB_PATH), "MultiSelectModel"
        )[0] == "Player",
    ))
    passed.append(check("命令指向当前程序", shellmenu.registered_command(TEST_ROOT) == cmd))
    passed.append(check("is_registered() 为真", shellmenu.is_registered(TEST_ROOT) is True))

    for label, path in (("打开方式应用条目", APP_PATH), ("PDF 候选 ProgID", PROGID_PATH)):
        try:
            winreg.OpenKey(winreg.HKEY_CURRENT_USER, path)
            passed.append(check(f"{label}已写入", True))
        except OSError:
            passed.append(check(f"{label}已写入", False))

    # 3.5) 改名前的旧菜单项要被清掉
    import winreg as _wr

    legacy_path = rf"{TEST_ROOT}\SystemFileAssociations\.pdf\shell\{shellmenu.LEGACY_VERBS[0]}\command"
    with _wr.CreateKeyEx(_wr.HKEY_CURRENT_USER, legacy_path, 0, _wr.KEY_WRITE) as key:
        _wr.SetValueEx(key, "", 0, _wr.REG_SZ, '"C:/旧版/InvoicePrint.exe" "%1"')
    passed.append(check("能检出并清理旧版菜单项", shellmenu.cleanup_legacy(TEST_ROOT) is True))
    passed.append(check(
        "旧版菜单项已删除",
        shellmenu._read_default(legacy_path) == "",
    ))
    passed.append(check(
        "清理旧版不影响当前菜单",
        shellmenu.registered_command(TEST_ROOT) == cmd,
    ))

    passed.append(check("卸载返回成功", shellmenu.unregister(TEST_ROOT) is True))
    passed.append(check("卸载后查不到命令", shellmenu.registered_command(TEST_ROOT) == ""))
    passed.append(check(
        "卸载后 is_registered() 为假",
        shellmenu.has_menu(TEST_ROOT) is False and shellmenu.is_registered(TEST_ROOT) is False,
    ))
    try:
        winreg.OpenKey(winreg.HKEY_CURRENT_USER, APP_PATH)
        passed.append(check("应用条目已清除", False))
    except OSError:
        passed.append(check("应用条目已清除", True))
    shellmenu._delete_tree(r"Software\InvoicePrintTest")

    # 4) 可选：在真实位置注册一次，跑完还原
    if "--real" in sys.argv:
        before = shellmenu.registered_command()
        try:
            passed.append(check("真实位置注册成功", shellmenu.register() is True))
            passed.append(check("真实位置可读出命令", shellmenu.is_registered() is True))
            passed.append(check("真实位置卸载成功", shellmenu.unregister() is True))
        finally:
            if before:
                shellmenu.register()   # 原来就有的话还原（同一份内容）

    total = len(passed)
    print()
    if all(passed):
        print(f"全部通过：{total} 项（右键菜单「{shellmenu.MENU_TEXT}」/ 打开方式 / 命令行解析）")
        return 0
    print(f"失败 {passed.count(False)} / {total} 项")
    return 1


if __name__ == "__main__":
    sys.exit(main())
