"""
Script → ScenePlan conversion.

Takes a generated script (with hook/context/rehook/twist/ending_fact fields)
plus the total audio duration and produces a ScenePlan whose scene timings
sum exactly to the audio duration.

This is the only place that knows how to map narrative roles to:
  - relative durations (from the preset's role weights)
  - image prompts (role-aware, using the preset's style tokens)

The resulting ScenePlan is saved as JSON and consumed by both the image
generator (for prompts) and the video assembler (for scene durations).
"""

from __future__ import annotations

import logging
import re
from typing import Any

import config
from pipeline.presets import Preset, get_preset
from pipeline.scene_spec import (
    ALL_ROLES,
    ScenePlan,
    SceneRole,
    SceneSpec,
)

logger = logging.getLogger(__name__)


_FILLER_OPENER_RE = re.compile(
    r"^(did you know[\s,]*|in \d{4}[\s,]*|in this moment[\s,]*"
    r"|at this point[\s,]*|this is[\s,]*|here,?\s)",
    flags=re.IGNORECASE,
)


def _visual_fragment(text: str, max_words: int = 14) -> str:
    if not text:
        return ""
    text = _FILLER_OPENER_RE.sub("", text.strip()).lstrip(" ,;:")
    words = text.split()
    return " ".join(words[:max_words]).rstrip(".,;:!?")


# Role → (shot framing, core lighting/palette fallback) used when a preset
# doesn't override. These bake the "scene-role-aware" image direction described
# in the Phase 1 spec: hooks get strong/immediate, context is explanatory,
# twists heighten drama, endings allow negative space for CTA.
_ROLE_DIRECTION_FALLBACK: dict[SceneRole, dict[str, str]] = {
    "hook": {
        "framing": "Wide panoramic establishing shot",
        "look":    "dawn golden hour, long shadows, immediate and arresting",
    },
    "context": {
        "framing": "Medium shot of historical figures or setting",
        "look":    "overcast natural daylight, explanatory composition",
    },
    "rehook": {
        "framing": "Cinematic mid-shot with atmospheric depth",
        "look":    "soft directional light, renewed tension",
    },
    "twist": {
        "framing": "Dynamic turning-point action shot",
        "look":    "hard side-lighting, high-contrast chiaroscuro, single vivid accent",
    },
    "ending": {
        "framing": "Quiet closing frame with negative space",
        "look":    "soft dusk light, muted palette, calm weight",
    },
}


def _build_image_prompt(
    role: SceneRole,
    fragment: str,
    event: dict[str, Any],
    preset: Preset,
) -> str:
    """Role-aware, preset-aware image prompt."""
    direction = _ROLE_DIRECTION_FALLBACK[role]
    role_dir = preset.for_role(role)
    year = event.get("year", "historical")
    location = event.get("location", "")
    visual_theme = event.get("visual_theme", event.get("event", ""))
    location_clause = f" in {location}" if location else ""

    prefix = role_dir.prompt_emphasis or direction["framing"]
    look = role_dir.style_tokens or direction["look"]
    fragment_clause = f"{fragment}, " if fragment else ""

    return (
        f"{prefix}, {visual_theme}, {year}{location_clause}, "
        f"{fragment_clause}{look}, {config.IMAGE_STYLE_PROMPT}"
    )


def _scene_durations(weights: list[float], total: float) -> list[float]:
    s = sum(weights)
    if s <= 0:
        return [total / len(weights)] * len(weights)
    return [total * (w / s) for w in weights]


def _role_text(script: dict, role: SceneRole) -> str:
    key = {
        "hook": "hook",
        "context": "context",
        "rehook": "rehook",
        "twist": "twist",
        "ending": "ending_fact",
    }[role]
    return str(script.get(key, "") or "")


def plan_scenes(
    script: dict,
    audio_duration: float,
    preset_name: str | None = None,
) -> ScenePlan:
    """Build a ScenePlan for one script+audio pair."""
    preset = get_preset(preset_name)
    event = script.get("source_event", {}) or {}

    # 1. Collect role weights and filter out empty roles so we don't render
    #    a silent scene with no content.
    roles_present: list[SceneRole] = []
    texts: list[str] = []
    weights: list[float] = []
    for role in ALL_ROLES:
        text = _role_text(script, role)
        if not text.strip():
            continue
        roles_present.append(role)
        texts.append(text)
        weights.append(preset.for_role(role).weight)

    if not roles_present:
        # Degenerate fallback — shouldn't happen, but keep things safe.
        roles_present = list(ALL_ROLES)
        texts = [_role_text(script, r) or " " for r in roles_present]
        weights = [preset.for_role(r).weight for r in roles_present]

    durations = _scene_durations(weights, audio_duration)

    # 2. Build SceneSpecs
    scenes: list[SceneSpec] = []
    cursor = 0.0
    for i, (role, text, dur) in enumerate(zip(roles_present, texts, durations)):
        role_dir = preset.for_role(role)
        fragment = _visual_fragment(text)
        img_prompt = _build_image_prompt(role, fragment, event, preset)

        scenes.append(SceneSpec(
            index=i,
            role=role,
            text=text,
            duration=round(dur, 3),
            image_prompt=img_prompt,
            start=round(cursor, 3),
            visual_hints={
                "framing": _ROLE_DIRECTION_FALLBACK[role]["framing"],
                "style_tokens": role_dir.style_tokens,
                "prompt_emphasis": role_dir.prompt_emphasis,
            },
        ))
        cursor += dur

    return ScenePlan(
        event_index=int(script.get("event_index", 0)),
        preset=preset.name,
        total_duration=round(audio_duration, 3),
        scenes=scenes,
    )


def plan_all(
    scripts: list[dict],
    audio_durations: list[float],
    slug: str,
    preset_name: str | None = None,
) -> list[ScenePlan]:
    """Plan scenes for every script, save each plan as JSON, return list of plans."""
    out_dir = config.OUTPUT_DIR / slug / "scene_plans"
    out_dir.mkdir(parents=True, exist_ok=True)

    plans: list[ScenePlan] = []
    for script, dur in zip(scripts, audio_durations):
        plan = plan_scenes(script, dur, preset_name=preset_name)
        path = out_dir / f"{plan.event_index}.json"
        plan.save(path)
        logger.info(
            f"[scene_planner] Plan for event {plan.event_index}: "
            f"{len(plan.scenes)} scenes, preset={plan.preset}, "
            f"total={plan.total_duration:.1f}s → {path.name}"
        )
        plans.append(plan)
    return plans
