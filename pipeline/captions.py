"""
STEP 5a (enhanced) — Caption generation with Whisper word timestamps.

If openai-whisper is installed: real word timestamps → ASS + SRT files.
If not installed: falls back to estimation-based SRT (existing behavior).

Install Whisper (optional): pip install openai-whisper
"""

import logging
from pathlib import Path
from typing import cast

from config import SUBTITLE_TIME_OFFSET, VIDEO_WIDTH, VIDEO_HEIGHT
from utils.subtitle_generator import generate_srt

logger = logging.getLogger(__name__)

_WORDS_PER_CARD = 3  # subtitle card size for Whisper path


def _has_whisper() -> bool:
    try:
        import whisper  # type: ignore[import-not-found]  # noqa: F401

        return True
    except ImportError:
        return False


def _seconds_to_srt_ts(s: float) -> str:
    h = int(s // 3600)
    m = int((s % 3600) // 60)
    sec = int(s % 60)
    ms = int((s % 1) * 1000)
    return f"{h:02d}:{m:02d}:{sec:02d},{ms:03d}"


def _seconds_to_ass_ts(s: float) -> str:
    h = int(s // 3600)
    m = int((s % 3600) // 60)
    sec = int(s % 60)
    cs = int((s % 1) * 100)
    return f"{h}:{m:02d}:{sec:02d}.{cs:02d}"


def _build_ass_header(W: int, H: int) -> str:
    font_size = int(H * 0.032)
    margin_v = int(H * 0.22)  # ~422px from bottom — above YouTube Shorts UI on phones
    margin_lr = int(W * 0.05)
    box_pad = int(font_size * 0.35)
    return (
        "[Script Info]\n"
        "ScriptType: v4.00+\n"
        f"PlayResX: {W}\n"
        f"PlayResY: {H}\n"
        "ScaledBorderAndShadow: yes\n"
        "WrapStyle: 1\n"
        "\n"
        "[V4+ Styles]\n"
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, "
        "OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, "
        "ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, "
        "Alignment, MarginL, MarginR, MarginV, Encoding\n"
        f"Style: Default,Arial,{font_size},"
        "&H00FFFFFF,&H000000FF,&H00000000,&H80000000,"
        "1,0,0,0,100,100,1,0,3,"
        f"{box_pad},0,2,{margin_lr},{margin_lr},{margin_v},1\n"
        "\n"
        "[Events]\n"
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
    )


def _generate_whisper_captions(
    audio_path: Path, script: dict, work_dir: Path
) -> tuple[Path, Path]:
    """
    Transcribe audio with Whisper word timestamps → ASS + SRT files.
    Returns (ass_path, srt_path).
    """
    import whisper  # type: ignore[import-not-found]

    logger.info("[captions] Loading Whisper model 'base'...")
    model = whisper.load_model("base")
    result = cast(dict, model.transcribe(str(audio_path), word_timestamps=True))

    # Flatten word timestamps from all segments
    words = []
    for seg in result.get("segments", []) or []:
        for w in seg.get("words", []) or []:
            words.append(
                {
                    "word": w["word"].strip(),
                    "start": float(w["start"]),
                    "end": float(w["end"]),
                }
            )

    if not words:
        raise ValueError("Whisper returned no word timestamps")

    idx = script.get("event_index", 0)
    ass_path = work_dir / f"{idx}_captions.ass"
    srt_path = work_dir / f"{idx}_captions.srt"

    header = _build_ass_header(VIDEO_WIDTH, VIDEO_HEIGHT)
    dialogues = []
    srt_entries = []
    card_num = 1

    for i in range(0, len(words), _WORDS_PER_CARD):
        card_words = words[i : i + _WORDS_PER_CARD]
        # SUBTITLE_TIME_OFFSET shifts captions earlier to compensate for
        # Whisper's tendency to land word-start timestamps after the onset.
        start = max(0.0, card_words[0]["start"] + SUBTITLE_TIME_OFFSET)
        end = max(start + 0.05, card_words[-1]["end"] + SUBTITLE_TIME_OFFSET)
        text = " ".join(w["word"] for w in card_words)
        text = text.replace("{", r"\{").replace("}", r"\}")

        dialogues.append(
            f"Dialogue: 0,{_seconds_to_ass_ts(start)},{_seconds_to_ass_ts(end)},"
            f"Default,,0,0,0,,{text}"
        )
        srt_entries.append(
            f"{card_num}\n"
            f"{_seconds_to_srt_ts(start)} --> {_seconds_to_srt_ts(end)}\n"
            f"{text}\n"
        )
        card_num += 1

    ass_path.write_text(header + "\n".join(dialogues) + "\n", encoding="utf-8-sig")
    srt_path.write_text("\n".join(srt_entries), encoding="utf-8")

    logger.info(
        f"[captions] Whisper: {len(dialogues)} cards → {ass_path.name}, {srt_path.name}"
    )
    return ass_path, srt_path


def generate_captions(
    audio_path: Path,
    script: dict,
    work_dir: Path,
    audio_duration: float,
) -> tuple[Path | None, Path]:
    """
    Generate captions for a single event.

    Returns (ass_path_or_None, srt_path).
    ass_path is set when Whisper is available; None means use the SRT as fallback.
    """
    work_dir.mkdir(parents=True, exist_ok=True)
    idx = script.get("event_index", 0)

    if _has_whisper():
        try:
            return _generate_whisper_captions(audio_path, script, work_dir)
        except Exception as e:
            logger.warning(
                f"[captions] Whisper failed for event {idx}: {e} — falling back to estimation"
            )

    # Estimation fallback using existing subtitle_generator
    srt_path = work_dir / f"{idx}.srt"
    if srt_path.exists() and srt_path.stat().st_size > 0:
        logger.info(f"[captions] Cache hit (estimation SRT): {srt_path.name}")
        return None, srt_path

    generate_srt(script, audio_duration, srt_path)
    logger.info(f"[captions] Estimation SRT for event {idx}: {srt_path.name}")
    return None, srt_path
