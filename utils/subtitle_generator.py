"""
SUBTITLE GENERATOR
Generates .srt subtitle files from script text.
Uses word-level timing estimation based on audio duration.
Saves to output/<slug>/subtitles/<event_idx>.srt
"""

import logging
import math
import re
from pathlib import Path

from config import OUTPUT_DIR, SUBTITLE_TIME_OFFSET

logger = logging.getLogger(__name__)

# Average reading speed for subtitles (words per second)
WORDS_PER_SECOND = 130 / 60  # ~2.17 wps


def _seconds_to_srt_timestamp(seconds: float) -> str:
    """Convert float seconds to SRT timestamp format: HH:MM:SS,mmm"""
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = int(seconds % 60)
    millis = int((seconds % 1) * 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"


def _chunk_words(words: list[str], max_words_per_line: int = 7) -> list[list[str]]:
    """Split word list into subtitle chunks (max N words each)."""
    chunks = []
    for i in range(0, len(words), max_words_per_line):
        chunks.append(words[i:i + max_words_per_line])
    return chunks


def generate_srt(script: dict, audio_duration: float, output_path: Path) -> Path:
    """
    Generate a .srt subtitle file for a script.

    Timing is distributed proportionally across words based on audio_duration.
    In the estimation fallback, each subtitle card shows 7 words max.

    Args:
        script: script dict with 'full_script' key
        audio_duration: total duration of the audio in seconds
        output_path: where to save the .srt file

    Returns:
        Path to the generated .srt file
    """
    output_path.parent.mkdir(parents=True, exist_ok=True)

    full_text = script.get("full_script", "")
    if not full_text:
        logger.warning("[subtitle_generator] No full_script text found — generating empty .srt")
        output_path.write_text("", encoding="utf-8")
        return output_path

    # Clean up text: normalize whitespace and remove special chars that break srt
    full_text = re.sub(r"\s+", " ", full_text).strip()
    words = full_text.split()

    if not words:
        output_path.write_text("", encoding="utf-8")
        return output_path

    # Start subtitles from the beginning of the audio (no buffer).
    # Kokoro and other local TTS engines produce speech with minimal
    # leading silence, so an artificial start delay causes subtitles to
    # lag behind the voice.
    start_buffer = 0.0
    available_duration = audio_duration
    seconds_per_word = available_duration / len(words)

    # Group into subtitle cards
    chunks = _chunk_words(words, max_words_per_line=7)

    srt_entries = []
    current_time = start_buffer

    for idx, chunk in enumerate(chunks):
        chunk_duration = len(chunk) * seconds_per_word
        # Add a tiny gap between cards (0.05s)
        gap = 0.05

        # Compute the RAW card boundaries on the audio timeline first, and
        # advance `current_time` using those raw values. The offset must only
        # shift the written-out timestamps — folding it into `current_time`
        # would compound the shift on every subsequent card and drift the
        # later subtitles further and further off-sync.
        raw_start = current_time
        raw_end = current_time + chunk_duration - gap

        start_time = max(0.0, raw_start + SUBTITLE_TIME_OFFSET)
        end_time = raw_end + SUBTITLE_TIME_OFFSET

        # Clamp to sensible bounds
        end_time = min(end_time, audio_duration - 0.1)
        end_time = max(end_time, start_time + 0.1)

        subtitle_text = " ".join(chunk)

        srt_entries.append(
            f"{idx + 1}\n"
            f"{_seconds_to_srt_timestamp(start_time)} --> {_seconds_to_srt_timestamp(end_time)}\n"
            f"{subtitle_text}\n"
        )

        # Advance using raw end so the NEXT card's timing stays anchored to
        # the audio, independent of the display-shift offset.
        current_time = raw_end + gap

    srt_content = "\n".join(srt_entries)
    output_path.write_text(srt_content, encoding="utf-8")

    logger.info(
        f"[subtitle_generator] Generated {len(srt_entries)} subtitle cards "
        f"for {len(words)} words ({audio_duration:.1f}s audio) → {output_path}"
    )
    return output_path


def generate_all_subtitles(
    scripts: list[dict],
    audio_paths: list[Path],
    slug: str,
    audio_durations: list[float],
) -> list[Path]:
    """
    Generate .srt files for all events.
    Returns list of Path objects to .srt files.
    """
    subtitle_dir = OUTPUT_DIR / slug / "subtitles"
    subtitle_dir.mkdir(parents=True, exist_ok=True)
    srt_paths = []

    for script, audio_path, duration in zip(scripts, audio_paths, audio_durations):
        idx = script.get("event_index", scripts.index(script))
        srt_path = subtitle_dir / f"{idx}.srt"

        if srt_path.exists() and srt_path.stat().st_size > 0:
            logger.info(f"[subtitle_generator] Cache hit: {srt_path.name}")
            srt_paths.append(srt_path)
            continue

        logger.info(f"[subtitle_generator] Generating subtitles for event {idx}...")
        generate_srt(script, duration, srt_path)
        srt_paths.append(srt_path)

    return srt_paths
