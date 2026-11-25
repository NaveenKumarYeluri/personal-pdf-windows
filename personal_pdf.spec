# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_data_files, collect_dynamic_libs

datas = [('assets', 'assets')] + collect_data_files('pymupdf')
binaries = collect_dynamic_libs('pymupdf')

a = Analysis(
    ['app/main.py'],
    pathex=['.'],
    binaries=binaries,
    datas=datas,
    hiddenimports=['pymupdf', 'PIL', 'numpy'],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        'IPython',
        'matplotlib',
        'pandas',
        'scipy',
        'tkinter',
        'pytest',
        'playwright',
        'pyarrow',
        'polars',
        'SQLAlchemy',
        'onnxruntime',
    ],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='Personal-PDF-Suite-Windows',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    icon='assets/app-icon.ico',
)
