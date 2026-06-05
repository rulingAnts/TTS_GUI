"""
xtts_engine.py — Coqui XTTS v2 TTS engine for Kokoro TTS Studio.

XTTS v2 supports 17 languages and voice cloning from a short audio sample.
Model weights (~1.8 GB) are downloaded on first load.
"""

import logging
import os
import threading
from pathlib import Path
from typing import Callable, List, Optional

import numpy as np

logger = logging.getLogger(__name__)

MODEL_ID   = "tts_models/multilingual/multi-dataset/xtts_v2"
SAMPLE_RATE = 24000  # XTTS v2 native sample rate

# Display name → XTTS language code
LANGUAGES: dict[str, str] = {
    "English":            "en",
    "Spanish":            "es",
    "French":             "fr",
    "German":             "de",
    "Italian":            "it",
    "Portuguese":         "pt",
    "Polish":             "pl",
    "Turkish":            "tr",
    "Russian":            "ru",
    "Dutch":              "nl",
    "Czech":              "cs",
    "Arabic":             "ar",
    "Chinese (Mandarin)": "zh-cn",
    "Japanese":           "ja",
    "Hungarian":          "hu",
    "Korean":             "ko",
    "Hindi":              "hi",
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _set_tts_home() -> Path:
    """Point TTS_HOME at our app-data directory so models stay in one place."""
    try:
        from app_paths import get_models_path
        home = get_models_path() / "xtts"
    except ImportError:
        home = Path.home() / ".local" / "share" / "tts"
    home.mkdir(parents=True, exist_ok=True)
    os.environ["TTS_HOME"] = str(home)
    return home


def is_model_ready() -> bool:
    """Return True if the XTTS v2 model files are present locally."""
    home = _set_tts_home()
    model_dir = home / "tts_models--multilingual--multi-dataset--xtts_v2"
    return (model_dir / "model.pth").exists()


# ---------------------------------------------------------------------------
# Engine
# ---------------------------------------------------------------------------

class XTTSEngine:
    def __init__(self) -> None:
        self._tts = None
        self._lock  = threading.Lock()
        self._loading = False
        self._load_error: str = ""

    # ── Lazy loader ────────────────────────────────────────────────────────

    def ensure_loaded(self) -> None:
        """Load (and if necessary download) the XTTS v2 model."""
        if self._tts is not None:
            return
        with self._lock:
            if self._tts is not None:
                return
            from TTS.api import TTS
            _set_tts_home()
            logger.info("Loading XTTS v2 model (this downloads ~1.8 GB on first run)…")
            self._tts = TTS(MODEL_ID, gpu=False, progress_bar=False)
            n = len(self._tts.speakers or [])
            logger.info("XTTS v2 ready — %d built-in speakers", n)

    def start_load_async(self) -> None:
        """Kick off model loading in a background thread."""
        if self._tts is not None or self._loading:
            return
        self._loading = True
        self._load_error = ""
        threading.Thread(target=self._load_thread, daemon=True).start()

    def _load_thread(self) -> None:
        try:
            self.ensure_loaded()
        except Exception as exc:
            logger.error("XTTS v2 load failed: %s", exc)
            self._load_error = str(exc)
        finally:
            self._loading = False

    @property
    def is_loaded(self) -> bool:
        return self._tts is not None

    @property
    def load_error(self) -> str:
        return self._load_error

    # ── Speaker list ───────────────────────────────────────────────────────

    @property
    def speakers(self) -> List[str]:
        if self._tts is None:
            return []
        return sorted(self._tts.speakers or [])

    # ── Core synthesis ─────────────────────────────────────────────────────

    def synthesize(
        self,
        text: str,
        language: str = "en",
        speaker_wav: Optional[str] = None,
        speaker: Optional[str] = None,
    ) -> np.ndarray:
        """
        Synthesize text and return a float32 numpy audio array at 24 kHz.

        speaker_wav (path to a .wav/.mp3 file) takes priority — XTTS clones
        that voice.  If absent, speaker (built-in name) is used.  If neither
        is provided, the first available built-in speaker is used.
        """
        self.ensure_loaded()

        kwargs: dict = {"text": text, "language": language}

        wav_path = speaker_wav and Path(speaker_wav)
        if wav_path and wav_path.exists():
            kwargs["speaker_wav"] = str(wav_path)
        elif speaker:
            kwargs["speaker"] = speaker
        elif self._tts.speakers:
            kwargs["speaker"] = self._tts.speakers[0]

        wav = self._tts.tts(**kwargs)
        return np.asarray(wav, dtype=np.float32)

    # ── Public: preview ────────────────────────────────────────────────────

    def preview_audio(
        self,
        text: str,
        language: str = "en",
        speaker_wav: Optional[str] = None,
        speaker: Optional[str] = None,
    ) -> None:
        """Synthesize up to 200 chars and play via the OS audio player."""
        import soundfile as sf
        import tempfile
        from audio_player import play_wav

        preview = text[:200].strip()
        if not preview:
            raise ValueError("No text for preview")

        wav = self.synthesize(preview, language, speaker_wav, speaker)

        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
            tmp = f.name
        try:
            sf.write(tmp, wav, SAMPLE_RATE)
            play_wav(tmp)
        finally:
            try:
                os.unlink(tmp)
            except OSError:
                pass

    # ── Public: full generation ────────────────────────────────────────────

    def generate_audio(
        self,
        text: str,
        language: str,
        speaker_wav: Optional[str],
        speaker: Optional[str],
        split_pattern: str,
        post_proc_options: dict,
        output_path: str,
        output_format: str,
        progress_callback: Callable[[int, int], None],
        cancel_flag: threading.Event,
    ) -> str:
        import re
        import soundfile as sf
        from audio_processing import post_process
        from tts_engine import _parse_pause_segments, _expand_to_items

        if split_pattern:
            chunks = [c.strip() for c in re.split(split_pattern, text) if c.strip()]
        else:
            chunks = [text.strip()] if text.strip() else []
        if not chunks:
            raise ValueError("No text to generate after splitting")

        all_audio: list[np.ndarray] = []
        for i, chunk in enumerate(chunks):
            if cancel_flag.is_set():
                break
            pause_segs = _parse_pause_segments(chunk)
            items = _expand_to_items(pause_segs, "")
            for item_type, item_val in items:
                if item_type == "silence":
                    all_audio.append(np.zeros(item_val, dtype=np.float32))
                else:
                    all_audio.append(self.synthesize(item_val, language, speaker_wav, speaker))
            progress_callback(i + 1, len(chunks))

        if not all_audio:
            raise RuntimeError("No audio was generated")

        combined = np.concatenate(all_audio)
        combined = post_process(combined, SAMPLE_RATE, post_proc_options)
        sf.write(output_path, combined, SAMPLE_RATE)
        return output_path
