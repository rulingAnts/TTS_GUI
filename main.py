#!/usr/bin/env python3
"""
Kokoro TTS Studio — pywebview desktop app entry point.

Import order is intentional:
  1. app_paths (no heavy deps)
  2. app_paths.setup_espeak_env()  ← must run before kokoro/torch import
  3. everything else
"""

# ── Step 1: path + espeak env setup (before any TTS imports) ──────────────
import app_paths
app_paths.setup_espeak_env()

# ── Step 2: stdlib + lightweight imports ──────────────────────────────────
import json
import logging
import os
import platform
import subprocess
import threading
import time
from datetime import datetime
from pathlib import Path

import webview

# ── Step 3: app modules (may import torch/kokoro internally) ──────────────
from tts_engine import TTSEngine
from piper_engine import PiperEngine
from voice_data import VOICE_DATA, PIPER_VOICE_DATA, validate_voice_data
from app_paths import (
    get_frontend_path,
    get_setup_frontend_path,
    get_outputs_path,
    get_models_path,
    get_piper_models_path,
    model_is_ready,
    piper_model_is_ready,
)

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
logger = logging.getLogger(__name__)


def _play_wav(path: str) -> None:
    """
    Play a WAV file using the best available method for the current platform.

    macOS  : afplay  (built-in, works from any thread)
    Windows: winsound (built-in Python module)
    Linux  : aplay → paplay → ffplay in order; falls back to sounddevice
    Any    : sounddevice as last resort
    """
    if sys.platform == "darwin":
        subprocess.run(["afplay", path], check=True)
        return

    if sys.platform == "win32":
        import winsound
        winsound.PlaySound(path, winsound.SND_FILENAME | winsound.SND_NODEFAULT)
        return

    # Linux / other — try common CLI players
    for cmd in [["aplay", path], ["paplay", path], ["ffplay", "-nodisp", "-autoexit", path]]:
        try:
            subprocess.run(cmd, check=True, capture_output=True)
            return
        except (FileNotFoundError, subprocess.CalledProcessError):
            continue

    # Universal fallback
    import soundfile as sf
    import sounddevice as sd
    data, sr = sf.read(path, dtype="float32")
    sd.play(data, sr)
    sd.wait()


def _check_espeak() -> bool:
    try:
        r = subprocess.run(
            ["espeak-ng", "--version"], capture_output=True, timeout=5
        )
        return r.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return False


def _espeak_install_hint() -> str:
    s = platform.system()
    if s == "Darwin":  return "brew install espeak-ng"
    if s == "Windows": return "https://github.com/espeak-ng/espeak-ng/releases"
    return "sudo apt-get install espeak-ng"


# ─────────────────────────────────────────────────────────────────────────────
# Main TTS API (exposed to the main window's JS)
# ─────────────────────────────────────────────────────────────────────────────

