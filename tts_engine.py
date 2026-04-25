import re
import logging
import threading
import numpy as np
from pathlib import Path
from typing import Callable, List, Optional

logger = logging.getLogger(__name__)

SAMPLE_RATE = 24000
_HERE = Path(__file__).parent


# ---------------------------------------------------------------------------
# Voice tensor helpers
# ---------------------------------------------------------------------------

def _find_voice_tensor_path(voice_id: str) -> Optional[Path]:
    """Locate the .pt file for a voice by searching common install paths."""
    try:
        import kokoro

        kokoro_dir = Path(kokoro.__file__).parent
        search_dirs = [
            # Local offline bundle (populated by setup_offline.py)
            _HERE / "models" / "Kokoro-82M" / "voices",
            _HERE / "models" / "voices",
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

    # Renormalise weights for successfully loaded tensors
    w_total = sum(weights)
    weights = [w / w_total for w in weights]
    blended = sum(w * t for w, t in zip(weights, tensors))
    return blended


# ---------------------------------------------------------------------------
# TTS Engine
# ---------------------------------------------------------------------------

class TTSEngine:
    def __init__(self):
        self._pipelines: dict = {}
        self._lock = threading.Lock()

    def _get_pipeline(self, lang_code: str):
        if lang_code not in self._pipelines:
            with self._lock:
                if lang_code not in self._pipelines:
                    from kokoro import KPipeline

                    logger.info("Loading Kokoro pipeline for lang_code=%s", lang_code)
                    self._pipelines[lang_code] = KPipeline(lang_code=lang_code)
        return self._pipelines[lang_code]

    def _prepare_voice(self, voice_specs: List[dict]):
        """Return a voice string or blended tensor."""
        if not voice_specs:
            return "af_heart"
        if len(voice_specs) == 1:
            return voice_specs[0]["voice_id"]
        return blend_voices(voice_specs)

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
        """
        Generate audio from text, apply post-processing, and save to file.
        Returns the output path. Raises on complete failure; saves partial
        audio if some chunks succeeded before cancellation/error.
        """
        from audio_processing import post_process

        pipeline = self._get_pipeline(lang_code)
        voice = self._prepare_voice(voice_specs)

        # Split text into chunks
        if split_pattern:
            chunks = [c.strip() for c in re.split(split_pattern, text) if c.strip()]
        else:
            chunks = [text.strip()] if text.strip() else []

        if not chunks:
            raise ValueError("No text to generate after splitting")

        all_audio: List[np.ndarray] = []
        failed_chunk: Optional[int] = None

        for i, chunk in enumerate(chunks):
            if cancel_flag.is_set():
                logger.info("Cancelled at chunk %d/%d", i + 1, len(chunks))
                break
            try:
                for _gs, _ps, audio in pipeline(chunk, voice=voice, speed=speed):
                    if cancel_flag.is_set():
                        break
                    if audio is not None and len(audio) > 0:
                        all_audio.append(np.array(audio, dtype=np.float32))
            except Exception as exc:
                logger.error("Error on chunk %d: %s", i + 1, exc)
                failed_chunk = i + 1
                # Continue to save partial audio rather than aborting
            finally:
                progress_callback(i + 1, len(chunks))

        if not all_audio:
            raise RuntimeError("No audio was generated")

        combined = np.concatenate(all_audio)
        combined = post_process(combined, SAMPLE_RATE, post_proc_options)

        self._save_audio(combined, output_path, output_format)

        if failed_chunk is not None:
            raise RuntimeError(
                f"Partial audio saved to {output_path} "
                f"(chunk {failed_chunk} failed)"
            )

        return output_path

    def preview_audio(
        self,
        text: str,
        voice_specs: List[dict],
        lang_code: str,
        speed: float,
    ) -> None:
        """Generate and play up to 200 chars of audio without saving."""
        import sounddevice as sd

        preview_text = text[:200].strip()
        if not preview_text:
            raise ValueError("No text for preview")

        pipeline = self._get_pipeline(lang_code)
        voice = self._prepare_voice(voice_specs)

        all_audio: List[np.ndarray] = []
        for _gs, _ps, audio in pipeline(preview_text, voice=voice, speed=speed):
            if audio is not None and len(audio) > 0:
                all_audio.append(np.array(audio, dtype=np.float32))

        if not all_audio:
            raise RuntimeError("No audio generated for preview")

        combined = np.concatenate(all_audio)
        sd.play(combined, SAMPLE_RATE)
        sd.wait()

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
            raise RuntimeError(
                f"pydub not installed; saved as WAV instead: {wav_path}"
            )
        except Exception as exc:
            wav_path = path.replace(".mp3", ".wav")
            sf.write(wav_path, audio, SAMPLE_RATE, format="WAV")
            raise RuntimeError(
                f"MP3 export failed ({exc}); saved as WAV: {wav_path}"
            )
