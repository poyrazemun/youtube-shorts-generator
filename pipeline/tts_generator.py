"""
STEP 4 — VOICE GENERATION (FREE/LOCAL)
Priority: Piper TTS → Coqui TTS → Edge TTS (Microsoft Neural, default)
Generates WAV audio from script text.
Saves to output/<slug>/audio/<event_idx>.wav
"""

import logging
import shutil
import subprocess
from pathlib import Path

import config
from pipeline.retry import with_retry

logger = logging.getLogger(__name__)


# ── Backend Detection ─────────────────────────────────────────────────────────

def _check_piper() -> bool:
    """Check if Piper TTS binary is available on PATH."""
    return shutil.which(config.PIPER_BINARY) is not None


def _check_coqui() -> bool:
    """Check if Coqui TTS Python library is installed."""
    try:
        import TTS  # noqa: F401
        return True
    except ImportError:
        return False


def detect_tts_backend() -> str:
    """Auto-detect which TTS engine is available."""
    if _check_piper():
        logger.info("[tts_generator] TTS Backend: Piper (local binary)")
        return "piper"
    if _check_coqui():
        logger.info("[tts_generator] TTS Backend: Coqui TTS (local Python)")
        return "coqui"
    logger.info("[tts_generator] TTS Backend: Edge TTS (en-US-ChristopherNeural)")
    return "edge_tts"


# ── Piper TTS ─────────────────────────────────────────────────────────────────

def _generate_piper(text: str, out_path: Path) -> Path:
    """
    Generate audio using Piper TTS.
    Command: echo "text" | piper --model en_US-lessac-medium --output_file out.wav
    """
    cmd = [
        config.PIPER_BINARY,
        "--model", config.PIPER_MODEL,
        "--output_file", str(out_path),
    ]

    try:
        result = subprocess.run(
            cmd,
            input=text.encode("utf-8"),
            capture_output=True,
            timeout=120,
        )
        if result.returncode != 0:
            stderr = result.stderr.decode("utf-8", errors="replace")
            raise RuntimeError(f"Piper exited with code {result.returncode}: {stderr}")
    except subprocess.TimeoutExpired:
        raise RuntimeError("Piper TTS timed out after 120 seconds")
    except FileNotFoundError:
        raise RuntimeError(f"Piper binary not found: {config.PIPER_BINARY}")

    if not out_path.exists() or out_path.stat().st_size == 0:
        raise RuntimeError("Piper produced no output file")

    return out_path


# ── Coqui TTS ─────────────────────────────────────────────────────────────────

def _generate_coqui(text: str, out_path: Path) -> Path:
    """Generate audio using Coqui TTS Python library."""
    from TTS.api import TTS as CoquiTTS

    tts = CoquiTTS(model_name="tts_models/en/ljspeech/tacotron2-DDC", progress_bar=False)
    tts.tts_to_file(text=text, file_path=str(out_path))

    if not out_path.exists() or out_path.stat().st_size == 0:
        raise RuntimeError("Coqui TTS produced no output file")

    return out_path


# ── Edge TTS (Microsoft Neural voices, free) ──────────────────────────────────

EDGE_TTS_VOICE = "en-US-ChristopherNeural"


@with_retry(max_retries=3, base_delay=2)
def _generate_edge_tts(text: str, out_path: Path) -> Path:
    """
    Generate audio using Edge TTS (Microsoft Neural TTS, free, no API key).
    edge-tts is async — we run it via asyncio.run().
    Outputs MP3, then converts to WAV via ffmpeg for pipeline consistency.
    """
    try:
        import edge_tts
    except ImportError:
        raise RuntimeError("edge-tts not installed. Run: pip install edge-tts")

    import asyncio

    mp3_path = out_path.with_suffix(".mp3")

    async def _synthesize() -> None:
        communicate = edge_tts.Communicate(text, EDGE_TTS_VOICE)
        await communicate.save(str(mp3_path))

    try:
        asyncio.run(_synthesize())
    except RuntimeError as e:
        # If an event loop is already running (e.g. inside Jupyter), use nest_asyncio
        if "cannot run nested" in str(e).lower():
            try:
                import nest_asyncio
                nest_asyncio.apply()
                loop = asyncio.get_event_loop()
                loop.run_until_complete(_synthesize())
            except ImportError:
                raise RuntimeError(
                    "Nested event loop detected. Run: pip install nest_asyncio"
                ) from e
        else:
            raise

    if not mp3_path.exists() or mp3_path.stat().st_size == 0:
        raise RuntimeError("Edge TTS produced no MP3 file")

    _convert_mp3_to_wav(mp3_path, out_path)
    mp3_path.unlink(missing_ok=True)

    return out_path


