"""
Quick visual test for video_assembler — no API calls, no cost.

Generates a test .mp4 using:
  - PNG/JPG images placed directly in test_output/ (5 recommended)
  - assets/voice_sample.wav as audio
  - Estimation-based SRT from a hardcoded script

Use this to preview subtitle position, CTA overlay, Ken Burns effect, etc.
Output: test_output/test_video.mp4

Usage:
  1. Drop your test images into test_output/ (any PNG or JPG, named so they sort correctly)
  2. py -3.12 test_video.py
"""

import logging
import sys
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(levelname)s  %(message)s")

# ── Config ────────────────────────────────────────────────────────────────────

AUDIO_PATH = Path("assets/voice_sample.wav")
OUT_DIR    = Path("test_output")
OUT_PATH   = OUT_DIR / "test_video.mp4"

IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg"}

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

# ── Helpers ───────────────────────────────────────────────────────────────────

def _load_test_images(img_dir: Path) -> list[Path]:
    """Load PNG/JPG files directly from img_dir, sorted by filename."""
    images = sorted(
        p for p in img_dir.iterdir()
        if p.is_file() and p.suffix.lower() in IMAGE_EXTENSIONS
    )
    return images


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
        sys.exit(
            f"Audio file not found: {AUDIO_PATH}\n"
            "Place a WAV file at assets/voice_sample.wav"
        )

    OUT_DIR.mkdir(parents=True, exist_ok=True)

    print("Loading test images from test_output/...")
    image_paths = _load_test_images(OUT_DIR)
    if not image_paths:
        sys.exit(
            "No images found in test_output/\n"
            "Drop PNG or JPG files directly into test_output/ and re-run."
        )
    print(f"  Found {len(image_paths)} image(s): {[p.name for p in image_paths]}")

    # Delete cached output so the assembler doesn't skip
    if OUT_PATH.exists():
        OUT_PATH.unlink()

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
