# 打印清晰度与送印位深

程序里有两条出纸路线：**矢量**（拼成 A4 PDF 交给打印引擎）和**位图**（逐页栅格化后
用 GDI 贴到打印机 DC）。清晰度与位深只影响位图路线，矢量路线不涉及栅格化。

## 一、参数

| 位置 | 内容 |
|---|---|
| `app/config.py` `Settings.render_dpi` | 默认 `300`，读取时校验 `integer(raw, 300, 150, 600)` |
| `app/config.py` `Settings.gray` | 默认 `True`（8 位灰度送印） |
| `app/ui.py` | 「打印清晰度」下拉 `200 / 300 / 600 dpi`、「送印色彩」灰度开关，改动即存 `config.json` |
| `app/worker.py` `PrintBatch` | `render_dpi` / `gray` 由界面带入工作线程 |

## 二、渲染链路

```python
# app/engine.py  _print_raster()
dpi_x = gdi.get_caps(hdc, LOGPIXELSX) or 300     # 设备实际 DPI
page_w = gdi.get_caps(hdc, HORZRES)              # 设备像素宽
cells  = grid_cells(page_w, page_h, cols, rows, margin, gap, order)

# app/engine.py  _draw_page()
dpi = effective_render_dpi(render_dpi, dpi_x)    # 设置值、设备值、600 三者取小，再夹在 150~600
img = render_page(ref.path, ref.index, dpi=dpi, rotate=rot, gray=gray)
if img.size != target:                           # target = 版式算出的设备像素矩形
    img = img.resize(target, Image.LANCZOS)
gdi.draw_image(hdc, img, x, y, w, h)             # StretchDIBits 贴图

# app/engine.py  render_page()
zoom = dpi / PT_PER_INCH                         # pt → 像素
pix  = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom),
                       colorspace=fitz.csGRAY if gray else fitz.csRGB, alpha=False)
```

PDF 先按 `dpi` 栅格化，再缩放到版式算出的落纸矩形，最后用 `StretchDIBits` 贴到打印机 DC。
版式（1×2、边距 0.51cm、缩放 95.5%）完全由 `grid_cells` / `fit_page` 决定，与分辨率无关。

每页会往 `run.log` 写一行，出纸清晰度有争议时不用再猜是哪一档生效：

```
渲染 inv_2024001.pdf 第 1 页：设置 300dpi → 实际 300dpi（设备 600dpi，灰度），源图 1123×794 像素，贴图 1123×794
```

### 为什么是「取小」而不是「取大」

写成 `max(render_dpi, min(device, 600))` 时，设备 600dpi 上把清晰度设成 300，
`max(300, 600) = 600` —— 设置形同虚设，白烧 CPU 和内存，纸上没有任何增益。
`effective_render_dpi()` 取 `min(设置, 设备, 600)` 并夹在 150～600：
设备 600 选 300 就是 300；设备 203（针式/热敏）就是 203；设置 120 抬到 150；设置 900 压到 600。

渲染分辨率与设备分辨率的关系：设备 600dpi、设置 300dpi 时，源图按 300dpi 生成，
再由 `StretchDIBits` 拉伸到 600dpi 的设备矩形 —— 信息量是 300dpi，落纸位置由版式计算。

## 三、送印位深

`app/gdi.py` 里有两条 DIB 载荷，共用 `_blit()`：

| 路线 | 位深 | 载荷函数 | 说明 |
|---|---|---|---|
| 灰度（默认） | 8 位 + 256 级调色板 | `gray_dib()` | 每像素 1 字节，黑白激光机数据量约为彩色的 1/4 |
| 彩色 | 24 位 BGR | `color_dib()` | 发票无透明通道，比 32 位 BGRA 少 1/4 |

两者都按 GDI 要求做「每行 4 字节对齐」，宽度不足时补零（灰度补白、彩色补黑），
补出来的列会被拉伸忽略，不影响出纸。8 位 DIB **必须**带调色板，否则 GDI 会把像素
当随机颜色解释 —— 这一点有回归测试守着。

## 四、实测（同一张电子发票，1×2 版式）

| | 600dpi 彩色 | 300dpi 灰度 |
|---|---|---|
| 渲染耗时 | 0.65 秒/页 | 0.08 秒/页 |
| 整页位图 | 62.6 MB | 15.7 MB |
| 每票送印 | 54 MB（32bpp） | 13.6 MB（8bpp） |

## 五、验证

```bash
python tools/test_dpi.py
```

32 项断言，覆盖：

1. **实际 DPI 判定**：设备 600/设置 300 → 300；设备 203 → 203；设置 120 → 150；设置 900 → 600。
2. **DIB 载荷**：8 位调色板 256 级、24 位无 alpha、宽度 999 时 stride 1000/3000（4 字节对齐）、
   24 位相对 32 位省 25%。
3. **渲染尺寸**：A4（595.276×841.89pt）在 300dpi 下 = 2480×3508 像素。
4. **端到端**：打到 `Microsoft Print to PDF`，页面仍是 A4 纵向、有内容，日志记录「实际 300dpi」。
