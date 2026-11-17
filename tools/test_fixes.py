"""1.2.2 修复项自测：打印中重复提示 / 禁止清空 / 多页合计 / 配置容错 / 历史可靠性。

对应 2026-11-04 全链路复验发现的 8 个问题，逐条给出「改前会失败、改后应通过」的断言。
打印函数被替换成空实现（可控制耗时），不会真出纸。
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import pymupdf as fitz  # noqa: E402
from PIL import Image  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from app import style  # noqa: E402
from app.config import CONFIG_FILE, Settings, app_dir, desktop_dir  # noqa: E402
import app.config as config_mod  # noqa: E402
from app.extract import _Word, _invoice_no, _pick_column, extract_fields  # noqa: E402
import app.gdi as gdi_mod  # noqa: E402
import app.history as history_mod  # noqa: E402
import app.worker as worker_mod  # noqa: E402

OUT = ROOT / "build_test"
history_mod.HISTORY_FILE = OUT / "fix_history.json"

# 日志也写到临时文件：否则自测产生的「历史记录 +1」会混进真实 run.log，
# 排查「记录去哪了」时会看到一堆对不上的行
import app.ui as ui_mod  # noqa: E402

ui_mod.LOG_FILE = OUT / "fix_run.log"

HEAD_X = [30, 225, 290, 320, 352, 430, 490, 540]
HEADS = ["项目名称", "规格型号", "单位", "数量", "单价", "金额", "税率/征收率", "税额"]


def make_pdf(path: Path, *, no="01417798", qty="1", price="1280.00",
             amount="1280.00", rate="6%", marks=True, price_size=8.0,
             unit="千克", spec="AFCONA-2035", name="*专项化学用品*分散剂") -> Path:
    """按真实数电票的列结构画一张票；marks=False 时去掉发票号码/价税合计（像合同）。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    doc = fitz.open()
    page = doc.new_page(width=595.28, height=396.85)

    def text(x, y, s, size=8):
        font = "helv" if s.isascii() else "china-s"
        try:
            page.insert_text((x, y), s, fontsize=size, fontname=font)
        except Exception:
            page.insert_text((x, y), s.encode("ascii", "ignore").decode() or "-",
                             fontsize=size, fontname="helv")

    if marks:
        text(360, 38, f"发票号码：253270000000{no}", 8)
        text(30, 120, "购买方名称：某某科技有限公司")

    top = 140
    page.draw_rect(fitz.Rect(24, top, 583, top + 20 + 3 * 16), width=0.8)
    page.draw_line(fitz.Point(24, top + 20), fitz.Point(583, top + 20), width=0.8)
    for x, h in zip(HEAD_X, HEADS):
        text(x, top + 14, h, 7.5)
    y = top + 36
    text(30, y, name)
    text(230, y, spec)
    text(292, y, unit)
    text(322, y, qty)
    # 单价右对齐到「金额」列之前：长小数会向左溢出一大截（真实票就是这样）
    width = fitz.get_text_length(price, fontname="helv", fontsize=price_size)
    text(max(120.0, 396.0 - width), y, price, price_size)
    text(435, y, amount)
    text(492, y, rate)
    text(542, y, "0.00")

    if marks:
        text(30, top + 20 + 3 * 16 + 30, "价税合计（大写）：壹仟贰佰捌拾元整")
    doc.save(str(path))
    doc.close()
    return path


results: list = []


def check(title: str, ok: bool, detail: str = "") -> bool:
    print(f"{'OK  ' if ok else 'FAIL'} {title}" + (f"  ({detail})" if detail else ""))
    results.append(ok)
    return ok


def wait_until(app, pred, timeout: float = 20.0) -> bool:
    start = time.time()
    while time.time() - start < timeout:
        app.processEvents()
        if pred():
            return True
        time.sleep(0.05)
    return False


def reset_history(win) -> None:
    win.history.records.clear()
    win.history.save()


def fake_print(delay: float = 0.0):
    def run(**kwargs):
        if delay:
            time.sleep(delay)
        return len(kwargs["sheets"]) * max(1, int(kwargs.get("copies", 1)))
    return run


