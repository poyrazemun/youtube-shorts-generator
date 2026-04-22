"""
Reusable overlay block system.

Each block is a pure function that takes ({params}, start, end, video_w, video_h)
and returns an ffmpeg filter fragment suitable for chaining with commas inside
a -vf pipeline. Blocks use `drawtext` + `drawbox` (ffmpeg builtins — no extra
dependencies).

Current blocks:
  - title_card  : upper-area title banner, used on hook scenes
  - fact_badge  : accent badge ("FACT", "!", "WAIT") at the corner — used on
                  twists and viral-style scenes
  - era_tag     : small year/location tag used on context scenes

The assembler composes these into the final -vf chain from each scene's
SceneSpec.overlays, using the scene's absolute start/end as enable times.
"""

from __future__ import annotations

from typing import Callable

import config


def _escape(text: str) -> str:
    """Escape a string for ffmpeg drawtext."""
    return (
        text.replace("\\", "\\\\")
        .replace("'", "\\'")
        .replace(":", "\\:")
        .replace(",", "\\,")
    )


def _enable_expr(start: float, end: float) -> str:
    return f"between(t,{start:.2f},{end:.2f})"


# ── title_card ───────────────────────────────────────────────────────────────

def title_card(params: dict, start: float, end: float, W: int, H: int) -> str:
    """
    Top (or center) banner with the video title text.
    params:
      position: "top" | "center"      (default: top)
      accent:   bool                  (adds a thin accent bar)
      text:     str                   (required — usually the script title)
    """
    text = params.get("text", "")
    if not text:
        return ""
    position = params.get("position", "top")
    accent = bool(params.get("accent", False))

    font_size = int(H * 0.033)
    pad = int(font_size * 0.45)
    safe = _escape(text)

    if position == "center":
        y_expr = "(h-text_h)/2"
    else:
        y_expr = f"h*0.10"

    box_color = "black@0.70" if not accent else "black@0.78"
    frag = (
        f"drawtext=text='{safe}':"
        f"fontsize={font_size}:fontcolor=white:"
        f"x=(w-text_w)/2:y={y_expr}:"
        f"box=1:boxcolor={box_color}:boxborderw={pad}:"
        f"fix_bounds=1:"
        f"enable='{_enable_expr(start, end)}'"
    )

    if accent:
        # Thin accent line just under the title box
        bar_h = max(2, int(H * 0.004))
        bar_y = f"(h*0.10)+text_h+{pad * 2 + 4}" if position == "top" else f"(h/2)+text_h/2+{pad + 4}"
        bar_w = int(W * 0.18)
        frag += (
            f",drawbox=x=(w-{bar_w})/2:y={bar_y}:"
            f"w={bar_w}:h={bar_h}:color=#E0B84A@0.95:t=fill:"
            f"enable='{_enable_expr(start, end)}'"
        )
    return frag


# ── fact_badge ───────────────────────────────────────────────────────────────

_FACT_BADGE_VARIANTS = {
    "mono":  {"text": "FACT",  "bg": "black@0.78",       "fg": "white"},
    "pop":   {"text": "FACT",  "bg": "#E0B84A@0.95",     "fg": "black"},
    "alert": {"text": "WAIT",  "bg": "#C23A2A@0.92",     "fg": "white"},
}


def fact_badge(params: dict, start: float, end: float, W: int, H: int) -> str:
    """
    Small corner badge that punctuates a scene.
    params:
      variant:   "mono" | "pop" | "alert"   (default: mono)
      text:      override label text         (default: variant's text)
      corner:    "tl" | "tr" | "bl" | "br"   (default: tl)
    """
    variant = params.get("variant", "mono")
    style = _FACT_BADGE_VARIANTS.get(variant, _FACT_BADGE_VARIANTS["mono"])
    text = params.get("text", style["text"])
    corner = params.get("corner", "tl")
    safe = _escape(text)

    font_size = int(H * 0.028)
    pad = int(font_size * 0.50)
    margin = int(H * 0.04)

    # Anchor from corner; "tl" = top-left, below the CTA-safe area
    if corner == "tr":
        x_expr = f"w-text_w-{margin}-{pad * 2}"
        y_expr = f"{margin}"
    elif corner == "bl":
        x_expr = f"{margin}"
        y_expr = f"h-text_h-{margin}-{pad * 2}"
    elif corner == "br":
        x_expr = f"w-text_w-{margin}-{pad * 2}"
        y_expr = f"h-text_h-{margin}-{pad * 2}"
    else:  # "tl"
        x_expr = f"{margin}"
        y_expr = f"{margin}"

    return (
        f"drawtext=text='{safe}':"
        f"fontsize={font_size}:fontcolor={style['fg']}:"
        f"x={x_expr}:y={y_expr}:"
        f"box=1:boxcolor={style['bg']}:boxborderw={pad}:"
        f"fix_bounds=1:"
        f"enable='{_enable_expr(start, end)}'"
    )


# ── era_tag ──────────────────────────────────────────────────────────────────

def era_tag(params: dict, start: float, end: float, W: int, H: int) -> str:
    """
    Small lower-left tag with year / location — used on context scenes.
    params:
      text:    override (default: constructed from year/location)
      year:    str
      location: str
      variant: "mono" | "bold" | "pop"
    """
    text = params.get("text")
    if not text:
        parts = [str(params.get("year", "")).strip(), str(params.get("location", "")).strip()]
        text = " · ".join(p for p in parts if p)
    if not text:
        return ""
    safe = _escape(text)

    variant = params.get("variant", "mono")
    if variant == "bold":
        bg, fg = "black@0.80", "#E8D99A"
    elif variant == "pop":
        bg, fg = "#E0B84A@0.90", "black"
    else:
        bg, fg = "black@0.65", "white"

    font_size = int(H * 0.022)
    pad = int(font_size * 0.45)
    margin_x = int(W * 0.05)
    # Above the subtitle band (subtitles sit at 22% from bottom)
    margin_y = int(H * 0.30)

    return (
        f"drawtext=text='{safe}':"
        f"fontsize={font_size}:fontcolor={fg}:"
        f"x={margin_x}:y=h-{margin_y}-text_h:"
        f"box=1:boxcolor={bg}:boxborderw={pad}:"
        f"fix_bounds=1:"
        f"enable='{_enable_expr(start, end)}'"
    )


# ── Registry ─────────────────────────────────────────────────────────────────

BLOCKS: dict[str, Callable] = {
    "title_card": title_card,
    "fact_badge": fact_badge,
    "era_tag":    era_tag,
}


def render_block(name: str, params: dict, start: float, end: float,
                 W: int | None = None, H: int | None = None) -> str:
    """Return the ffmpeg -vf fragment for a named block, or '' if unknown."""
    if name not in BLOCKS:
        return ""
    W = W or config.VIDEO_WIDTH
    H = H or config.VIDEO_HEIGHT
    try:
        return BLOCKS[name](params or {}, start, end, W, H)
    except Exception:
        # Blocks must never break the pipeline — failing open is the right call.
        return ""


def list_blocks() -> list[str]:
    return list(BLOCKS.keys())
