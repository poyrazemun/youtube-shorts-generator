"""
Manual image mode — pause the pipeline after scene planning so the user can
generate images externally (Midjourney, DALL-E, Sora, etc.) and drop them into
output/<slug>/images/<idx>/img_N.png. Resume by re-running the pipeline.
"""

from __future__ import annotations

import logging
from pathlib import Path

import config

logger = logging.getLogger(__name__)


class MissingManualImageError(RuntimeError):
    """Raised when --manual-images is set but expected PNGs are missing."""

    def __init__(self, missing: list[Path]):
        self.missing = missing
        super().__init__(
            f"{len(missing)} manual image(s) missing — see prompts.md in each event folder."
        )


def _event_dir(slug: str, event_index: int) -> Path:
    return config.OUTPUT_DIR / slug / "images" / str(event_index)


def _expected_paths(slug: str, event_index: int, count: int) -> list[Path]:
    d = _event_dir(slug, event_index)
    return [d / f"img_{i}.png" for i in range(count)]


def write_prompt_pack(scripts: list[dict], scene_plans: list, slug: str) -> list[Path]:
    """
    For each event, write a `prompts.md` next to where the user will drop PNGs.
    Returns the list of prompts.md paths written.
    """
    plans_by_idx = {p.event_index: p for p in (scene_plans or [])}
    written: list[Path] = []

    for script in scripts:
        idx = script.get("event_index", 0)
        plan = plans_by_idx.get(idx)
        if plan is None:
            logger.warning(f"[manual_images] No scene plan for event {idx} — skipping prompt pack.")
            continue

        event = script.get("source_event", {})
        d = _event_dir(slug, idx)
        d.mkdir(parents=True, exist_ok=True)

        lines: list[str] = []
        lines.append(f"# Image prompts — event {idx}")
        lines.append("")
        lines.append(f"**Title:** {script.get('title', '?')}")
        lines.append(f"**Year/Location:** {event.get('year', '?')} — {event.get('location', '?')}")
        lines.append(f"**Visual theme:** {event.get('visual_theme', '?')}")
        lines.append("")
        lines.append(f"Drop your finished PNGs in this folder as `img_0.png` … `img_{len(plan.scenes) - 1}.png`.")
        lines.append("Target size: 1080×1920 (9:16). Other sizes are auto-cropped on resume.")
        lines.append("")
        lines.append("---")
        lines.append("")

        for scene in plan.scenes:
            lines.append(f"## img_{scene.index}.png — {scene.role.upper()}")
            lines.append("")
            lines.append(f"**Narration:** {scene.text}")
            lines.append("")
            lines.append("**Full prompt (FLUX / SDXL style):**")
            lines.append("")
            lines.append("```")
            lines.append(scene.image_prompt)
            lines.append("```")
            lines.append("")
            hints = scene.visual_hints or {}
            framing = hints.get("framing", "")
            style = hints.get("style_tokens", "")
            short = ", ".join(filter(None, [
                framing,
                event.get("visual_theme", ""),
                f"{event.get('year', '')} {event.get('location', '')}".strip(),
                style,
                "9:16 vertical, cinematic historical photograph, photorealistic",
            ]))
            lines.append("**Short prompt (Midjourney / DALL-E style):**")
            lines.append("")
            lines.append("```")
            lines.append(short)
            lines.append("```")
            lines.append("")
            lines.append("---")
            lines.append("")

        out = d / "prompts.md"
        out.write_text("\n".join(lines), encoding="utf-8")
        written.append(out)
        logger.info(f"[manual_images] Wrote prompt pack: {out}")

    return written


def verify_and_normalize(
    scripts: list[dict],
    scene_plans: list,
    slug: str,
) -> list[list[Path]]:
    """
    Check that all expected PNGs exist for each event. Cover-crop any image
    that isn't exactly 1080×1920 so the assembler doesn't have to letterbox.

    Raises MissingManualImageError listing every missing path if any are absent.
    Returns [[event0 imgs], [event1 imgs], ...] on success.
    """
    plans_by_idx = {p.event_index: p for p in (scene_plans or [])}
    missing: list[Path] = []
    all_paths: list[list[Path]] = []

    target_w = config.VIDEO_WIDTH
    target_h = config.VIDEO_HEIGHT

    for script in scripts:
        idx = script.get("event_index", 0)
        plan = plans_by_idx.get(idx)
        count = len(plan.scenes) if plan else config.IMAGES_PER_EVENT
        paths = _expected_paths(slug, idx, count)

        event_imgs: list[Path] = []
        for p in paths:
            if not p.exists() or p.stat().st_size < 1024:
                missing.append(p)
                continue
            _normalize_to_target(p, target_w, target_h)
            event_imgs.append(p)
        all_paths.append(event_imgs)

    if missing:
        raise MissingManualImageError(missing)

    return all_paths


def _normalize_to_target(path: Path, target_w: int, target_h: int) -> None:
    """Cover-crop `path` to exactly target_w×target_h if it isn't already."""
    try:
        from PIL import Image, ImageOps
    except ImportError as e:
        raise RuntimeError("Pillow not installed. Run: pip install Pillow") from e

    with Image.open(path) as im:
        if im.size == (target_w, target_h):
            return
        logger.info(f"[manual_images] Resizing {path.name} {im.size} → ({target_w},{target_h})")
        fitted = ImageOps.fit(
            im.convert("RGB"),
            (target_w, target_h),
            method=Image.Resampling.LANCZOS,
            centering=(0.5, 0.5),
        )
        fitted.save(str(path), "PNG")