def _convert_mp3_to_wav(mp3_path: Path, wav_path: Path) -> None:
    """Convert MP3 to WAV using ffmpeg subprocess."""
    cmd = [
        "ffmpeg", "-y",
        "-i", str(mp3_path),
        "-ar", "44100",    # 44.1kHz sample rate
        "-ac", "1",        # mono
        "-acodec", "pcm_s16le",
        str(wav_path),
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, timeout=60)
        if result.returncode != 0:
            stderr = result.stderr.decode("utf-8", errors="replace")
            raise RuntimeError(f"ffmpeg conversion failed: {stderr}")
    except FileNotFoundError:
        raise RuntimeError(
            "ffmpeg not found. Install ffmpeg and ensure it's on PATH.\n"
            "  Windows: winget install ffmpeg\n"
            "  macOS: brew install ffmpeg\n"
            "  Linux: apt install ffmpeg"
        )


# ── Audio Duration ────────────────────────────────────────────────────────────

def get_audio_duration(wav_path: Path) -> float:
    """Get duration of WAV file in seconds using ffprobe."""
    cmd = [
        "ffprobe", "-v", "quiet",
        "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1",
        str(wav_path),
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, timeout=30, text=True)
        if result.returncode == 0 and result.stdout.strip():
            return float(result.stdout.strip())
    except (FileNotFoundError, ValueError, subprocess.TimeoutExpired):
        pass

    # Fallback: estimate from WAV header
    try:
        import wave
        with wave.open(str(wav_path), "r") as wf:
            frames = wf.getnframes()
            rate = wf.getframerate()
            return frames / float(rate)
    except Exception:
        return 25.0  # assume 25s if we can't determine


# ── Main Entry Point ──────────────────────────────────────────────────────────

def generate_audio(scripts: list[dict], slug: str) -> list[Path]:
    """
    Generate WAV audio for each script.
    Returns list of Path objects (one per event). Resumable.
    """
    backend = detect_tts_backend()
    audio_dir = config.OUTPUT_DIR / slug / "audio"
    audio_dir.mkdir(parents=True, exist_ok=True)

    audio_paths = []

    for script in scripts:
        idx = script.get("event_index", scripts.index(script))
        out_path = audio_dir / f"{idx}.wav"

        if out_path.exists() and out_path.stat().st_size > 1024:
            logger.info(f"[tts_generator] Cache hit: {out_path.name}")
            audio_paths.append(out_path)
            continue

        text = script.get("full_script", "")
        if not text:
            logger.warning(f"[tts_generator] Script {idx} has no full_script text — skipping.")
            audio_paths.append(None)
            continue

        logger.info(
            f"[tts_generator] Generating audio for event {idx} "
            f"({len(text.split())} words) using {backend}..."
        )

        try:
            if backend == "piper":
                _generate_piper(text, out_path)
            elif backend == "coqui":
                _generate_coqui(text, out_path)
            else:
                _generate_edge_tts(text, out_path)

            duration = get_audio_duration(out_path)
            logger.info(
                f"[tts_generator] Audio saved: {out_path.name} ({duration:.1f}s)"
            )
            audio_paths.append(out_path)

        except Exception as e:
            logger.error(f"[tts_generator] Failed to generate audio for event {idx}: {e}")
            raise

    return audio_paths
