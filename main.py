"""
Kokoro TTS Studio — pywebview desktop app entry point.
"""

import json
import logging
import os
import platform
import subprocess
import threading
from datetime import datetime
from pathlib import Path

import webview

from tts_engine import TTSEngine
from voice_data import VOICE_DATA, validate_voice_data

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
logger = logging.getLogger(__name__)

_HERE = Path(__file__).parent
DEFAULT_OUTPUT_DIR = str(_HERE / "outputs")


def _check_espeak() -> bool:
    try:
        r = subprocess.run(
            ["espeak-ng", "--version"], capture_output=True, timeout=5
        )
        return r.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return False


def _espeak_install_hint() -> str:
    sys = platform.system()
    if sys == "Darwin":
        return "brew install espeak-ng"
    if sys == "Windows":
        return "https://github.com/espeak-ng/espeak-ng/releases"
    return "sudo apt-get install espeak-ng"


# ---------------------------------------------------------------------------
# pywebview API class
# ---------------------------------------------------------------------------

class Api:
    def __init__(self):
        self.window = None  # Injected after webview.create_window()
        self._engine = TTSEngine()
        self._voice_data, self._unavailable = validate_voice_data()

        # Generation state
        self._running = False
        self._progress = 0.0
        self._current_chunk = 0
        self._total_chunks = 0
        self._cancel_flag = threading.Event()
        self._last_result: dict | None = None
        self._last_output_path: str | None = None

        Path(DEFAULT_OUTPUT_DIR).mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------
    # Voice data
    # ------------------------------------------------------------------

    def get_voice_data(self) -> dict:
        try:
            return {
                "voices": self._voice_data,
                "unavailable": self._unavailable,
            }
        except Exception as exc:
            logger.error("get_voice_data: %s", exc)
            return {"voices": VOICE_DATA, "unavailable": [], "error": str(exc)}

    # ------------------------------------------------------------------
    # Generation
    # ------------------------------------------------------------------

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

            t = threading.Thread(target=self._generate_thread, args=(params,), daemon=True)
            t.start()
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

            voice_specs = params.get("voices", [{"voice_id": "af_heart", "weight": 100}])
            lang_code = params.get("lang_code", "a")
            speed = float(params.get("speed", 1.0))
            split_pattern = params.get("split_pattern", r"\n\n+")
            output_format = params.get("output_format", "wav").lower()
            post_proc = params.get("post_processing", {"normalize": True})

            out_dir = params.get("output_dir") or DEFAULT_OUTPUT_DIR
            out_name = (params.get("output_filename") or "").strip()
            if not out_name:
                out_name = f"output_{datetime.now().strftime('%Y%m%d_%H%M%S')}"

            ext = output_format if output_format in ("wav", "flac", "mp3") else "wav"
            output_path = str(Path(out_dir) / f"{out_name}.{ext}")
            Path(out_dir).mkdir(parents=True, exist_ok=True)

            def progress_cb(current: int, total: int) -> None:
                self._current_chunk = current
                self._total_chunks = total
                self._progress = current / total if total > 0 else 0.0

            result_path = self._engine.generate_audio(
                text=text,
                voice_specs=voice_specs,
                lang_code=lang_code,
                speed=speed,
                split_pattern=split_pattern,
                post_proc_options=post_proc,
                output_path=output_path,
                output_format=output_format,
                progress_callback=progress_cb,
                cancel_flag=self._cancel_flag,
            )

            self._last_output_path = result_path

            duration = 0.0
            try:
                import soundfile as sf
                info = sf.info(result_path)
                duration = info.duration
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

    # ------------------------------------------------------------------
    # Preview
    # ------------------------------------------------------------------

    def preview(self, params: dict) -> dict:
        try:
            # "Play Last" passes play_file — stream the saved file directly
            play_file = params.get("play_file", "")
            if play_file:
                return self._play_file(play_file)

            text = params.get("text", "")[:200].strip()
            if not text:
                return {"success": False, "error": "No text for preview"}

            voice_specs = params.get("voices", [{"voice_id": "af_heart", "weight": 100}])
            lang_code = params.get("lang_code", "a")
            speed = float(params.get("speed", 1.0))

            self._engine.preview_audio(
                text=text,
                voice_specs=voice_specs,
                lang_code=lang_code,
                speed=speed,
            )
            return {"success": True, "error": None}
        except Exception as exc:
            logger.error("preview: %s", exc)
            return {"success": False, "error": str(exc)}

    def _play_file(self, path: str) -> dict:
        try:
            import soundfile as sf
            import sounddevice as sd
            data, sr = sf.read(path, dtype="float32")
            sd.play(data, sr)
            sd.wait()
            return {"success": True, "error": None}
        except Exception as exc:
            logger.error("_play_file: %s", exc)
            return {"success": False, "error": str(exc)}

    # ------------------------------------------------------------------
    # Control & status
    # ------------------------------------------------------------------

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

    # ------------------------------------------------------------------
    # File system
    # ------------------------------------------------------------------

    def browse_file(self) -> str:
        try:
            result = self.window.create_file_dialog(
                webview.OPEN_DIALOG,
                allow_multiple=False,
                file_types=("Text Files (*.txt)", "All Files (*.*)"),
            )
            return result[0] if result else ""
        except Exception as exc:
            logger.error("browse_file: %s", exc)
            return ""

    def browse_folder(self) -> str:
        try:
            result = self.window.create_file_dialog(webview.FOLDER_DIALOG)
            return result[0] if result else ""
        except Exception as exc:
            logger.error("browse_folder: %s", exc)
            return ""

    def open_folder(self, path: str) -> None:
        try:
            target = path or DEFAULT_OUTPUT_DIR
            sys = platform.system()
            if sys == "Darwin":
                subprocess.Popen(["open", target])
            elif sys == "Windows":
                subprocess.Popen(["explorer", target])
            else:
                subprocess.Popen(["xdg-open", target])
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
        return DEFAULT_OUTPUT_DIR


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    api = Api()

    html_path = str(_HERE / "frontend" / "index.html")

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

    def on_loaded() -> None:
        if not _check_espeak():
            hint = _espeak_install_hint()
            msg = (
                "espeak-ng is not installed or not on PATH.\n\n"
                f"Install it with:\n  {hint}\n\n"
                "Text-to-speech will not work without it."
            )
            window.evaluate_js(f"showStartupError({json.dumps(msg)})")

    webview.start(on_loaded, debug=os.environ.get("TTS_DEBUG") == "1")


if __name__ == "__main__":
    main()