def main() -> int:
    OUT.mkdir(exist_ok=True)
    CONFIG_FILE.unlink(missing_ok=True)
    history_mod.HISTORY_FILE.unlink(missing_ok=True)

    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    app.setStyleSheet(style.build_qss(app_dir() / "ui"))

    # ================================================================== 1 列归位
    print("\n— 1 单价列：右对齐长数字不能被丢掉")
    cols = {"name": (24, 220), "spec": (220, 300), "qty": (300, 326),
            "price": (334, 398), "amount": (398, 470)}
    # 中心点 328 落在 326~334 的夹缝里（旧逻辑直接丢掉），右边缘 356 在单价列内
    spilled = _Word(300.0, 200.0, 356.0, 210.0, "72.5663716814159")
    check("溢出数字按右边缘归到「单价」列", _pick_column(spilled, cols) == "price",
          str(_pick_column(spilled, cols)))
    inside = _Word(340.0, 200.0, 380.0, 210.0, "1280.00")
    check("正常落在列内的数字仍然归本列", _pick_column(inside, cols) == "price",
          str(_pick_column(inside, cols)))
    outside = _Word(500.0, 200.0, 520.0, 210.0, "备注")
    check("表外文字不硬塞进任何列", _pick_column(outside, cols) is None,
          str(_pick_column(outside, cols)))

    long_price = make_pdf(OUT / "fix_long_price.pdf", price="1234.5663716814159",
                          amount="1234.57")
    f = extract_fields(str(long_price))
    check("长小数单价能取到票面值", f.price_base is not None
          and abs(f.price_base - 1234.5663717) < 0.001, str(f.price_base))
    check("不再提示「已用金额÷数量折算」", "金额÷数量" not in (f.note or ""), f.note)
    check("含税单价 = 票面 × 1.06", f.unit_price is not None
          and abs(f.unit_price - 1234.5663717 * 1.06) < 0.02, f.unit_price_text)

    # ================================================================== 2 小数数量
    print("\n— 2 数量：小数不能四舍五入成整数")
    for raw, want in (("0.5", "0.5"), ("2.5", "2.5"), ("12.25", "12.25"), ("300", "300")):
        pdf = make_pdf(OUT / f"fix_qty_{raw}.pdf", qty=raw)
        fq = extract_fields(str(pdf))
        check(f"数量 {raw} → {want}", fq.qty_text == want, fq.qty_text)

    # ================================================================== 3 子目录
    print("\n— 3 拖文件夹：子文件夹里的 PDF 也要打")
    folder = OUT / "fix_folder"
    if folder.exists():
        import shutil
        shutil.rmtree(folder)
    make_pdf(folder / "top.pdf", no="01418001")
    make_pdf(folder / "sub" / "mid.pdf", no="01418002")
    make_pdf(folder / "sub" / "deep" / "low.pdf", no="01418003")
    (folder / "sub" / "readme.txt").write_text("不是 PDF", encoding="utf-8")

    worker_mod.print_sheets = fake_print()
    win = MainWindow_().new()
    win.settings.auto_print = False
    win.confirm_duplicates = False
    win.on_paths_dropped([str(folder)])
    names = sorted(inv.path.name for inv in win.invoices.values())
    check("递归收到 3 个 PDF（含两层子目录）", len(win.invoices) == 3, str(names))
    check("子目录里的文件确实进来了",
          {"top.pdf", "mid.pdf", "low.pdf"} == set(names), str(names))
    check("非 PDF 文件被忽略", all(n.endswith(".pdf") for n in names))
    win.close()

    # ================================================================== 4 双击开界面
    print("\n— 4 拖图标打印后再双击 exe：应打开主界面")
    pdf = make_pdf(OUT / "fix_headless.pdf", no="01418010")
    win = MainWindow_(headless=True).new()
    win.settings.auto_print = True
    win.confirm_duplicates = False
    win.start_headless([str(pdf)])
    wait_until(app, lambda: win.invoices and next(iter(win.invoices.values())).status == "已打印")
    check("无界面实例仍在跑（卡片还在）", win.headless and win.mini is not None)
    win.show_main_ui()          # 第二次双击 exe 走的是这条路
    app.processEvents()
    check("切回主界面：headless 关闭", win.headless is False)
    check("主窗口可见", win.isVisible())
    check("恢复「关掉最后一个窗口就退出」", app.quitOnLastWindowClosed() is True,
          str(app.quitOnLastWindowClosed()))
    win.close()

    # 4b 真实单实例管道：第二次双击发的就是 {"files": [], "show": true}
    print("\n— 4b 走真实命名管道（模拟第二个进程双击）")
    from PySide6.QtNetwork import QLocalSocket
    import main as main_mod

    win2 = MainWindow_(headless=True).new()
    win2.settings.auto_print = True
    win2.confirm_duplicates = False
    # 用独立的管道名：正式版可能正开着，别把测试消息投给它
    main_mod.SERVER_NAME = f"LehuaPurchasePrintAssistant.test.{os.getpid()}"
    server = main_mod.serve_single_instance(win2)
    check("单实例管道已监听", server.isListening())
    win2.start_headless([str(pdf)])
    wait_until(app, lambda: win2.invoices
               and next(iter(win2.invoices.values())).status == "已打印")
    check("此时仍是小卡片模式（主界面没开）", win2.headless is True)

    sock = QLocalSocket()
    sock.connectToServer(main_mod.SERVER_NAME)
    connected = sock.waitForConnected(1000)
    sock.write(json.dumps({"files": [], "show": True}).encode("utf-8"))
    sock.flush()
    sock.waitForBytesWritten(500)
    ack = b""
    deadline = time.time() + 4
    while time.time() < deadline and not ack:
        app.processEvents()
        if sock.waitForReadyRead(100):
            ack = bytes(sock.readAll())
    sock.disconnectFromServer()
    check("管道连接成功", connected)
    check("服务器回执 ok", ack.strip() == b"ok", repr(ack))
    wait_until(app, lambda: win2.headless is False, timeout=3)
    check("收到 show 后切回主界面", win2.headless is False and win2.isVisible())
    win2.close()

    # ================================================================== 5 打印中关窗
    print("\n— 5 打印中关窗口：不能崩，也不能丢记录")
    worker_mod.print_sheets = fake_print(delay=1.0)
    win = MainWindow_().new()
    win.settings.auto_print = False
    win.confirm_duplicates = False
    win.on_paths_dropped([str(pdf)])
    inv = next(iter(win.invoices.values()))
    reset_history(win)
    win.show()
    app.processEvents()
    win._submit_invoice(inv)
    app.processEvents()
    win.close()                       # 第一次点 ×
    app.processEvents()
    check("第一次关窗被拦下（改为打完再退）", win._close_when_idle is True)
    check("窗口没有立即关闭（还在打）", win.isVisible())
    check("打印线程仍在跑，没有被撕掉", win._still_printing())
    done = wait_until(app, lambda: not win._still_printing(), timeout=15)
    check("这一批正常打完", done)
    check("打完后写进历史（1 条）", len(win.history.records) == 1,
          str([(r.invoice_no, r.status) for r in win.history.records]))
    closed = wait_until(app, lambda: not win.isVisible(), timeout=5)
    check("打完自动关闭窗口", closed)

    # ================================================================== 6 份数
    print("\n— 6 份数：张数要含份数")
    worker_mod.print_sheets = fake_print()
    win = MainWindow_().new()
    win.settings.auto_print = False
    win.confirm_duplicates = False
    win.copies_spin.setValue(3)
    app.processEvents()
    win.on_paths_dropped([str(pdf)])
    inv = next(iter(win.invoices.values()))
    check("份数 3 → 列表显示 3 张纸（1 张版 × 3 份）", inv.total_sheets == 3,
          str(inv.total_sheets))
    reset_history(win)
    win._submit_invoice(inv)
    wait_until(app, lambda: inv.status == "已打印", timeout=15)
    check("打完后出纸张数 = 3", inv.printed_sheets == 3, str(inv.printed_sheets))
    win.close()

    # ================================================================== 7 无打印机
    print("\n— 7 没有打印机：也要留一条失败记录")
    win = MainWindow_().new()
    win.settings.auto_print = False
    win.confirm_duplicates = False
    win.on_paths_dropped([str(pdf)])
    inv = next(iter(win.invoices.values()))
    reset_history(win)
    win.current_printer = lambda: ""
    submitted = win._submit_invoice(inv)
    app.processEvents()
    check("不提交作业", submitted is False)
    check("行状态 = 打印失败", inv.status == "打印失败", inv.status)
    check("历史记录 1 条（状态打印失败）",
          len(win.history.records) == 1
          and win.history.records[0].status == "打印失败",
          str([(r.invoice_no, r.status) for r in win.history.records]))
    win.close()

    # ================================================================== 8 非发票
    print("\n— 8 非发票 PDF：不能算出荒谬单价")
    contract = make_pdf(OUT / "fix_contract.pdf", marks=False, no="",
                        price="99800000.00", amount="99800000.00", rate="13%")
    fc = extract_fields(str(contract))
    check("合同不会给出单价", fc.unit_price is None, fc.unit_price_text)
    check("备注说明「不像发票」", "不像发票" in (fc.note or ""), fc.note)
    check("也不会瞎猜发票号", fc.invoice_no == "", fc.invoice_no)

    check("纯文本里没有「发票号码」时不再抓 8 位数字",
          _invoice_no("合同金额 12345678 元，账号 6222021234567890123") == "",
          _invoice_no("合同金额 12345678 元，账号 6222021234567890123"))
    check("20 位数电票号码仍能兜底",
          _invoice_no("某处 25327000000001417798 一行") == "01417798",
          _invoice_no("某处 25327000000001417798 一行"))

    # ================================================================== 9 导出目录
    print("\n— 9 导出 CSV：默认落到真正的桌面")
    win = MainWindow_().new()
    reset_history(win)
    win.history.records.append(history_mod.PrintRecord(
        when="2026-11-04 10:00:00", invoice_no="01417798", material="分散剂",
        spec="AFCONA-2035", qty="0.5", unit_price="￥82.00", status="已打印"))
    win.history.save()
    captured = {}
    import app.ui as ui_mod
    original = ui_mod.QFileDialog.getSaveFileName

    def fake_dialog(parent, caption, directory, filters, *args, **kwargs):
        captured["path"] = directory
        return "", ""

    ui_mod.QFileDialog.getSaveFileName = staticmethod(fake_dialog)
    try:
        win.export_history()
    finally:
        ui_mod.QFileDialog.getSaveFileName = original
    default_parent = Path(captured.get("path", "")).parent
    check("默认目录就是系统桌面", default_parent == desktop_dir(),
          f"{default_parent} vs {desktop_dir()}")
    check("该目录真实存在（不是不存在的 %USERPROFILE%\\Desktop）",
          default_parent.is_dir(), str(default_parent))
    win.close()

    # ========================================================== 10 打印中再次拖同票
    print("\n— 10 第一张还在打印时，再次拖入同一发票：必须提示重复")
    worker_mod.print_sheets = fake_print(delay=1.0)
    dup_pdf = make_pdf(OUT / "fix_active_dup.pdf", no="01418020")
    win = MainWindow_().new()
    win.settings.auto_print = True
    win.confirm_duplicates = True
    asked = []
    win._ask_duplicates = lambda hits: (asked.append(hits), False)[1]
    before = len(win.history.records)
    win.on_paths_dropped([str(dup_pdf)])
    started = wait_until(app, lambda: any(
        i.status == "打印中" for i in win.invoices.values()), 5)
    win.on_paths_dropped([str(dup_pdf)])
    check("第一张确实还在打印", started)
    check("打印中重复拖入会提示", bool(asked), f"提示次数={len(asked)}")
    check("提示原因是当前列表已有同号",
          bool(asked) and "当前列表" in asked[0][0][1],
          asked[0][0][1] if asked else "—")
    check("选择跳过后只提交第一次",
          sum(1 for i in win.invoices.values() if i.status != "已跳过") == 1,
          str([i.status for i in win.invoices.values()]))
    wait_until(app, lambda: not win._still_printing(), 10)
    check("只新增一条历史记录", len(win.history.records) == before + 1,
          f"{before} → {len(win.history.records)}")
    win.close()

    # ================================================================ 11 打印中清空
    print("\n— 11 打印中清空列表：应拒绝清空，且两张都要留历史")
    worker_mod.print_sheets = fake_print(delay=1.0)
    clear_a = make_pdf(OUT / "fix_clear_a.pdf", no="01418021")
    clear_b = make_pdf(OUT / "fix_clear_b.pdf", no="01418022")
    win = MainWindow_().new()
    win.settings.auto_print = True
    win.confirm_duplicates = False
    before = len(win.history.records)
    win.on_paths_dropped([str(clear_a), str(clear_b)])
    busy = wait_until(app, lambda: win._still_printing(), 5)
    win.clear_queue()
    check("打印中清空被拒绝", busy and len(win.invoices) == 2
          and win.table.rowCount() == 2,
          f"busy={busy}, invoices={len(win.invoices)}, rows={win.table.rowCount()}")
    wait_until(app, lambda: not win._still_printing(), 12)
    check("两张发票都留下了历史", len(win.history.records) == before + 2,
          f"{before} → {len(win.history.records)}")
    win.close()

    # ============================================================== 12 多页明细合计
    print("\n— 12 多页发票：明细分列在两页，数量和单价要合并")
    page1 = make_pdf(OUT / "fix_multi_p1.pdf", no="01418030", qty="2",
                     price="100.00", amount="200.00")
    page2 = make_pdf(OUT / "fix_multi_p2.pdf", no="01418030", qty="3",
                     price="100.00", amount="300.00")
    multi = fitz.open()
    for page in (page1, page2):
        with fitz.open(page) as source:
            multi.insert_pdf(source)
    multi_path = OUT / "fix_multi_page.pdf"
    multi.save(str(multi_path))
    multi.close()
    fm = extract_fields(str(multi_path))
    check("跨页数量合计为 5", fm.qty_text == "5", fm.qty_text)
    check("跨页含税单价仍为 ￥106.00", fm.unit_price_text == "￥106.00",
          fm.unit_price_text)

    # ================================================================ 13 损坏配置
    print("\n— 13 配置字段损坏：逐项回退默认值，不能启动崩溃")
    bad_config = OUT / "fix_bad_config.json"
    bad_config.write_text(json.dumps({
        "copies": "not-a-number", "render_dpi": -1, "paper": [],
        "auto_print": "yes", "pad_mode": "???",
        "spec": {"cols": 9, "rows": "x", "scale_pct": 1000, "margin_cm": 99},
    }), encoding="utf-8")
    original_config = config_mod.CONFIG_FILE
    config_mod.CONFIG_FILE = bad_config
    try:
        settings = Settings.load()
        check("份数回退为 1", settings.copies == 1, str(settings.copies))
        check("打印分辨率回退为 300", settings.render_dpi == 300, str(settings.render_dpi))
        check("纸张回退为 A4", settings.paper == "A4", settings.paper)
        check("开关回退为默认布尔值", settings.auto_print is True, str(settings.auto_print))
        check("版式回退为默认 1×2",
              (settings.spec.cols, settings.spec.rows) == (1, 2),
              f"{settings.spec.cols}×{settings.spec.rows}")
        check("缩放回退为 95.5", settings.spec.scale_pct == 95.5,
              str(settings.spec.scale_pct))
        started_ok = True
        try:
            bad_win = MainWindow_().new()
            bad_win.close()
        except Exception as exc:
            started_ok = False
            check("损坏配置下界面仍能启动", False, repr(exc))
        if started_ok:
            check("损坏配置下界面仍能启动", True)
    finally:
        config_mod.CONFIG_FILE = original_config

    # ============================================================ 14 历史清空/原子写
    print("\n— 14 历史：写失败不能假报成功，成功写入不留临时文件")
    blocker = OUT / "fix_history_blocker"
    blocker.write_text("not a directory", encoding="utf-8")
    h = history_mod.History(path=blocker / "history.json")
    h.records = [history_mod.PrintRecord(invoice_no="88888888", status="已打印")]
    cleared = h.clear()
    check("写盘失败时 clear 返回 False", cleared is False, str(cleared))
    check("写盘失败时记录恢复，不出现界面空、磁盘有",
          len(h.records) == 1, str(len(h.records)))
    check("留下写入失败原因", "写入失败" in h.last_error, h.last_error[:50])

    win = MainWindow_().new()
    win.history.path = blocker / "history.json"
    win.history.records = [history_mod.PrintRecord(invoice_no="77777777", status="已打印")]
    toasts = []
    win._toast = lambda text, level="info": toasts.append((text, level))
    original_question = ui_mod.QMessageBox.question
    ui_mod.QMessageBox.question = staticmethod(lambda *a, **k: ui_mod.QMessageBox.Yes)
    try:
        win.clear_history()
    finally:
        ui_mod.QMessageBox.question = original_question
    check("界面显示清空失败而不是成功",
          bool(toasts) and toasts[-1][1] == "error"
          and "失败" in toasts[-1][0], str(toasts[-1] if toasts else ""))
    check("失败后历史记录仍保留", len(win.history.records) == 1,
          str(len(win.history.records)))
    win.close()

    atomic_path = OUT / "fix_atomic_history.json"
    atomic_path.unlink(missing_ok=True)
    ha = history_mod.History(path=atomic_path)
    check("原子写入成功", ha.add(history_mod.PrintRecord(invoice_no="66666666")))
    leftovers = list(atomic_path.parent.glob(f"{atomic_path.name}.*.tmp"))
    check("写入后没有残留临时文件", not leftovers, str(leftovers))

    # ================================================================ 15 GDI 绘制失败
    print("\n— 15 打印绘制失败：必须抛错，不能继续记成已打印")
    original_gdi = gdi_mod.gdi32

    class BadGDI:
        SetStretchBltMode = staticmethod(lambda *args: 4)
        StretchDIBits = staticmethod(lambda *args: 0)

    class GoodGDI:
        SetStretchBltMode = staticmethod(lambda *args: 4)
        StretchDIBits = staticmethod(lambda *args: 2)

    img = Image.new("RGB", (2, 2), "white")
    try:
        gdi_mod.gdi32 = BadGDI
        try:
            gdi_mod.draw_image(0, img, 0, 0, 2, 2)
            failed_loudly = False
        except OSError as exc:
            failed_loudly = "绘制打印页面失败" in str(exc)
        check("绘制返回 0 时抛错", failed_loudly)
        gdi_mod.gdi32 = GoodGDI
        try:
            gdi_mod.draw_image(0, img, 0, 0, 2, 2)
            good_ok = True
        except Exception:
            good_ok = False
        check("正常绘制不误报", good_ok)
    finally:
        gdi_mod.gdi32 = original_gdi

    # ---------------------------------------------------------------- 汇总
    print()
    total = len(results)
    passed = sum(1 for r in results if r)
    if passed == total:
        print(f"全部通过：{total} 项（列归位 / 小数数量 / 子目录 / 双击开界面 / "
              f"关窗等待 / 份数 / 无打印机记录 / 非发票 / 导出目录 / "
              f"打印中重复提示 / 打印中禁止清空 / 多页合计 / 损坏配置 / "
              f"历史原子写 / GDI 绘制失败）")
    else:
        print(f"失败 {total - passed} 项 / 共 {total} 项")
    return 0 if passed == total else 1


class MainWindow_:
    """延迟导入 + 统一参数，省得每处重复。"""

    def __init__(self, headless: bool = False) -> None:
        self.headless = headless

    def new(self):
        from app.ui import MainWindow
        return MainWindow(headless=self.headless)


if __name__ == "__main__":
    sys.exit(main())
