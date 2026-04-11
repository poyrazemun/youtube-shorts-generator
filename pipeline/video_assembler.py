"""
STEP 5 — VIDEO ASSEMBLY
Uses ffmpeg (via subprocess) to:
  - Combine 5 images (each ~5 seconds) into a slideshow
  - Add voiceover audio
  - Burn subtitles from .srt file
  - Enforce 1080x1920 (9:16 vertical)
  - Total duration 20-30 seconds
Output: output/<slug>/video/<event_idx>.mp4 and output/<slug>_<event_idx>.mp4
"""

import logging
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

import config
from pipeline.tts_generator import get_audio_duration

logger = logging.getLogger(__name__)

CTA_OVERLAY_TEXT = config.SUBSCRIBE_CTA
CTA_DURATION_SECS = 3.0  # show in last 3 seconds of video


def _build_cta_drawtext(audio_duration: float) -> str:
    """Return an ffmpeg drawtext filter string for the subscribe CTA overlay."""
    H = config.VIDEO_HEIGHT  # 1920
    font_size = int(
        H * 0.026
    )  # ~50px — fits "Follow @ThatActuallyHappened11" within 1080px
    box_pad = int(font_size * 0.4)  # padding inside background box
    start_time = max(0.0, audio_duration - CTA_DURATION_SECS)
    safe_text = (
        CTA_OVERLAY_TEXT.replace("\\", "\\\\").replace("'", "\\'").replace(":", "\\:")
    )
    return (
        f"drawtext=text='{safe_text}':"
        f"fontsize={font_size}:"
        f"fontcolor=white:"
        f"x=(w-text_w)/2:"
        f"y=h*0.05:"
        f"box=1:"
        f"boxcolor=black@0.65:"
        f"boxborderw={box_pad}:"
        f"fix_bounds=1:"
        f"enable='gte(t,{start_time:.2f})'"
    )


def _image_durations(n: int, total: float) -> list[float]:
    """
    Weighted slide durations: images 1 & 2 each get 25% of total (hook visual
    stays on screen longer); remaining images share the rest equally.
    Falls back to equal distribution for n < 3.
    """
    if n >= 3:
        front = total * 0.25
        rest = (total - 2 * front) / (n - 2)
        return [front, front] + [rest] * (n - 2)
    return [total / n] * n


def _build_slideshow_filter(
    image_paths: list[Path], audio_duration: float
) -> tuple[str, list]:
    """
    Build ffmpeg filtergraph for image slideshow.
    Images 1 & 2 are front-loaded (25% each) to keep the hook visual on screen
    longer; remaining slides share the rest equally.
    Returns (filter_complex string, input_args list).
    """
    n = len(image_paths)
    durations = _image_durations(n, audio_duration)
    W, H = config.VIDEO_WIDTH, config.VIDEO_HEIGHT

    # Build input args: one -loop 1 -t <duration> -i <image> per image
    input_args = []
    for img_path, dur in zip(image_paths, durations):
        input_args.extend(
            ["-loop", "1", "-t", f"{dur:.3f}", "-i", str(img_path)]
        )

    # Scale each image to 1080x1920 (pad with black if needed), then concatenate
    scale_parts = []
    for i in range(n):
        scale_parts.append(
            f"[{i}:v]scale={W}:{H}:force_original_aspect_ratio=decrease,"
            f"pad={W}:{H}:(ow-iw)/2:(oh-ih)/2:color=black,"
            f"setsar=1,fps=24[v{i}]"
        )

    concat_inputs = "".join(f"[v{i}]" for i in range(n))
    concat_part = f"{concat_inputs}concat=n={n}:v=1:a=0[video_raw]"

    filter_complex = ";".join(scale_parts) + ";" + concat_part
    return filter_complex, input_args


