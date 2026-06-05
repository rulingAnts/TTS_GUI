import re
import logging
import threading
import numpy as np
from pathlib import Path
from typing import Callable, List, Optional, Union

logger = logging.getLogger(__name__)

SAMPLE_RATE = 24000

def _get_base() -> Path:
    """Use app_paths if available (works in both dev and PyInstaller)."""
    try:
        from app_paths import get_base_path, get_models_path
        return get_base_path()
    except ImportError:
        return Path(__file__).parent

# Pause durations inserted for (...) markers (in milliseconds)
SHORT_PAUSE_MS = 500   # inline (...)  within a sentence
LONG_PAUSE_MS  = 1200  # standalone (...) on its own line


# ---------------------------------------------------------------------------
# Pause-marker parsing
# ---------------------------------------------------------------------------

_LONG_TOKEN  = "\x00LONG_PAUSE\x00"
_SHORT_TOKEN = "\x00SHORT_PAUSE\x00"
_TOKEN_RE    = re.compile(
    f"({re.escape(_LONG_TOKEN)}|{re.escape(_SHORT_TOKEN)})"
)


def _parse_pause_segments(text: str) -> List[Union[str, int]]:
    """
    Split text into a sequence of alternating content and silence items:
      str → text to synthesise
      int → silence length in samples

    Rules applied in order:
      1. A line containing *only* (...) (plus optional whitespace) →
         LONG_PAUSE_MS of silence (paragraph-level beat).
      2. (...) appearing inline within other text →
         SHORT_PAUSE_MS of silence.
    """
    # 1. Replace standalone-line (...) with a unique token
    processed = re.sub(r"(?m)^[ \t]*\(\.{3}\)[ \t]*$", _LONG_TOKEN, text)
    # 2. Replace any remaining inline (...) with a shorter-pause token
    processed = processed.replace("(...)", _SHORT_TOKEN)

    long_samples  = int(SAMPLE_RATE * LONG_PAUSE_MS  / 1000)
    short_samples = int(SAMPLE_RATE * SHORT_PAUSE_MS / 1000)

    segments: List[Union[str, int]] = []
    for part in _TOKEN_RE.split(processed):
        if part == _LONG_TOKEN:
            segments.append(long_samples)
        elif part == _SHORT_TOKEN:
            segments.append(short_samples)
        elif part.strip():
            segments.append(part)

    return segments


def _expand_to_items(
    segments: List[Union[str, int]],
    split_pattern: str,
) -> List[tuple]:
    """
    Expand pause segments into a flat list of ("chunk", str) and
    ("silence", int) items, applying the user's split_pattern to each
    text segment.
    """
    items = []
    for seg in segments:
        if isinstance(seg, int):
            items.append(("silence", seg))
        else:
            if split_pattern:
                chunks = [c.strip() for c in re.split(split_pattern, seg) if c.strip()]
            else:
                chunks = [seg.strip()] if seg.strip() else []
            for chunk in chunks:
                items.append(("chunk", chunk))
    return items


# ---------------------------------------------------------------------------
# Voice tensor helpers
# ---------------------------------------------------------------------------

def _find_voice_tensor_path(voice_id: str) -> Optional[Path]:
    """Locate the .pt file for a voice by searching common install paths."""
    try:
        import kokoro

        kokoro_dir = Path(kokoro.__file__).parent
        base = _get_base()
        # Also check user app-data models dir (populated by first-run download)
        try:
            from app_paths import get_models_path
            user_models = get_models_path() / "Kokoro-82M" / "voices"
        except ImportError:
            user_models = None

        search_dirs = [
            *([] if user_models is None else [user_models]),
            # Local offline bundle (populated by setup_offline.py)
            base / "models" / "Kokoro-82M" / "voices",
            base / "models" / "voices",
            # Pip package install directory
            kokoro_dir / "voices",
            kokoro_dir / "voice",
            kokoro_dir,
            Path.home() / ".cache" / "kokoro" / "voices",
        ]
        for d in search_dirs:
            p = d / f"{voice_id}.pt"
            if p.exists():
                return p

        # HuggingFace hub cache
        hf_cache = Path.home() / ".cache" / "huggingface" / "hub"
        if hf_cache.exists():
            for voices_dir in hf_cache.rglob("voices"):
                p = voices_dir / f"{voice_id}.pt"
                if p.exists():
                    return p
    except Exception as exc:
        logger.error("Error searching for voice tensor %s: %s", voice_id, exc)
    return None


