"""
script_parser.py — Parse stage-play style scripts into speaker/dialogue pairs.

Supported format:
    Seth: Hello, welcome to the show.
    Tanya: Thanks for having me!
    Seth: Let's dive in...

Rules:
    - "Speaker Name: dialogue"   starts a new speaker turn
    - Continuation lines         (no speaker prefix) append to the current speaker
    - Lines matching (...)  or [stage directions] alone on a line are skipped
    - Empty lines are ignored
    - (...) pause markers inside dialogue are preserved for the TTS engine
    - Speaker names: start with a letter, up to 40 chars, letters/digits/spaces/hyphens
"""

import re
from dataclasses import dataclass, field
from typing import List

# "Speaker Name: dialogue" — speaker name captured in group 1, rest in group 2
_SPEAKER_RE = re.compile(r'^([A-Za-z][A-Za-z0-9 _\-]{0,39}):\s*(.*)')

# Full-line stage directions: whole line is (text) or [text]
_DIRECTION_RE = re.compile(r'^\s*[\(\[].+[\)\]]\s*$')

# Map voice ID prefix → Kokoro lang_code (used by backend to select pipeline)
_PREFIX_TO_LANG = {
    "af_": "a", "am_": "a",   # American English
    "bf_": "b", "bm_": "b",   # British English
    "jf_": "j", "jm_": "j",   # Japanese
    "zf_": "z", "zm_": "z",   # Mandarin Chinese
    "ef_": "e", "em_": "e",   # Spanish
    "ff_": "f", "fm_": "f",   # French
    "hf_": "h", "hm_": "h",   # Hindi
    "if_": "i", "im_": "i",   # Italian
    "pf_": "p", "pm_": "p",   # Brazilian Portuguese
}


def lang_code_for_voice(voice_id: str) -> str:
    """Infer Kokoro lang_code from a voice ID prefix (e.g. 'af_heart' → 'a')."""
    return _PREFIX_TO_LANG.get(voice_id[:3], "a")


@dataclass
class ScriptLine:
    speaker: str
    text: str


@dataclass
class ParsedScript:
    lines: List[ScriptLine] = field(default_factory=list)
    speakers: List[str] = field(default_factory=list)   # ordered by first appearance
    speaker_line_counts: dict = field(default_factory=dict)
    error: str = ""


def parse_script(text: str) -> ParsedScript:
    """
    Parse a script into a list of (speaker, text) pairs.
    Returns a ParsedScript; check .error before using .lines.
    """
    lines: List[ScriptLine] = []
    speakers: List[str] = []
    seen: set = set()
    counts: dict = {}
    current_speaker: str | None = None
    current_parts: List[str] = []

    def flush() -> None:
        nonlocal current_speaker, current_parts
        if current_speaker and current_parts:
            joined = " ".join(current_parts).strip()
            if joined:
                lines.append(ScriptLine(speaker=current_speaker, text=joined))
                counts[current_speaker] = counts.get(current_speaker, 0) + 1
        current_parts = []

    for raw in text.splitlines():
        stripped = raw.strip()
        if not stripped:
            continue
        if _DIRECTION_RE.match(stripped):
            continue

        m = _SPEAKER_RE.match(stripped)
        if m:
            flush()
            speaker = m.group(1).strip()
            dialogue = m.group(2).strip()
            current_speaker = speaker
            if speaker not in seen:
                seen.add(speaker)
                speakers.append(speaker)
            current_parts = [dialogue] if dialogue else []
        elif current_speaker:
            current_parts.append(stripped)
        else:
            # Text before any speaker identified — treat as Narrator
            current_speaker = "Narrator"
            if "Narrator" not in seen:
                seen.add("Narrator")
                speakers.append("Narrator")
            current_parts.append(stripped)

    flush()

    if not lines:
        return ParsedScript(
            error='No speaker lines found. Use format:  "Speaker: dialogue text"'
        )

    return ParsedScript(
        lines=lines,
        speakers=speakers,
        speaker_line_counts=counts,
    )
