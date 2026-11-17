"""界面配色与样式表（浅色主题）。"""
from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw

BG = "#F2F4F8"
CARD = "#FFFFFF"
BORDER = "#E4E8F0"
TEXT = "#1F2937"
MUTED = "#6B7280"
ACCENT = "#2563EB"
ACCENT_DARK = "#1D4ED8"
OK = "#0F9D58"
WARN = "#B45309"
ERROR = "#DC2626"

QSS = f"""
#Root {{ background: {BG}; }}
#Card {{
    background: {CARD};
    border: 1px solid {BORDER};
    border-radius: 12px;
}}
#Title {{ font-size: 19px; font-weight: 600; color: #111827; }}
#Subtitle {{ font-size: 12px; color: {MUTED}; }}
#SectionTitle {{ font-size: 13px; font-weight: 600; color: #111827; }}
#Field {{ font-size: 12px; color: #4B5563; }}
#Muted {{ font-size: 12px; color: {MUTED}; }}
#Hint {{ font-size: 11px; color: #9AA3B2; }}

QComboBox, QSpinBox, QDoubleSpinBox {{
    background: #FFFFFF;
    border: 1px solid #DDE3EC;
    border-radius: 8px;
    padding: 5px 8px;
    min-height: 22px;
    selection-background-color: {ACCENT};
    selection-color: #FFFFFF;
}}
QComboBox:hover, QSpinBox:hover, QDoubleSpinBox:hover {{ border-color: #C3CDDD; }}
QComboBox:focus, QSpinBox:focus, QDoubleSpinBox:focus {{ border: 1px solid {ACCENT}; }}
QComboBox::drop-down {{ border: none; width: 20px; }}
QComboBox QAbstractItemView {{
    background: #FFFFFF;
    border: 1px solid {BORDER};
    outline: none;
    selection-background-color: #EAF1FF;
    selection-color: #1F2937;
    padding: 4px;
}}

QPushButton#Primary {{
    background: {ACCENT}; color: #FFFFFF; border: none; border-radius: 8px;
    padding: 8px 18px; font-weight: 600;
}}
QPushButton#Primary:hover {{ background: {ACCENT_DARK}; }}
QPushButton#Primary:disabled {{ background: #BFD0F2; color: #F5F8FF; }}
QPushButton#Ghost {{
    background: #FFFFFF; color: #374151; border: 1px solid #DDE3EC;
    border-radius: 8px; padding: 7px 14px;
}}
QPushButton#Ghost:hover {{ background: #F3F6FC; border-color: #C3CDDD; }}
QPushButton#Ghost:disabled {{ color: #AEB6C2; }}
QPushButton#Link {{
    background: transparent; color: {ACCENT}; border: none; padding: 2px 4px;
}}
QPushButton#Link:hover {{ color: {ACCENT_DARK}; text-decoration: underline; }}

QCheckBox {{ spacing: 8px; color: #374151; font-size: 13px; }}
QCheckBox::indicator {{ width: 15px; height: 15px; }}

QTableWidget {{
    background: {CARD};
    border: none;
    gridline-color: transparent;
    selection-background-color: #EAF1FF;
    selection-color: #1F2937;
}}
QTableWidget::item {{ padding: 2px 8px; border-bottom: 1px solid #F4F6FA; }}
QHeaderView::section {{
    background: {CARD};
    color: {MUTED};
    border: none;
    border-bottom: 1px solid #EEF1F6;
    padding: 7px 8px;
    font-size: 12px;
    font-weight: 600;
}}
QScrollBar:vertical {{ background: transparent; width: 9px; margin: 2px; }}
QScrollBar::handle:vertical {{ background: #CFD6E2; border-radius: 4px; min-height: 24px; }}
QScrollBar::handle:vertical:hover {{ background: #B7C0D0; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; width: 0; }}
QScrollBar:horizontal {{ background: transparent; height: 9px; margin: 2px; }}
QScrollBar::handle:horizontal {{ background: #CFD6E2; border-radius: 4px; min-width: 24px; }}

QStatusBar {{ background: transparent; color: {MUTED}; font-size: 12px; }}
QToolTip {{
    background: #1F2937; color: #F9FAFB; border: none;
    padding: 6px 8px; border-radius: 6px;
}}

/* 设置面板的折叠把手：竖长小标签，收起后仍留在原位当展开入口 */
#CollapseBar {{ background: transparent; }}
QToolButton#Collapse {{
    background: #FFFFFF;
    border: 1px solid {BORDER};
    border-radius: 9px;
    color: #8A93A3;
    font-size: 15px;
    padding: 0px;
}}
QToolButton#Collapse:hover {{
    background: #EFF4FF;
    border: 1px solid #BFD2F5;
    color: {ACCENT};
}}
QToolButton#Collapse:pressed {{ background: #E1EAFC; }}
"""


