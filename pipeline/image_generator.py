"""
STEP 3 — IMAGE GENERATION
Priority: Hugging Face (needs token) → Replicate (needs token, FLUX.1-dev)
          → PIL placeholder (guaranteed offline fallback)
Generates 5 cinematic 9:16 images per event.
Saves images to output/<slug>/images/<event_idx>/img_N.png
"""

import json
import logging
import os
import random
import re
import time
import urllib.request
import urllib.error
from pathlib import Path
from typing import Any

import config
from pipeline.retry import with_retry

logger = logging.getLogger(__name__)


# ── Backend Detection ─────────────────────────────────────────────────────────

def detect_backend() -> str:
    """
    Auto-detect which image generation backend to use as primary.
    Priority: Hugging Face (if token set) → Replicate (if token set)
              → PIL placeholder (offline fallback).
    Per-image fallback chain in generate_images: primary → pil.
    """
    if os.getenv("HUGGINGFACE_API_TOKEN", ""):
        logger.info("[image_generator] Backend: Hugging Face Inference API (FLUX.1-schnell)")
        return "huggingface"
    if config.REPLICATE_API_TOKEN:
        logger.info("[image_generator] Backend: Replicate (remote, paid)")
        return "replicate"
    logger.warning("[image_generator] No API backend configured — using PIL placeholder")
    return "pil"


# ── Prompt Building ───────────────────────────────────────────────────────────

# Filler phrase openers that add no visual information and should be stripped
# from narrative text before embedding it into an image generation prompt.
_FILLER_OPENER_RE = re.compile(
    r"^(did you know[\s,]*|in \d{4}[\s,]*|in this moment[\s,]*"
    r"|at this point[\s,]*|this is[\s,]*|here,?\s)",
    flags=re.IGNORECASE,
)


def _extract_visual_fragment(text: str, max_words: int = 12) -> str:
    """
    Extract a short, visually descriptive fragment from a narrative sentence.
    Strips common filler openers and trims to a concise noun/verb phrase
    suitable for inclusion in an image generation prompt.
    """
    if not text:
        return ""
    text = _FILLER_OPENER_RE.sub("", text.strip())
    # Strip any residual leading punctuation or whitespace left by the substitution
    text = text.lstrip(" ,;:")
    words = text.split()
    return " ".join(words[:max_words]).rstrip(".,;:!?")


def _build_image_prompts(script: dict) -> list[str]:
    """
    Build 5 visually distinct image prompts for an event's visual sequence.

    Each prompt is tied to a different narrative beat (hook, context, twist,
    aftermath, symbol) and uses a unique combination of lighting, color palette,
    composition, and subject focus so the resulting images look meaningfully
    different from one another while all remaining relevant to the event.
    """
    event = script.get("source_event", {})
    visual_theme = event.get("visual_theme", event.get("event", ""))
    year = event.get("year", "historical")
    location = event.get("location", "")

    # Core identifiers shared by all shots
    event_core = f"{visual_theme}, {year}"
    location_clause = f" in {location}" if location else ""

    # Narrative beats from the generated script — used as shot-specific subjects
    hook_fragment = _extract_visual_fragment(script.get("hook", ""))
    context_fragment = _extract_visual_fragment(script.get("context", ""))
    twist_fragment = _extract_visual_fragment(script.get("twist", ""))
    ending_fragment = _extract_visual_fragment(script.get("ending_fact", ""))

    style = config.IMAGE_STYLE_PROMPT

    prompts = [
        # Shot 1 — Establishing / Hook
        # Panoramic wide shot, dawn golden hour, warm amber palette.
        # Immediately communicates scale and the shocking premise of the event.
        (
            f"Wide panoramic establishing shot, {event_core}{location_clause}, "
            f"{hook_fragment + ', ' if hook_fragment else ''}"
            f"dawn golden hour light, long shadows across vast landscape, "
            f"warm amber and burnt-orange color grading, "
            f"{style}"
        ),

        # Shot 2 — Context / People
        # Medium crowd or figure shot, overcast flat daylight, cool blue-gray palette.
        # Shows the human element and historical setting.
        (
            f"Medium shot of historical figures or crowd, {event_core}{location_clause}, "
            f"{context_fragment + ', ' if context_fragment else ''}"
            f"overcast flat natural daylight, tense atmospheric composition, "
            f"desaturated cool blue-gray and slate tones, "
            f"{style}"
        ),

        # Shot 3 — Twist / Action
        # Dynamic action or confrontation, hard dramatic side-lighting, high-contrast chiaroscuro.
        # Captures the pivotal turning-point moment.
        (
            f"Dynamic action shot at the decisive turning point, {event_core}{location_clause}, "
            f"{twist_fragment + ', ' if twist_fragment else ''}"
            f"hard dramatic side-lighting with deep ink-black shadows, "
            f"high-contrast chiaroscuro, urgent motion blur, "
            f"monochromatic near-black tones with single vivid accent color, "
            f"{style}"
        ),

        # Shot 4 — Aftermath / Consequence
        # Quiet sparse scene, dusk fading light, muted sepia palette.
        # Communicates emotional weight and historical consequence.
        (
            f"Quiet aftermath scene, sparse and desolate, {event_core}{location_clause}, "
            f"{ending_fragment + ', ' if ending_fragment else ''}"
            f"dusk fading twilight, low warm horizon light, solitary figure or lone object, "
            f"muted sepia and dusty brown tones, melancholy and weight, "
            f"moody {style}"
        ),

        # Shot 5 — Symbol / Artifact
        # Extreme close-up of a symbolic object or detail, isolated studio spotlight.
        # Timeless, iconic, and visually unlike the other four shots.
        (
            f"Extreme close-up of symbolic historical artifact or telling detail, "
            f"{event_core}{location_clause}, "
            f"isolated on deep-black background, single soft spotlight, "
            f"rich warm candlelight and burnished gold tones, "
            f"fine texture and craftsmanship, timeless and iconic, "
            f"{style}"
        ),
    ]
    return prompts


