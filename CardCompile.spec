# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller specification for CardCompile (folder deployment)."""

from pathlib import Path

block_cipher = None
ROOT = Path(SPECPATH)

datas = [
    (str(ROOT / "resources"), "resources"),
    (str(ROOT / "database" / "schema.sql"), "database"),
]

hiddenimports = [
    "pymupdf",
    "PIL.Image",
    "PIL.ImageOps",
    "PySide6.QtCore",
    "PySide6.QtGui",
    "PySide6.QtWidgets",
]

a = Analysis(
    ["main.py"],
    pathex=[str(ROOT)],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    # Never bundle tooling or unused scientific packages - the application only
    # needs Qt, Pillow and PyMuPDF.
    excludes=[
        "tkinter",
        "matplotlib",
        "scipy",
        "pandas",
        "IPython",
        "pytest",
        "_pytest",
        "setuptools",
        "pip",
        "wheel",
        "pygments",
        "docutils",
        "tests",
        "tools",
        # Optional machine-learning stacks that the PyMuPDF hook would otherwise
        # drag in; the application never uses them.
        "torch",
        "torchvision",
        "torchaudio",
        "onnxruntime",
        "transformers",
        "tensorflow",
        "jax",
        "numba",
        "llvmlite",
        "cv2",
        "sklearn",
        "seaborn",
        "sympy",
        "psutil",
        "charset_normalizer",
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="CardCompile",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    icon=str(ROOT / "resources" / "icons" / "app.ico")
    if (ROOT / "resources" / "icons" / "app.ico").is_file()
    else None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="CardCompile",
)
