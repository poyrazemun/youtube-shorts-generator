"""
Preset / template system.

A Preset declares, per scene role:
  - style tokens injected into image prompts (palette, lens, grade)
  - a duration weight (relative share of total audio duration)
  - a role-specific prompt lead-in

Presets are intentionally lightweight: they are pure data, consumed by the
scene_planner to build role-aware image prompts and per-scene durations.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class RoleDirection:
    style_tokens: str = ""         # appended to image prompt
    weight: float = 1.0            # relative duration weight
    prompt_emphasis: str = ""      # role-specific prompt lead-in


@dataclass
class Preset:
    name: str
    description: str
    roles: dict[str, RoleDirection]

    def for_role(self, role: str) -> RoleDirection:
        return self.roles.get(role, RoleDirection())


# ── documentary_clean ────────────────────────────────────────────────────────
# Restrained, archival look. Balanced pacing.
DOCUMENTARY_CLEAN = Preset(
    name="documentary_clean",
    description="Archival, restrained, balanced pacing.",
    roles={
        "hook": RoleDirection(
            style_tokens="muted documentary palette, soft diffused lighting, archival 35mm grain",
            weight=1.25,
            prompt_emphasis="Strong, immediate establishing frame",
        ),
        "context": RoleDirection(
            style_tokens="neutral color grading, natural daylight, explanatory wide composition",
            weight=0.9,
            prompt_emphasis="Explanatory, informative scene",
        ),
        "rehook": RoleDirection(
            style_tokens="soft focus, cinematic mid-shot, muted tones",
            weight=0.9,
        ),
        "twist": RoleDirection(
            style_tokens="heightened contrast, deep shadows, single accent color",
            weight=1.0,
            prompt_emphasis="Pivotal, striking moment with strong contrast",
        ),
        "ending": RoleDirection(
            style_tokens="clean negative space, calm composition, gentle light",
            weight=0.95,
            prompt_emphasis="Contemplative closing frame with room for CTA",
        ),
    },
)

# ── dramatic_history ─────────────────────────────────────────────────────────
# Bold, high-contrast, chiaroscuro.
DRAMATIC_HISTORY = Preset(
    name="dramatic_history",
    description="High-contrast chiaroscuro prompt style.",
    roles={
        "hook": RoleDirection(
            style_tokens="cinematic chiaroscuro, deep ink-black shadows, dramatic rim light",
            weight=1.3,
            prompt_emphasis="Arresting, unforgettable opening image",
        ),
        "context": RoleDirection(
            style_tokens="moody painterly lighting, burnt sienna and ink-black palette",
            weight=0.85,
        ),
        "rehook": RoleDirection(
            style_tokens="tense atmospheric framing, single vivid accent color",
            weight=0.85,
        ),
        "twist": RoleDirection(
            style_tokens="extreme chiaroscuro, near-black shadows, blood-red or amber accent",
            weight=1.1,
            prompt_emphasis="Maximum drama — the reveal moment",
        ),
        "ending": RoleDirection(
            style_tokens="solemn dusk light, muted sepia, quiet weight",
            weight=0.9,
            prompt_emphasis="Resonant closing image with emotional weight",
        ),
    },
)

# ── viral_fact_card ──────────────────────────────────────────────────────────
# Punchy, saturated, TikTok energy in the prompt style.
VIRAL_FACT_CARD = Preset(
    name="viral_fact_card",
    description="Punchy TikTok-style image prompts. Saturated, high-contrast.",
    roles={
        "hook": RoleDirection(
            style_tokens="saturated cinematic grade, punchy contrast, vertical-first composition",
            weight=1.3,
            prompt_emphasis="Scroll-stopping hero frame",
        ),
        "context": RoleDirection(
            style_tokens="vibrant but grounded palette, clear subject separation",
            weight=0.8,
        ),
        "rehook": RoleDirection(
            style_tokens="bold color blocking, strong silhouette",
            weight=0.8,
        ),
        "twist": RoleDirection(
            style_tokens="high-impact contrast, vivid accent, shallow depth",
            weight=1.1,
            prompt_emphasis="The jaw-drop moment — maximum visual punch",
        ),
        "ending": RoleDirection(
            style_tokens="clean framing with negative space for CTA overlay",
            weight=1.0,
            prompt_emphasis="Clean, uncluttered closing frame with negative space",
        ),
    },
)


PRESETS: dict[str, Preset] = {
    DOCUMENTARY_CLEAN.name: DOCUMENTARY_CLEAN,
    DRAMATIC_HISTORY.name: DRAMATIC_HISTORY,
    VIRAL_FACT_CARD.name: VIRAL_FACT_CARD,
}

DEFAULT_PRESET = DOCUMENTARY_CLEAN.name


def get_preset(name: str | None) -> Preset:
    """Return the named preset, falling back to the default."""
    if not name:
        return PRESETS[DEFAULT_PRESET]
    if name not in PRESETS:
        raise ValueError(
            f"Unknown preset '{name}'. Available: {', '.join(PRESETS)}"
        )
    return PRESETS[name]


def list_presets() -> list[str]:
    return list(PRESETS.keys())
