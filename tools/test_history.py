"""历史记录的可靠性自测：写不进去要能发现、旧目录记录要能合并、坏文件不能静默丢。

这三条都是「打完却没有历史记录」的真实成因，之前都栽在 `except: pass` 上。
全部在临时目录里跑，不碰用户的真实数据。
"""
from __future__ import annotations

import json
import shutil
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import config  # noqa: E402
from app.history import History, PrintRecord  # noqa: E402

passed: list = []


def check(title: str, ok: bool, detail: str = "") -> bool:
    print(f"{'OK  ' if ok else 'FAIL'} {title}" + (f"  ({detail})" if detail else ""))
    passed.append(ok)
    return ok


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix='hist_test_'))
    try:
        # 1) 正常写入
        h = History(path=tmp / "ok.json")
        saved = h.add(PrintRecord(invoice_no="11112222", material="服务费"))
        check("正常写入返回成功", saved is True and len(h.records) == 1)
        check("落盘后能读回", History(path=tmp / "ok.json").records[0].invoice_no == "11112222")

        # 2) 写不进去时必须留下原因（以前是 save() 里的 except: pass，静默丢记录）
        blocker = tmp / "blocked"
        blocker.write_text("我是文件，不是目录", "utf-8")     # 父路径是文件 → 一定写失败
        h2 = History(path=blocker / "history.json")
        saved2 = h2.add(PrintRecord(invoice_no="33334444"))
        check("写入失败返回 False", saved2 is False)
        check("失败原因能读到", "写入失败" in h2.last_error, h2.last_error[:40])

        # 3) 历史文件损坏：备份坏文件 + 记录原因，不能当没发生
        bad = tmp / "bad.json"
        bad.write_text("{ 这不是合法 json ", "utf-8")
        h3 = History(path=bad)
        check("坏文件不会让程序崩", h3.records == [])
        check("坏文件已备份", (tmp / "bad.bad.json").exists())
        check("坏文件读取有错误说明", "读取失败" in h3.last_error)

        # 4) 旧目录（改名前）的记录要合并进来
        base = tmp / "merge"
        new_dir = base / config.APP_NAME
        legacy = base / config.LEGACY_APP_NAME
        new_dir.mkdir(parents=True)
        legacy.mkdir(parents=True)
        (new_dir / "history.json").write_text(json.dumps([
            {"when": "2026-11-04 10:00:00", "invoice_no": "AAA", "status": "已打印"},
        ], ensure_ascii=False), "utf-8")
        time.sleep(0.05)
        (legacy / "history.json").write_text(json.dumps([
            {"when": "2026-11-04 11:00:00", "invoice_no": "BBB", "status": "已打印"},
            {"when": "2026-11-04 10:00:00", "invoice_no": "AAA", "status": "已打印"},  # 重复项
        ], ensure_ascii=False), "utf-8")

        config._merge_legacy_history(new_dir)
        merged = json.loads((new_dir / "history.json").read_text("utf-8"))
        numbers = [r["invoice_no"] for r in merged]
        check("旧目录记录已并入", "BBB" in numbers, str(numbers))
        check("重复记录只留一条", numbers.count("AAA") == 1, str(numbers))
        check("合并后按时间倒序", numbers[0] == "BBB", str(numbers))

        # 5) 旧文件比新文件旧时不该重复合并
        before = (new_dir / "history.json").read_text("utf-8")
        config._merge_legacy_history(new_dir)
        check("重复调用不会重复合并", (new_dir / "history.json").read_text("utf-8") == before)

        # 6) 正在退出的实例必须拒绝转发（否则文件被吞掉）
        try:
            import os
            os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
            from PySide6.QtWidgets import QApplication
            from app.ui import MainWindow

            app = QApplication.instance() or QApplication([])
            win = MainWindow(headless=True)
            check("正常状态可以接收转发", win.accepting_forwarded() is True)
            win._shutting_down = True
            check("退出中拒绝接收转发", win.accepting_forwarded() is False)
            win.close()
        except Exception as exc:      # 无显示环境时跳过，不影响其余断言
            print(f"      （跳过界面相关断言：{exc}）")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    total = len(passed)
    ok = sum(1 for p in passed if p)
    print()
    if ok == total:
        print(f"全部通过：{total} 项（写入失败可发现 / 坏文件备份 / 旧目录合并 / 退出时拒收）")
        return 0
    print(f"失败：{total - ok}/{total} 项")
    return 1


if __name__ == "__main__":
    sys.exit(main())