class Api:
    def __init__(self):
        self.window = None
        self._engine = TTSEngine()
        self._piper_engine = PiperEngine()
        self._voice_data, self._unavailable = validate_voice_data()
        self._running = False
        self._progress = 0.0
        self._current_chunk = 0
        self._total_chunks = 0
        self._cancel_flag = threading.Event()
        self._last_result: dict | None = None
        self._last_output_path: str | None = None
        # Piper model download state
        self._piper_downloading = False
        self._piper_dl_progress = 0.0
        self._piper_dl_status = ""
        self._piper_dl_done = False
        self._piper_dl_error: str | None = None
        self._piper_dl_cancel = threading.Event()

    def get_voice_data(self) -> dict:
        try:
            return {
                "voices": self._voice_data,
                "unavailable": self._unavailable,
                "piper_voices": PIPER_VOICE_DATA,
            }
        except Exception as exc:
            logger.error("get_voice_data: %s", exc)
            return {"voices": VOICE_DATA, "unavailable": [], "piper_voices": PIPER_VOICE_DATA, "error": str(exc)}

    def generate(self, params: dict) -> dict:
        try:
            if self._running:
                return {"started": False, "error": "Generation already in progress"}
            self._cancel_flag.clear()
            self._running = True
            self._progress = 0.0
            self._current_chunk = 0
            self._total_chunks = 0
            self._last_result = None
            threading.Thread(target=self._generate_thread, args=(params,), daemon=True).start()
            return {"started": True}
        except Exception as exc:
            logger.error("generate: %s", exc)
            self._running = False
            return {"started": False, "error": str(exc)}

    def _generate_thread(self, params: dict) -> None:
        try:
            text = params.get("text", "").strip()
            if not text:
                self._last_result = {"success": False, "error": "No text provided"}
                return

            engine    = params.get("engine", "kokoro")
            speed     = float(params.get("speed", 1.0))
            split_pat = params.get("split_pattern", r"\n\n+")
            out_fmt   = params.get("output_format", "wav").lower()
            post_proc = params.get("post_processing", {"normalize": True})

            out_dir  = params.get("output_dir") or str(get_outputs_path())
            out_name = (params.get("output_filename") or "").strip()
            if not out_name:
                out_name = f"output_{datetime.now().strftime('%Y%m%d_%H%M%S')}"

            ext = out_fmt if out_fmt in ("wav", "flac", "mp3") else "wav"
            output_path = str(Path(out_dir) / f"{out_name}.{ext}")
            Path(out_dir).mkdir(parents=True, exist_ok=True)

            def progress_cb(current: int, total: int) -> None:
                self._current_chunk = current
                self._total_chunks  = total
                self._progress = current / total if total > 0 else 0.0

            if engine == "piper":
                piper_voice = params.get("piper_voice", "id_ID-argis-medium")
                result_path = self._piper_engine.generate_audio(
                    text=text,
                    voice_id=piper_voice,
                    speed=speed,
                    split_pattern=split_pat,
                    post_proc_options=post_proc,
                    output_path=output_path,
                    output_format=out_fmt,
                    progress_callback=progress_cb,
                    cancel_flag=self._cancel_flag,
                )
            else:
                voice_specs = params.get("voices", [{"voice_id": "af_heart", "weight": 100}])
                lang_code   = params.get("lang_code", "a")
                result_path = self._engine.generate_audio(
                    text=text,
                    voice_specs=voice_specs,
                    lang_code=lang_code,
                    speed=speed,
                    split_pattern=split_pat,
                    post_proc_options=post_proc,
                    output_path=output_path,
                    output_format=out_fmt,
                    progress_callback=progress_cb,
                    cancel_flag=self._cancel_flag,
                )

            self._last_output_path = result_path
            duration = 0.0
            try:
                import soundfile as sf
                duration = sf.info(result_path).duration
            except Exception:
                pass

            self._last_result = {
                "success": True,
                "output_path": result_path,
                "duration_seconds": duration,
                "error": None,
            }
        except Exception as exc:
            logger.error("_generate_thread: %s", exc, exc_info=True)
            self._last_result = {"success": False, "error": str(exc)}
        finally:
            self._running = False
            self._progress = 1.0

    def preview(self, params: dict) -> dict:
        try:
            play_file = params.get("play_file", "")
            if play_file:
                return self._play_file(play_file)
            text = params.get("text", "")[:200].strip()
            if not text:
                return {"success": False, "error": "No text for preview"}

            engine = params.get("engine", "kokoro")
            speed  = float(params.get("speed", 1.0))

            if engine == "piper":
                self._piper_engine.preview_audio(
                    text=text,
                    voice_id=params.get("piper_voice", "id_ID-argis-medium"),
                    speed=speed,
                )
            else:
                self._engine.preview_audio(
                    text=text,
                    voice_specs=params.get("voices", [{"voice_id": "af_heart", "weight": 100}]),
                    lang_code=params.get("lang_code", "a"),
                    speed=speed,
                )
            return {"success": True, "error": None}
        except Exception as exc:
            logger.error("preview: %s", exc)
            return {"success": False, "error": str(exc)}

    def _play_file(self, path: str) -> dict:
        try:
            _play_wav(path)
            return {"success": True, "error": None}
        except Exception as exc:
            logger.error("_play_file: %s", exc)
            return {"success": False, "error": str(exc)}

    def cancel(self) -> None:
        self._cancel_flag.set()

    def get_progress(self) -> dict:
        return {
            "running": self._running,
            "progress": self._progress,
            "current_chunk": self._current_chunk,
            "total_chunks": self._total_chunks,
            "result": self._last_result,
            "last_output_path": self._last_output_path,
        }

    def browse_file(self) -> str:
        try:
            dialog = getattr(webview, "FileDialog", None)
            mode = dialog.OPEN if dialog else webview.OPEN_DIALOG  # type: ignore[attr-defined]
            result = self.window.create_file_dialog(
                mode,
                allow_multiple=False,
                file_types=("Text Files (*.txt)", "All Files (*.*)"),
            )
            return result[0] if result else ""
        except Exception as exc:
            logger.error("browse_file: %s", exc)
            return ""

    def browse_folder(self) -> str:
        try:
            dialog = getattr(webview, "FileDialog", None)
            mode = dialog.FOLDER if dialog else webview.FOLDER_DIALOG  # type: ignore[attr-defined]
            result = self.window.create_file_dialog(mode)
            return result[0] if result else ""
        except Exception as exc:
            logger.error("browse_folder: %s", exc)
            return ""

    def open_folder(self, path: str) -> None:
        try:
            target = path or str(get_outputs_path())
            sys_name = platform.system()
            if sys_name == "Darwin":   subprocess.Popen(["open", target])
            elif sys_name == "Windows": subprocess.Popen(["explorer", target])
            else:                       subprocess.Popen(["xdg-open", target])
        except Exception as exc:
            logger.error("open_folder: %s", exc)

    def load_file(self, path: str) -> str:
        try:
            with open(path, "r", encoding="utf-8") as fh:
                return fh.read()
        except Exception as exc:
            logger.error("load_file: %s", exc)
            return ""

    def get_default_output_dir(self) -> str:
        return str(get_outputs_path())

    def test_voice(self, voice_id: str) -> dict:
        """
        Play a short hardcoded sample sentence in the given voice.
        Generates to a temp WAV file and plays it via the OS system player
        (afplay on macOS, winsound on Windows) — more reliable than
        sounddevice in a pywebview GUI context.
        """
        _SAMPLE = "Hello! This is the selected voice. How does it sound to you?"
        tmp_path = None
        try:
            if self._running:
                return {"success": False, "error": "Generation in progress — try again after it finishes"}

            import numpy as np
            import soundfile as sf
            import tempfile

            from script_parser import lang_code_for_voice
            lang_code = lang_code_for_voice(voice_id)
            voice     = self._engine._resolve_voice(voice_id)
            pipeline  = self._engine._get_pipeline(lang_code)

            # Generate audio directly (avoids pause-parsing overhead)
            all_audio = []
            for _gs, _ps, audio in pipeline(_SAMPLE, voice=voice, speed=1.0):
                if audio is None or len(audio) == 0:
                    continue
                if hasattr(audio, "cpu"):
                    audio = audio.cpu().numpy()
                all_audio.append(np.asarray(audio, dtype=np.float32))

            if not all_audio:
                return {"success": False, "error": "Pipeline produced no audio — check terminal for errors"}

            combined = np.concatenate(all_audio)

            # Sanity check: flag silent output (e.g. MPS NaN-collapsed to zero)
            if not np.any(combined != 0):
                return {"success": False, "error": "Audio is silent — possible MPS compatibility issue; try restarting"}

            # Write temp WAV and play via OS player (bypasses PortAudio/sounddevice)
            with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
                tmp_path = f.name
            sf.write(tmp_path, combined, 24000)

            _play_wav(tmp_path)

            return {"success": True, "error": None}

        except Exception as exc:
            logger.error("test_voice %s: %s", voice_id, exc, exc_info=True)
            return {"success": False, "error": str(exc)}
        finally:
            if tmp_path:
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass

    # ------------------------------------------------------------------
    # Podcast / multi-speaker
    # ------------------------------------------------------------------

    def parse_script(self, text: str) -> dict:
        """Parse a stage-play script; return speakers and line count."""
        try:
            from script_parser import parse_script
            result = parse_script(text)
            if result.error:
                return {"success": False, "error": result.error}
            return {
                "success": True,
                "speakers": result.speakers,
                "line_count": len(result.lines),
                "speaker_line_counts": result.speaker_line_counts,
                # First 5 lines as a preview for the status bar
                "preview": [
                    {"speaker": l.speaker, "text": l.text[:80]}
                    for l in result.lines[:5]
                ],
            }
        except Exception as exc:
            logger.error("parse_script: %s", exc)
            return {"success": False, "error": str(exc)}

    def generate_podcast(self, params: dict) -> dict:
        """Start multi-speaker podcast generation in a background thread."""
        try:
            if self._running:
                return {"started": False, "error": "Generation already in progress"}
            self._cancel_flag.clear()
            self._running = True
            self._progress = 0.0
            self._current_chunk = 0
            self._total_chunks = 0
            self._last_result = None
            threading.Thread(
                target=self._generate_podcast_thread, args=(params,), daemon=True
            ).start()
            return {"started": True}
        except Exception as exc:
            logger.error("generate_podcast: %s", exc)
            self._running = False
            return {"started": False, "error": str(exc)}

    def _generate_podcast_thread(self, params: dict) -> None:
        try:
            from script_parser import parse_script

            text = params.get("text", "").strip()
            if not text:
                self._last_result = {"success": False, "error": "No script text"}
                return

            speaker_voices  = params.get("speaker_voices", {})
            post_proc       = params.get("post_processing", {"normalize": True})
            out_fmt         = params.get("output_format", "wav").lower()
            out_dir         = params.get("output_dir") or str(get_outputs_path())
            out_name        = (params.get("output_filename") or "").strip()
            between_ms      = int(params.get("between_speakers_ms", 500))

            if not out_name:
                out_name = f"podcast_{datetime.now().strftime('%Y%m%d_%H%M%S')}"

            ext = out_fmt if out_fmt in ("wav", "flac", "mp3") else "wav"
            output_path = str(Path(out_dir) / f"{out_name}.{ext}")
            Path(out_dir).mkdir(parents=True, exist_ok=True)

            parsed = parse_script(text)
            if parsed.error:
                self._last_result = {"success": False, "error": parsed.error}
                return

            lines = [{"speaker": l.speaker, "text": l.text} for l in parsed.lines]

            def progress_cb(current: int, total: int) -> None:
                self._current_chunk = current
                self._total_chunks  = total
                self._progress = current / total if total > 0 else 0.0

            result_path = self._engine.generate_podcast(
                lines=lines,
                speaker_voices=speaker_voices,
                post_proc_options=post_proc,
                output_path=output_path,
                output_format=out_fmt,
                progress_callback=progress_cb,
                cancel_flag=self._cancel_flag,
                between_speakers_ms=between_ms,
            )

            self._last_output_path = result_path
            duration = 0.0
            try:
                import soundfile as sf
                duration = sf.info(result_path).duration
            except Exception:
                pass

            self._last_result = {
                "success": True,
                "output_path": result_path,
                "duration_seconds": duration,
                "error": None,
            }
        except Exception as exc:
            logger.error("_generate_podcast_thread: %s", exc, exc_info=True)
            self._last_result = {"success": False, "error": str(exc)}
        finally:
            self._running = False
            self._progress = 1.0

    def get_piper_status(self) -> dict:
        return {"ready": piper_model_is_ready()}

    def start_piper_download(self) -> dict:
        if self._running:
            return {"started": False, "error": "Cannot download while generating"}
        if self._piper_downloading:
            return {"started": False, "error": "Already downloading"}
        self._piper_dl_cancel.clear()
        self._piper_downloading = True
        self._piper_dl_progress = 0.0
        self._piper_dl_status = "Starting…"
        self._piper_dl_done = False
        self._piper_dl_error = None
        threading.Thread(target=self._piper_download_thread, daemon=True).start()
        return {"started": True}

    def get_piper_download_progress(self) -> dict:
        return {
            "downloading": self._piper_downloading,
            "progress": self._piper_dl_progress,
            "status": self._piper_dl_status,
            "done": self._piper_dl_done,
            "error": self._piper_dl_error,
        }

    def _piper_download_thread(self) -> None:
        try:
            from model_downloader import download_piper_model

            dest = get_piper_models_path()
            logger.info("Downloading Piper model to %s", dest)

            def progress_cb(fraction: float, status: str) -> None:
                self._piper_dl_progress = fraction
                self._piper_dl_status = status

            download_piper_model(dest, progress_cb, self._piper_dl_cancel)
            self._piper_dl_done = True
            self._piper_dl_status = "Download complete"
            logger.info("Piper model download complete")
        except Exception as exc:
            logger.error("Piper download failed: %s", exc)
            self._piper_dl_error = str(exc)
        finally:
            self._piper_downloading = False
            self._piper_dl_progress = 1.0