def _convert_srt_to_ass(srt_path: Path, ass_path: Path) -> Path:
    """
    Convert a .srt subtitle file to a fully-styled .ass file.

    Sets PlayResX/PlayResY to the actual video pixel dimensions so every
    measurement (FontSize, MarginV, Outline) is in real pixels — no scaling
    surprises from the ASS default PlayResY=288 reference coordinate space.

    Subtitle style targets:
      - Bottom-center, 9% from bottom edge
      - ~60px font height (readable on mobile)
      - Semi-transparent dark box behind text for readability
      - Max ~2 lines visible (subtitle_generator caps cards at 7 words)
    """
    W = config.VIDEO_WIDTH  # 1080
    H = config.VIDEO_HEIGHT  # 1920

    font_size = int(H * 0.032)  # ~61px  — clear on mobile, ≤ 7% of height per line
    margin_v = int(H * 0.22)  # ~422px from bottom — above YouTube Shorts UI on phones
    margin_lr = int(W * 0.05)  # ~54px horizontal padding
    box_pad = int(font_size * 0.35)  # ~21px padding inside the background box

    # ASS color: &HAABBGGRR  (AA: 00=opaque, FF=transparent)
    primary_colour = "&H00FFFFFF"  # white, fully opaque
    back_colour = "&H80000000"  # black, 50% transparent (box background)
    outline_colour = "&H00000000"  # black, fully opaque (thin text outline)

    ass_header = (
        "[Script Info]\n"
        "ScriptType: v4.00+\n"
        f"PlayResX: {W}\n"
        f"PlayResY: {H}\n"
        "ScaledBorderAndShadow: yes\n"
        "WrapStyle: 1\n"  # smart word-wrap
        "\n"
        "[V4+ Styles]\n"
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, "
        "OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, "
        "ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, "
        "Alignment, MarginL, MarginR, MarginV, Encoding\n"
        f"Style: Default,"
        f"Arial,{font_size},"
        f"{primary_colour},"
        f"&H000000FF,"  # SecondaryColour (unused)
        f"{outline_colour},"
        f"{back_colour},"
        f"1,"  # Bold
        f"0,0,0,"  # Italic, Underline, StrikeOut
        f"100,100,1,0,"  # ScaleX, ScaleY, Spacing, Angle
        f"3,"  # BorderStyle=3: background box
        f"{box_pad},"  # Outline = box internal padding
        f"0,"  # Shadow
        f"2,"  # Alignment=2: bottom-center
        f"{margin_lr},{margin_lr},{margin_v},"
        f"1\n"  # Encoding
        "\n"
        "[Events]\n"
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
    )

    def _srt_ts_to_ass(ts: str) -> str:
        """HH:MM:SS,mmm  →  H:MM:SS.cc"""
        ts = ts.strip()
        h, m, rest = ts.split(":")
        s, ms = rest.split(",")
        return f"{int(h)}:{m}:{s}.{int(ms) // 10:02d}"

    srt_text = srt_path.read_text(encoding="utf-8").strip()
    blocks = re.split(r"\n\n+", srt_text)
    dialogues = []

    for block in blocks:
        lines = block.strip().splitlines()
        if len(lines) < 3 or " --> " not in lines[1]:
            continue
        start_raw, end_raw = lines[1].split(" --> ", 1)
        text = " ".join(lines[2:])
        # Escape ASS special characters
        text = text.replace("{", r"\{").replace("}", r"\}")
        dialogues.append(
            f"Dialogue: 0,{_srt_ts_to_ass(start_raw)},{_srt_ts_to_ass(end_raw)},"
            f"Default,,0,0,0,,{text}"
        )

    ass_path.write_text(
        ass_header + "\n".join(dialogues) + "\n",
        encoding="utf-8-sig",  # UTF-8 BOM — ensures libass reads correctly on Windows
    )
    logger.debug(
        f"[video_assembler] ASS subtitle written: {ass_path} ({len(dialogues)} lines)"
    )
    return ass_path


