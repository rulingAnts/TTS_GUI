import logging
import threading
import numpy as np
from pathlib import Path
from typing import Callable, List

logger = logging.getLogger(__name__)


class PiperEngine:
    def __init__(self):
        self._voices: dict = {}
        self._lock = threading.Lock()

    def _get_voice(self, voice_id: str):
        if voice_id not in self._voices:
            with self._lock:
                if voice_id not in self._voices:
                    from piper import PiperVoice
                    from app_paths import get_piper_models_path

                    models_path = get_piper_models_path()
                    onnx_path = models_path / f"{voice_id}.onnx"
                    config_path = models_path / f"{voice_id}.onnx.json"

                    if not onnx_path.exists():
                        raise FileNotFoundError(
                            f"Piper model not found: {onnx_path}\n"
                            "Use the Download button in the Piper voice panel."
                        )

                    logger.info("Loading Piper voice: %s", voice_id)
                    cfg = str(config_path) if config_path.exists() else None
                    self._voices[voice_id] = PiperVoice.load(str(onnx_path), config_path=cfg)

        return self._voices[voice_id]

    def _synth_to_float32(self, voice, text: str, speed: float):
        """Synthesize text, return (float32 numpy array, sample_rate)."""
        # Piper length_scale: higher = slower. Invert our speed multiplier.
        length_scale = 1.0 / max(speed, 0.1)
        raw_chunks = list(voice.synthesize_stream_raw(text, length_scale=length_scale))
        if not raw_chunks:
            return np.zeros(0, dtype=np.float32), voice.config.sample_rate
        raw = b"".join(raw_chunks)
        audio_int16 = np.frombuffer(raw, dtype=np.int16)
        audio_float32 = audio_int16.astype(np.float32) / 32768.0
        return audio_float32, voice.config.sample_rate

    def generate_audio(
        self,
        text: str,
        voice_id: str,
        speed: float,
        split_pattern: str,
        post_proc_options: dict,
        output_path: str,
        output_format: str,
        progress_callback: Callable[[int, int], None],
        cancel_flag: threading.Event,
    ) -> str:
        from audio_processing import post_process
        from tts_engine import _parse_pause_segments, _expand_to_items
        from tts_engine import SAMPLE_RATE as KOKORO_RATE

        voice = self._get_voice(voice_id)
        sample_rate = voice.config.sample_rate

        pause_segments = _parse_pause_segments(text)
        items = _expand_to_items(pause_segments, split_pattern)
        text_chunks = [v for t, v in items if t == "chunk"]
        total_chunks = len(text_chunks)

        if total_chunks == 0:
            raise ValueError("No text to generate after splitting")

        all_audio: List[np.ndarray] = []
        chunk_idx = 0

        for item_type, item_val in items:
            if cancel_flag.is_set():
                logger.info("Piper generation cancelled at chunk %d/%d", chunk_idx, total_chunks)
                break

            if item_type == "silence":
                # item_val is sample count at Kokoro's 24 kHz; rescale to Piper's rate
                silence_samples = int(item_val * sample_rate / KOKORO_RATE)
                all_audio.append(np.zeros(silence_samples, dtype=np.float32))
            else:
                audio, _ = self._synth_to_float32(voice, item_val, speed)
                if audio.size > 0:
                    all_audio.append(audio)
                chunk_idx += 1
                progress_callback(chunk_idx, total_chunks)

        if not all_audio:
            raise RuntimeError("No audio was generated")

        combined = np.concatenate(all_audio)
        combined = post_process(combined, sample_rate, post_proc_options)
        self._save_audio(combined, output_path, output_format, sample_rate)
        return output_path

    def preview_audio(self, text: str, voice_id: str, speed: float) -> None:
        import sounddevice as sd

        preview_text = text[:200].strip()
        if not preview_text:
            raise ValueError("No text for preview")

        voice = self._get_voice(voice_id)
        audio, sample_rate = self._synth_to_float32(voice, preview_text, speed)

        if audio.size == 0:
            raise RuntimeError("No audio generated for preview")

        sd.play(audio, sample_rate)
        sd.wait()

    def _save_audio(self, audio: np.ndarray, path: str, fmt: str, sample_rate: int) -> None:
        import soundfile as sf
        import tempfile
        import os

        fmt_upper = fmt.upper()
        if fmt_upper in ("WAV", "FLAC"):
            sf.write(path, audio, sample_rate, format=fmt_upper)
        elif fmt_upper == "MP3":
            try:
                import pydub
                with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
                    tmp = f.name
                try:
                    sf.write(tmp, audio, sample_rate, format="WAV")
                    seg = pydub.AudioSegment.from_wav(tmp)
                    seg.export(path, format="mp3", bitrate="192k")
                finally:
                    os.unlink(tmp)
            except ImportError:
                wav_path = path.replace(".mp3", ".wav")
                sf.write(wav_path, audio, sample_rate, format="WAV")
                raise RuntimeError(f"pydub not installed; saved as WAV: {wav_path}")
            except Exception as exc:
                wav_path = path.replace(".mp3", ".wav")
                sf.write(wav_path, audio, sample_rate, format="WAV")
                raise RuntimeError(f"MP3 export failed ({exc}); saved as WAV: {wav_path}")
        else:
            sf.write(path, audio, sample_rate, format="WAV")
