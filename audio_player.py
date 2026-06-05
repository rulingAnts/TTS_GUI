"""
audio_player.py — Cross-platform audio file playback.

Shared by main.py (test_voice, play_last) and xtts_engine.py (preview).
Avoids a circular import between those two modules.
"""

import subprocess
import sys


def play_wav(path: str) -> None:
    """
    Play a WAV file using the best available method for the current platform.

    macOS  : afplay  (built-in, works from any GUI thread)
    Windows: winsound (built-in Python stdlib)
    Linux  : aplay → paplay → ffplay, then sounddevice as last resort
    """
    if sys.platform == "darwin":
        subprocess.run(["afplay", path], check=True)
        return

    if sys.platform == "win32":
        import winsound
        winsound.PlaySound(path, winsound.SND_FILENAME | winsound.SND_NODEFAULT)
        return

    # Linux / other — try common CLI players in order
    for cmd in [
        ["aplay",  path],
        ["paplay", path],
        ["ffplay", "-nodisp", "-autoexit", path],
    ]:
        try:
            subprocess.run(cmd, check=True, capture_output=True)
            return
        except (FileNotFoundError, subprocess.CalledProcessError):
            continue

    # Universal fallback via sounddevice
    import soundfile as sf
    import sounddevice as sd
    data, sr = sf.read(path, dtype="float32")
    sd.play(data, sr)
    sd.wait()
