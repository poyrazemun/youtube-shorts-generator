"""
SceneSpec — the scene planning layer between script generation and rendering.

A SceneSpec describes one beat of the video in a renderer-agnostic way:
the narrative role, its text, its duration, and visual hints for image
generation.

The list `ScenePlan.scenes` is saved as JSON in the output folder so it
can be inspected, edited, and replayed deterministically.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path
from typing import Any, Literal

SceneRole = Literal["hook", "context", "rehook", "twist", "ending"]

ALL_ROLES: tuple[SceneRole, ...] = ("hook", "context", "rehook", "twist", "ending")


@dataclass
class SceneSpec:
    """One beat of the video."""
    index: int
    role: SceneRole
    text: str
    duration: float
    image_prompt: str                 # final prompt sent to image backend
    image_path: str | None = None     # resolved after image generation
    start: float = 0.0                # absolute start in video
    # Visual-direction hints surfaced from the role — lets image_generator
    # be scene-role-aware without re-deriving which role maps to which look.
    visual_hints: dict[str, Any] = field(default_factory=dict)

    @property
    def end(self) -> float:
        return self.start + self.duration


_SCENE_FIELDS = {f.name for f in fields(SceneSpec)}


@dataclass
class ScenePlan:
    """Full plan for one event → one video."""
    event_index: int
    preset: str
    total_duration: float
    scenes: list[SceneSpec]

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_index": self.event_index,
            "preset": self.preset,
            "total_duration": self.total_duration,
            "scenes": [asdict(s) for s in self.scenes],
        }

    def save(self, path: Path) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(self.to_dict(), indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        return path

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> ScenePlan:
        # Tolerate unknown keys in on-disk JSONs (older runs may contain
        # `motion` / `overlays` fields from the retired rendering layer).
        scenes = [
            SceneSpec(**{k: v for k, v in s.items() if k in _SCENE_FIELDS})
            for s in d["scenes"]
        ]
        return cls(
            event_index=d["event_index"],
            preset=d["preset"],
            total_duration=d["total_duration"],
            scenes=scenes,
        )

    @classmethod
    def load(cls, path: Path) -> ScenePlan:
        return cls.from_dict(json.loads(path.read_text(encoding="utf-8")))
