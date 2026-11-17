"""从发票 PDF 里自动提取历史记录要用的字段。

字段与规则（按需求逐条落地）：

* **材料名称**：明细表「项目名称」列，**去掉开头的 ``*xx*`` 税收分类简称前缀**
  后剩下的具体名称（``*信息技术服务*技术服务费`` → ``技术服务费``），
  然后再拼上规格型号得到历史记录里显示的最终值（``技术服务费 V1.0``）。
* **规格型号**：明细表「规格型号」列的内容。
* **数量**：明细表「数量」列的值（多行明细时求和）。
* **税率**：明细表「税率/征收率」列，``6%`` 解析成 ``0.06``。
* **单价（含税）**：``票面单价 × (1 + 对应税率)``，见下方 :data:`PRICE_FORMULA`。
  票面没有单价列时先按 ``金额 ÷ 数量`` 折算成单价，再乘税率。
* **发票号**：``发票号码`` 的后 8 位。

提取策略是「按坐标找表格列」而不是硬啃正则：
先用 PyMuPDF 的 words（每个词带 x0/y0/x1/y1）定位表头行
（``项目名称 / 规格型号 / 数量 / 单价 / 税额`` 这些小标题共同处于同一 y 区间），
再用表头的 x 坐标切出列区间，最后把表头下方、合计行上方的词按行归位。
这样不依赖具体的换行/空格方式，也能容忍列宽差异。

每一层都有兜底：找不到表头就退回全文正则；找不到文字层（扫描件）就返回空值并在
``note`` 里说明，调用方据此显示"—"而不是崩掉。
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence, Tuple

try:  # PyMuPDF >= 1.24
    import pymupdf as fitz  # type: ignore
except ImportError:  # pragma: no cover
    import fitz  # type: ignore

# 含税单价的计算口径（取票面「单价」列 × 对应税率）：
#   "inclusive"（默认）  单价 × (1 + 税率)   得到含税单价
#   "tax"              单价 × 税率          只取单位税额（一般用不到）
PRICE_FORMULA = "inclusive"

# 表头别名（真实发票的写法有好几种）
ALIASES: Dict[str, Tuple[str, ...]] = {
    "name": ("项目名称", "货物或应税劳务名称", "货物或应税劳务、服务名称",
             "货物名称", "服务名称", "应税劳务名称"),
    "spec": ("规格型号", "规格"),
    "qty": ("数量",),
    "price": ("单价",),
    "amount": ("金额",),
    "tax_rate": ("税率/征收率", "税率", "征收率"),
    "tax": ("税额",),
}

STOP_WORDS = ("合计", "价税合计", "小写", "大写")

NUM_RE = re.compile(r"-?\d[\d,，]*(?:\.\d+)?")
RATE_RE = re.compile(r"(\d+(?:\.\d+)?)\s*[%％]")
STAR_RE = re.compile(r"\*([^*\n]{1,40})\*")
CJK_RE = re.compile(r"[\u4e00-\u9fff]")
INVOICE_NO_RE = re.compile(r"发票号码[^0-9]{0,8}([0-9][0-9 \u00a0]{5,40})")
INVOICE_NO_RE2 = re.compile(r"(?:No|NO|№)[:：]?\s*([0-9]{8,20})")
# 一张真发票总会有其中至少一个词；用来把合同、报价单之类挡在外面
INVOICE_MARKS = re.compile(r"发票号码|价税合计|销售方|购买方|开票日期|发票代码|发票专用章")


# --------------------------------------------------------------------------- #
# 结果结构
# --------------------------------------------------------------------------- #
@dataclass
class InvoiceFields:
    invoice_no: str = ""
    material: str = ""
    spec: str = ""
    qty: Optional[float] = None
    tax_rate: Optional[float] = None      # 0.06 这样的税率
    price_base: Optional[float] = None    # 票面单价（多行时按数量加权）
    unit_price: Optional[float] = None    # 含税单价 = price_base × (1 + tax_rate)
    note: str = ""          # 提取过程中的异常/缺失说明
    has_text: bool = True   # 是否有文字层（扫描件为 False）
    is_invoice: bool = True # 内容是否像发票；扫描件没有文字层，一律按 True 放行

    # ------------------------------------------------------------ 展示用
    @property
    def material_full(self) -> str:
        """历史记录里的「材料名称」= 项目名称（已去掉 ``*xx*`` 前缀）+ 规格型号。

        规格型号为空时就是项目名称本身，两边都空时返回空串（界面显示 "—"）。
        """
        parts = [(p or "").strip() for p in (self.material, self.spec)]
        return " ".join(p for p in parts if p)

    @property
    def qty_text(self) -> str:
        # 数量允许小数（0.5 吨、2.5 千克）：整数不带小数点，小数原样保留
        return _num_text(self.qty, digits=None)

    @property
    def tax_rate_text(self) -> str:
        if self.tax_rate is None:
            return "—"
        pct = self.tax_rate * 100
        body = f"{pct:.2f}".rstrip("0").rstrip(".")
        return f"{body}%"

    @property
    def unit_price_text(self) -> str:
        return _num_text(self.unit_price, digits=2, money=True)

    @property
    def formula_text(self) -> str:
        """把计算过程写清楚，显示在历史记录的悬停提示里。"""
        if self.unit_price is None:
            return ""
        base = _num_text(self.price_base, digits=2)
        if self.tax_rate is None:
            return f"单价 {base}（未识别到税率，按不含税）"
        if PRICE_FORMULA == "tax":
            return f"单价 {base} × {self.tax_rate_text} = {self.unit_price_text}"
        return (f"单价 {base} × (1 + {self.tax_rate_text})"
                f" = {self.unit_price_text}")

    def as_row(self) -> Dict[str, str]:
        return {
            "invoice_no": self.invoice_no or "—",
            # 历史记录的「材料名称」列显示 项目名称 + 规格型号
            "material": self.material_full or "—",
            "spec": self.spec or "—",
            "qty": self.qty_text,
            "tax_rate": self.tax_rate_text,
            "unit_price": self.unit_price_text,
        }


def _num_text(value: Optional[float], digits: Optional[int] = 2,
              money: bool = False) -> str:
    """数字转文字。``digits=None`` = 自动：整数不补小数，小数最多保留 4 位。"""
    if value is None:
        return "—"
    if digits is None:
        if abs(value - round(value)) < 1e-9:
            return f"{int(round(value)):,}"
        body = f"{value:,.4f}".rstrip("0").rstrip(".")
        return body
    if digits == 0 and abs(value - round(value)) < 1e-9:
        return f"{int(round(value))}"
    body = f"{value:,.{digits}f}"
    return f"￥{body}" if money else body


# --------------------------------------------------------------------------- #
# 小工具
# --------------------------------------------------------------------------- #
def _num(text: str) -> Optional[float]:
    """从 "￥1,280.00" / "1280" / "6%" 里取出数字；取不到返回 None。"""
    if not text:
        return None
    m = NUM_RE.search(text.replace(" ", ""))
    if not m:
        return None
    try:
        return float(m.group(0).replace(",", "").replace("，", ""))
    except ValueError:
        return None


def _rate(text: str) -> Optional[float]:
    """把税率格解析成小数：``6%`` → 0.06、``0.06`` → 0.06、``6`` → 0.06。

    ``免税`` / ``不征税`` / ``***`` 这类没有数值的写法返回 None。
    """
    flat = _flat(text)
    if not flat:
        return None
    m = RATE_RE.search(flat)
    if m:
        try:
            return float(m.group(1)) / 100.0
        except ValueError:
            return None
    value = _num(flat)
    if value is None:
        return None
    if value > 1:          # 写成 6 而不是 6%/0.06
        value = value / 100.0
    return round(value, 6)


def _flat(text: str) -> str:
    return (text or "").replace(" ", "").replace("\u00a0", "").replace("\u3000", "")


def _match_key(word: str) -> Optional[str]:
    """表头词 -> 字段键（按别名匹配，容忍被拆开写的 "税率 / 征收率"）。"""
    w = _flat(word)
    for key, names in ALIASES.items():
        for name in names:
            if w == name or w.startswith(name) or (name in w and len(w) <= len(name) + 2):
                return key
    return None


@dataclass
class _Word:
    x0: float
    y0: float
    x1: float
    y1: float
    text: str


def _words(page) -> List[_Word]:
    """取页面的文字（span 级，逐字符重建）。

    * 用 span 而不是 word：发票里的数字/型号常被按单字切开（"1280.00" → "1 2 8 0 . 0"），
      span 保留原始文字运行；
    * 再按字符坐标重建：只有字间距大到半个字号才算空格，避免字体回退造成的假空格。
    """
    out: List[_Word] = []
    data = page.get_text("dict")
    for block in data.get("blocks", []):
        if block.get("type") != 0:  # 0 = 文字块
            continue
        for line in block.get("lines", []):
            for span in line.get("spans", []):
                text = _span_text(span)
                if not text:
                    continue
                x0, y0, x1, y1 = span.get("bbox", (0, 0, 0, 0))
                out.append(_Word(float(x0), float(y0), float(x1), float(y1), text))
    return out


def _span_text(span) -> str:
    chars = span.get("chars") or []
    raw = (span.get("text") or "").strip()
    if not chars:
        return raw
    out = ""
    prev_right: Optional[float] = None
    for ch in chars:
        box = ch.get("bbox") or (0, 0, 0, 0)
        size = max(4.0, float(box[3]) - float(box[1]))
        if prev_right is not None and float(box[0]) - prev_right > size * 0.5:
            out += " "
        out += ch.get("c", "")
        prev_right = float(box[2])
    return re.sub(r"\s+", " ", out).strip()


def _join(ws: Sequence[_Word]) -> str:
    """按横坐标拼接同一格里的文字；只有明显间距才补空格（避免 "1 2 8 0"）。"""
    out = ""
    prev: Optional[_Word] = None
    for w in sorted(ws, key=lambda x: x.x0):
        if prev is not None:
            size = max(4.0, prev.y1 - prev.y0)
            if w.x0 - prev.x1 > size * 0.6:
                out += " "
        out += w.text
        prev = w
    return _tighten(out.strip())


def _tighten(text: str) -> str:
    """纯西文格子里把被字体拆开的碎片拼回去："V 1 . 0" → "V1.0"、"1 280.00" → "1280.00"。

    中文格子里不动（中文本来就是逐字排的，空格有含义）。
    """
    if not text or CJK_RE.search(text):
        return text
    tokens = text.split(" ")
    if len(tokens) < 2:
        return text
    if all(len(t) <= 1 for t in tokens):
        return "".join(tokens)
    out: List[str] = []
    for token in tokens:
        if out and (len(token) <= 1 or len(out[-1]) <= 1):
            out[-1] += token
        else:
            out.append(token)
    return " ".join(out)


# --------------------------------------------------------------------------- #
# 表格结构识别
# --------------------------------------------------------------------------- #
def _header_band(words: Sequence[_Word]) -> Tuple[Dict[str, _Word], float, float]:
    """找出表头行，返回 ({字段: 词}, 行顶, 行底)。

    做法：把词按 y 聚成行，选「命中的字段种类最多」的那一行。
    """
    lines: List[List[_Word]] = []
    for w in sorted(words, key=lambda x: (x.y0, x.x0)):
        if lines and abs(w.y0 - lines[-1][0].y0) <= 3.0:
            lines[-1].append(w)
        else:
            lines.append([w])

    best: Dict[str, _Word] = {}
    best_top = best_bottom = 0.0
    for line in lines:
        found: Dict[str, _Word] = {}
        for w in line:
            key = _match_key(w.text)
            if key and key not in found:
                found[key] = w
        if len(found) > len(best):
            best = found
            best_top = min(w.y0 for w in line)
            best_bottom = max(w.y1 for w in line)

    return best, best_top, best_bottom


def _columns(headers: Dict[str, _Word], page_width: float) -> Dict[str, Tuple[float, float]]:
    """把表头的 x 坐标切成列区间：左边界取表头起点，右边界取与下一列表头的中点。"""
    items = sorted(headers.items(), key=lambda kv: kv[1].x0)
    cols: Dict[str, Tuple[float, float]] = {}
    for i, (key, word) in enumerate(items):
        left = word.x0 - 2.0
        if i + 1 < len(items):
            right = (word.x1 + items[i + 1][1].x0) / 2.0
        else:
            right = page_width - 2.0
        cols[key] = (left, max(right, left + 6.0))
    return cols


def _rows_below(words: Sequence[_Word], top: float, bottom: float,
                cols: Dict[str, Tuple[float, float]]) -> List[Dict[str, List[_Word]]]:
    """把表格区域里的词按行/列归位。"""
    body = [w for w in words if w.y0 > top - 0.5 and w.y0 < bottom]
    body.sort(key=lambda w: (w.y0, w.x0))

    lines: List[List[_Word]] = []
    for w in body:
        if lines and abs(w.y0 - lines[-1][0].y0) <= 3.5:
            lines[-1].append(w)
        else:
            lines.append([w])

    rows: List[Dict[str, List[_Word]]] = []
    for line in lines:
        row: Dict[str, List[_Word]] = {k: [] for k in cols}
        for w in line:
            key = _pick_column(w, cols)
            if key is not None:
                row[key].append(w)
        if any(row.get(k) for k in ("name", "qty", "price", "amount")):
            rows.append(row)
    return rows


def _pick_column(word: _Word, cols: Dict[str, Tuple[float, float]]) -> Optional[str]:
    """判断一个词属于哪一列。

    依次尝试：
    1. **中心点**落在哪列 —— 绝大多数文字都这样；
    2. **右边缘**落在哪列 —— 数字是右对齐的，长小数会向左溢出，
       中心点掉进两列之间的夹缝里（`72.5663716814159` 就这么整段丢失过）；
    3. 与列区间**重叠最多**的那列；
    4. 离得最近、且距离不超过 40pt 的那列（否则算表外文字）。
    """
    left_, right_ = word.x0, word.x1
    center = (left_ + right_) / 2.0

    for key, (left, right) in cols.items():
        if left <= center <= right:
            return key
    for key, (left, right) in cols.items():
        if left <= right_ <= right:
            return key

    best_key: Optional[str] = None
    best_overlap = 0.0
    for key, (left, right) in cols.items():
        overlap = min(right, right_) - max(left, left_)
        if overlap > best_overlap:
            best_key, best_overlap = key, overlap
    if best_key is not None:
        return best_key

    nearest: Optional[str] = None
    nearest_gap = 40.0
    for key, (left, right) in cols.items():
        gap = min(abs(center - left), abs(center - right))
        if gap < nearest_gap:
            nearest, nearest_gap = key, gap
    return nearest


def _stop_y(words: Sequence[_Word], header_top: float) -> float:
    """合计行（"合 计"/"价税合计"）的 y，作为明细区的下边界。"""
    lines: List[List[_Word]] = []
    for w in sorted(words, key=lambda x: x.y0):
        if lines and abs(w.y0 - lines[-1][0].y0) <= 4.0:
            lines[-1].append(w)
        else:
            lines.append([w])
    for line in lines:
        top = min(w.y0 for w in line)
        if top <= header_top:
            continue
        head = "".join(_flat(w.text) for w in sorted(line, key=lambda x: x.x0))[:8]
        if head.startswith("价税合计") or head.startswith("合计"):
            return top
    return float("inf")


# --------------------------------------------------------------------------- #
# 单价 / 税率聚合
# --------------------------------------------------------------------------- #
@dataclass
class _Line:
    """一条明细行的数值快照。"""
    price: Optional[float]
    qty: Optional[float]
    rate: Optional[float]
    amount: Optional[float]


def _aggregate(rows: Sequence[Dict[str, List[_Word]]]) -> Tuple[
        Optional[float], Optional[float], bool, bool]:
    """把明细行折算成一个「票面单价 + 税率」。

    * 单价按**数量加权**（等价于 金额合计 ÷ 数量合计），单行时就是票面单价；
    * 税率按**金额加权**，单行时就是该行的税率；
    * 返回 ``(单价, 税率, 是否缺单价, 是否缺税率)``。

    缺单价时用 ``金额 ÷ 数量`` 逐行折算兜底，保证数量 > 1 的票也能算出单价。
    """
    lines: List[_Line] = []
    for row in rows:
        lines.append(_Line(
            price=_num(_join(row.get("price", []))),
            qty=_num(_join(row.get("qty", []))),
            rate=_rate(_join(row.get("tax_rate", []))),
            amount=_num(_join(row.get("amount", []))),
        ))

    pieces: List[Tuple[float, float, Optional[float]]] = []
    missing_price = False
    for line in lines:
        price = line.price
        if price is None and line.amount is not None and line.qty:
            price = line.amount / line.qty
            missing_price = True
        if price is None:
            if line.amount is not None:
                missing_price = True
            continue
        weight = line.qty if (line.qty is not None and line.qty > 0) else 1.0
        pieces.append((price, weight, line.rate))

    if not pieces:
        return None, None, missing_price, True

    weight_sum = sum(w for _, w, _ in pieces)
    base = sum(p * w for p, w, _ in pieces) / weight_sum

    taxed = [(p * w, r) for p, w, r in pieces if r is not None]
    amount_sum = sum(amount for amount, _ in taxed)
    rate = (sum(amount * r for amount, r in taxed) / amount_sum) if amount_sum else None
    missing_rate = any(r is None for _, _, r in pieces)
    return base, rate, missing_price, missing_rate


# --------------------------------------------------------------------------- #
# 主入口
# --------------------------------------------------------------------------- #
def extract_fields(path: str) -> InvoiceFields:
    """解析一个 PDF，返回历史记录需要的字段；任何异常都收敛成 note。"""
    result = InvoiceFields()
    try:
        doc = fitz.open(path)
    except Exception as exc:
        result.has_text = False
        result.note = f"无法打开：{exc}"
        return result

    try:
        if doc.needs_pass:
            result.has_text = False
            result.note = "PDF 已加密"
            return result

        full_text = "\n".join(page.get_text("text") for page in doc)
        if not full_text.strip():
            result.has_text = False
            result.note = "没有文字层（可能是扫描件）"
            return result

        result.invoice_no = _invoice_no(full_text)

        # 「像不像发票」：有发票专用字样（价税合计/销售方/发票号码…），
        # 或能读出规范的发票号码。合同、报价单这两样都没有。
        # 注意：扫描件在上一段就 return 了，is_invoice 保持 True —— 认不出文字
        # 不能当成「不是发票」，否则真发票的扫描件会被误拦。
        result.is_invoice = bool(INVOICE_MARKS.search(full_text)) or bool(result.invoice_no)

        # 每个明细页都可能有一部分行；不能只取“表头命中最多”的第一页，
        # 否则多页发票的数量和加权单价会漏掉后续页。
        rows: List[Dict[str, List[_Word]]] = []
        detail_pages = 0
        for page in doc:
            words = _words(page)
            headers, top, bottom = _header_band(words)
            if len(headers) < 2:
                continue
            detail_pages += 1
            cols = _columns(headers, page.rect.width)
            rows.extend(_rows_below(words, bottom - 0.5, _stop_y(words, top), cols))

        if not detail_pages:
            # 没有表格结构：项目名称仍可从全文里「项目名称」后的 *xx* 捞，其余留空
            result.material = _material_from_text(full_text)
            result.note = result.note or "未识别到明细表头"
            return result

        # 材料名称：明细列第一格「项目名称」去掉 *xx* 税收分类简称后的名称
        name_cell = _first_name_cell(rows)
        result.material = _project_name(name_cell) or _material_from_text(full_text)
        if name_cell and not STAR_RE.search(name_cell):
            _add_note(result, "项目名称未找到 *xx* 前缀，已取整格文字")
        elif name_cell and not _STAR_PREFIX_RE.sub("", _clean(name_cell)).strip():
            # 票面只写了 "*信息技术服务*"，没有具体货物/服务名
            _add_note(result, "项目名称只有税收分类简称，已按简称记录")

        # 带数量/单价/金额的行才算一条明细；项目名称折行的续行不计数
        item_rows = [r for r in rows
                     if any(_join(r.get(k, [])) for k in ("qty", "price", "amount"))]

        spec_cell = _join(rows[0].get("spec", [])) if rows else ""
        if not spec_cell:
            # 规格型号也可能折到同一明细的下一行
            for row in rows[1:]:
                if any(_join(row.get(k, [])) for k in ("qty", "price", "amount")):
                    break
                piece = _join(row.get("spec", []))
                if piece:
                    spec_cell = piece
                    break
        # 规格型号不取单位：单位格被并进这一列时把尾词削掉
        result.spec = _strip_unit(_clean(spec_cell))
        if not result.spec:
            _add_note(result, "未识别到规格型号")

        qty = _sum_rows(rows, "qty")
        result.qty = qty

        base, rate, missing_price, missing_rate = _aggregate(rows)
        result.price_base = base
        result.tax_rate = rate

        if base is None:
            _add_note(result, "未识别到单价")
        else:
            factor = rate if rate is not None else 0.0
            # 含税单价 = 票面单价 × (1 + 税率)
            result.unit_price = base * factor if PRICE_FORMULA == "tax" \
                else base * (1.0 + factor)
            if rate is None:
                _add_note(result, "未识别到税率，单价按不含税计算")
            if missing_price:
                _add_note(result, "未识别到单价，已用金额÷数量折算")
            elif missing_rate:
                _add_note(result, "部分明细行未识别到税率")
            if len(item_rows) > 1:
                _add_note(result, "多行明细，单价按数量加权、税率按金额加权")

        if qty is None:
            _add_note(result, "未识别到数量")

        # 合同、报价单里也有「单价/金额」字样，碰巧命中表头时算出来的数字毫无意义，
        # 这种票不进历史记录的单价列（早先一张购销合同算出了 ￥2,185,019,336.28）
        if not INVOICE_MARKS.search(full_text) and not result.invoice_no:
            result.price_base = None
            result.unit_price = None
            result.tax_rate = None
            _add_note(result, "内容不像发票（未找到发票号码/价税合计），单价未采用")
        elif result.unit_price is not None and abs(result.unit_price) >= 1e7:
            bad = result.unit_price_text
            result.price_base = None
            result.unit_price = None
            _add_note(result, f"单价数值异常（{bad}），未采用")

        if not result.invoice_no:
            _add_note(result, "未识别到发票号码")
        return result
    finally:
        try:
            doc.close()
        except Exception:
            pass


def _invoice_no(text: str) -> str:
    """发票号码的后 8 位。"""
    for regex in (INVOICE_NO_RE, INVOICE_NO_RE2):
        m = regex.search(text)
        if not m:
            continue
        digits = re.sub(r"\D", "", m.group(1))
        if len(digits) >= 8:
            return digits[-8:]
    # 最后一招：数电票号码是 20 位，常单独一行、没有「发票号码」标签。
    # 只认 20 位的，别把合同里的账号/金额尾巴当成发票号。
    runs = re.findall(r"\d{20}", text)
    if runs:
        return max(runs, key=len)[-8:]
    return ""


# 数电票「项目名称」的固定写法：``*税收分类简称*具体货物或服务名称``。
# 取名称时要把开头的这段星号前缀整段削掉（连续多段也一起削）。
_STAR_PREFIX_RE = re.compile(r"^\s*(?:\*[^*\n]{1,40}\*)+\s*")
# 多行明细被拼进同一格时，第二行往后的前缀前面带空格（"分散剂 *化学试剂*颜料"）
_STAR_SEG_RE = re.compile(r"\s+\*[^*\n]{1,40}\*")


def _first_name_cell(rows: Sequence[Dict[str, List[_Word]]]) -> str:
    """取第一条明细的「项目名称」，折行的名称要接起来。

    数电票的明细格放不下时会折成两行（``*化学试剂助剂*流变助`` /
    ``剂 DeuRheo 201P``），只取第一行会把名称截断。续行只含名称、
    没有数量/单价/金额，据此判断；遇到下一条明细（重新以 ``*xx*`` 开头）就停。
    """
    if not rows:
        return ""
    text = _join(rows[0].get("name", []))
    for row in rows[1:]:
        piece = _join(row.get("name", []))
        if not piece:
            break
        if any(_join(row.get(key, [])) for key in ("qty", "price", "amount")):
            break            # 有数字 = 已经到下一条明细
        if _STAR_PREFIX_RE.match(piece.strip()):
            break            # 重新出现 *xx* 前缀 = 新的一条明细
        # 中文折行不该补空格（"流变助" + "剂 …"），西文之间才补
        if text and CJK_RE.search(text[-1]) and CJK_RE.search(piece[0]):
            text += piece
        else:
            text = f"{text} {piece}".strip()
    return text


def _project_name(cell: str) -> str:
    """明细「项目名称」格 → 去掉 ``*xx*`` 税收分类简称前缀后的名称。

    ``*信息技术服务*技术服务费``          → ``技术服务费``
    ``*专项化学用品*分散剂``              → ``分散剂``
    ``*化学试剂*分散剂 *化学试剂*颜料``   → ``分散剂 颜料``（多行明细拼成一格）
    ``技术服务费``                        → ``技术服务费``（本来没有前缀，原样保留）
    ``*信息技术服务*``                    → ``信息技术服务``（只有简称时的兜底）
    """
    text = _clean(cell)
    if not text:
        return ""
    body = _STAR_PREFIX_RE.sub("", text).strip()
    if body:
        # 后面还夹着别的行前缀时，把它当分隔符，留下各自的具体名称
        body = _STAR_SEG_RE.sub(" ", body)
        return _clean(body)[:40]
    # 整格只有 *xx*（票面没写具体名称）：退回简称，至少不是空值
    m = STAR_RE.search(text)
    return m.group(1).strip() if m else text[:40]


def _material_from_text(text: str) -> str:
    """兜底：全文里「项目名称」标签之后第一个 ``*xx*`` 后面的名称。"""
    tail = ""
    for label in ALIASES["name"]:
        pos = text.find(label)
        if pos >= 0:
            tail = text[pos + len(label):]
            break
    if not tail:
        tail = text
    m = STAR_RE.search(tail)
    if not m:
        return ""
    rest = tail[m.end():]
    lines = rest.splitlines()
    return _project_name(lines[0] if lines else "")


def _clean(cell: str) -> str:
    return re.sub(r"\s+", " ", _tighten((cell or "").strip()))


# 明细表「单位」列不进规格型号：表头里没有「单位」这一列时，单位格的文字
# 会被归到规格型号的列区间里（真实票上就是 "AFCONA-2035 千克"、"RF-106 KG"）。
# 这里只削**独立的尾词**，型号里本来就带的数量字样（"25KG内涂装圆铁"）不动。
_UNIT_WORDS = {
    "kg", "kgs", "g", "mg", "t", "l", "ml", "cm", "mm", "m", "m2", "m3", "㎡", "m³",
    "千克", "公斤", "克", "吨", "毫升", "升", "米", "平方米", "立方米",
    "个", "只", "支", "件", "套", "台", "张", "包", "桶", "箱", "袋", "瓶",
    "卷", "组", "对", "份", "片", "块", "根", "条", "把", "捆", "盘", "盒",
    "罐", "双", "批", "次", "项",
}
# 可以紧贴着型号写的单位（"XY-100千克"）；单字单位容易误伤，只认带空格的形式
_UNIT_GLUED = ("千克", "公斤", "克", "吨", "毫升", "升", "平方米", "立方米")


def _strip_unit(text: str) -> str:
    """去掉规格型号尾巴上被串进来的单位词，返回纯型号。

    ``"AFCONA-2035 千克"`` → ``"AFCONA-2035"``；``"RF-106 KG"`` → ``"RF-106"``；
    ``"kg"`` → ``""``（这一格本来就是单位）；``"25KG内涂装圆铁"`` 保持原样。
    """
    out = (text or "").strip()
    if out.lower() in _UNIT_WORDS or out in _UNIT_WORDS:
        return ""       # 这一格整个就是单位（规格列本来是空的）
    for _ in range(3):
        before = out
        if " " in out:
            head, tail = out.rsplit(" ", 1)
            token = tail.strip(" .,，、/／()（）")
            if token.lower() in _UNIT_WORDS or token in _UNIT_WORDS:
                out = head.strip()
                continue
        for unit in _UNIT_GLUED:
            if out.endswith(unit) and len(out) > len(unit):
                prev = out[-len(unit) - 1]
                # 只在「数字/字母 + 单位」时削（XY-100千克）；
                # "…45000元/吨"、"白漆千克" 这种是正文，不动
                if prev.isascii() and prev.isalnum():
                    out = out[: -len(unit)].rstrip()
                    break
        if out == before:
            break
    return out



def _add_note(result: InvoiceFields, text: str) -> None:
    """把一条提取备注追加到 note（多条用顿号连起来），不覆盖已有说明。"""
    if text in result.note:
        return
    result.note = f"{result.note}；{text}" if result.note else text


def _sum_rows(rows: Sequence[Dict[str, List[_Word]]], key: str) -> Optional[float]:
    """某一列所有数值求和；该列没有任何数字时返回 None。"""
    values: List[float] = []
    for row in rows:
        if key not in row:
            continue
        cell = _join(row[key])
        if not cell:
            continue
        value = _num(cell)
        if value is not None:
            values.append(value)
    if not values:
        return None
    return sum(values)
