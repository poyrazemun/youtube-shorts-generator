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
  Step 3: Image Generation  (A1111 / ComfyUI / Replicate)
  Step 4: TTS Generation    (Piper / Coqui / gTTS)
  Step 5: Video Assembly    (ffmpeg)
  Step 6: YouTube Upload    (YouTube Data API v3)

Each step saves output to disk before the next step runs.
If a step fails, re-running the command resumes from that step.
"""

import argparse
import re
import sys
from pathlib import Path


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


def run_pipeline(topic: str, keyword: str, count: int, skip_upload: bool = False, verbose: bool = False):
    """
    Execute the full pipeline end-to-end.

    Args:
        topic: Channel topic string
        keyword: Keyword to focus event discovery on
        count: Number of videos to generate
        skip_upload: If True, skip YouTube upload (useful for testing)
        verbose: If True, set console log level to DEBUG
    """
    import config
    from pipeline.log import get_logger, set_verbose
    from pipeline.state import PipelineState

    set_verbose(verbose)
    logger = get_logger("orchestrator")

    _print_banner()
    logger.info(f"Starting pipeline: topic='{topic}', keyword='{keyword}', count={count}")

    if not config.ANTHROPIC_API_KEY:
        logger.error("ANTHROPIC_API_KEY is not set. Add it to your .env file.")
        sys.exit(1)

    slug = _make_slug(topic, keyword)
    logger.info(f"Pipeline slug: {slug}")
    logger.info(f"Output directory: {config.OUTPUT_DIR / slug}")

    state = PipelineState(slug)

    # ── STEP 1: Event Discovery ────────────────────────────────────────────────
    _print_step(1, "EVENT DISCOVERY")
    from pipeline.event_discovery import discover_events
    try:
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
    from pipeline.script_generator import generate_scripts
    try:
        scripts = generate_scripts(events=events, slug=slug)
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

    # ── STEP 3: Image Generation ───────────────────────────────────────────────
    _print_step(3, "IMAGE GENERATION")
    from pipeline.image_generator import generate_images
    try:
        all_image_paths = generate_images(scripts=scripts, slug=slug)
        total_images = sum(len(imgs) for imgs in all_image_paths)
        logger.info(f"Step 3 complete: {total_images} images generated across {len(scripts)} events.")
    except Exception as e:
        logger.error(f"Step 3 FAILED: {e}")
        logger.error("Ensure A1111, ComfyUI, or REPLICATE_API_TOKEN is configured.")
        sys.exit(1)

    # ── STEP 4: TTS Generation ─────────────────────────────────────────────────
    _print_step(4, "VOICE GENERATION (TTS)")
    from pipeline.tts_generator import generate_audio, get_audio_duration
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

    except Exception as e:
        logger.error(f"Step 4 FAILED: {e}")
        sys.exit(1)

    # ── Background music selection ─────────────────────────────────────────────
    from pipeline.music import select_track
    music_path = select_track()
    if music_path:
        logger.info(f"Background music: {music_path.name}")
    else:
        logger.info("No background music (add MP3s to assets/music/ to enable).")

    # ── STEP 5a: Caption Generation ────────────────────────────────────────────
    _print_step(5, "VIDEO ASSEMBLY (+ Captions)")
    from pipeline.captions import generate_captions
    subtitle_dir = config.OUTPUT_DIR / slug / "subtitles"
    ass_paths: list = []
    srt_paths: list = []
    for script, audio_path, duration in zip(scripts, audio_paths, audio_durations):
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
    from pipeline.video_assembler import assemble_all_videos
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
        )
        logger.info(f"Step 5 complete: {len(video_paths)} videos assembled.")
        for i, (vp, s) in enumerate(zip(video_paths, scripts)):
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
    from pipeline.thumbnail import generate_thumbnail
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

    from pipeline.youtube_uploader import upload_all_videos
    try:
        upload_results = upload_all_videos(
            video_paths=video_paths,
            scripts=scripts,
            slug=slug,
            thumbnail_paths=thumbnail_paths,
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
        "--verbose",
        action="store_true",
        help="Enable DEBUG-level console logging",
    )

    args = parser.parse_args()

    # ── Validate: manual mode requires --topic and --keyword ──────────────────
    is_auto_mode = args.auto or args.refresh_topics or args.analytics
    if not is_auto_mode:
        if not args.topic or not args.keyword:
            parser.error(
                "--topic and --keyword are required in manual mode. "
                "Use --auto to run from the topic queue, or --refresh-topics to generate one."
            )
        if args.count < 1 or args.count > 20:
            parser.error("--count must be between 1 and 20")

    # ── Logging init ──────────────────────────────────────────────────────────
    from pipeline.log import get_logger, set_verbose
    set_verbose(args.verbose)
    logger = get_logger("orchestrator")

    # ── Dispatch ──────────────────────────────────────────────────────────────

    if args.analytics:
        from pipeline import analytics as analytics_mod
        result = analytics_mod.fetch_analytics()
        hints = analytics_mod.get_performance_hints()
        print("\n" + "═" * 60)
        print("   YOUTUBE ANALYTICS")
        print("═" * 60)
        print(f"  Videos analyzed: {result['total_videos']}")
        if hints:
            print(f"\n  {hints}")
        else:
            print("  No videos uploaded yet.")
        print()
        sys.exit(0)

    elif args.refresh_topics:
        from pipeline import analytics as analytics_mod
        from pipeline import topic_discovery
        hints = analytics_mod.get_performance_hints()
        if hints:
            logger.info("[auto] Refreshing topic queue with analytics hints.")
        else:
            logger.info("[auto] Refreshing topic queue (no analytics data yet).")
        added = topic_discovery.refresh_queue(performance_hints=hints)
        queue = topic_discovery.load_queue()
        pending = sum(1 for t in queue["topics"] if t["status"] == "pending")
        print(f"\n  Topic queue refreshed: {added} new entries added. {pending} pending total.\n")
        sys.exit(0)

    elif args.auto:
        from pipeline import topic_discovery
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
                count=entry["count"],
                skip_upload=args.no_upload,
                verbose=args.verbose,
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
        )


if __name__ == "__main__":
    main()
