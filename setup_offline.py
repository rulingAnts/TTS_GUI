#!/usr/bin/env python3
"""
setup_offline.py — Extract offline model bundles from GitHub Releases
and place them where the app expects them.

Kokoro (default):
    python setup_offline.py [<bundle-dir-or-zip>]
    python setup_offline.py /path/to/hexgrad-kokoro-82m-weights-YYYYMMDD.zip
    python setup_offline.py /Users/Seth/Downloads/bundle

XTTS v2:
    python setup_offline.py --xtts [<zip-or-dir>]
    python setup_offline.py --xtts /path/to/xtts-v2-model-YYYYMMDD.zip

If no path is given, ~/Downloads is searched automatically.
Both flags can be used together to install both models in one run:
    python setup_offline.py /path/to/kokoro.zip --xtts /path/to/xtts.zip
"""

import shutil
import sys
import zipfile
from pathlib import Path

PROJECT_DIR = Path(__file__).parent
MODELS_DIR  = PROJECT_DIR / "models"
HOME_CACHE  = Path.home() / ".cache"

PIP_CACHE_MAP = {
    "hub":         HOME_CACHE / "huggingface" / "hub",
    "kokoro-onnx": HOME_CACHE / "kokoro-onnx",
}

XTTS_MODEL_DIR = "tts_models--multilingual--multi-dataset--xtts_v2"


# ── Shared helpers ────────────────────────────────────────────────────────────

def copy_tree(src: Path, dst: Path) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    if dst.exists():
        shutil.copytree(src, dst, dirs_exist_ok=True)
    else:
        shutil.copytree(src, dst)


def extract_zip(zip_path: Path, tmp: Path) -> None:
    tmp.mkdir(exist_ok=True)
    print(f"Extracting {zip_path.name} …")
    with zipfile.ZipFile(zip_path, "r") as zf:
        # If the zip contains only one entry that is itself a zip, unwrap it
        names = zf.namelist()
        if len(names) == 1 and names[0].endswith(".zip"):
            import io
            print(f"  (double-zipped — unwrapping inner {names[0]}) …")
            inner_data = zf.read(names[0])
            with zipfile.ZipFile(io.BytesIO(inner_data)) as inner:
                inner.extractall(tmp)
        else:
            zf.extractall(tmp)


def resolve_source(arg: Path) -> Path:
    """Return the root extracted directory (extracts zip if needed)."""
    if arg.is_dir():
        return arg
    tmp = PROJECT_DIR / "_extract_tmp"
    extract_zip(arg, tmp)
    # Walk common top-level folder names
    for candidate in [tmp / "xtts-bundle", tmp / "bundle", tmp]:
        if candidate.exists() and candidate.is_dir():
            return candidate
    return tmp


# ── Kokoro setup ──────────────────────────────────────────────────────────────

def find_kokoro_in_downloads() -> Path | None:
    dl = Path.home() / "Downloads"
    if (dl / "bundle").is_dir():
        return dl / "bundle"
    candidates = sorted(
        list(dl.glob("*kokoro*weights*.zip")) +
        list(dl.glob("hexgrad-*weights*.zip")),
        key=lambda p: p.stat().st_mtime, reverse=True,
    )
    return candidates[0] if candidates else None


def install_kokoro(source: Path) -> None:
    print(f"\n── Kokoro model ─────────────────────────────────────────────")
    bundle = resolve_source(source)

    kokoro_src = bundle / "Kokoro-82M"
    if kokoro_src.exists():
        dest = MODELS_DIR / "Kokoro-82M"
        MODELS_DIR.mkdir(exist_ok=True)
        print(f"Copying model files → {dest} …")
        copy_tree(kokoro_src, dest)
        voices_dir = dest / "voices"
        n = len(list(voices_dir.glob("*.pt"))) if voices_dir.exists() else 0
        print(f"  ✓ {n} voice tensors")
        print(f"  ✓ kokoro-v1_0.pth" if (dest / "kokoro-v1_0.pth").exists()
              else "  ⚠ kokoro-v1_0.pth not found")
    else:
        print("  ⚠ Kokoro-82M/ not found in bundle — skipping")

    pip_cache_src = bundle / "pip-cache"
    if pip_cache_src.exists():
        for folder_name, cache_dest in PIP_CACHE_MAP.items():
            src = pip_cache_src / folder_name
            if src.is_dir():
                print(f"Copying pip-cache/{folder_name}/ → {cache_dest} …")
                copy_tree(src, cache_dest)
                print(f"  ✓ Done")
            else:
                print(f"  — pip-cache/{folder_name}/ not present, skipping")
    else:
        print("  ⚠ pip-cache/ not found in bundle — skipping")

    _cleanup()
    print(f"\n✅  Kokoro setup complete.")
    print(f"   {MODELS_DIR}/Kokoro-82M/voices/  ← voice tensors")
    print(f"   {HOME_CACHE}/huggingface/hub/     ← HF hub cache")


# ── XTTS v2 setup ─────────────────────────────────────────────────────────────

