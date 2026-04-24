"""
YouTube thumbnail generator (1280×720, 16:9).
Uses HuggingFace FLUX.1-schnell for the base image, Pillow for title overlay.
Falls back to PIL-only dark gradient + text if HuggingFace fails or token is absent.
"""
import io
import json
import logging
import os
import urllib.error
import urllib.request
from pathlib import Path


logger = logging.getLogger(__name__)

THUMB_W = 1280
THUMB_H = 720
HF_THUMB_URL = (
    "https://router.huggingface.co/hf-inference/models/black-forest-labs/FLUX.1-schnell"
)


def _fetch_hf_image(prompt: str) -> bytes | None:
    """Request a 1280×720 image from HuggingFace. Returns raw bytes or None."""
    token = os.getenv("HUGGINGFACE_API_TOKEN", "")
    if not token:
        return None

    payload = json.dumps(
        {"inputs": prompt, "parameters": {"width": THUMB_W, "height": THUMB_H}}
    ).encode("utf-8")

    req = urllib.request.Request(
        HF_THUMB_URL,
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
            data = resp.read(_MAX_IMAGE_BYTES + 1)
        if len(data) > _MAX_IMAGE_BYTES:
            logger.warning(
                f"[thumbnail] HuggingFace response exceeds {_MAX_IMAGE_BYTES} byte cap"
            )
            return None
        if len(data) < 1024:
            return None
        if not (data.startswith(b"\x89PNG\r\n\x1a\n") or data[:3] == b"\xff\xd8\xff"):
            logger.warning("[thumbnail] HuggingFace response is not a PNG or JPEG")
            return None
        return data
    except Exception as e:
        logger.warning(f"[thumbnail] HuggingFace request failed: {e}")
        return None


def _make_pil_gradient(title: str, out_path: Path) -> Path:
    """Dark gradient thumbnail with centred title text (PIL-only fallback)."""
    from PIL import Image, ImageDraw, ImageFont

    img = Image.new("RGB", (THUMB_W, THUMB_H))
    draw = ImageDraw.Draw(img)

    for y in range(THUMB_H):
        t = y / THUMB_H
        r = int(10 + 70 * t)
        g = int(10 + 20 * t)
        b = int(20 + 40 * t)
        draw.line([(0, y), (THUMB_W, y)], fill=(r, g, b))

    font_size = 72
    try:
        font = ImageFont.truetype("arial.ttf", font_size)
    except Exception:
        font = ImageFont.load_default()

    words = title.split()
    lines: list[str] = []
    current: list[str] = []
    for word in words:
        test = " ".join(current + [word])
        bbox = draw.textbbox((0, 0), test, font=font)
        if bbox[2] > THUMB_W - 100 and current:
            lines.append(" ".join(current))
            current = [word]
        else:
            current.append(word)
    if current:
        lines.append(" ".join(current))

    line_h = font_size + 12
    y_start = 30  # top-aligned, not centered

    for i, line in enumerate(lines):
        y = y_start + i * line_h
        # Black shadow for contrast, then yellow text on top
        draw.text((52, y + 3), line, font=font, fill=(0, 0, 0))
        draw.text((50, y), line, font=font, fill=(255, 220, 0))

    img.save(str(out_path), "PNG")
    return out_path


def generate_thumbnail(script: dict, work_dir: Path) -> Path | None:
    """
    Generate a 1280×720 thumbnail PNG.
    Returns the Path to the saved thumbnail, or None if Pillow is not installed.
    """
    try:
        from PIL import Image
    except ImportError:
        logger.warning("[thumbnail] Pillow not installed — skipping thumbnail.")
        return None

    work_dir.mkdir(parents=True, exist_ok=True)
    idx = script.get("event_index", 0)
    out_path = work_dir / f"{idx}_thumbnail.png"

    if out_path.exists() and out_path.stat().st_size > 1024:
        logger.info(f"[thumbnail] Cache hit: {out_path.name}")
        return out_path

    event = script.get("source_event", {})
    title = script.get("title", "Unreal History")
    visual_theme = event.get("visual_theme", event.get("event", "historical scene"))
    year = event.get("year", "")
    location = event.get("location", "")

    prompt = (
        f"YouTube thumbnail: dramatic close-up of a shocked or awed historical figure, "
        f"{visual_theme}, {location}, {year}, bright warm colors, high contrast, "
        "cinematic lighting, extremely detailed, photorealistic, 16:9, no text"
    )

    image_bytes = _fetch_hf_image(prompt)
    if image_bytes:
        try:
            from PIL import ImageDraw, ImageFont

            img = Image.open(io.BytesIO(image_bytes)).convert("RGB")
            img = img.resize((THUMB_W, THUMB_H), Image.Resampling.LANCZOS)
            draw = ImageDraw.Draw(img)

            # Dark overlay on top third (title lives here)
            overlay_h = THUMB_H // 3
            for dy in range(overlay_h):
                alpha = int(160 * (1 - dy / overlay_h))
                draw.line([(0, dy), (THUMB_W, dy)], fill=(0, 0, 0, alpha))

            font_size = 68
            try:
                font = ImageFont.truetype("arialbd.ttf", font_size)
            except Exception:
                try:
                    font = ImageFont.truetype("arial.ttf", font_size)
                except Exception:
                    font = ImageFont.load_default()

            short_title = title[:60]
            text_y = 24
            # Black shadow for contrast, then yellow text on top
            draw.text((52, text_y + 3), short_title, font=font, fill=(0, 0, 0))
            draw.text((50, text_y), short_title, font=font, fill=(255, 220, 0))

            img.save(str(out_path), "PNG")
            logger.info(f"[thumbnail] HuggingFace thumbnail: {out_path.name}")
            return out_path
        except Exception as e:
            logger.warning(f"[thumbnail] HF image processing failed: {e} — PIL fallback")

    # PIL-only fallback
    try:
        _make_pil_gradient(title, out_path)
        logger.info(f"[thumbnail] PIL fallback thumbnail: {out_path.name}")
        return out_path
    except Exception as e:
        logger.warning(f"[thumbnail] Thumbnail generation failed: {e}")
        return None
