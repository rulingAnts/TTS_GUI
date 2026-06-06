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

_MODEL_SUBDIR = "tts_models--multilingual--multi-dataset--xtts_v2"


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


def _find_model_dir() -> Optional[Path]:
    """
    Return the directory that contains model.pth + config.json, or None.

    We check several candidate locations because:
    - setup_offline.py places the model at  <xtts_home>/<MODEL_SUBDIR>/
    - The TTS model manager may place it at  <xtts_home>/tts/<MODEL_SUBDIR>/
    - Older TTS versions may have dropped files directly into <xtts_home>/tts/
    """
    home = _set_tts_home()
    candidates = [
        home / _MODEL_SUBDIR,                   # setup_offline.py location
        home / "tts" / _MODEL_SUBDIR,           # TTS model-manager location
        home / "tts",                            # flat drop (some TTS versions)
    ]
    for candidate in candidates:
        if (candidate / "model.pth").exists() and (candidate / "config.json").exists():
            return candidate
    return None


def is_model_ready() -> bool:
    """Return True if the XTTS v2 model files are present locally."""
    return _find_model_dir() is not None


def _patch_torchaudio_load() -> None:
    """
    torchaudio 2.9+ removed set_audio_backend() and now requires torchcodec
    for torchaudio.load() on macOS.  We don't want to pull in torchcodec, so
    we patch torchaudio.load with a soundfile-based implementation.

    soundfile handles WAV/FLAC/OGG natively; for MP3 it falls back to
    pydub/ffmpeg if available, otherwise raises a clear error.

    This is only called once (idempotent guard on _TORCHAUDIO_PATCHED).
    """
    import torchaudio  # noqa: PLC0415

    # Already patched in a previous call
    if getattr(torchaudio, "_ktts_soundfile_patched", False):
        return

    import torch
    import soundfile as sf

    def _sf_load(
        filepath,
        frame_offset: int = 0,
        num_frames: int = -1,
        normalize: bool = True,
        channels_first: bool = True,
        format=None,
        backend=None,
        encoding=None,
    ):
        path_str = str(filepath)
        sf_kwargs: dict = {"start": frame_offset, "dtype": "float32", "always_2d": True}
        if num_frames >= 0:
            sf_kwargs["frames"] = num_frames
        try:
            data, sample_rate = sf.read(path_str, **sf_kwargs)
        except Exception as exc:
            raise RuntimeError(
                f"torchaudio.load (soundfile fallback) could not read {path_str}: {exc}"
            ) from exc

        # soundfile → (frames, channels); torch expects (channels, frames)
        tensor = torch.from_numpy(data.T if channels_first else data)
        return tensor, sample_rate

    torchaudio.load = _sf_load
    torchaudio._ktts_soundfile_patched = True
    logger.info("torchaudio.load patched with soundfile backend")


# ---------------------------------------------------------------------------
# Engine
# ---------------------------------------------------------------------------

class XTTSEngine:
    def __init__(self) -> None:
        self._tts = None
        self._lock  = threading.Lock()
        self._loading = False
        self._load_error: str = ""
        self._speakers_cache: List[str] = []   # populated during load

    # ── Lazy loader ────────────────────────────────────────────────────────

    def ensure_loaded(self) -> None:
        """Load the XTTS v2 model from local files (no network access)."""
        if self._tts is not None:
            return
        with self._lock:
            if self._tts is not None:
                return

            # Suppress the interactive CLI license prompt — XTTS v2 is
            # available under the Coqui Public Model License (CPML) for
            # non-commercial use.  The user agrees by choosing to use it.
            os.environ["COQUI_TOS_AGREED"] = "1"
            _set_tts_home()

            model_dir = _find_model_dir()
            if model_dir is None:
                home = _set_tts_home()
                raise FileNotFoundError(
                    f"XTTS v2 model not found under {home}\n"
                    "Install it with:\n"
                    "  python setup_offline.py --xtts <xtts-v2-model-*.zip>"
                )

            from TTS.api import TTS

            # PyTorch ≥ 2.6 changed torch.load to default weights_only=True,
            # which blocks XTTS v2's checkpoints (they use custom pickle
            # classes like XttsConfig).  We know these files are from the
            # official Coqui release, so temporarily allow full unpickling.
            import torch as _torch
            _orig_load = _torch.load
            def _load_unsafe(*args, **kw):
                kw.setdefault("weights_only", False)
                return _orig_load(*args, **kw)
            _torch.load = _load_unsafe

            logger.info("Loading XTTS v2 from local files: %s", model_dir)
            try:
                # model_path must be the directory; TTS appends /model.pth itself.
                self._tts = TTS(
                    model_path=str(model_dir),
                    config_path=str(model_dir / "config.json"),
                    gpu=False,
                    progress_bar=False,
                )
            finally:
                _torch.load = _orig_load  # always restore

            # torchaudio 2.9+ removed the old audio-backend API and now needs
            # torchcodec for torchaudio.load().  Replace it with our soundfile
            # shim so XTTS voice-sample cloning works without torchcodec.
            _patch_torchaudio_load()

            # .speakers on TTS(nn.Module) is unreliable when loaded via
            # model_path — any internal AttributeError gets swallowed by
            # Module.__getattr__ and surfaces as a missing-attribute error.
            # Go straight to the speaker_manager to build our own list.
            try:
                sm = self._tts.synthesizer.tts_model.speaker_manager
                if sm is not None:
                    # name_to_id may be a dict or a dict_keys object;
                    # iterating either yields the speaker names directly.
                    if hasattr(sm, "name_to_id"):
                        self._speakers_cache = sorted(sm.name_to_id)
                    elif hasattr(sm, "speaker_names"):
                        self._speakers_cache = sorted(sm.speaker_names)
            except Exception as e:
                logger.warning("Could not load speaker list: %s", e)
                self._speakers_cache = []

            n = len(self._speakers_cache)
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
        return self._speakers_cache

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
        elif self._speakers_cache:
            kwargs["speaker"] = self._speakers_cache[0]

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