# ── Hugging Face Inference API Backend ───────────────────────────────────────

HF_MODEL_URL = "https://router.huggingface.co/hf-inference/models/black-forest-labs/FLUX.1-schnell"


@with_retry(max_retries=3, base_delay=2)
def _generate_huggingface(prompt: str, out_path: Path) -> Path:
    """
    Generate one image via Hugging Face Inference API (FLUX.1-schnell).
    Requires HUGGINGFACE_API_TOKEN in .env (free tier available).
    POST {"inputs": prompt} → returns raw image bytes directly.
    """
    token = os.getenv("HUGGINGFACE_API_TOKEN", "")
    if not token:
        raise RuntimeError("HUGGINGFACE_API_TOKEN is not set in .env")

    payload = json.dumps({
        "inputs": prompt,
        "parameters": {
            "width": config.IMAGE_WIDTH,
            "height": config.IMAGE_HEIGHT,
        },
    }).encode("utf-8")

    req = urllib.request.Request(
        HF_MODEL_URL,
        data=payload,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
        method="POST",
    )

    _MAX_IMAGE_BYTES = 50 * 1024 * 1024  # 50 MB cap
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            image_bytes = resp.read(_MAX_IMAGE_BYTES + 1)
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Hugging Face API error {e.code}: {body}") from e
    except urllib.error.URLError as e:
        raise RuntimeError(f"Hugging Face connection failed: {e}") from e

    if len(image_bytes) > _MAX_IMAGE_BYTES:
        raise RuntimeError(
            f"Hugging Face response exceeds {_MAX_IMAGE_BYTES} byte cap"
        )
    if len(image_bytes) < 1024:
        raise RuntimeError(
            f"Hugging Face returned suspiciously small response ({len(image_bytes)} bytes)"
        )
    if not (image_bytes.startswith(b"\x89PNG\r\n\x1a\n") or image_bytes[:3] == b"\xff\xd8\xff"):
        raise RuntimeError(
            "Hugging Face response is not a PNG or JPEG image"
        )

    out_path.write_bytes(image_bytes)
    return out_path


# ── Replicate Backend (direct REST API — no replicate package, no Pydantic) ───

