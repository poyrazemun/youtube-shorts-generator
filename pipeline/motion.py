"""
Motion preset system.

Named motion presets are resolved into an ffmpeg filter fragment that operates
on a single scaled+padded image stream at [in], producing motion at [out].

We use ffmpeg's `zoompan` filter with a high frame-count per image so the
motion is smooth (zoompan works on input frames — each loaded image is a
single frame — so we pre-render the motion at the final fps × duration).

Presets:
  - static_hold       : no motion, locked frame
  - slow_push_in      : gradual zoom from 1.00 → 1.08
  - drift_left        : 1.06 zoom panning slowly left
  - drift_right       : 1.06 zoom panning slowly right
  - dramatic_zoom     : faster zoom 1.00 → 1.18, slight upward drift
"""

from __future__ import annotations

import config

FPS = 24

# Named motion presets → parameter dicts consumed by _build_zoompan
_PRESETS = {
    "static_hold": {"z_start": 1.0, "z_end": 1.0, "x": "iw/2-(iw/zoom/2)", "y": "ih/2-(ih/zoom/2)"},
    "slow_push_in": {"z_start": 1.0, "z_end": 1.08},
    "drift_left":   {"z_start": 1.06, "z_end": 1.06, "pan": "left"},
    "drift_right":  {"z_start": 1.06, "z_end": 1.06, "pan": "right"},
    "dramatic_zoom": {"z_start": 1.0, "z_end": 1.18, "drift_up": True},
}

DEFAULT_MOTION = "static_hold"


def list_motions() -> list[str]:
    return list(_PRESETS.keys())


def _zoom_expr(z_start: float, z_end: float, frames: int) -> str:
    """
    zoompan zoom expression. `on` is the current output frame index (0..frames-1).
    Linearly interpolate zoom from z_start to z_end across `frames`.
    """
    if abs(z_end - z_start) < 1e-4:
        return f"{z_start:.4f}"
    # zoom clamped to avoid runaway on final frame
    return f"min({z_start:.4f}+({z_end - z_start:.4f})*on/{max(frames - 1, 1)},{max(z_start, z_end):.4f})"


def build_motion_filter(
    motion: str,
    duration: float,
    width: int | None = None,
    height: int | None = None,
    in_label: str = "in",
    out_label: str = "out",
) -> str:
    """
    Return an ffmpeg filter fragment: `[in_label]<zoompan>[out_label]`.

    Note: zoompan's output resolution is set via `s=WxH`; the x/y expressions
    are evaluated in *input* pixel space, so we always drive zoompan with the
    already-scaled 1080×1920 frame.
    """
    if motion not in _PRESETS:
        motion = DEFAULT_MOTION
    params = _PRESETS[motion]

    W = width or config.VIDEO_WIDTH
    H = height or config.VIDEO_HEIGHT
    frames = max(int(round(duration * FPS)), 2)

    z_start = params.get("z_start", 1.0)
    z_end = params.get("z_end", 1.0)
    zexpr = _zoom_expr(z_start, z_end, frames)

    # Pan expressions
    pan = params.get("pan")
    drift_up = params.get("drift_up", False)
    if "x" in params and "y" in params:
        x_expr = params["x"]
        y_expr = params["y"]
    elif pan == "left":
        # start right-of-center, drift to left-of-center as `on` grows
        x_expr = f"(iw-iw/zoom)*(1-on/{max(frames - 1, 1)})"
        y_expr = "ih/2-(ih/zoom/2)"
    elif pan == "right":
        x_expr = f"(iw-iw/zoom)*(on/{max(frames - 1, 1)})"
        y_expr = "ih/2-(ih/zoom/2)"
    elif drift_up:
        x_expr = "iw/2-(iw/zoom/2)"
        # drift from just-below-center to just-above
        y_expr = f"(ih-ih/zoom)*(0.55-0.1*on/{max(frames - 1, 1)})"
    else:
        # centered zoom
        x_expr = "iw/2-(iw/zoom/2)"
        y_expr = "ih/2-(ih/zoom/2)"

    # zoompan needs fps driven externally via d=1 per input + inputs looped.
    # We already -loop 1 -t <dur> each image, so zoompan's d=1 combined with
    # `fps=FPS` after it yields FPS*duration frames of smooth motion.
    return (
        f"[{in_label}]zoompan="
        f"z='{zexpr}':"
        f"x='{x_expr}':"
        f"y='{y_expr}':"
        f"d=1:"
        f"s={W}x{H}:"
        f"fps={FPS}"
        f"[{out_label}]"
    )
