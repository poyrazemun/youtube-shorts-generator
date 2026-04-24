"""
Unreal History Bot — CLI Orchestrator
======================================
Automated pipeline for generating and uploading YouTube Shorts about
strange and unbelievable historical events.

Usage:
  python orchestrator.py --topic "Strange Moments in History" --keyword "war" --count 5

Pipeline:
  Step 1: Event Discovery   (Claude API)
  Step 2: Script Generation (Claude API)
  Step 3: Image Generation  (Hugging Face / Replicate / PIL fallback)
  Step 4: TTS Generation    (Piper / Coqui / gTTS)
  Step 5: Video Assembly    (ffmpeg)
  Step 6: YouTube Upload    (YouTube Data API v3)

Each step saves output to disk before the next step runs.
If a step fails, re-running the command resumes from that step.
"""

import argparse
import json
import os
import re
import sys

import config
from pipeline import analytics as analytics_mod
from pipeline import topic_discovery
from pipeline.captions import generate_captions
from pipeline.event_discovery import discover_events
from pipeline.image_generator import generate_images
from pipeline.log import get_logger, set_verbose
from pipeline.music import select_track
from pipeline.presets import DEFAULT_PRESET, list_presets
from pipeline.scene_planner import plan_all as plan_all_scenes
from pipeline.script_generator import generate_scripts
from pipeline.state import PipelineState
from pipeline.thumbnail import generate_thumbnail
from pipeline.tts_generator import generate_audio, get_audio_duration
from pipeline.video_assembler import assemble_all_videos
from pipeline.youtube_uploader import upload_all_videos


# Hardcoded fixture used by --dry-run to skip Claude calls (steps 1 + 2) and
# exercise the rest of the pipeline end-to-end with zero API spend.
_DRY_RUN_EVENT = {
    "year": "1518",
    "event": "The Dancing Plague of Strasbourg — 400 people danced uncontrollably for two months, and dozens died of exhaustion.",
    "location": "Strasbourg",
    "visual_theme": "medieval townspeople dancing in a cobblestone square",
}
_DRY_RUN_SCRIPT_TEXT = (
    "In 1518, 400 people danced non-stop for two months — and couldn't stop. "
    "It started with one woman in Strasbourg. Within a week, hundreds joined her. "
    "But here's the strangest part. "
    "Authorities hired musicians thinking music would help — it only made it worse. "
    "Dozens collapsed from exhaustion, and no one ever figured out why."
)


def _build_dry_run_payload() -> tuple[list[dict], list[dict]]:
    """Return fixture (events, scripts) for --dry-run; no Claude calls."""
    event = dict(_DRY_RUN_EVENT)
    words = _DRY_RUN_SCRIPT_TEXT.split()
    script = {
        "title": "The Dancing Plague of 1518",
        "description": "Dry-run fixture script. Not for upload.",
        "hashtags": ["history", "shorts", "dryrun"],
        "youtube_tags": ["history", "shorts", "dryrun", "test"],
        "hook_type": "SPECIFIC_NUMBER",
        "hook": "In 1518, 400 people danced non-stop for two months — and couldn't stop.",
        "context": "It started with one woman in Strasbourg. Within a week, hundreds joined her.",
        "rehook": "But here's the strangest part.",
        "twist": "Authorities hired musicians thinking music would help — it only made it worse.",
        "ending_fact": "Dozens collapsed from exhaustion, and no one ever figured out why.",
        "full_script": _DRY_RUN_SCRIPT_TEXT,
        "pin_comment": "Dry-run fixture — not posted.",
        "word_count": len(words),
        "estimated_seconds": 25,
        "event_index": 0,
        "source_event": event,
    }
    return [event], [script]


def _force_free_backends() -> None:
    """Strip paid-backend credentials so image generation falls back to PIL."""
    os.environ.pop("HUGGINGFACE_API_TOKEN", None)
    config.REPLICATE_API_TOKEN = ""