def _generate_replicate(prompt: str, out_path: Path) -> Path:
    """
    Generate one image via Replicate REST API using only urllib.
    Avoids the replicate Python package (broken on Python 3.14 due to Pydantic v1).
    API docs: https://replicate.com/docs/reference/http
    """
    # Extract model owner/name and version from the config string "owner/model:version"
    model_ref = config.REPLICATE_IMAGE_MODEL
    if ":" in model_ref:
        model_id, version = model_ref.split(":", 1)
    else:
        model_id, version = model_ref, None

    headers = {
        "Authorization": f"Bearer {config.REPLICATE_API_TOKEN}",
        "Content-Type": "application/json",
        "Prefer": "wait=60",  # ask Replicate to wait up to 60s before returning
    }

    # FLUX.1-dev requires dimensions to be multiples of 32.
    # Rather than snapping arbitrary config values, use the native aspect_ratio preset
    # which guarantees correct output size without any dimension arithmetic.
    payload: dict[str, Any] = {
        "input": {
            "prompt": prompt,
            "aspect_ratio": "9:16",     # native preset — guaranteed portrait output
            "num_outputs": 1,
            "num_inference_steps": 28,  # FLUX.1-dev optimal steps
            "output_format": "png",
            "output_quality": 100,
        }
    }
    if version:
        payload["version"] = version  # required for generic /v1/predictions endpoint

    data = json.dumps(payload).encode("utf-8")
    # Use model-specific endpoint when no version is pinned (e.g. flux-schnell)
    # Generic /v1/predictions requires a version hash; model endpoint does not.
    if version:
        api_url = "https://api.replicate.com/v1/predictions"
    else:
        api_url = f"https://api.replicate.com/v1/models/{model_id}/predictions"

    # 429 retry loop — up to 3 attempts, honouring retry_after from response body
    prediction = None
    for rate_attempt in range(3):
        req = urllib.request.Request(api_url, data=data, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=90) as resp:
                prediction = json.loads(resp.read().decode("utf-8"))
            break  # success — exit retry loop
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", errors="replace")
            if e.code == 429:
                try:
                    retry_after = int(json.loads(body).get("retry_after", 60))
                except (ValueError, AttributeError):
                    retry_after = 60
                logger.warning(
                    f"[image_generator] Replicate rate-limited (429) — "
                    f"waiting {retry_after}s (attempt {rate_attempt + 1}/3)"
                )
                time.sleep(retry_after)
                continue
            raise RuntimeError(f"Replicate API error {e.code}: {body}") from e
        except urllib.error.URLError as e:
            raise RuntimeError(f"Replicate connection failed: {e}") from e
    else:
        raise RuntimeError("Replicate rate limit not resolved after 3 retries")

    prediction_id = prediction.get("id")
    if not prediction_id:
        raise RuntimeError(f"Replicate returned no prediction ID: {prediction}")

    # Poll until done (Prefer: wait=60 may have already resolved it)
    for attempt in range(90):  # up to ~90 seconds of polling
        status = prediction.get("status", "")

        if status == "succeeded":
            output = prediction.get("output", [])
            if not output:
                raise RuntimeError("Replicate succeeded but returned no output URLs")
            img_url = str(output[0]) if isinstance(output, list) else str(output)
            if not img_url.startswith("https://"):
                raise RuntimeError(
                    f"Replicate returned non-https output URL: {img_url!r}"
                )
            _MAX_IMAGE_BYTES = 50 * 1024 * 1024  # 50 MB cap
            with urllib.request.urlopen(img_url, timeout=60) as img_resp:
                # Re-check the final URL in case urllib followed a cross-scheme
                # redirect (e.g. 302 → http://…) past the initial https guard.
                final_url = getattr(img_resp, "url", img_url)
                if not final_url.startswith("https://"):
                    raise RuntimeError(
                        f"Replicate redirected to non-https URL: {final_url!r}"
                    )
                data = img_resp.read(_MAX_IMAGE_BYTES + 1)
            if len(data) > _MAX_IMAGE_BYTES:
                raise RuntimeError(
                    f"Replicate image exceeds {_MAX_IMAGE_BYTES} byte cap"
                )
            out_path.write_bytes(data)
            return out_path

        if status in ("failed", "canceled"):
            error = prediction.get("error", "unknown error")
            raise RuntimeError(f"Replicate prediction {status}: {error}")

        # Not done yet — poll
        time.sleep(1)
        poll_url = f"https://api.replicate.com/v1/predictions/{prediction_id}"
        poll_req = urllib.request.Request(
            poll_url,
            headers={"Authorization": f"Bearer {config.REPLICATE_API_TOKEN}"},
            method="GET",
        )
        try:
            with urllib.request.urlopen(poll_req, timeout=30) as poll_resp:
                prediction = json.loads(poll_resp.read().decode("utf-8"))
        except urllib.error.URLError as e:
            logger.warning(f"[image_generator] Replicate poll attempt {attempt} failed: {e}")
            continue

    raise RuntimeError("Replicate timed out after 90 seconds")


# ── PIL Placeholder Backend (guaranteed offline fallback) ─────────────────────

