"""
Quick visual test for video_assembler — no API calls, no cost.

Generates a test .mp4 using:
  - Solid-colour placeholder images (PIL, instant)
  - assets/voice_sample.wav as audio
  - Estimation-based SRT from a hardcoded script

Use this to preview subtitle position, CTA overlay, font size, etc.
Output: test_output/test_video.mp4
"""

import logging
import sys
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(levelname)s  %(message)s")

# ── Config ────────────────────────────────────────────────────────────────────

AUDIO_PATH = Path("assets/voice_sample.wav")
OUT_DIR    = Path("test_output")
OUT_PATH   = OUT_DIR / "test_video.mp4"

# Fake script — edit this text to test different subtitle lengths / content
TEST_SCRIPT = {
    "full_script": (
        "A man once sold the Eiffel Tower — twice. "
        "Victor Lustig posed as a government official and convinced scrap dealers "
        "the tower was being demolished. "
        "But here's what nobody tells you — "
        "after the first buyer paid and said nothing out of embarrassment, "
        "Lustig went back and did it again to a second victim. "
        "He was never caught for the tower. Only later — for counterfeiting."
    ),
    "event_index": 0,
}

# Placeholder image colours (one per slide)
SLIDE_COLORS = [
    (30,  30,  60),   # dark navy
    (60,  20,  20),   # dark red
    (20,  50,  30),   # dark green
    (50,  40,  10),   # dark amber
    (20,  30,  60),   # dark blue
]

# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_placeholder_images(out_dir: Path) -> list[Path]:
    """Generate solid-colour 1080×1920 PNG images using PIL."""
    try:
        from PIL import Image, ImageDraw, ImageFont
    except ImportError:
        sys.exit("Pillow is required: pip install Pillow")

    import config
    W, H = config.VIDEO_WIDTH, config.VIDEO_HEIGHT
    paths = []
    for i, colour in enumerate(SLIDE_COLORS):
        img = Image.new("RGB", (W, H), colour)
        draw = ImageDraw.Draw(img)
        label = f"Slide {i + 1}"
        draw.text((W // 2, H // 2), label, fill=(200, 200, 200), anchor="mm")
        p = out_dir / f"slide_{i}.png"
        img.save(p)
        paths.append(p)
    return paths


def _get_audio_duration(audio_path: Path) -> float:
    """Return duration of a WAV file in seconds."""
    import wave
    with wave.open(str(audio_path), "rb") as wf:
        frames = wf.getnframes()
        rate   = wf.getframerate()
        return frames / float(rate)


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    if not AUDIO_PATH.exists():
        sys.exit(f"Audio file not found: {AUDIO_PATH}\n"
                 "Place a WAV file at assets/voice_sample.wav")

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    img_dir = OUT_DIR / "slides"
    img_dir.mkdir(exist_ok=True)

    # Delete cached output so the assembler doesn't skip
    if OUT_PATH.exists():
        OUT_PATH.unlink()

    print("Generating placeholder images...")
    image_paths = _make_placeholder_images(img_dir)

    print("Measuring audio duration...")
    audio_duration = _get_audio_duration(AUDIO_PATH)
    print(f"  Audio: {audio_duration:.1f}s")

    print("Generating estimation-based SRT...")
    from utils.subtitle_generator import generate_srt
    srt_path = OUT_DIR / "test.srt"
    generate_srt(TEST_SCRIPT, audio_duration, srt_path)

    print("Assembling video...")
    from pipeline.video_assembler import assemble_video
    assemble_video(
        image_paths=image_paths,
        audio_path=AUDIO_PATH,
        srt_path=srt_path,
        out_path=OUT_PATH,
        audio_duration=audio_duration,
    )

    print(f"\nDone → {OUT_PATH}")


if __name__ == "__main__":
    main()
