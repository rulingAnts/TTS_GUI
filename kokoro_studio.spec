# -*- mode: python ; coding: utf-8 -*-
"""
kokoro_studio.spec — PyInstaller spec for Kokoro TTS Studio.

Produces a --onedir bundle.  On macOS also produces a .app via BUNDLE.
Run via:   pyinstaller kokoro_studio.spec --clean
"""

import sys
import os
from pathlib import Path
from PyInstaller.utils.hooks import collect_all, collect_data_files, collect_submodules

block_cipher = None  # deprecated in PyInstaller 6.x — keep for compat

# ── Icon paths (drop real icons here; build continues without them) ────────
_assets = Path("assets")
icon_mac = str(_assets / "icon.icns") if (_assets / "icon.icns").exists() else None
icon_win = str(_assets / "icon.ico")  if (_assets / "icon.ico").exists()  else None

# ── Collect heavy packages ─────────────────────────────────────────────────
datas      = []
binaries   = []
hiddenimports = []

def _add(pkg):
    global datas, binaries, hiddenimports
    d, b, h = collect_all(pkg)
    datas += d; binaries += b; hiddenimports += h

# Core ML stack
_add("torch")
_add("kokoro")
_add("misaki")        # kokoro's G2P engine
_add("espeakng_loader")  # bundles pre-built espeak-ng binary + data
_add("phonemizer")    # misaki → espeak-ng bridge

# NLP / spacy (misaki dependency)
_add("spacy")
_add("thinc")
_add("blis")
_add("cymem")
_add("preshed")
_add("murmurhash")
_add("srsly")
_add("catalogue")
_add("weasel")
_add("confection")

# Audio
_add("librosa")
_add("soundfile")
_add("sounddevice")
_add("audioread")

# Networking (for first-run download)
_add("huggingface_hub")
_add("requests")

# App resources
datas += [
    ("frontend",       "frontend"),
    ("setup_frontend", "setup_frontend"),
]

# ── Additional hidden imports ──────────────────────────────────────────────
hiddenimports += [
    # App modules
    "app_paths",
    "tts_engine",
    "audio_processing",
    "voice_data",
    "model_downloader",
    # torch internals that PyInstaller commonly misses
    "torch",
    "torch._C",
    "torch._dynamo",
    "torch.nn",
    "torch.nn.modules",
    "torch.nn.functional",
    "torch.utils",
    "torch.utils.data",
    "torch.jit",
    "torch.jit._state",
    "torch.backends",
    "torch.backends.cpu",
    "torch.ao",
    "torch.fx",
    # scipy (librosa dependency)
    "scipy",
    "scipy.signal",
    "scipy.fft",
    "scipy._lib",
    "scipy.ndimage",
    # numpy
    "numpy",
    "numpy.core",
    "numpy.lib",
    # packaging / metadata
    "pkg_resources",
    "importlib.metadata",
    "importlib.resources",
    # pywebview internals
    "webview",
    "webview.platforms",
    # misc
    "cffi",
    "pycparser",
    "charset_normalizer",
    "idna",
    "urllib3",
    "certifi",
]

# ── Excludes (trim size; these packages are never used at runtime) ─────────
excludes = [
    "numba",          # librosa works without it (slower but fine)
    "matplotlib",
    "IPython",
    "ipykernel",
    "jupyter",
    "notebook",
    "tkinter",
    "_tkinter",
    "PyQt5",
    "PyQt6",
    "PySide2",
    "PySide6",
    "wx",
    "gi",
    "cv2",
    "PIL",            # not used
    "Pillow",
    "pytest",
    "black",
    "mypy",
    "pylint",
    "docutils",
    "sphinx",
]

# ── Analysis ───────────────────────────────────────────────────────────────
a = Analysis(
    ["main.py"],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=excludes,
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

# ── Executable ────────────────────────────────────────────────────────────
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="Kokoro TTS Studio",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,           # no terminal window in production
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,        # None = native arch; set 'universal2' for fat Mac binary
    codesign_identity=None,
    entitlements_file=None,
    icon=icon_win if sys.platform == "win32" else icon_mac,
)

# ── Collect (onedir layout) ───────────────────────────────────────────────
coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="Kokoro TTS Studio",
)

# ── macOS .app bundle ─────────────────────────────────────────────────────
if sys.platform == "darwin":
    app = BUNDLE(
        coll,
        name="Kokoro TTS Studio.app",
        icon=icon_mac,
        bundle_identifier="com.kokorottsstudio.app",
        info_plist={
            "CFBundleName": "Kokoro TTS Studio",
            "CFBundleDisplayName": "Kokoro TTS Studio",
            "CFBundleVersion": "1.0.0",
            "CFBundleShortVersionString": "1.0",
            "NSHighResolutionCapable": True,
            "NSMicrophoneUsageDescription":
                "Kokoro TTS Studio uses audio output for voice preview playback.",
            "LSMinimumSystemVersion": "12.0",
            "NSAppleEventsUsageDescription":
                "Kokoro TTS Studio needs Automation access to open the output folder.",
        },
    )
