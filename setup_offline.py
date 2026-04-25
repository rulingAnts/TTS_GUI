#!/usr/bin/env python3
"""
setup_offline.py — Unpack the Kokoro model weights zip downloaded from
GitHub Releases and place everything where the app expects it.

Run once after downloading the zip:
    python setup_offline.py /path/to/hexgrad-kokoro-82m-weights-YYYYMMDD.zip

If you don't pass a path the script searches ~/Downloads automatically.
"""

import shutil
import sys
import zipfile
from pathlib import Path

PROJECT_DIR = Path(__file__).parent
MODELS_DIR  = PROJECT_DIR / "models"
HOME_CACHE  = Path.home() / ".cache"


def find_zip_in_downloads() -> Path | None:
    downloads = Path.home() / "Downloads"
    candidates = sorted(
        list(downloads.glob("*kokoro*weights*.zip")) +
        list(downloads.glob("hexgrad-*weights*.zip")),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    return candidates[0] if candidates else None


def copy_tree(src: Path, dst: Path) -> None:
    """Copy src into dst, merging if dst already exists."""
    if dst.exists():
        shutil.copytree(src, dst, dirs_exist_ok=True)
    else:
        shutil.copytree(src, dst)


def main() -> None:
    # ── Locate zip ────────────────────────────────────────────────────────
    if len(sys.argv) >= 2:
        zip_path = Path(sys.argv[1])
    else:
        zip_path = find_zip_in_downloads()
        if zip_path:
            print(f"Found zip: {zip_path}")
        else:
            print("Usage: python setup_offline.py <path-to-weights.zip>")
            print("       (or drop the zip in ~/Downloads and re-run without args)")
            sys.exit(1)

    if not zip_path.exists():
        print(f"Error: file not found: {zip_path}")
        sys.exit(1)

    # ── Extract to a temp dir ─────────────────────────────────────────────
    tmp = PROJECT_DIR / "_extract_tmp"
    tmp.mkdir(exist_ok=True)
    print(f"Extracting {zip_path.name} …")
    with zipfile.ZipFile(zip_path, "r") as zf:
        zf.extractall(tmp)

    bundle = tmp / "bundle"
    if not bundle.exists():
        # Some zips land without a bundle/ prefix — handle both layouts
        bundle = tmp

    # ── 1. Model weights + voice tensors → models/Kokoro-82M/ ────────────
    kokoro_src = bundle / "Kokoro-82M"
    if kokoro_src.exists():
        dest = MODELS_DIR / "Kokoro-82M"
        print(f"Copying model files → {dest} …")
        MODELS_DIR.mkdir(exist_ok=True)
        copy_tree(kokoro_src, dest)

        voices_dir = dest / "voices"
        n_voices = len(list(voices_dir.glob("*.pt"))) if voices_dir.exists() else 0
        print(f"  ✓ {n_voices} voice tensors")
        if (dest / "kokoro-v1_0.pth").exists():
            print("  ✓ kokoro-v1_0.pth")
    else:
        print("  ⚠ Kokoro-82M/ folder not found in zip — skipping model copy")

    # ── 2. Pip runtime cache → ~/.cache/ ──────────────────────────────────
    pip_cache_src = bundle / "pip-cache"
    if pip_cache_src.exists():
        HOME_CACHE.mkdir(exist_ok=True)
        for item in pip_cache_src.iterdir():
            if not item.is_dir():
                continue
            dest = HOME_CACHE / item.name
            print(f"Copying pip cache {item.name} → {dest} …")
            copy_tree(item, dest)
            print(f"  ✓ Done")
    else:
        print("  ⚠ pip-cache/ folder not found in zip — skipping cache copy")

    # ── Cleanup ────────────────────────────────────────────────────────────
    shutil.rmtree(tmp)

    # ── Report ────────────────────────────────────────────────────────────
    print()
    print("✅  Setup complete. File layout:")
    print(f"   {MODELS_DIR}/Kokoro-82M/voices/   ← voice tensors (blending)")
    print(f"   {HOME_CACHE}/huggingface/          ← kokoro pip runtime cache")
    print()
    print("Run the app:  python main.py")


if __name__ == "__main__":
    main()
