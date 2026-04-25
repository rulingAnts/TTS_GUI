import logging
import numpy as np

logger = logging.getLogger(__name__)


def post_process(audio: np.ndarray, sample_rate: int, options: dict) -> np.ndarray:
    """Apply post-processing effects to audio. Returns processed array."""
    if audio is None or len(audio) == 0:
        return audio

    try:
        import librosa

        if options.get("normalize", True):
            peak = np.max(np.abs(audio))
            if peak > 0:
                audio = librosa.util.normalize(audio)

        if options.get("trim_silence", False):
            db = options.get("trim_db", -40)
            audio, _ = librosa.effects.trim(audio, top_db=abs(db))

        if options.get("noise_gate", False):
            threshold = 0.01
            audio = np.where(np.abs(audio) < threshold, 0.0, audio)

        if options.get("fade_in_ms", 0) > 0:
            n = min(int(sample_rate * options["fade_in_ms"] / 1000), len(audio))
            audio = audio.copy()
            audio[:n] *= np.linspace(0.0, 1.0, n)

        if options.get("fade_out_ms", 0) > 0:
            n = min(int(sample_rate * options["fade_out_ms"] / 1000), len(audio))
            audio = audio.copy()
            audio[-n:] *= np.linspace(1.0, 0.0, n)

    except ImportError:
        logger.warning("librosa not available; skipping post-processing")
    except Exception as exc:
        logger.error("Post-processing error: %s", exc)

    return audio