def assemble_video(
    image_paths: list[Path],
    audio_path: Path,
    srt_path: Path,
    out_path: Path,
    audio_duration: float,
    ass_path: Path | None = None,
    music_path: Path | None = None,
) -> Path:
    """
    Assemble a single video from images + audio + subtitles.

    Args:
        image_paths: list of 5 PNG images
        audio_path: WAV audio file
        srt_path: .srt subtitle file (used if ass_path is not provided)
        out_path: output .mp4 path
        audio_duration: duration of audio in seconds
        ass_path: pre-built .ass file from captions.py (Whisper path); takes
                  priority over srt_path if provided

    Returns:
        Path to the assembled .mp4 file
    """
    if out_path.exists() and out_path.stat().st_size > 10240:
        logger.info(f"[video_assembler] Cache hit: {out_path.name}")
        return out_path

    out_path.parent.mkdir(parents=True, exist_ok=True)

    if not image_paths:
        raise ValueError("No images provided for video assembly")
    if audio_path is None or not audio_path.exists():
        raise FileNotFoundError(f"Audio file not found: {audio_path}")

    logger.info(
        f"[video_assembler] Assembling video: {len(image_paths)} images, "
        f"{audio_duration:.1f}s audio → {out_path.name}"
    )

    filter_complex, image_input_args = _build_slideshow_filter(
        image_paths, audio_duration
    )

    # We need to add subtitle burn-in as a second filter pass to avoid
    # complexities with subtitles in the main filtergraph (path escaping issues)
    # Strategy: assemble video without subs first, then burn subs in second pass

    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_nosub = Path(tmpdir) / "nosub.mp4"

        # Pass 1: assemble slideshow + audio (no subtitles yet)
        n = len(image_paths)
        cmd_pass1 = [
            "ffmpeg",
            "-y",
        ]
        cmd_pass1.extend(image_input_args)
        cmd_pass1.extend(
            [
                "-i",
                str(audio_path),
            ]
        )

        if music_path and music_path.exists():
            cmd_pass1.extend(["-i", str(music_path)])
            amix = (
                f";[{n}:a][{n + 1}:a]amix=inputs=2:duration=first:"
                f"dropout_transition=2:weights=1 0.15[amixed]"
            )
            full_filter = filter_complex + amix
            audio_map = "[amixed]"
            logger.debug(
                f"[video_assembler] Mixing background music: {music_path.name}"
            )
        else:
            full_filter = filter_complex
            audio_map = f"{n}:a"

        cmd_pass1.extend(
            [
                "-filter_complex",
                full_filter,
                "-map",
                "[video_raw]",
                "-map",
                audio_map,
                "-c:v",
                "libx264",
                "-preset",
                "fast",
                "-crf",
                "23",
                "-c:a",
                "aac",
                "-b:a",
                "128k",
                "-shortest",
                "-r",
                "24",
                "-pix_fmt",
                "yuv420p",
                str(tmp_nosub),
            ]
        )

        logger.debug(f"[video_assembler] Pass 1 cmd: {' '.join(cmd_pass1)}")

        result = subprocess.run(cmd_pass1, capture_output=True, timeout=300)
        if result.returncode != 0:
            stderr = result.stderr.decode("utf-8", errors="replace")
            raise RuntimeError(
                f"ffmpeg pass 1 failed (exit {result.returncode}):\n{stderr[-2000:]}"
            )

        # Pass 2: burn subtitles
        # Prefer pre-built .ass from captions.py (Whisper path); fall back to SRT conversion
        tmp_ass = None
        if ass_path and ass_path.exists() and ass_path.stat().st_size > 0:
            logger.debug(f"[video_assembler] Using pre-built ASS: {ass_path.name}")
            tmp_ass = ass_path
        elif srt_path and srt_path.exists() and srt_path.stat().st_size > 0:
            # Convert .srt → .ass with pixel-accurate styling (avoids PlayResY=288 scaling)
            tmp_ass_converted = Path(tmpdir) / "subtitles.ass"
            try:
                _convert_srt_to_ass(srt_path, tmp_ass_converted)
                tmp_ass = tmp_ass_converted
            except Exception as e:
                logger.warning(
                    f"[video_assembler] SRT→ASS conversion failed: {e} — skipping subs"
                )

        cta_filter = _build_cta_drawtext(audio_duration)

        if tmp_ass and tmp_ass.exists():
            # Escape path for ffmpeg filter (Windows: backslashes→slashes, colon→\:)
            ass_escaped = str(tmp_ass).replace("\\", "/").replace(":", "\\:")
            vf_filter = f"ass='{ass_escaped}',{cta_filter}"

            cmd_pass2 = [
                "ffmpeg",
                "-y",
                "-i",
                str(tmp_nosub),
                "-vf",
                vf_filter,
                "-c:v",
                "libx264",
                "-preset",
                "fast",
                "-crf",
                "23",
                "-c:a",
                "copy",
                str(out_path),
            ]

            logger.debug(f"[video_assembler] Pass 2 cmd: {' '.join(cmd_pass2)}")

            result2 = subprocess.run(cmd_pass2, capture_output=True, timeout=300)
            if result2.returncode != 0:
                stderr2 = result2.stderr.decode("utf-8", errors="replace")
                logger.warning(
                    f"[video_assembler] Subtitle burn failed — falling back to CTA-only pass.\n"
                    f"Error: {stderr2[-500:]}"
                )
                # Fallback: burn CTA overlay only (no subtitles)
                cmd_cta_only = [
                    "ffmpeg",
                    "-y",
                    "-i",
                    str(tmp_nosub),
                    "-vf",
                    cta_filter,
                    "-c:v",
                    "libx264",
                    "-preset",
                    "fast",
                    "-crf",
                    "23",
                    "-c:a",
                    "copy",
                    str(out_path),
                ]
                result_cta = subprocess.run(
                    cmd_cta_only, capture_output=True, timeout=300
                )
                if result_cta.returncode != 0:
                    shutil.copy2(tmp_nosub, out_path)
        else:
            # No subtitles — burn CTA overlay only
            cmd_pass2 = [
                "ffmpeg",
                "-y",
                "-i",
                str(tmp_nosub),
                "-vf",
                cta_filter,
                "-c:v",
                "libx264",
                "-preset",
                "fast",
                "-crf",
                "23",
                "-c:a",
                "copy",
                str(out_path),
            ]

            logger.debug(
                f"[video_assembler] Pass 2 (CTA only) cmd: {' '.join(cmd_pass2)}"
            )

            result2 = subprocess.run(cmd_pass2, capture_output=True, timeout=300)
            if result2.returncode != 0:
                stderr2 = result2.stderr.decode("utf-8", errors="replace")
                logger.warning(
                    f"[video_assembler] CTA overlay failed — copying video without overlay.\n"
                    f"Error: {stderr2[-500:]}"
                )
                shutil.copy2(tmp_nosub, out_path)

    final_duration = get_audio_duration(out_path)
    logger.info(
        f"[video_assembler] Video assembled: {out_path.name} "
        f"({final_duration:.1f}s, {out_path.stat().st_size // 1024}KB)"
    )
    return out_path


