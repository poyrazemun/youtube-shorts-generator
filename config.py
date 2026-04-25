"""
Central configuration for Unreal History Bot.
All paths, API settings, and pipeline constants live here.
"""

import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

# ── Base Paths ────────────────────────────────────────────────────────────────
BASE_DIR = Path(__file__).parent
OUTPUT_DIR = BASE_DIR / "output"
ASSETS_DIR = BASE_DIR / "assets"
LOGS_DIR = BASE_DIR / "logs"
MUSIC_DIR = ASSETS_DIR / "music"

for _d in [
    OUTPUT_DIR,
    ASSETS_DIR / "images",
    ASSETS_DIR / "audio",
    ASSETS_DIR / "video",
    LOGS_DIR,
    MUSIC_DIR,
]:
    _d.mkdir(parents=True, exist_ok=True)

# ── API Keys ──────────────────────────────────────────────────────────────────
# Note: these default to "" when unset; callers must use a truthiness check
# (e.g. `if config.ANTHROPIC_API_KEY:`), not `is not None`, since the empty
# string is a valid "missing" value here.
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
REPLICATE_API_TOKEN = os.getenv("REPLICATE_API_TOKEN", "")
YOUTUBE_CLIENT_SECRETS_FILE = os.getenv(
    "YOUTUBE_CLIENT_SECRETS_FILE", str(BASE_DIR / "client_secrets.json")
)
YOUTUBE_CREDENTIALS_FILE = os.getenv(
    "YOUTUBE_CREDENTIALS_FILE", str(BASE_DIR / "credentials.json")
)

# ── Claude Settings ───────────────────────────────────────────────────────────
CLAUDE_MODEL = "claude-sonnet-4-6"
CLAUDE_MAX_TOKENS = 4096

# ── Image Generation ──────────────────────────────────────────────────────────
IMAGES_PER_EVENT = 5
IMAGE_WIDTH = 608  # 9:16 friendly width for SD (multiple of 64)
IMAGE_HEIGHT = 1080  # 9:16 friendly height for SD (multiple of 64)
# Final video resolution
VIDEO_WIDTH = 1080
VIDEO_HEIGHT = 1920

IMAGE_STYLE_PROMPT = (
    "cinematic historical photograph, dramatic lighting, epic composition, "
    "9:16 vertical portrait, ultra detailed, photorealistic, dark moody atmosphere, "
    "documentary style, award winning photography"
)
IMAGE_NEGATIVE_PROMPT = (
    "text, watermark, logo, modern, cartoon, anime, ugly, blurry, "
    "low quality, deformed, extra limbs, "
    "blood, gore, graphic wounds, open wounds, corpses, dead bodies, "
    "execution scene, graphic violence, disturbing imagery, graphic suffering, mutilation"
)

# Replicate model for image generation
# FLUX.1-dev: high quality, optimal for cinematic historical images (~$0.025/image)
REPLICATE_IMAGE_MODEL = "black-forest-labs/flux-dev"

# ── TTS Settings ──────────────────────────────────────────────────────────────
PIPER_MODEL = os.getenv("PIPER_MODEL", "en_US-lessac-medium")
PIPER_BINARY = os.getenv("PIPER_BINARY", "piper")  # must be on PATH

# Kokoro TTS (open-weight neural TTS, best quality — requires Python 3.10-3.12)
KOKORO_VOICE = os.getenv(
    "KOKORO_VOICE", "bm_george"
)  # af_heart, am_echo, bf_emma, bm_george
KOKORO_LANG_CODE = os.getenv(
    "KOKORO_LANG_CODE", "b"
)  # 'a'=American English, 'b'=British
KOKORO_SPEED = float(os.getenv("KOKORO_SPEED", "1.1"))

# ── Video Assembly ────────────────────────────────────────────────────────────
SECONDS_PER_IMAGE = 5  # base duration per image slide
TARGET_DURATION_MIN = 20  # minimum short duration in seconds
TARGET_DURATION_MAX = 30  # maximum short duration in seconds
SUBSCRIBE_CTA = "Follow @ThatActuallyHappened11"
# Global nudge applied to every subtitle card's start/end timestamps.
# Negative = subtitles appear earlier (compensate for "late" captions).
# Positive = subtitles appear later (compensate for "early" captions).
# Tune in 0.1s increments via env var, e.g. SUBTITLE_TIME_OFFSET=-0.2
SUBTITLE_TIME_OFFSET = float(os.getenv("SUBTITLE_TIME_OFFSET", "0.0"))
FONT_SIZE = 48
FONT_COLOR = "white"
SUBTITLE_OUTLINE_COLOR = "black"
SUBTITLE_OUTLINE_WIDTH = 3

# ── YouTube ───────────────────────────────────────────────────────────────────
YOUTUBE_CATEGORY_ID = "27"  # Education
YOUTUBE_PRIVACY = os.getenv("YOUTUBE_PRIVACY", "private")  # start private for safety
YOUTUBE_SCOPES = [
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube.readonly",
    "https://www.googleapis.com/auth/youtube.force-ssl",  # required for posting comments
]

# ── Logging ───────────────────────────────────────────────────────────────────
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")

# ── Scene Planning (Phase 1 architecture) ─────────────────────────────────────
# Default preset used when --preset is not passed. Must be a key of
# pipeline.presets.PRESETS.
DEFAULT_SCENE_PRESET = os.getenv("DEFAULT_SCENE_PRESET", "documentary_clean")

# ── Tier 3: Automation ────────────────────────────────────────────────────────
TOPICS_QUEUE_PATH = BASE_DIR / "topics_queue.json"
VIDEO_REGISTRY_PATH = BASE_DIR / "video_registry.json"
ANALYTICS_PATH = OUTPUT_DIR / "analytics.json"

# ── Cost Tracking ─────────────────────────────────────────────────────────────
# Per-million-token rates in USD. Add new model entries here as needed.
# Source: https://www.anthropic.com/pricing
CLAUDE_PRICING: dict[str, dict[str, float]] = {
    "claude-sonnet-4-6": {"input": 3.0, "output": 15.0},
    "claude-opus-4-7":   {"input": 15.0, "output": 75.0},
    "claude-haiku-4-5":  {"input": 1.0, "output": 5.0},
}

# Per-image USD rate by image-generation provider.
# Replicate FLUX.1-dev is ~$0.025/image. HuggingFace is free on the inference
# API. PIL is the offline placeholder fallback (always free).
IMAGE_PRICING: dict[str, float] = {
    "replicate":   float(os.getenv("IMAGE_COST_REPLICATE", "0.025")),
    "huggingface": float(os.getenv("IMAGE_COST_HUGGINGFACE", "0.0")),
    "pil":         0.0,
}