def _load_font(size: int):
    """Try to load a TrueType font; fall back to PIL's built-in bitmap font."""
    from PIL import ImageFont
    candidates = [
        # Windows
        "C:/Windows/Fonts/Georgia.ttf",
        "C:/Windows/Fonts/Georgiab.ttf",
        "C:/Windows/Fonts/Times New Roman.ttf",
        "C:/Windows/Fonts/timesnewroman.ttf",
        "C:/Windows/Fonts/Arial.ttf",
        # macOS
        "/System/Library/Fonts/Times.ttc",
        "/Library/Fonts/Arial.ttf",
        # Linux
        "/usr/share/fonts/truetype/dejavu/DejaVuSerif.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSerif-Regular.ttf",
    ]
    for path in candidates:
        try:
            return ImageFont.truetype(path, size)
        except (IOError, OSError):
            continue
    return ImageFont.load_default()


def _wrap_text(text: str, font, max_width: int, draw) -> list[str]:
    """Word-wrap text to fit within max_width pixels."""
    words = text.split()
    lines, current = [], []
    for word in words:
        test = " ".join(current + [word])
        bbox = draw.textbbox((0, 0), test, font=font)
        if bbox[2] - bbox[0] <= max_width:
            current.append(word)
        else:
            if current:
                lines.append(" ".join(current))
            current = [word]
    if current:
        lines.append(" ".join(current))
    return lines or [text[:30]]