def blend_voices(voice_specs: List[dict]):
    """
    Blend voice tensors by weighted average.
    voice_specs: [{"voice_id": "af_heart", "weight": 60}, ...]
    Returns a torch.Tensor (blended) or a str (single voice ID on fallback).
    """
    import torch

    if len(voice_specs) == 1:
        return voice_specs[0]["voice_id"]

    total_weight = sum(v["weight"] for v in voice_specs)
    if total_weight <= 0:
        return voice_specs[0]["voice_id"]

    tensors = []
    weights = []

    for spec in voice_specs:
        path = _find_voice_tensor_path(spec["voice_id"])
        if path is None:
            logger.warning("Cannot find tensor for %s — skipping in blend", spec["voice_id"])
            continue
        try:
            t = torch.load(path, weights_only=True)
            tensors.append(t)
            weights.append(spec["weight"] / total_weight)
        except Exception as exc:
            logger.error("Error loading tensor for %s: %s", spec["voice_id"], exc)

    if not tensors:
        logger.warning("No tensors loaded; falling back to first voice")
        return voice_specs[0]["voice_id"]

    if len(tensors) == 1:
        return tensors[0]

    w_total = sum(weights)
    weights = [w / w_total for w in weights]
    blended = sum(w * t for w, t in zip(weights, tensors))
    return blended


# ---------------------------------------------------------------------------
# TTS Engine
# ---------------------------------------------------------------------------

def _best_device() -> str:
    """
    Return the best PyTorch device that Kokoro is known to work on.

    MPS (Apple Silicon GPU) is intentionally skipped: Kokoro's model uses
    ops (e.g. split_with_sizes) that are not yet fully supported on MPS,
    causing RuntimeErrors during inference.  CPU on M-series Macs is fast
    enough and completely reliable.  Revisit once Kokoro adds MPS support.

    To override, set the TTS_DEVICE environment variable:
        TTS_DEVICE=mps python3 main.py   # try MPS at your own risk
        TTS_DEVICE=cuda python3 main.py  # Nvidia GPU
    """
    import os, torch

    override = os.environ.get("TTS_DEVICE", "").strip().lower()
    if override:
        logger.info("TTS_DEVICE override: %s", override)
        return override

    if torch.cuda.is_available():
        return "cuda"
    return "cpu"


