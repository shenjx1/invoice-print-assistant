# -*- mode: python ; coding: utf-8 -*-
"""文件夹版（onedir）构建：启动不解包，冷启动 ~1-2 秒。

精简原则：程序实际只用到 QtCore/QtGui/QtWidgets/QtNetwork + Pillow + PyMuPDF，
以下依赖都是被 PyInstaller 连带收进来的，全部剔除。
"""
import os

# SPECPATH 由 PyInstaller 注入（spec 所在目录 = 仓库根），换机器、换路径都不用改本文件
ROOT = os.path.abspath(SPECPATH)

# 明确不用的大件：numpy 从未被引用；Qt Quick/Qml/Pdf 不涉及；
# opengl32sw 是软件 OpenGL 兜底，Widgets 走光栅渲染用不到。
DROP_BINARIES = {
    'opengl32sw.dll',
    'qt6quick.dll',
    'qt6qml.dll',
    'qt6qmlmodels.dll',
    'qt6pdf.dll',
    'qt6opengl.dll',
    'qt6virtualkeyboard.dll',
}

a = Analysis(
    [ROOT + '/main.py'],
    pathex=[ROOT],
    binaries=[],
    datas=[(ROOT + '/assets', 'assets')],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        'numpy', 'numpy.core', 'scipy', 'pandas', 'matplotlib',
        'PySide6.QtWebEngineCore', 'PySide6.QtWebEngineWidgets',
        'PySide6.QtWebEngineQuick', 'PySide6.Qt3DCore', 'PySide6.QtQuick',
        'PySide6.QtQml', 'PySide6.QtPdf', 'PySide6.QtPdfWidgets',
        'PySide6.QtMultimedia', 'PySide6.QtDesigner', 'PySide6.QtOpenGL',
        'PIL.AvifImagePlugin', 'PIL.ImageQt', 'PIL.ImageTk',
        'tkinter', 'test', 'unittest', 'pydoc_data',
    ],
    noarchive=False,
    optimize=0,
)

a.binaries = [b for b in a.binaries if os.path.basename(b[0]).lower() not in DROP_BINARIES]
# Qt 自带的多语言翻译文件：程序没有加载 QTranslator，用不到
a.datas = [
    d for d in a.datas
    if not (d[0].lower().endswith('.qm') and 'translation' in d[0].lower())
]

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='发票打印助手',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    version=ROOT + '/tools/version_info.txt',
    icon=[ROOT + '/assets/icon.ico'],
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name='发票打印助手',
)
