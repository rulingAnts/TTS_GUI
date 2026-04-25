"""
model_downloader.py — Downloads Kokoro model weights from HuggingFace
with per-file progress reporting.  Used by the first-run setup screen.
"""

import logging
import threading
from pathlib import Path
from typing import Callable, Optional

logger = logging.getLogger(__name__)

REPO_ID = "hexgrad/Kokoro-82M"

_IGNORE_PREFIXES = ("samples/", "eval/")
_IGNORE_SUFFIXES = (".md", ".txt", ".gitattributes")


def _want(filename: str) -> bool:
    if any(filename.startswith(p) for p in _IGNORE_PREFIXES):
        return False
    if any(filename.endswith(s) for s in _IGNORE_SUFFIXES):
        return False
    return True


def list_model_files() -> list[str]:
    """Return the list of files to download from the HF repo."""
    from huggingface_hub import HfApi

    api = HfApi()
    return [f for f in api.list_repo_files(REPO_ID) if _want(f)]


def download_model(
    dest_dir: Path,
    progress_cb: Callable[[float, str], None],
    cancel_event: Optional[threading.Event] = None,
) -> None:
    """
    Download all required Kokoro model files into dest_dir.

    progress_cb(fraction 0-1, current_filename) is called after each file.
    Raises RuntimeError on cancellation or download failure.
    """
    from huggingface_hub import hf_hub_download

    files = list_model_files()
    total = len(files)
    if total == 0:
        raise RuntimeError("No files found in HuggingFace repo — check connectivity")

    for i, filename in enumerate(files):
        if cancel_event and cancel_event.is_set():
            raise RuntimeError("Download cancelled by user")

        logger.info("Downloading %s (%d/%d)", filename, i + 1, total)
        hf_hub_download(
            repo_id=REPO_ID,
            filename=filename,
            local_dir=str(dest_dir),
        )
        progress_cb((i + 1) / total, filename)