# --------------------------------------------------------------------------- #
# 下拉箭头 / 微调箭头：QSS 不能画三角形，运行时生成小图再引用
# --------------------------------------------------------------------------- #
def _arrow_image(kind: str, size: tuple, color=(91, 100, 114, 255), scale: int = 4) -> Image.Image:
    w, h = size[0] * scale, size[1] * scale
    img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    if kind == "down":  # 实心下三角
        draw.polygon([(0, 0), (w, 0), (w // 2, h)], fill=color)
    elif kind == "up":  # 实心上三角
        draw.polygon([(0, h), (w, h), (w // 2, 0)], fill=color)
    else:  # chevron
        draw.line([(0, 0), (w // 2, h - 2), (w, 0)], fill=color, width=scale * 2, joint="curve")
    return img.resize(size, Image.LANCZOS)


def ensure_arrow_assets(directory: Path) -> dict:
    """生成并缓存箭头图标，返回名称→路径。"""
    directory.mkdir(parents=True, exist_ok=True)
    specs = {
        "chevron": ("chevron", (11, 7)),
        "up": ("up", (8, 5)),
        "down": ("down", (8, 5)),
    }
    paths = {}
    for name, (kind, size) in specs.items():
        path = directory / f"{name}.png"
        if not path.exists():
            _arrow_image(kind, size).save(path)
        paths[name] = path.as_posix()
    return paths


def build_qss(asset_dir: Path) -> str:
    """返回完整样式表（在基础样式后追加箭头规则，后面的规则会覆盖前面的）。"""
    a = ensure_arrow_assets(asset_dir)
    return QSS + f"""
QComboBox::drop-down {{
    subcontrol-origin: padding;
    subcontrol-position: center right;
    width: 22px;
    border: none;
    background: transparent;
}}
QComboBox::down-arrow {{
    image: url("{a['chevron']}");
    width: 11px;
    height: 7px;
}}
QSpinBox, QDoubleSpinBox {{ padding-right: 20px; }}
QSpinBox::up-button, QDoubleSpinBox::up-button {{
    subcontrol-origin: border;
    subcontrol-position: top right;
    width: 18px;
    margin: 2px 2px 0 0;
    border: none;
    border-top-right-radius: 7px;
    background: transparent;
}}
QSpinBox::down-button, QDoubleSpinBox::down-button {{
    subcontrol-origin: border;
    subcontrol-position: bottom right;
    width: 18px;
    margin: 0 2px 2px 0;
    border: none;
    border-bottom-right-radius: 7px;
    background: transparent;
}}
QSpinBox::up-button:hover, QDoubleSpinBox::up-button:hover,
QSpinBox::down-button:hover, QDoubleSpinBox::down-button:hover {{
    background: #EEF2F8;
}}
QSpinBox::up-arrow, QDoubleSpinBox::up-arrow {{
    image: url("{a['up']}");
    width: 8px;
    height: 5px;
}}
QSpinBox::down-arrow, QDoubleSpinBox::down-arrow {{
    image: url("{a['down']}");
    width: 8px;
    height: 5px;
}}
"""