def find_xtts_in_downloads() -> Path | None:
    dl = Path.home() / "Downloads"
    if (dl / "xtts-bundle").is_dir():
        return dl / "xtts-bundle"
    candidates = sorted(
        list(dl.glob("xtts-v2*.zip")) +
        list(dl.glob("*xtts*model*.zip")),
        key=lambda p: p.stat().st_mtime, reverse=True,
    )
    return candidates[0] if candidates else None


def get_xtts_dest() -> Path:
    """
    Returns the TTS_HOME-relative path where the app looks for the model.
    Mirrors _set_tts_home() in xtts_engine.py.
    """
    try:
        sys.path.insert(0, str(PROJECT_DIR))
        from app_paths import get_models_path
        return get_models_path() / "xtts"
    except Exception:
        return Path.home() / ".local" / "share" / "tts"


def install_xtts(source: Path) -> None:
    print(f"\n── XTTS v2 model ────────────────────────────────────────────")
    bundle = resolve_source(source)

    # The model directory could be at the bundle root or inside xtts-bundle/
    model_src = None
    for candidate in [
        bundle / XTTS_MODEL_DIR,
        bundle / "xtts-bundle" / XTTS_MODEL_DIR,
    ]:
        if candidate.is_dir():
            model_src = candidate
            break

    if model_src is None:
        print(f"  ✗ Could not find {XTTS_MODEL_DIR}/ in the extracted bundle.")
        print(f"    Searched: {bundle}")
        _cleanup()
        return

    dest_root = get_xtts_dest()
    dest = dest_root / XTTS_MODEL_DIR
    print(f"Copying XTTS v2 model → {dest} …")
    dest_root.mkdir(parents=True, exist_ok=True)
    copy_tree(model_src, dest)

    # Verify key files
    for fname in ["model.pth", "config.json", "vocab.json", "speakers_xtts.pth"]:
        p = dest / fname
        if p.exists():
            size_mb = p.stat().st_size / 1_048_576
            print(f"  ✓ {fname} ({size_mb:.0f} MB)")
        else:
            print(f"  ⚠ {fname} not found")

    _cleanup()
    print(f"\n✅  XTTS v2 setup complete.")
    print(f"   {dest}")
    print(f"   Launch the app → XTTS v2 tab → 'Load XTTS v2'")


# ── Cleanup ───────────────────────────────────────────────────────────────────

def _cleanup() -> None:
    tmp = PROJECT_DIR / "_extract_tmp"
    if tmp.exists():
        shutil.rmtree(tmp)


# ── Entry point ───────────────────────────────────────────────────────────────

def main() -> None:
    args = sys.argv[1:]

    # Parse flags
    xtts_mode   = "--xtts"   in args
    kokoro_mode = not xtts_mode or ("--xtts" in args and len(args) > 1 and args[0] != "--xtts")

    # Remove flag from args list to leave only paths
    plain_args = [a for a in args if not a.startswith("--")]

    # ── XTTS install ──────────────────────────────────────────────────────
    if xtts_mode:
        if plain_args and "--xtts" in args:
            # --xtts comes before the path, or the path is the non-flag arg
            xtts_idx = args.index("--xtts")
            xtts_path_args = [a for i, a in enumerate(args)
                              if i != xtts_idx and not a.startswith("--")]
            source_str = xtts_path_args[0] if xtts_path_args else None
        else:
            source_str = plain_args[0] if plain_args else None

        if source_str:
            source = Path(source_str)
            if not source.exists():
                print(f"Error: not found: {source}")
                sys.exit(1)
        else:
            source = find_xtts_in_downloads()
            if source:
                print(f"Found XTTS bundle: {source}")
            else:
                print("No XTTS bundle found in ~/Downloads.")
                print("Usage: python setup_offline.py --xtts <xtts-v2-model-YYYYMMDD.zip>")
                sys.exit(1)

        install_xtts(source)

    # ── Kokoro install ────────────────────────────────────────────────────
    if not xtts_mode or kokoro_mode:
        # If only --xtts was given, skip Kokoro
        if xtts_mode and not kokoro_mode:
            pass
        else:
            kokoro_path_args = [a for a in plain_args if Path(a).exists()
                                and "xtts" not in a.lower()]
            if kokoro_path_args:
                source = Path(kokoro_path_args[0])
            elif not xtts_mode:
                # No flags at all — original Kokoro-only mode
                if plain_args:
                    source = Path(plain_args[0])
                    if not source.exists():
                        print(f"Error: not found: {source}")
                        sys.exit(1)
                else:
                    source = find_kokoro_in_downloads()
                    if source:
                        print(f"Found Kokoro bundle: {source}")
                    else:
                        print("Usage: python setup_offline.py [<bundle-dir-or-zip>]")
                        print("       python setup_offline.py --xtts [<xtts-zip>]")
                        sys.exit(1)
                install_kokoro(source)
                return
            else:
                # xtts mode only, no kokoro path
                return

            install_kokoro(source)

    print("\nRun the app:  python main.py")


if __name__ == "__main__":
    main()