def _generate_pil(prompt: str, out_path: Path, script: dict | None = None) -> Path:
    """
    Generate a styled cinematic historical image using Pillow only.
    No internet or API key required — the guaranteed offline fallback.
    Produces a dark, atmospheric 9:16 portrait with event text overlay.
    """
    try:
        from PIL import Image, ImageDraw, ImageFilter
    except ImportError as e:
        raise RuntimeError("Pillow not installed. Run: pip install Pillow") from e

    W, H = config.IMAGE_WIDTH, config.IMAGE_HEIGHT
    rng = random.Random(hash(prompt) % 2 ** 32)

    # ── Background gradient (dark charcoal → deep sepia) ──────────────────────
    img = Image.new("RGB", (W, H))
    draw = ImageDraw.Draw(img)
    for y in range(H):
        t = y / H
        r = int(18 + t * 35)
        g = int(12 + t * 18)
        b = int(22 + t * 10)
        draw.line([(0, y), (W, y)], fill=(r, g, b))

    # ── Film grain ─────────────────────────────────────────────────────────────
    for _ in range(W * H // 12):
        x = rng.randint(0, W - 1)
        y = rng.randint(0, H - 1)
        v = rng.randint(0, 55)
        draw.point((x, y), fill=(v, int(v * 0.85), int(v * 0.7)))

    img = img.filter(ImageFilter.GaussianBlur(radius=0.4))

    # ── Vignette (dark border) ─────────────────────────────────────────────────
    vignette = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    vdraw = ImageDraw.Draw(vignette)
    steps = 70
    for i in range(steps):
        alpha = int(210 * ((1 - i / steps) ** 2))
        vdraw.rectangle([i, i, W - i - 1, H - i - 1], outline=(0, 0, 0, alpha))
    img = Image.alpha_composite(img.convert("RGBA"), vignette).convert("RGB")
    draw = ImageDraw.Draw(img)

    # ── Decorative gold lines ─────────────────────────────────────────────────
    gold = (190, 148, 65)
    dim_gold = (120, 92, 38)
    margin = 38
    draw.line([(margin, 90), (W - margin, 90)], fill=gold, width=2)
    draw.line([(margin, 94), (W - margin, 94)], fill=dim_gold, width=1)
    draw.line([(margin, H - 90), (W - margin, H - 90)], fill=dim_gold, width=1)
    draw.line([(margin, H - 94), (W - margin, H - 94)], fill=gold, width=2)

    # ── Fonts ──────────────────────────────────────────────────────────────────
    font_header = _load_font(28)
    font_body = _load_font(42)
    font_meta = _load_font(26)

    # ── "UNREAL HISTORY" header ────────────────────────────────────────────────
    header_text = "UNREAL HISTORY"
    draw.text((W // 2 + 1, 57), header_text, font=font_header, fill=(0, 0, 0), anchor="mm")
    draw.text((W // 2, 56), header_text, font=font_header, fill=gold, anchor="mm")

    # ── Event text (extract readable portion from prompt) ─────────────────────
    # The prompt starts with a scene description before the first comma.
    readable = prompt.split(",")[0].strip()

    lines = _wrap_text(readable, font_body, W - 80, draw)
    line_height = 56
    total_text_h = len(lines) * line_height
    start_y = (H - total_text_h) // 2

    for i, line in enumerate(lines):
        y = start_y + i * line_height
        # Drop shadow
        draw.text((W // 2 + 2, y + 2), line, font=font_body, fill=(0, 0, 0), anchor="mm")
        draw.text((W // 2, y), line, font=font_body, fill=(238, 225, 195), anchor="mm")

    # ── Event metadata from script ─────────────────────────────────────────────
    if script:
        event = script.get("source_event", {})
        year = event.get("year", "")
        location = event.get("location", "")
        meta = "  |  ".join(filter(None, [year, location]))
        if meta:
            draw.text((W // 2 + 1, H - 56 + 1), meta, font=font_meta, fill=(0, 0, 0), anchor="mm")
            draw.text((W // 2, H - 56), meta, font=font_meta, fill=gold, anchor="mm")

    img.save(str(out_path), "PNG")
    return out_path


# ── Main Entry Point ──────────────────────────────────────────────────────────

def generate_images(
    scripts: list[dict],
    slug: str,
    scene_plans: list | None = None,
) -> list[list[Path]]:
    """
    Generate images per script/event.

    If `scene_plans` is provided, image prompts come from each scene's
    `image_prompt` (produced by pipeline.scene_planner, role-aware + preset-aware).
    Otherwise falls back to the legacy 5-shot hardcoded prompt set.

    Returns list of lists: [[img0, img1, ...], [img0, img1, ...], ...]
    Resumable — skips already-generated images.
    """
    backend = detect_backend()
    all_image_paths = []

    # Index plans by event_index for safe alignment (scripts may be out of order)
    plans_by_idx = {}
    if scene_plans:
        for p in scene_plans:
            plans_by_idx[p.event_index] = p

    for script in scripts:
        idx = script.get("event_index", scripts.index(script))
        img_dir = config.OUTPUT_DIR / slug / "images" / str(idx)
        img_dir.mkdir(parents=True, exist_ok=True)

        plan = plans_by_idx.get(idx)
        if plan is not None:
            prompts = [scene.image_prompt for scene in plan.scenes]
            logger.info(
                f"[image_generator] Using scene plan ({len(prompts)} scenes, "
                f"preset={plan.preset}) for event {idx}"
            )
        else:
            prompts = _build_image_prompts(script)
        event_images = []

        logger.info(
            f"[image_generator] Generating {len(prompts)} images "
            f"for event {idx} using {backend}..."
        )

        for img_idx, prompt in enumerate(prompts):
            out_path = img_dir / f"img_{img_idx}.png"

            if out_path.exists() and out_path.stat().st_size > 1024:
                logger.info(f"[image_generator] Cache hit: {out_path.name}")
                event_images.append(out_path)
                continue

            logger.info(f"[image_generator] Generating image {img_idx + 1}/{len(prompts)}...")

            generated = False
            last_error = None

            # Build fallback chain: primary → pil (guaranteed)
            backends_to_try = [backend] if backend != "pil" else []
            backends_to_try.append("pil")

            for attempt_backend in backends_to_try:
                try:
                    if attempt_backend == "huggingface":
                        _generate_huggingface(prompt, out_path)
                    elif attempt_backend == "replicate":
                        _generate_replicate(prompt, out_path)
                    elif attempt_backend == "pil":
                        _generate_pil(prompt, out_path, script)

                    logger.info(f"[image_generator] Saved: {out_path} (via {attempt_backend})")
                    generated = True
                    break

                except Exception as e:
                    last_error = e
                    next_backends = backends_to_try[backends_to_try.index(attempt_backend) + 1:]
                    if next_backends:
                        logger.warning(
                            f"[image_generator] {attempt_backend} failed for image {img_idx}: {e} "
                            f"— trying {next_backends[0]}"
                        )
                    else:
                        logger.error(f"[image_generator] PIL fallback also failed: {e}")

            if not generated:
                raise RuntimeError(
                    f"All image backends failed for image {img_idx}. Last error: {last_error}"
                )

            event_images.append(out_path)

        all_image_paths.append(event_images)
        logger.info(f"[image_generator] Event {idx} images complete: {len(event_images)} images")

    return all_image_paths
