# Kokoro TTS Studio

A polished desktop GUI for [Kokoro TTS](https://github.com/hexgrad/kokoro) (hexgrad/Kokoro-82M),
built with Python + pywebview. Runs fully offline after the initial model download.

---

## Features

- Three-column interface: text input · voice & generation settings · audio post-processing
- All Kokoro languages and voices with gender filtering
- Voice blending (up to 3 voices with weighted mixing)
- Audio post-processing: normalize, trim silence, noise gate, fade in/out
- Output formats: WAV, FLAC, MP3
- Chunked generation with progress bar — handles long texts reliably
- Plays preview audio without saving; plays last output on demand
- Outputs are DAW-ready (DaVinci Resolve, Logic Pro, etc.)

---

## System Requirements

### espeak-ng (required)

Kokoro uses espeak-ng for phoneme generation. Install it before running the app:

**macOS**
```bash
brew install espeak-ng
```

**Linux (Debian/Ubuntu)**
```bash
sudo apt-get install espeak-ng
```

**Windows**
Download the MSI installer from:
https://github.com/espeak-ng/espeak-ng/releases

---

## Python Setup

1. **Clone / download** this project folder.

2. **Create a virtual environment** (Python 3.10+):
   ```bash
   python3 -m venv .venv
   source .venv/bin/activate      # macOS/Linux
   .venv\Scripts\activate         # Windows
   ```

3. **Install Python dependencies:**
   ```bash
   pip install -r requirements.txt
   ```

4. **Run the app:**
   ```bash
   python main.py
   ```

---

## First Run

On first launch, Kokoro will download model weights (~330 MB) from Hugging Face
and cache them locally (`~/.cache/huggingface/hub/`). Subsequent launches are
fully offline.

---

## Debug Mode

Set the environment variable `TTS_DEBUG=1` to open the pywebview developer tools:

```bash
TTS_DEBUG=1 python main.py
```

---

## Output Files

Generated audio is saved to `./outputs/` by default (configurable in the UI).
Files are named `output_YYYYMMDD_HHMMSS.wav` unless you set a custom filename.

WAV and FLAC outputs are lossless and import cleanly into:
- **DaVinci Resolve** — File → Import → Media
- **Logic Pro** — drag into timeline or browser
- Any other DAW that accepts PCM or FLAC

MP3 export requires `pydub` and `ffmpeg` (install ffmpeg separately or via
`brew install ffmpeg`).

---

## Voice Blending

Enable blending in the Voice panel to mix up to 3 voices by weighted average
of their style tensors. The resulting blend spec (e.g. `af_heart:60,am_echo:40`)
is displayed in real time. This requires the voice `.pt` tensor files to be
present in the Kokoro installation.

---

## Troubleshooting

| Symptom | Fix |
|---|---|
| "espeak-ng not found" dialog | Install espeak-ng per the instructions above |
| Voice shows "(unavailable)" | That voice's tensor file wasn't found; try reinstalling kokoro |
| MP3 export falls back to WAV | Install pydub (`pip install pydub`) and ffmpeg |
| Black window on launch | Ensure pywebview ≥ 5.0 is installed |

---

## Attributions

See [LICENSE](LICENSE).
