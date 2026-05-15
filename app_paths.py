"""
app_paths.py — Central path resolution for both development and
PyInstaller-bundled modes.

All modules should import from here rather than using __file__ or
hardcoded paths, so the app works correctly in both environments.

Import order in main.py must be:
    1. import app_paths
    2. app_paths.setup_espeak_env()   ← before ANY kokoro/torch import
    3. everything else
"""

import os
import sys
from pathlib import Path


# ── Core path resolution ──────────────────────────────────────────────────

def get_base_path() -> Path:
    """
    Root directory that contains bundled resources.
    • PyInstaller bundle → sys._MEIPASS  (the extracted _internal dir)
    • Development       → directory containing this file
    """
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        return Path(sys._MEIPASS)
    return Path(__file__).parent


def get_frontend_path() -> Path:
    """Path to the frontend/ directory (index.html, style.css, app.js)."""
    return get_base_path() / "frontend"


def get_setup_frontend_path() -> Path:
    """Path to setup_frontend/ (first-run download UI)."""
    return get_base_path() / "setup_frontend"


# ── User-writable app data (outside the bundle, survives updates) ─────────

def get_app_data_path() -> Path:
    """
    Platform-specific user data directory.
    All user data (models, outputs, settings) lives here, NOT in the bundle.
    """
    if sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support" / "KokoroTTSStudio"
    elif sys.platform == "win32":
        appdata = os.environ.get("APPDATA") or str(Path.home())
        base = Path(appdata) / "KokoroTTSStudio"
    else:
        base = Path.home() / ".kokorottsstudio"
    base.mkdir(parents=True, exist_ok=True)
    return base


def get_models_path() -> Path:
    """Where downloaded / offline model weights are stored."""
    path = get_app_data_path() / "models"
    path.mkdir(parents=True, exist_ok=True)
    return path


def get_outputs_path() -> Path:
    """Default output directory for generated audio files."""
    path = get_app_data_path() / "outputs"
    path.mkdir(parents=True, exist_ok=True)
    return path


def get_espeak_data_path() -> Path:
    """Bundled espeak-ng-data directory (PyInstaller only)."""
    return get_base_path() / "espeak-ng-data"


# ── espeak-ng environment setup ───────────────────────────────────────────

def _augment_path() -> None:
    """
    Verify that espeak-ng is reachable on PATH; if not, probe common
    Homebrew / system binary directories and add the ones that actually
    contain an espeak-ng executable.

    GUI apps launched outside a shell (IDLE, Finder, PyInstaller bundles)
    receive a minimal system PATH that omits /opt/homebrew/bin, so Homebrew-
    installed tools like espeak-ng are invisible without this.
    """
    import shutil

    # If espeak-ng is already on PATH, nothing to do
    if shutil.which("espeak-ng"):
        return

    # Executable name differs by platform
    exe = "espeak-ng.exe" if sys.platform == "win32" else "espeak-ng"

    candidates = [
        # macOS — Apple Silicon (arm64)
        "/opt/homebrew/bin",
        "/opt/homebrew/sbin",
        # macOS — Intel (x86_64) / manual installs
        "/usr/local/bin",
        "/usr/local/sbin",
        # macOS / Linux — system paths
        "/usr/bin",
        "/bin",
        "/usr/sbin",
        "/sbin",
        # Windows — default espeak-ng installer locations
        r"C:\Program Files\eSpeak NG",
        r"C:\Program Files (x86)\eSpeak NG",
        r"C:\Program Files\eSpeak",
        r"C:\Program Files (x86)\eSpeak",
    ]
    current = os.environ.get("PATH", "").split(os.pathsep)

    # Only add directories that both exist AND contain the espeak-ng binary
    verified = [
        p for p in candidates
        if p not in current
        and (Path(p) / exe).exists()
    ]

    if verified:
        os.environ["PATH"] = os.pathsep.join(verified + current)


def setup_espeak_env() -> None:
    """
    Configure espeak-ng so kokoro / misaki / phonemizer find the correct
    data files.  MUST be called before importing kokoro or torch.

    • In development: delegates to espeakng_loader.ensure() which ships
      pre-built espeak-ng binaries as a pip package.
    • In a PyInstaller bundle: points env vars at _MEIPASS/espeak-ng-data.
    """
    # Fix PATH first so espeak-ng is findable regardless of launch method
    _augment_path()

    if getattr(sys, "frozen", False):
        # Bundled mode — point to files extracted by PyInstaller
        data_dir = get_espeak_data_path()
        if data_dir.exists():
            os.environ["ESPEAK_DATA_PATH"] = str(data_dir)
            os.environ["PHONEMIZER_BACKEND_ESPEAK_PATH"] = str(data_dir)

    # Always try espeakng_loader — it's a pip dep and handles both modes
    try:
        import espeakng_loader  # type: ignore
        espeakng_loader.ensure()
    except Exception:
        pass  # Fall back to system espeak-ng in PATH


# ── Model readiness check ─────────────────────────────────────────────────

def model_is_ready() -> bool:
    """
    Returns True if Kokoro model weights are available locally.
    Checks (in order):
      1. get_models_path()/Kokoro-82M/   (our managed location)
      2. dev-mode models/ directory next to this file
      3. HuggingFace hub cache (~/.cache/huggingface/hub/)
    """
    # 1. Managed location (bundled app or after setup_offline.py)
    managed = get_models_path() / "Kokoro-82M" / "kokoro-v1_0.pth"
    if managed.exists():
        return True

    # 2. Dev-mode local models/ directory
    dev = Path(__file__).parent / "models" / "Kokoro-82M" / "kokoro-v1_0.pth"
    if dev.exists():
        return True

    # 3. HF hub cache (kokoro downloads here by default)
    hf_hub = Path.home() / ".cache" / "huggingface" / "hub"
    if hf_hub.is_dir():
        for entry in hf_hub.iterdir():
            if entry.name.startswith("models--hexgrad--Kokoro"):
                for snap in (entry / "snapshots").glob("*/kokoro-v1_0.pth"):
                    return True

    return False
