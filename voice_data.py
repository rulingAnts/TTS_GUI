import logging
from pathlib import Path
from typing import Set, Tuple

logger = logging.getLogger(__name__)

VOICE_DATA = {
    "American English": {
        "lang_code": "a",
        "accents": {
            "American English": {
                "female": [
                    "af_alloy", "af_aoede", "af_bella", "af_heart",
                    "af_jessica", "af_kore", "af_nicole", "af_nova",
                    "af_river", "af_sarah", "af_sky",
                ],
                "male": [
                    "am_adam", "am_echo", "am_eric", "am_fenrir",
                    "am_liam", "am_michael", "am_onyx", "am_puck", "am_santa",
                ],
            }
        },
    },
    "British English": {
        "lang_code": "b",
        "accents": {
            "British English": {
                "female": ["bf_emma", "bf_isabella"],
                "male": ["bm_george", "bm_lewis"],
            }
        },
    },
    "Japanese": {
        "lang_code": "j",
        "accents": {
            "Japanese": {
                "female": ["jf_alpha", "jf_gongitsune", "jf_nezumi", "jf_tebukuro"],
                "male": ["jm_kumo"],
            }
        },
    },
    "Mandarin Chinese": {
        "lang_code": "z",
        "accents": {
            "Mandarin Chinese": {
                "female": ["zf_xiaobei", "zf_xiaoni", "zf_xiaoxiao", "zf_xiaoyi"],
                "male": ["zm_yunjian", "zm_yunxi", "zm_yunxia", "zm_yunyang"],
            }
        },
    },
    "Spanish": {
        "lang_code": "e",
        "accents": {
            "Spanish": {
                "female": ["ef_dora"],
                "male": ["em_alex", "em_santa"],
            }
        },
    },
    "French": {
        "lang_code": "f",
        "accents": {
            "French": {
                "female": ["ff_siwis"],
                "male": [],
            }
        },
    },
    "Hindi": {
        "lang_code": "h",
        "accents": {
            "Hindi": {
                "female": ["hf_alpha", "hf_beta"],
                "male": ["hm_omega", "hm_psi"],
            }
        },
    },
    "Italian": {
        "lang_code": "i",
        "accents": {
            "Italian": {
                "female": ["if_sara"],
                "male": ["im_nicola"],
            }
        },
    },
    "Brazilian Portuguese": {
        "lang_code": "p",
        "accents": {
            "Brazilian Portuguese": {
                "female": ["pf_dora"],
                "male": ["pm_alex", "pm_santa"],
            }
        },
    },
}


def get_all_voice_ids() -> Set[str]:
    ids: Set[str] = set()
    for lang_data in VOICE_DATA.values():
        for accent_data in lang_data["accents"].values():
            ids.update(accent_data.get("female", []))
            ids.update(accent_data.get("male", []))
    return ids


_HERE = Path(__file__).parent


def discover_installed_voices() -> Set[str]:
    available: Set[str] = set()
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
            if d.is_dir():
                for pt in d.glob("*.pt"):
                    available.add(pt.stem)

        # HuggingFace hub cache
        hf_cache = Path.home() / ".cache" / "huggingface" / "hub"
        if hf_cache.exists():
            for voices_dir in hf_cache.rglob("voices"):
                if voices_dir.is_dir():
                    for pt in voices_dir.glob("*.pt"):
                        available.add(pt.stem)

        # Try KPipeline.list_voices() if the method exists
        try:
            from kokoro import KPipeline

            if hasattr(KPipeline, "list_voices"):
                available.update(KPipeline.list_voices())
        except Exception as exc:
            logger.debug("KPipeline.list_voices() not available: %s", exc)

    except ImportError:
        logger.warning("kokoro package not installed; cannot validate voices")
    except Exception as exc:
        logger.error("Error discovering installed voices: %s", exc)

    return available


def validate_voice_data() -> Tuple[dict, list]:
    """
    Returns (VOICE_DATA, unavailable_ids).
    unavailable_ids is a list of voice IDs present in VOICE_DATA but not
    found in the local Kokoro installation.
    """
    installed = discover_installed_voices()

    if not installed:
        logger.info("Could not enumerate installed voices; assuming all available")
        return VOICE_DATA, []

    unavailable = []
    for voice_id in get_all_voice_ids():
        if voice_id not in installed:
            logger.warning("Voice listed but not found in installation: %s", voice_id)
            unavailable.append(voice_id)

    return VOICE_DATA, unavailable