def assemble_all_videos(
    scripts: list[dict],
    all_image_paths: list[list[Path]],
    audio_paths: list[Path],
    srt_paths: list[Path],
    audio_durations: list[float],
    slug: str,
    ass_paths: list[Path | None] | None = None,
    music_path: Path | None = None,
) -> list[Path]:
    """
    Assemble videos for all events. Returns list of output .mp4 paths.
    Final outputs are also copied to the top-level output/<slug>_<idx>.mp4.
    ass_paths: optional list of pre-built .ass files (from captions.py Whisper path).
    """
    video_dir = config.OUTPUT_DIR / slug / "video"
    video_dir.mkdir(parents=True, exist_ok=True)

    # Normalise ass_paths to a list aligned with scripts
    _ass_paths = ass_paths if ass_paths else [None] * len(scripts)

    output_paths = []

    for i, (script, images, audio, srt, duration, ass) in enumerate(
        zip(
            scripts,
            all_image_paths,
            audio_paths,
            srt_paths,
            audio_durations,
            _ass_paths,
        )
    ):
        idx = script.get("event_index", i)
        out_path = video_dir / f"{idx}.mp4"

        logger.info(f"[video_assembler] Assembling video {idx + 1}/{len(scripts)}...")

        try:
            final_path = assemble_video(
                image_paths=images,
                audio_path=audio,
                srt_path=srt,
                out_path=out_path,
                audio_duration=duration,
                ass_path=ass,
                music_path=music_path,
            )
            output_paths.append(final_path)

            # Also copy to top-level output dir with descriptive name
            event_title = script.get("title", f"event_{idx}")
            safe_title = "".join(
                c if c.isalnum() or c in "-_ " else "" for c in event_title
            )
            safe_title = safe_title.replace(" ", "_")[:50]
            final_output = config.OUTPUT_DIR / f"{slug}_{idx}_{safe_title}.mp4"

            shutil.copy2(final_path, final_output)
            logger.info(f"[video_assembler] Final output: {final_output}")

        except Exception as e:
            logger.error(f"[video_assembler] Failed to assemble video {idx}: {e}")
            raise

    return output_paths
