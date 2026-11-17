"""字段提取自测：覆盖正常票、多行明细、旧表头、无星号、无文字层、坏文件。

用法：python tools/test_extract.py
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import pymupdf as fitz  # noqa: E402

from app.extract import (  # noqa: E402
    _Word, _first_name_cell, _project_name, _strip_unit, extract_fields,
)

OUT = ROOT / "build_test" / "extract_cases"
HEAD_W = 595.28
HEAD_H = 396.85

XS = {"name": 30, "spec": 225, "unit": 288, "qty": 318,
      "price": 356, "amount": 425, "rate": 483, "tax": 536}


def make_invoice(path: Path, no: str = "25327000000001417798",
                 rows: list | None = None, header: str = "项目名称",
                 text_layer: bool = True, unit: str = "次",
                 unit_x: int | None = None) -> Path:
    """按真实数电票的列结构画一张票；rows 每项 (名称, 规格, 数量, 单价, 金额, 税额)。

    ``unit_x`` 用来把「单位」摆得更靠近「规格型号」——真实票上单位格会被并进
    规格列，程序必须把单位削掉。
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = rows or [("*信息技术服务*技术服务费", "V1.0", "1", "1280.00", "1280.00", "76.80")]
    doc = fitz.open()
    page = doc.new_page(width=HEAD_W, height=HEAD_H)
    red = (0.72, 0.05, 0.10)

    def text(x, y, s, size=8):
        font = "helv" if str(s).isascii() else "china-s"
        page.insert_text((x, y), str(s), fontsize=size, color=(0, 0, 0), fontname=font)

    if text_layer:
        text(200, 38, "电子发票（增值税专用发票）", 14)
        text(360, 38, f"发票号码：{no}")
        text(30, 120, "购买方名称：某某科技有限公司")

        top = 140
        heads = [header, "规格型号", "单位", "数量", "单价", "金额", "税率/征收率", "税额"]
        for key, head in zip(("name", "spec", "unit", "qty", "price", "amount", "rate", "tax"), heads):
            text(XS[key], top + 14, head, 7.5)
        for i, row in enumerate(rows):
            y = top + 36 + i * 16
            name, spec, qty, price, amount, tax = row[:6]
            rate = row[6] if len(row) > 6 else "6%"
            text(XS["name"], y, name)
            text(XS["spec"], y, spec)
            text(unit_x if unit_x is not None else XS["unit"], y, unit)
            text(XS["qty"], y, qty)
            text(XS["price"], y, price)
            text(XS["amount"], y, amount)
            text(XS["rate"], y, rate)
            text(XS["tax"], y, tax)
        total_y = top + 150
        text(30, total_y, "价税合计（大写）：壹仟贰佰捌拾元整", 9)
        text(300, total_y, "合计  1280.00  76.80", 9)

    page.draw_rect(fitz.Rect(12, 12, 583, 385), color=red, width=1.0)
    doc.save(str(path))
    doc.close()
    return path