# ─────────────────────────────────────────────────────────────────────────────
# First-run model download (setup window)
# ─────────────────────────────────────────────────────────────────────────────

class _DownloadApi:
    """JS API exposed during the first-run setup window."""

    def __init__(self):
        self.window = None
        self._cancel = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        """Called by on_setup_loaded; starts download in background thread."""
        self._cancel.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def retry(self) -> None:
        """Called from JS retry button."""
        self._cancel.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _run(self) -> None:
        try:
            from model_downloader import download_model

            dest = get_models_path() / "Kokoro-82M"
            logger.info("Downloading Kokoro model to %s", dest)

            def progress_cb(fraction: float, filename: str) -> None:
                pct = round(fraction * 100)
                short = Path(filename).name
                js = f"updateProgress({pct}, {json.dumps(short)})"
                try:
                    self.window.evaluate_js(js)
                except Exception:
                    pass

            download_model(dest, progress_cb, self._cancel)
            self.window.evaluate_js("onDownloadComplete()")
            time.sleep(1.5)
            _open_main_window(self.window)

        except Exception as exc:
            logger.error("Download failed: %s", exc)
            try:
                self.window.evaluate_js(f"onDownloadError({json.dumps(str(exc))})")
            except Exception:
                pass


def _open_main_window(setup_window=None) -> None:
    """Create the main TTS window, then destroy the setup window (if any)."""
    api = Api()
    html_path = str(get_frontend_path() / "index.html")

    main_win = webview.create_window(
        "Kokoro TTS Studio",
        html_path,
        js_api=api,
        width=1280,
        height=820,
        min_size=(860, 600),
        background_color="#1a1a2e",
    )
    api.window = main_win

    def on_main_loaded():
        if not _check_espeak():
            hint = _espeak_install_hint()
            msg = (
                "espeak-ng is not installed or not on PATH.\n\n"
                f"Install it with:\n  {hint}\n\n"
                "Text-to-speech will not work without it."
            )
            main_win.evaluate_js(f"showStartupError({json.dumps(msg)})")

    main_win.events.loaded += on_main_loaded

    if setup_window:
        setup_window.destroy()