class TTSEngine:
    def __init__(self):
        self._pipelines: dict = {}
        self._voice_cache: dict = {}   # voice_id → loaded tensor (or str fallback)
        self._lock = threading.Lock()
        self._device = _best_device()
        logger.info("TTS device: %s", self._device)

    def _get_pipeline(self, lang_code: str):
        if lang_code not in self._pipelines:
            with self._lock:
                if lang_code not in self._pipelines:
                    from kokoro import KPipeline

                    logger.info(
                        "Loading Kokoro pipeline lang_code=%s device=%s",
                        lang_code, self._device,
                    )
                    try:
                        # Kokoro ≥ 0.9.4 accepts a device kwarg
                        self._pipelines[lang_code] = KPipeline(
                            lang_code=lang_code, device=self._device
                        )
                    except TypeError:
                        # Older versions don't — fall back silently
                        logger.warning("KPipeline doesn't accept device=; running on CPU")
                        self._pipelines[lang_code] = KPipeline(lang_code=lang_code)
        return self._pipelines[lang_code]

    def _resolve_voice(self, voice_id: str):
        """
        Return a pre-loaded voice tensor for voice_id (cached after first load),
        moved to the active device so it's ready for the pipeline.
        Falls back to the bare string if no local .pt file is found.
        """
        if voice_id not in self._voice_cache:
            path = _find_voice_tensor_path(voice_id)
            if path is not None:
                try:
                    import torch
                    tensor = torch.load(path, weights_only=True)
                    self._voice_cache[voice_id] = tensor.to(self._device)
                    logger.info("Loaded voice tensor %s → %s", voice_id, self._device)
                except Exception as exc:
                    logger.warning(
                        "Could not load tensor for %s (%s); falling back to string",
                        voice_id, exc,
                    )
                    self._voice_cache[voice_id] = voice_id
            else:
                logger.warning("No local tensor for %s — will attempt HF download", voice_id)
                self._voice_cache[voice_id] = voice_id
        return self._voice_cache[voice_id]

    def _prepare_voice(self, voice_specs: List[dict]):
        if not voice_specs:
            return self._resolve_voice("af_heart")
        if len(voice_specs) == 1:
            return self._resolve_voice(voice_specs[0]["voice_id"])
        return blend_voices(voice_specs)

    def _synthesise_chunk(self, pipeline, chunk: str, voice, speed: float) -> List[np.ndarray]:
        """Run one text chunk through the pipeline, return list of CPU float32 arrays."""
        parts = []
        for _gs, _ps, audio in pipeline(chunk, voice=voice, speed=speed):
            if audio is None or len(audio) == 0:
                continue
            # Pipeline may return a torch tensor (possibly on MPS/CUDA) or a numpy array
            if hasattr(audio, "cpu"):
                audio = audio.cpu().numpy()
            parts.append(np.asarray(audio, dtype=np.float32))
        return parts

    # ------------------------------------------------------------------
    # Public: full generation
    # ------------------------------------------------------------------

    def generate_audio(
        self,
        text: str,
        voice_specs: List[dict],
        lang_code: str,
        speed: float,
        split_pattern: str,
        post_proc_options: dict,
        output_path: str,
        output_format: str,
        progress_callback: Callable[[int, int], None],
        cancel_flag: threading.Event,
    ) -> str:
        from audio_processing import post_process

        pipeline = self._get_pipeline(lang_code)
        voice    = self._prepare_voice(voice_specs)

        # Parse (...) pause markers, then expand text segments into chunks
        pause_segments = _parse_pause_segments(text)
        items = _expand_to_items(pause_segments, split_pattern)

        text_chunks = [v for t, v in items if t == "chunk"]
        total_chunks = len(text_chunks)

        if total_chunks == 0:
            raise ValueError("No text to generate after splitting")

        all_audio: List[np.ndarray] = []
        failed_chunk: Optional[int] = None
        chunk_idx = 0

        for item_type, item_val in items:
            if cancel_flag.is_set():
                logger.info("Cancelled at chunk %d/%d", chunk_idx, total_chunks)
                break

            if item_type == "silence":
                # Insert silence for (...) pause markers
                all_audio.append(np.zeros(item_val, dtype=np.float32))
            else:
                try:
                    parts = self._synthesise_chunk(pipeline, item_val, voice, speed)
                    all_audio.extend(parts)
                except Exception as exc:
                    logger.error("Error on chunk %d: %s", chunk_idx + 1, exc)
                    failed_chunk = chunk_idx + 1
                finally:
                    chunk_idx += 1
                    progress_callback(chunk_idx, total_chunks)

        if not all_audio:
            raise RuntimeError("No audio was generated")

        combined = np.concatenate(all_audio)
        combined = post_process(combined, SAMPLE_RATE, post_proc_options)
        self._save_audio(combined, output_path, output_format)

        if failed_chunk is not None:
            raise RuntimeError(
                f"Partial audio saved to {output_path} (chunk {failed_chunk} failed)"
            )

        return output_path

    # ------------------------------------------------------------------
    # Public: preview (first 200 chars, played via sounddevice)
    # ------------------------------------------------------------------

    def preview_audio(
        self,
        text: str,
        voice_specs: List[dict],
        lang_code: str,
        speed: float,
    ) -> None:
        import sounddevice as sd

        preview_text = text[:200].strip()
        if not preview_text:
            raise ValueError("No text for preview")

        pipeline = self._get_pipeline(lang_code)
        voice    = self._prepare_voice(voice_specs)

        # Honour pause markers even in preview
        pause_segments = _parse_pause_segments(preview_text)
        items = _expand_to_items(pause_segments, split_pattern="")

        all_audio: List[np.ndarray] = []
        for item_type, item_val in items:
            if item_type == "silence":
                all_audio.append(np.zeros(item_val, dtype=np.float32))
            else:
                all_audio.extend(self._synthesise_chunk(pipeline, item_val, voice, speed))

        if not all_audio:
            raise RuntimeError("No audio generated for preview")

        combined = np.concatenate(all_audio)
        sd.play(combined, SAMPLE_RATE)
        sd.wait()

    # ------------------------------------------------------------------
    # Public: podcast (multi-speaker script) generation
    # ------------------------------------------------------------------

    def generate_podcast(
        self,
        lines: List[dict],
        speaker_voices: dict,
        post_proc_options: dict,
        output_path: str,
        output_format: str,
        progress_callback: Callable[[int, int], None],
        cancel_flag: threading.Event,
        between_speakers_ms: int = 500,
        between_same_ms: int = 150,
    ) -> str:
        """
        Generate multi-speaker audio from a list of parsed script lines.

        lines:          [{"speaker": "Seth", "text": "Hello..."}, ...]
        speaker_voices: {"Seth": {"voice_id": "am_echo", "lang_code": "a", "speed": 1.0}, ...}
        between_speakers_ms: silence when the speaker changes
        between_same_ms:     silence between consecutive lines from the same speaker
        """
        from audio_processing import post_process

        if not lines:
            raise ValueError("No lines to generate")

        gap_change = np.zeros(int(SAMPLE_RATE * between_speakers_ms / 1000), dtype=np.float32)
        gap_same   = np.zeros(int(SAMPLE_RATE * between_same_ms   / 1000), dtype=np.float32)

        all_audio: List[np.ndarray] = []
        prev_speaker: str | None = None
        total = len(lines)

        for i, line in enumerate(lines):
            if cancel_flag.is_set():
                logger.info("Podcast generation cancelled at line %d/%d", i + 1, total)
                break

            speaker  = line["speaker"]
            text     = line["text"].strip()
            cfg      = speaker_voices.get(speaker, {})
            voice_id = cfg.get("voice_id", "af_heart")
            lang     = cfg.get("lang_code", "a")
            speed    = float(cfg.get("speed", 1.0))
            voice    = self._resolve_voice(voice_id)   # load tensor locally

            # Gap between turns
            if prev_speaker is not None:
                all_audio.append(
                    gap_change.copy() if speaker != prev_speaker else gap_same.copy()
                )

            # Expand any (...) pause markers within this line
            pause_segs = _parse_pause_segments(text)
            items = _expand_to_items(pause_segs, split_pattern="")

            pipeline = self._get_pipeline(lang)

            for item_type, item_val in items:
                if cancel_flag.is_set():
                    break
                if item_type == "silence":
                    all_audio.append(np.zeros(item_val, dtype=np.float32))
                else:
                    try:
                        parts = self._synthesise_chunk(pipeline, item_val, voice, speed)
                        all_audio.extend(parts)
                    except Exception as exc:
                        logger.error("Error on line %d (%s): %s", i + 1, speaker, exc)

            prev_speaker = speaker
            progress_callback(i + 1, total)

        if not all_audio:
            raise RuntimeError("No audio was generated")

        combined = np.concatenate(all_audio)
        combined = post_process(combined, SAMPLE_RATE, post_proc_options)
        self._save_audio(combined, output_path, output_format)
        return output_path

    # ------------------------------------------------------------------
    # Audio file saving
    # ------------------------------------------------------------------

    def _save_audio(self, audio: np.ndarray, path: str, fmt: str) -> None:
        import soundfile as sf

        fmt_upper = fmt.upper()
        if fmt_upper in ("WAV", "FLAC"):
            sf.write(path, audio, SAMPLE_RATE, format=fmt_upper)
        elif fmt_upper == "MP3":
            self._save_mp3(audio, path)
        else:
            sf.write(path, audio, SAMPLE_RATE, format="WAV")

    def _save_mp3(self, audio: np.ndarray, path: str) -> None:
        import soundfile as sf
        import tempfile
        import os

        try:
            import pydub

            with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
                tmp = f.name
            try:
                sf.write(tmp, audio, SAMPLE_RATE, format="WAV")
                seg = pydub.AudioSegment.from_wav(tmp)
                seg.export(path, format="mp3", bitrate="192k")
            finally:
                os.unlink(tmp)
        except ImportError:
            wav_path = path.replace(".mp3", ".wav")
            sf.write(wav_path, audio, SAMPLE_RATE, format="WAV")
            raise RuntimeError(f"pydub not installed; saved as WAV instead: {wav_path}")
        except Exception as exc:
            wav_path = path.replace(".mp3", ".wav")
            sf.write(wav_path, audio, SAMPLE_RATE, format="WAV")
            raise RuntimeError(f"MP3 export failed ({exc}); saved as WAV: {wav_path}")