def _make_slug(topic: str, keyword: str) -> str:
    """Generate a filesystem-safe slug from topic and keyword."""
    combined = f"{topic}_{keyword}".lower()
    slug = re.sub(r"[^a-z0-9_-]", "_", combined)
    slug = re.sub(r"_+", "_", slug).strip("_")
    return slug[:60]


def _print_banner():
    print("\n" + "═" * 60)
    print("   UNREAL HISTORY BOT — YouTube Shorts Generator")
    print("═" * 60 + "\n")


def _print_step(n: int, name: str):
    print(f"\n{'─' * 60}")
    print(f"  STEP {n}: {name}")
    print(f"{'─' * 60}")


def _print_results(upload_results: list):
    print("\n" + "═" * 60)
    print("   UPLOAD RESULTS")
    print("═" * 60)
    for r in upload_results:
        print(f"  [{r.get('event_index', '?')}] {r.get('title', 'Untitled')}")
        print(f"       {r.get('url', 'no url')} ({r.get('privacy', 'unknown')})")
    print()


def run_pipeline(topic: str, keyword: str, count: int, skip_upload: bool = False, verbose: bool = False, no_edit: bool = False, preset: str | None = None, dry_run: bool = False):
    """
    Execute the full pipeline end-to-end.

    Args:
        topic: Channel topic string
        keyword: Keyword to focus event discovery on
        count: Number of videos to generate
        skip_upload: If True, skip YouTube upload (useful for testing)
        verbose: If True, set console log level to DEBUG
        no_edit: If True, skip prompt editing pause (automation mode)
        dry_run: If True, skip Claude (use fixture event + script), force PIL
                 image backend, and skip YouTube upload — zero API spend, to
                 validate pipeline wiring after structural changes.
    """
    set_verbose(verbose)
    logger = get_logger("orchestrator")

    _print_banner()
    if dry_run:
        logger.info("DRY RUN: skipping Claude calls, forcing PIL images, skipping upload.")
        skip_upload = True
        _force_free_backends()
    logger.info(f"Starting pipeline: topic='{topic}', keyword='{keyword}', count={count}")

    if not dry_run and not config.ANTHROPIC_API_KEY:
        logger.error("ANTHROPIC_API_KEY is not set. Add it to your .env file.")
        sys.exit(1)

    slug = _make_slug(topic, keyword)
    logger.info(f"Pipeline slug: {slug}")
    logger.info(f"Output directory: {config.OUTPUT_DIR / slug}")

    state = PipelineState(slug)

    # ── STEP 1: Event Discovery ────────────────────────────────────────────────
    _print_step(1, "EVENT DISCOVERY")
    try:
        if dry_run:
            events, _ = _build_dry_run_payload()
            events_path = config.OUTPUT_DIR / slug / "events.json"
            events_path.parent.mkdir(parents=True, exist_ok=True)
            events_path.write_text(json.dumps(events, indent=2, ensure_ascii=False), encoding="utf-8")
            logger.info("Step 1: using dry-run fixture event.")
        else:
            events = discover_events(topic=topic, keyword=keyword, count=count, slug=slug)
        logger.info(f"Step 1 complete: {len(events)} events discovered.")
        for i, e in enumerate(events):
            logger.info(f"  [{i}] {e.get('year', '?')} — {e.get('event', '')[:80]}")
        state.complete(0, "events", [str(config.OUTPUT_DIR / slug / "events.json")])
    except Exception as e:
        logger.error(f"Step 1 FAILED: {e}")
        state.fail(0, "events", str(e))
        sys.exit(1)

    # ── STEP 2: Script Generation ──────────────────────────────────────────────
    _print_step(2, "SCRIPT GENERATION")
    try:
        if dry_run:
            _, scripts = _build_dry_run_payload()
            scripts_path = config.OUTPUT_DIR / slug / "scripts.json"
            scripts_path.write_text(json.dumps(scripts, indent=2, ensure_ascii=False), encoding="utf-8")
            logger.info("Step 2: using dry-run fixture script.")
        else:
            scripts = generate_scripts(events=events, slug=slug, no_edit=no_edit)
        logger.info(f"Step 2 complete: {len(scripts)} scripts generated.")
        for s in scripts:
            logger.info(
                f"  [{s.get('event_index', '?')}] '{s.get('title', 'untitled')}' "
                f"— {s.get('word_count', '?')} words, ~{s.get('estimated_seconds', '?')}s"
            )
        for s in scripts:
            state.complete(s.get("event_index", 0), "scripts")
    except Exception as e:
        logger.error(f"Step 2 FAILED: {e}")
        state.fail(0, "scripts", str(e))
        sys.exit(1)

    # ── STEP 2.5: Scene Planning (pre-render — uses estimated durations) ───────
    active_preset = preset or config.DEFAULT_SCENE_PRESET
    logger.info(f"[scene_planner] Using preset: {active_preset}")
    try:
        estimated_durations = [
            float(s.get("estimated_seconds") or 25.0) for s in scripts
        ]
        scene_plans = plan_all_scenes(
            scripts=scripts,
            audio_durations=estimated_durations,
            slug=slug,
            preset_name=active_preset,
        )
    except Exception as e:
        logger.warning(f"[scene_planner] Planning failed ({e}) — falling back to legacy render path")
        scene_plans = []

    # ── STEP 3: Image Generation ───────────────────────────────────────────────
    _print_step(3, "IMAGE GENERATION")
    try:
        all_image_paths = generate_images(
            scripts=scripts, slug=slug, scene_plans=scene_plans or None
        )
        total_images = sum(len(imgs) for imgs in all_image_paths)
        logger.info(f"Step 3 complete: {total_images} images generated across {len(scripts)} events.")
        for s, imgs in zip(scripts, all_image_paths, strict=True):
            state.complete(s.get("event_index", 0), "images", [str(p) for p in imgs])
    except Exception as e:
        logger.error(f"Step 3 FAILED: {e}")
        state.fail(0, "images", str(e))
        logger.error("Ensure HUGGINGFACE_API_TOKEN or REPLICATE_API_TOKEN is configured.")
        sys.exit(1)

    # ── STEP 4: TTS Generation ─────────────────────────────────────────────────
    _print_step(4, "VOICE GENERATION (TTS)")
    try:
        audio_paths = generate_audio(scripts=scripts, slug=slug)
        logger.info(f"Step 4 complete: {len(audio_paths)} audio files generated.")

        # Compute durations for subtitle generation and video assembly
        audio_durations = []
        for audio_path in audio_paths:
            if audio_path and audio_path.exists():
                d = get_audio_duration(audio_path)
                audio_durations.append(d)
                logger.info(f"  Audio duration: {d:.1f}s")
            else:
                audio_durations.append(25.0)
                logger.warning("  Audio path missing — defaulting to 25s duration")

        for s, ap in zip(scripts, audio_paths, strict=True):
            if ap and ap.exists():
                state.complete(s.get("event_index", 0), "audio", [str(ap)])

    except Exception as e:
        logger.error(f"Step 4 FAILED: {e}")
        state.fail(0, "audio", str(e))
        sys.exit(1)

    # ── Re-plan scenes with actual audio durations + resolved image paths ─────
    if scene_plans:
        try:
            scene_plans = plan_all_scenes(
                scripts=scripts,
                audio_durations=audio_durations,
                slug=slug,
                preset_name=active_preset,
            )
            # Attach the resolved image paths so scene_plans/<idx>.json is a
            # fully self-describing artifact of what was rendered.
            for plan, imgs in zip(scene_plans, all_image_paths, strict=True):
                for scene, img in zip(plan.scenes, imgs, strict=False):
                    scene.image_path = str(img)
                plan.save(config.OUTPUT_DIR / slug / "scene_plans" / f"{plan.event_index}.json")
        except Exception as e:
            logger.warning(f"[scene_planner] Re-plan after TTS failed ({e}) — continuing")

    # ── Background music selection ─────────────────────────────────────────────
    music_path = select_track()
    if music_path:
        logger.info(f"Background music: {music_path.name}")
    else:
        logger.info("No background music (add MP3s to assets/music/ to enable).")

    # ── STEP 5a: Caption Generation ────────────────────────────────────────────
    _print_step(5, "VIDEO ASSEMBLY (+ Captions)")
    subtitle_dir = config.OUTPUT_DIR / slug / "subtitles"
    ass_paths: list = []
    srt_paths: list = []
    for script, audio_path, duration in zip(scripts, audio_paths, audio_durations, strict=True):
        idx = script.get("event_index", 0)
        try:
            ass_p, srt_p = generate_captions(audio_path, script, subtitle_dir, duration)
            ass_paths.append(ass_p)
            srt_paths.append(srt_p)
            state.complete(idx, "captions")
        except Exception as e:
            logger.warning(f"Caption generation failed for event {idx}: {e} — continuing without subs")
            state.fail(idx, "captions", str(e))
            ass_paths.append(None)
            srt_paths.append(None)
    logger.info(f"Captions generated: {len(srt_paths)} caption files.")

    # ── STEP 5b: Video Assembly ────────────────────────────────────────────────
    try:
        video_paths = assemble_all_videos(
            scripts=scripts,
            all_image_paths=all_image_paths,
            audio_paths=audio_paths,
            srt_paths=srt_paths,
            audio_durations=audio_durations,
            slug=slug,
            ass_paths=ass_paths,
            music_path=music_path,
            scene_plans=scene_plans or None,
        )
        logger.info(f"Step 5 complete: {len(video_paths)} videos assembled.")
        for i, (vp, s) in enumerate(zip(video_paths, scripts, strict=True)):
            if vp and vp.exists():
                logger.info(f"  {vp} ({vp.stat().st_size // 1024}KB)")
                state.complete(s.get("event_index", i), "video", [str(vp)])
    except Exception as e:
        logger.error(f"Step 5 FAILED: {e}")
        state.fail(0, "video", str(e))
        sys.exit(1)

    # ── STEP 6: YouTube Upload ─────────────────────────────────────────────────
    if skip_upload:
        logger.info("Step 6 SKIPPED (--no-upload flag set).")
        print("\nVideos ready for manual upload:")
        for vp in video_paths:
            if vp:
                print(f"  {vp}")
        return

    _print_step(6, "YOUTUBE UPLOAD")

    # Generate thumbnails before upload
    thumbnail_dir = config.OUTPUT_DIR / slug / "thumbnails"
    thumbnail_paths: list = []
    for script in scripts:
        try:
            thumb = generate_thumbnail(script, thumbnail_dir)
            thumbnail_paths.append(thumb)
            if thumb:
                logger.info(f"  Thumbnail: {thumb.name}")
        except Exception as e:
            logger.warning(f"Thumbnail generation failed for event {script.get('event_index', '?')}: {e}")
            thumbnail_paths.append(None)

    try:
        upload_results = upload_all_videos(
            video_paths=video_paths,
            scripts=scripts,
            slug=slug,
            thumbnail_paths=thumbnail_paths,
            topic=topic,
            keyword=keyword,
        )
        logger.info(f"Step 6 complete: {len(upload_results)} videos uploaded.")
        for r in upload_results:
            state.complete(r.get("event_index", 0), "upload", [r.get("url", "")])
        _print_results(upload_results)
    except FileNotFoundError as e:
        logger.error(f"Step 6 FAILED — missing file: {e}")
        logger.error("Set up YouTube API credentials (see README.md).")
        logger.info("Videos are ready in output/ — upload manually or fix credentials and re-run.")
        sys.exit(1)
    except Exception as e:
        logger.error(f"Step 6 FAILED: {e}")
        logger.info("Videos are assembled in output/ — re-run to retry upload.")
        sys.exit(1)

    logger.info("Pipeline complete!")