def main() -> int:
    fails = []

    # 1) 正常单行：单价 1280.00 × (1 + 6%) = 1356.80
    p = make_invoice(OUT / "normal.pdf")
    f = extract_fields(str(p))
    checks = [
        ("发票号", f.invoice_no, "01417798"),
        # 材料名称 = 项目名称去掉 *xx* 前缀后的具体名称
        ("材料名称", f.material, "技术服务费"),
        ("材料名称（拼规格）", f.material_full, "技术服务费 V1.0"),
        ("历史记录材料名称", f.as_row()["material"], "技术服务费 V1.0"),
        ("规格型号", f.spec, "V1.0"),
        ("数量", f.qty_text, "1"),
        ("税率", f.tax_rate_text, "6%"),
        ("单价", f.unit_price_text, "￥1,356.80"),
    ]
    for label, got, want in checks:
        if got != want:
            fails.append(f"正常票 {label}={got!r}，期望 {want!r}")
    if "(1 + 6%)" not in f.formula_text:
        fails.append(f"正常票 计算过程={f.formula_text!r}，期望含 (1 + 6%)")

    # 1b) 数量 > 1：单价列是单件价，含税单价 = 单价 × (1 + 税率)，
    #     不再用「(单价+税额)/数量」那种会被数量摊薄的口径
    p1b = make_invoice(OUT / "qty_gt_1.pdf", rows=[
        ("*信息技术服务*技术服务费", "V1.0", "3", "100.00", "300.00", "39.00", "13%"),
    ])
    f1b = extract_fields(str(p1b))
    if f1b.qty_text != "3":
        fails.append(f"数量>1 数量={f1b.qty_text}，期望 3")
    if f1b.tax_rate_text != "13%":
        fails.append(f"数量>1 税率={f1b.tax_rate_text!r}，期望 13%")
    # 100 × 1.13 = 113.00（旧口径会算成 (100+39)/3 = 46.33）
    if f1b.unit_price_text != "￥113.00":
        fails.append(f"数量>1 单价={f1b.unit_price_text}，期望 ￥113.00")

    # 1c) 税率写成小数 0.06 也要认
    p1c = make_invoice(OUT / "rate_decimal.pdf", rows=[
        ("*信息技术服务*技术服务费", "V1.0", "2", "200.00", "400.00", "24.00", "0.06"),
    ])
    f1c = extract_fields(str(p1c))
    if f1c.tax_rate_text != "6%":
        fails.append(f"小数税率={f1c.tax_rate_text!r}，期望 6%")
    if f1c.unit_price_text != "￥212.00":
        fails.append(f"小数税率 单价={f1c.unit_price_text}，期望 ￥212.00")

    # 1d) 免税：没有税率 -> 单价按不含税，并给出备注
    p1d = make_invoice(OUT / "tax_free.pdf", rows=[
        ("*信息技术服务*技术服务费", "V1.0", "1", "50.00", "50.00", "0.00", "免税"),
    ])
    f1d = extract_fields(str(p1d))
    if f1d.tax_rate_text != "—":
        fails.append(f"免税 税率={f1d.tax_rate_text!r}，期望 —")
    if f1d.unit_price_text != "￥50.00":
        fails.append(f"免税 单价={f1d.unit_price_text}，期望 ￥50.00")
    if "税率" not in f1d.note:
        fails.append(f"免税应备注税率缺失：{f1d.note!r}")

    # 2) 两行明细：数量按全票合计，单价按数量加权后再乘税率
    p2 = make_invoice(OUT / "two_rows.pdf", rows=[
        ("*信息技术服务*技术服务费", "V1.0", "2", "500.00", "1000.00", "60.00"),
        ("*信息技术服务*软件维护费", "V2.1", "3", "100.00", "300.00", "18.00"),
    ])
    f2 = extract_fields(str(p2))
    if f2.qty_text != "5":
        fails.append(f"两行明细 数量={f2.qty_text}，期望 5")
    # 加权单价 (500×2 + 100×3)/5 = 260；260 × 1.06 = 275.60
    if f2.unit_price_text != "￥275.60":
        fails.append(f"两行明细 单价={f2.unit_price_text}，期望 ￥275.60")
    if f2.spec != "V1.0":
        fails.append(f"两行明细 规格={f2.spec}，期望取首行 V1.0")

    # 3) 旧表头「货物或应税劳务名称」
    p3 = make_invoice(OUT / "old_header.pdf", header="货物或应税劳务名称")
    f3 = extract_fields(str(p3))
    if f3.material != "技术服务费":
        fails.append(f"旧表头 材料名称={f3.material!r}，期望 技术服务费")

    # 4) 名称里没有星号对 -> 退回整格文字
    p4 = make_invoice(OUT / "no_star.pdf", rows=[("技术服务费", "V1.0", "1", "100", "100", "6")])
    f4 = extract_fields(str(p4))
    if f4.material != "技术服务费":
        fails.append(f"无星号 材料名称={f4.material!r}，期望 技术服务费")
    if not f4.note:
        fails.append("无星号时应给出提取备注")

    # 5) 没有文字层（扫描件）：不崩、全空、有说明
    p5 = make_invoice(OUT / "no_text.pdf", text_layer=False)
    f5 = extract_fields(str(p5))
    if f5.has_text or f5.material or f5.invoice_no:
        fails.append(f"无文字层应返回空值，实际 {f5.as_row()}")
    if "文字层" not in f5.note:
        fails.append(f"无文字层说明缺失：{f5.note!r}")
    if any(v != "—" for v in f5.as_row().values()):
        fails.append(f"无文字层应全部显示 —：{f5.as_row()}")

    # 6) 坏文件：不抛异常
    bad = OUT / "broken.pdf"
    bad.write_bytes(b"%PDF-1.4 not really")
    f6 = extract_fields(str(bad))
    if f6.has_text or not f6.note:
        fails.append(f"坏文件应返回空值+说明，实际 note={f6.note!r}")

    # 7) 发票号码只有 8 位 / 带空格
    p7 = make_invoice(OUT / "short_no.pdf", no="25327000000099991234")
    f7 = extract_fields(str(p7))
    if f7.invoice_no != "99991234":
        fails.append(f"20 位发票号取后 8 位={f7.invoice_no!r}，期望 99991234")

    # 8) 规格型号不取单位：真实票上「单位」格会被并进规格列
    p8 = make_invoice(OUT / "spec_with_unit.pdf", unit="千克", unit_x=250, rows=[
        ("*专项化学用品*分散剂", "AFCONA-2035", "300", "82.00", "24600.00", "3198.00", "13%"),
    ])
    f8 = extract_fields(str(p8))
    if f8.spec != "AFCONA-2035":
        fails.append(f"规格型号带单位 规格={f8.spec!r}，期望 AFCONA-2035")

    # 8b) 规格列本来是空的，被单位占满 -> 清空并给出备注
    p8b = make_invoice(OUT / "spec_only_unit.pdf", unit="kg", unit_x=250, rows=[
        ("*颜料*钛白粉", "", "4500", "5.60", "25200.00", "3276.00", "13%"),
    ])
    f8b = extract_fields(str(p8b))
    if f8b.spec:
        fails.append(f"规格列只有单位时应为空，实际 {f8b.spec!r}")
    if "规格型号" not in f8b.note:
        fails.append(f"规格只有单位时应备注未识别到规格型号：{f8b.note!r}")

    # 8c) 只削独立单位词，型号里自带的字样不动
    unit_cases = [
        ("AFCONA-2035 千克", "AFCONA-2035"),
        ("RF-106 KG", "RF-106"),
        ("kg", ""),
        ("25KG内涂装圆铁", "25KG内涂装圆铁"),
        ("V1.0", "V1.0"),
        ("XY-100千克", "XY-100"),
        ("公斤/桶 YB-201A 江苏常州 45000元/吨", "公斤/桶 YB-201A 江苏常州 45000元/吨"),
    ]
    for src, want in unit_cases:
        got = _strip_unit(src)
        if got != want:
            fails.append(f"单位剥离 {src!r} -> {got!r}，期望 {want!r}")

    # 9) 材料名称取「项目名称去掉 *xx* 前缀」后的部分，再拼规格型号
    name_cases = [
        ("*信息技术服务*技术服务费", "技术服务费"),
        ("*专项化学用品*分散剂", "分散剂"),
        ("*颜料*钛白粉", "钛白粉"),
        ("*化学试剂* 无水乙醇", "无水乙醇"),
        ("技术服务费", "技术服务费"),                      # 没有前缀：整格保留
        ("*信息技术服务*", "信息技术服务"),                  # 只有前缀：退回简称
        ("*化学试剂*分散剂 *化学试剂*颜料", "分散剂 颜料"),   # 多行明细拼成一格
        ("", ""),
    ]
    for src, want in name_cases:
        got = _project_name(src)
        if got != want:
            fails.append(f"项目名称去前缀 {src!r} -> {got!r}，期望 {want!r}")

    # 9b) 规格型号为空：材料名称就只是项目名称，不能留下多余空格
    p9 = make_invoice(OUT / "no_spec.pdf", rows=[
        ("*颜料*钛白粉", "", "10", "5.00", "50.00", "6.50", "13%"),
    ])
    f9 = extract_fields(str(p9))
    if f9.material != "钛白粉":
        fails.append(f"规格为空 材料名称={f9.material!r}，期望 钛白粉")
    if f9.material_full != "钛白粉":
        fails.append(f"规格为空 拼接结果={f9.material_full!r}，期望 钛白粉")

    # 9c) 项目名称只有 *xx*：退回简称并给出备注
    p9c = make_invoice(OUT / "name_only_prefix.pdf", rows=[
        ("*信息技术服务*", "V1.0", "1", "100", "100", "6"),
    ])
    f9c = extract_fields(str(p9c))
    if f9c.material != "信息技术服务":
        fails.append(f"只有前缀 材料名称={f9c.material!r}，期望 信息技术服务")
    if "税收分类简称" not in f9c.note:
        fails.append(f"只有前缀时应备注：{f9c.note!r}")

    # 9d) 项目名称折行：续行只含名称、没有数字，要接成完整名称
    def w(x, y, s):
        return _Word(x, y, x + 8 * len(s), y + 8, s)

    wrapped = [
        {"name": [w(10, 160, "*化学试剂助剂*流变助")],
         "spec": [w(225, 160, "50KG圆铁桶")],
         "qty": [w(318, 160, "200")], "price": [], "amount": []},
        {"name": [w(10, 173, "剂 DeuRheo 201P")],
         "spec": [], "qty": [], "price": [], "amount": []},
        {"name": [w(10, 190, "*化学试剂助剂*颜料")],
         "spec": [], "qty": [w(318, 190, "5")], "price": [], "amount": []},
    ]
    cell = _first_name_cell(wrapped)
    if cell != "*化学试剂助剂*流变助剂 DeuRheo 201P":
        fails.append(f"折行名称拼接={cell!r}，期望 *化学试剂助剂*流变助剂 DeuRheo 201P")
    if _project_name(cell) != "流变助剂 DeuRheo 201P":
        fails.append(f"折行名称去前缀={_project_name(cell)!r}，"
                     f"期望 流变助剂 DeuRheo 201P")

    if fails:
        print("失败：")
        for f in fails:
            print("  -", f)
        return 1
    print("全部通过：字段映射 / 缺失兜底 / 坏文件容错 正常")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
