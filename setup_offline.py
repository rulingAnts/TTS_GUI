#!/usr/bin/env python3
"""
setup_offline.py — Place Kokoro model weights where the app expects them.

Accepts either a zip file or an already-extracted bundle directory:
    python setup_offline.py /path/to/hexgrad-kokoro-82m-weights-YYYYMMDD.zip
    python setup_offline.py /path/to/bundle

If no path is given the script searches ~/Downloads for a matching zip or
a 'bundle' folder automatically.

pip-cache layout produced by the GitHub Actions workflow:
    pip-cache/hub/          → ~/.cache/huggingface/hub/
    pip-cache/kokoro-onnx/  → ~/.cache/kokoro-onnx/   (if present)
"""

import shutil
import sys
import zipfile
from pathlib import Path

PROJECT_DIR = Path(__file__).parent
MODELS_DIR  = PROJECT_DIR / "models"
HOME_CACHE  = Path.home() / ".cache"

# Maps folder name inside pip-cache/ → destination under ~/.cache/
PIP_CACHE_MAP = {
    "hub":         HOME_CACHE / "huggingface" / "hub",
    "kokoro-onnx": HOME_CACHE / "kokoro-onnx",
}


def find_bundle_in_downloads() -> Path | None:
    downloads = Path.home() / "Downloads"
    # Prefer an already-extracted bundle/ dir
    if (downloads / "bundle").is_dir():
        return downloads / "bundle"
    # Fall back to the most recently modified matching zip
    candidates = sorted(
        list(downloads.glob("*kokoro*weights*.zip")) +
        list(downloads.glob("hexgrad-*weights*.zip")),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    return candidates[0] if candidates else None


def copy_tree(src: Path, dst: Path) -> None:
    """Copy src into dst, merging if dst already exists."""
    dst.parent.mkdir(parents=True, exist_ok=True)
    if dst.exists():
        shutil.copytree(src, dst, dirs_exist_ok=True)
    else:
        shutil.copytree(src, dst)


def resolve_bundle(arg: Path) -> Path:
    """Return the bundle/ directory, extracting a zip if necessary."""
    if arg.is_dir():
        # Already a directory — use it directly
        return arg

    # It's a zip — extract to a temp dir
    tmp = PROJECT_DIR / "_extract_tmp"
    tmp.mkdir(exist_ok=True)
    print(f"Extracting {arg.name} …")
    with zipfile.ZipFile(arg, "r") as zf:
        zf.extractall(tmp)
    bundle = tmp / "bundle"
    return bundle if bundle.exists() else tmp


def main() -> None:
    # ── Locate source ─────────────────────────────────────────────────────
    if len(sys.argv) >= 2:
        source = Path(sys.argv[1])
    else:
        source = find_bundle_in_downloads()
        if source:
            print(f"Found: {source}")
        else:
            print("Usage: python setup_offline.py <bundle-dir-or-zip>")
            print("       (or place bundle/ or a matching zip in ~/Downloads)")
            sys.exit(1)

    if not source.exists():
        print(f"Error: not found: {source}")
        sys.exit(1)

    bundle = resolve_bundle(source)
    tmp_to_clean = PROJECT_DIR / "_extract_tmp"

    # ── 1. Voice tensors + model weights → models/Kokoro-82M/ ────────────
    kokoro_src = bundle / "Kokoro-82M"
    if kokoro_src.exists():
        dest = MODELS_DIR / "Kokoro-82M"
        print(f"Copying model files → {dest} …")
        MODELS_DIR.mkdir(exist_ok=True)
        copy_tree(kokoro_src, dest)

        voices_dir = dest / "voices"
        n_voices = len(list(voices_dir.glob("*.pt"))) if voices_dir.exists() else 0
        print(f"  ✓ {n_voices} voice tensors")
        print(f"  ✓ kokoro-v1_0.pth" if (dest / "kokoro-v1_0.pth").exists() else "  ⚠ kokoro-v1_0.pth not found")
    else:
        print("  ⚠ Kokoro-82M/ not found in bundle — skipping model copy")

    # ── 2. Pip runtime cache → ~/.cache/ with explicit path mapping ───────
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
        print("  ⚠ pip-cache/ not found in bundle — skipping cache copy")

    # ── Cleanup temp extraction dir (not the user's bundle dir) ──────────
    if tmp_to_clean.exists():
        shutil.rmtree(tmp_to_clean)

    # ── Report ────────────────────────────────────────────────────────────
    print()
    print("✅  Setup complete.")
    print(f"   {MODELS_DIR}/Kokoro-82M/voices/  ← voice tensors (blending)")
    print(f"   {HOME_CACHE}/huggingface/hub/     ← HF hub cache (pipeline)")
    print()
    print("Run the app:  python main.py")


if __name__ == "__main__":
    main()