def main():
    parser = argparse.ArgumentParser(
        description="Unreal History Bot — Generate and upload YouTube Shorts about strange historical events.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python orchestrator.py --topic "Strange Moments in History" --keyword "war" --count 5
  python orchestrator.py --topic "Unbelievable Events" --keyword "plague" --count 3 --no-upload
  python orchestrator.py --auto
  python orchestrator.py --refresh-topics
  python orchestrator.py --list-topics
  python orchestrator.py --analytics
        """,
    )

    # ── Execution mode (mutually exclusive) ───────────────────────────────────
    mode_group = parser.add_mutually_exclusive_group()
    mode_group.add_argument(
        "--auto",
        action="store_true",
        help="Automated mode: pick next topic from queue and run full pipeline",
    )
    mode_group.add_argument(
        "--refresh-topics",
        action="store_true",
        dest="refresh_topics",
        help="Regenerate the topic queue using Claude (uses analytics hints if available)",
    )
    mode_group.add_argument(
        "--analytics",
        action="store_true",
        help="Fetch YouTube analytics and print performance summary, then exit",
    )
    mode_group.add_argument(
        "--list-topics",
        action="store_true",
        dest="list_topics",
        help="Print the current topic queue and exit",
    )
    mode_group.add_argument(
        "--clear-topics",
        action="store_true",
        dest="clear_topics",
        help="Wipe the entire topic queue and immediately generate a fresh one",
    )
    mode_group.add_argument(
        "--delete-topic",
        metavar="ID",
        default=None,
        dest="delete_topic",
        help="Remove a single topic from the queue by its ID",
    )
    mode_group.add_argument(
        "--pick",
        metavar="ID",
        default=None,
        help="Run the pipeline on a specific topic by its ID (shown in --list-topics)",
    )

    # ── Manual mode args (required when not using --auto/--refresh-topics/--analytics) ──
    parser.add_argument(
        "--topic",
        default=None,
        help="Topic for the YouTube channel / video batch (e.g. 'Strange Moments in History')",
    )
    parser.add_argument(
        "--keyword",
        default=None,
        help="Keyword to focus event discovery (e.g. 'war', 'plague', 'invention')",
    )
    parser.add_argument(
        "--count",
        type=int,
        default=1,
        help="Number of videos to generate (default: 1)",
    )
    parser.add_argument(
        "--no-upload",
        action="store_true",
        help="Skip YouTube upload — just generate and save videos locally",
    )
    parser.add_argument(
        "--no-edit",
        action="store_true",
        dest="no_edit",
        help="Skip prompt editing pause — send prompts to Claude without stopping (automation mode)",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Enable DEBUG-level console logging",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        dest="dry_run",
        help=(
            "Validate pipeline wiring end-to-end with zero API spend: "
            "skips Claude (uses a fixture event + script), forces PIL image backend, "
            "and skips YouTube upload. Topic/keyword default to a fixture value."
        ),
    )
    parser.add_argument(
        "--preset",
        default=None,
        choices=list_presets(),
        help=f"Scene preset (default: {DEFAULT_PRESET}). "
             f"Controls motion, overlays, and image-prompt style per scene role.",
    )

    args = parser.parse_args()

    # ── Validate: manual mode requires --topic and --keyword ──────────────────
    is_auto_mode = (
        args.auto or args.refresh_topics or args.analytics
        or args.list_topics or args.pick
        or args.clear_topics or args.delete_topic
    )
    if not is_auto_mode:
        if args.dry_run:
            # Dry-run doesn't need real topic/keyword; fill fixture defaults.
            args.topic = args.topic or "Dry Run"
            args.keyword = args.keyword or "dryrun"
        elif not args.topic or not args.keyword:
            parser.error(
                "--topic and --keyword are required in manual mode. "
                "Use --auto to run from the topic queue, or --refresh-topics to generate one."
            )
        if args.count < 1 or args.count > 20:
            parser.error("--count must be between 1 and 20")

    # ── Logging init ──────────────────────────────────────────────────────────
    set_verbose(args.verbose)
    logger = get_logger("orchestrator")

    # ── Dispatch ──────────────────────────────────────────────────────────────

    if args.analytics:
        result = analytics_mod.fetch_analytics()
        videos = result.get("videos", [])
        print("\n" + "═" * 60)
        print("   YOUTUBE ANALYTICS")
        print("═" * 60)
        print(f"  Total videos on channel: {result['total_videos']}")
        if videos:
            print()
            print("  VIDEOS (sorted by views):")
            print("  " + "─" * 56)
            for v in sorted(videos, key=lambda x: x.get("view_count", 0), reverse=True):
                views = v.get("view_count", 0)
                likes = v.get("like_count", 0)
                title = v.get("title") or v.get("topic") or v.get("video_id")
                kw = v.get("keyword", "")
                kw_tag = f"  [{kw}]" if kw and kw != "unknown" else ""
                print(f"  {views:>6} views  {likes:>4} likes  {title}{kw_tag}")
            top_kw = result.get("top_keywords", [])
            if top_kw:
                print()
                print("  TOP KEYWORDS:")
                for k in top_kw:
                    print(f"    {k['keyword']:20s}  {k['avg_views']:,} avg views  ({k['video_count']} video{'s' if k['video_count'] != 1 else ''})")
        else:
            print("  No videos found on channel.")
        print()
        sys.exit(0)

    elif args.refresh_topics:
        hints = analytics_mod.get_performance_hints()
        if hints:
            logger.info("[auto] Refreshing topic queue with analytics hints.")
        else:
            logger.info("[auto] Refreshing topic queue (no analytics data yet).")
        added = topic_discovery.refresh_queue(performance_hints=hints)
        queue = topic_discovery.load_queue()
        pending = sum(1 for t in queue["topics"] if t["status"] == "pending")
        print(f"\n  Topic queue replaced: {added} new topics. {pending} pending total.\n")
        print("  " + "─" * 56)
        for i, t in enumerate(queue["topics"], 1):
            status_tag = f"[{t['status']}]" if t["status"] != "pending" else ""
            print(f"  {i:>2}. {t['topic']} ({t['keyword']}) {status_tag}".rstrip())
        print()
        sys.exit(0)

    elif args.list_topics:
        queue = topic_discovery.load_queue()
        topics = queue.get("topics", [])
        pending   = [t for t in topics if t["status"] == "pending"]
        done      = [t for t in topics if t["status"] == "done"]
        failed    = [t for t in topics if t["status"] == "failed"]
        in_prog   = [t for t in topics if t["status"] == "in_progress"]
        print("\n" + "═" * 60)
        print("   TOPIC QUEUE")
        print("═" * 60)
        print(f"  Generated : {queue.get('generated_at', 'unknown')}")
        print(f"  Pending   : {len(pending)}  |  In-progress: {len(in_prog)}  |  Done: {len(done)}  |  Failed: {len(failed)}")
        if pending or in_prog:
            print()
            print("  PENDING / IN-PROGRESS:")
            print("  " + "─" * 56)
            for i, t in enumerate(pending + in_prog, 1):
                status_tag = "[in_progress]" if t["status"] == "in_progress" else ""
                score = t.get("virality_score", "?")
                print(f"  {i:>2}. [id:{t['id']}] [{score}/10] {t['topic']} ({t['keyword']}) {status_tag}".rstrip())
        if done:
            print()
            print("  DONE:")
            print("  " + "─" * 56)
            for t in done:
                print(f"      ✓ [id:{t['id']}] {t['topic']} ({t['keyword']})")
        if failed:
            print()
            print("  FAILED:")
            print("  " + "─" * 56)
            for t in failed:
                print(f"      ✗ [id:{t['id']}] {t['topic']} ({t['keyword']})")
        print()
        sys.exit(0)

    elif args.clear_topics:
        print("\n  This will wipe the entire topic queue and generate a fresh one.")
        confirm = input("  Are you sure? (y/n): ").strip().lower()
        if confirm != "y":
            print("  Aborted.")
            sys.exit(0)
        topic_discovery.save_queue({"generated_at": None, "topics": []})
        logger.info("[clear-topics] Queue wiped.")
        print("  Queue cleared. Generating fresh topics...\n")
        hints = analytics_mod.get_performance_hints()
        added = topic_discovery.refresh_queue(performance_hints=hints)
        queue = topic_discovery.load_queue()
        pending = sum(1 for t in queue["topics"] if t["status"] == "pending")
        print(f"  Done: {added} new topics generated. {pending} pending total.\n")
        print("  " + "─" * 56)
        for i, t in enumerate(queue["topics"], 1):
            score = t.get("virality_score", "?")
            print(f"  {i:>2}. [id:{t['id']}] [{score}/10] {t['topic']} ({t['keyword']})")
        print()
        sys.exit(0)

    elif args.delete_topic:
        queue = topic_discovery.load_queue()
        match = next((t for t in queue["topics"] if t["id"] == args.delete_topic), None)
        if match is None:
            logger.error(
                f"[delete-topic] ID '{args.delete_topic}' not found. "
                "Run --list-topics to see valid IDs."
            )
            sys.exit(1)
        print(f"\n  Topic to delete: [{match.get('virality_score', '?')}/10] {match['topic']} ({match['keyword']}) [status: {match['status']}]")
        confirm = input("  Are you sure? (y/n): ").strip().lower()
        if confirm != "y":
            print("  Aborted.")
            sys.exit(0)
        queue["topics"] = [t for t in queue["topics"] if t["id"] != args.delete_topic]
        topic_discovery.save_queue(queue)
        print(f"  Deleted topic '{args.delete_topic}'.")
        print()
        sys.exit(0)

    elif args.pick:
        entry = topic_discovery.pick_topic_by_id(args.pick)
        if entry is None:
            logger.error(
                f"[pick] Topic ID '{args.pick}' not found in queue. "
                "Run --list-topics to see valid IDs."
            )
            sys.exit(1)

        slug = _make_slug(entry["topic"], entry["keyword"])
        logger.info(
            f"[pick] Running: '{entry['topic']}' / '{entry['keyword']}' "
            f"(id={entry['id']}, score={entry.get('virality_score', '?')})"
        )
        try:
            run_pipeline(
                topic=entry["topic"],
                keyword=entry["keyword"],
                count=1,
                skip_upload=args.no_upload,
                verbose=args.verbose,
                no_edit=args.no_edit,
                preset=args.preset,
            )
            topic_discovery.mark_topic_done(entry["id"], slug)
            logger.info(f"[pick] Topic '{entry['keyword']}' complete.")
        except SystemExit as exc:
            topic_discovery.mark_topic_failed(
                entry["id"], f"pipeline sys.exit({exc.code})"
            )
            logger.error(f"[pick] Topic '{entry['keyword']}' failed — marked in queue.")
            raise

    elif args.auto:
        entry = topic_discovery.pick_next_topic()
        if entry is None:
            logger.warning(
                "Topic queue is empty or exhausted. "
                "Run: python orchestrator.py --refresh-topics"
            )
            sys.exit(0)

        slug = _make_slug(entry["topic"], entry["keyword"])
        logger.info(
            f"[auto] Running: '{entry['topic']}' / '{entry['keyword']}' "
            f"(count={entry['count']}, id={entry['id']})"
        )
        try:
            run_pipeline(
                topic=entry["topic"],
                keyword=entry["keyword"],
                count=1,
                skip_upload=args.no_upload,
                verbose=args.verbose,
                no_edit=args.no_edit,
                preset=args.preset,
            )
            topic_discovery.mark_topic_done(entry["id"], slug)
            logger.info(f"[auto] Topic '{entry['keyword']}' complete.")
        except SystemExit as exc:
            topic_discovery.mark_topic_failed(
                entry["id"], f"pipeline sys.exit({exc.code})"
            )
            logger.error(f"[auto] Topic '{entry['keyword']}' failed — marked in queue.")
            raise  # re-raise so scheduler sees non-zero exit code

    else:
        # Manual mode — existing behaviour unchanged
        run_pipeline(
            topic=args.topic,
            keyword=args.keyword,
            count=args.count,
            skip_upload=args.no_upload,
            verbose=args.verbose,
            no_edit=args.no_edit,
            preset=args.preset,
            dry_run=args.dry_run,
        )


if __name__ == "__main__":
    main()