# ─────────────────────────────────────────────────────────────────────────────
# Entry point
# ─────────────────────────────────────────────────────────────────────────────

def main() -> None:
    import sys

    if model_is_ready():
        # ── Normal launch ──────────────────────────────────────────────────
        api = Api()
        html_path = str(get_frontend_path() / "index.html")

        window = webview.create_window(
            "Kokoro TTS Studio",
            html_path,
            js_api=api,
            width=1280,
            height=820,
            min_size=(860, 600),
            background_color="#1a1a2e",
        )
        api.window = window

        def on_loaded():
            if not _check_espeak():
                hint = _espeak_install_hint()
                msg = (
                    "espeak-ng is not installed or not on PATH.\n\n"
                    f"Install it with:\n  {hint}\n\n"
                    "Text-to-speech will not work without it."
                )
                window.evaluate_js(f"showStartupError({json.dumps(msg)})")

        webview.start(on_loaded, debug=os.environ.get("TTS_DEBUG") == "1")

    else:
        # ── First-run: show setup/download window ──────────────────────────
        dl_api = _DownloadApi()
        setup_html = str(get_setup_frontend_path() / "setup.html")

        setup_win = webview.create_window(
            "Kokoro TTS Studio — First Run Setup",
            setup_html,
            js_api=dl_api,
            width=520,
            height=340,
            resizable=False,
            background_color="#0d0d1a",
        )
        dl_api.window = setup_win

        def on_setup_loaded():
            dl_api.start()

        webview.start(
            on_setup_loaded,
            debug=os.environ.get("TTS_DEBUG") == "1",
        )


if __name__ == "__main__":
    main()
