"""
T3-B — YouTube Analytics
Reads all output/<slug>/uploads.json files, fetches video statistics from the
YouTube Data API, and saves a summary to output/analytics.json.
get_performance_hints() returns a plain-English summary for use in Claude prompts.
Falls back gracefully (returns "") if anything fails.
"""

import json
import logging
import os
from collections import defaultdict
from datetime import datetime, timezone, timedelta

import config
from pipeline import topic_discovery
from pipeline.retry import with_retry

logger = logging.getLogger(__name__)

_STATS_BATCH_SIZE = 50  # YouTube API max IDs per videos.list call
_HINTS_MAX_AGE_HOURS = 24


# ── Auth ──────────────────────────────────────────────────────────────────────


def _get_authenticated_service():
    """Build authenticated YouTube API service (same pattern as youtube_uploader)."""
    try:
        from google.oauth2.credentials import Credentials
        from google.auth.transport.requests import Request
        from google_auth_oauthlib.flow import InstalledAppFlow
        from googleapiclient.discovery import build
    except ImportError:
        raise RuntimeError(
            "Google API packages not installed. Run:\n"
            "  pip install google-api-python-client google-auth-oauthlib google-auth-httplib2"
        )

    creds = None
    creds_path = config.YOUTUBE_CREDENTIALS_FILE
    secrets_path = config.YOUTUBE_CLIENT_SECRETS_FILE

    if os.path.exists(creds_path):
        try:
            creds = Credentials.from_authorized_user_file(
                creds_path, config.YOUTUBE_SCOPES
            )
        except Exception as e:
            logger.warning(
                f"[analytics] Could not load credentials: {e} — re-authorizing."
            )
            creds = None

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            try:
                creds.refresh(Request())
            except Exception as e:
                logger.warning(
                    f"[analytics] Token refresh failed: {e} — re-authorizing."
                )
                creds = None

        if not creds:
            if not os.path.exists(secrets_path):
                raise FileNotFoundError(
                    f"YouTube client secrets not found: {secrets_path}"
                )
            flow = InstalledAppFlow.from_client_secrets_file(
                secrets_path, config.YOUTUBE_SCOPES
            )
            creds = flow.run_local_server(port=0, open_browser=True)

        with open(creds_path, "w") as f:
            f.write(creds.to_json())
        try:
            os.chmod(creds_path, 0o600)
        except Exception:
            pass  # Windows does not support chmod — harmless

    return build("youtube", "v3", credentials=creds)


# ── Helpers ───────────────────────────────────────────────────────────────────


def _utcnow() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _collect_uploaded_video_ids() -> list[dict]:
    """
    Walk all output/<slug>/uploads.json files.
    Returns list of {video_id, title, slug, keyword, topic}.
    """
    # Build slug → {keyword, topic} map from the topic queue
    slug_to_meta: dict[str, dict] = {}
    try:
        queue = topic_discovery.load_queue()
        for entry in queue.get("topics", []):
            if entry.get("slug"):
                slug_to_meta[entry["slug"]] = {
                    "keyword": entry.get("keyword", "unknown"),
                    "topic": entry.get("topic", "unknown"),
                }
    except Exception as e:
        logger.debug(f"[analytics] Could not load topic queue for slug lookup: {e}")

    collected = []
    if not config.OUTPUT_DIR.exists():
        return collected

    for slug_dir in config.OUTPUT_DIR.iterdir():
        if not slug_dir.is_dir():
            continue
        uploads_file = slug_dir / "uploads.json"
        if not uploads_file.exists():
            continue
        try:
            with open(uploads_file, "r", encoding="utf-8") as f:
                uploads = json.load(f)
            for u in uploads:
                vid = u.get("video_id") or u.get("url", "").split("v=")[-1]
                if not vid or vid == u.get("url", ""):
                    continue
                slug = slug_dir.name
                meta = slug_to_meta.get(slug, {})
                if not meta:
                    # Best-effort fallback: last underscore token = keyword
                    parts = slug.rsplit("_", 1)
                    meta = {
                        "keyword": parts[-1] if len(parts) > 1 else slug,
                        "topic": parts[0] if len(parts) > 1 else "unknown",
                    }
                collected.append(
                    {
                        "video_id": vid,
                        "title": u.get("title", ""),
                        "slug": slug,
                        "keyword": meta["keyword"],
                        "topic": meta["topic"],
                    }
                )
        except Exception as e:
            logger.warning(f"[analytics] Could not read {uploads_file}: {e}")

    logger.info(f"[analytics] Found {len(collected)} uploaded videos across all slugs.")
    return collected


@with_retry(max_retries=3, base_delay=2)
def _fetch_channel_videos(service) -> list[dict]:
    """
    Fetch all videos uploaded to the authenticated channel via the uploads playlist.
    Returns list of {video_id, title, slug, keyword, topic} with unknown metadata
    for videos not tracked in uploads.json.
    """
    # Get the channel's uploads playlist ID
    ch_resp = service.channels().list(part="contentDetails", mine=True).execute()
    items = ch_resp.get("items", [])
    if not items:
        return []
    uploads_playlist_id = (
        items[0]
        .get("contentDetails", {})
        .get("relatedPlaylists", {})
        .get("uploads", "")
    )
    if not uploads_playlist_id:
        return []

    # Page through the playlist to collect all video IDs
    videos = []
    page_token = None
    while True:
        kwargs = {
            "part": "snippet",
            "playlistId": uploads_playlist_id,
            "maxResults": 50,
        }
        if page_token:
            kwargs["pageToken"] = page_token

        pl_resp = service.playlistItems().list(**kwargs).execute()
        for item in pl_resp.get("items", []):
            snippet = item.get("snippet", {})
            vid_id = snippet.get("resourceId", {}).get("videoId", "")
            if vid_id:
                videos.append(
                    {
                        "video_id": vid_id,
                        "title": snippet.get("title", ""),
                        "slug": "unknown",
                        "keyword": "unknown",
                        "topic": "unknown",
                    }
                )
        page_token = pl_resp.get("nextPageToken")
        if not page_token:
            break

    logger.info(
        f"[analytics] Fetched {len(videos)} videos from channel uploads playlist."
    )
    return videos


@with_retry(max_retries=3, base_delay=2)
def _fetch_stats_batch(service, video_ids: list[str]) -> list[dict]:
    """Fetch statistics for up to 50 video IDs in one API call."""
    resp = (
        service.videos()
        .list(
            part="snippet,statistics",
            id=",".join(video_ids),
        )
        .execute()
    )

    results = []
    for item in resp.get("items", []):
        stats = item.get("statistics", {})
        results.append(
            {
                "video_id": item["id"],
                "title": item.get("snippet", {}).get("title", ""),
                "view_count": int(stats.get("viewCount", 0)),
                "like_count": int(stats.get("likeCount", 0)),
                "comment_count": int(stats.get("commentCount", 0)),
            }
        )
    return results


def _compute_summaries(videos: list[dict]) -> tuple[list[dict], list[dict]]:
    """Return (top_keywords, worst_keywords) sorted by avg views."""
    kw_stats: dict = defaultdict(lambda: {"total_views": 0, "count": 0})
    for v in videos:
        kw = v.get("keyword", "unknown")
        kw_stats[kw]["total_views"] += v["view_count"]
        kw_stats[kw]["count"] += 1

    ranked = sorted(
        [
            {
                "keyword": k,
                "avg_views": v["total_views"] // max(v["count"], 1),
                "video_count": v["count"],
            }
            for k, v in kw_stats.items()
        ],
        key=lambda x: x["avg_views"],
        reverse=True,
    )
    return ranked[:5], ranked[-5:]


def _compute_hook_summaries(videos: list[dict]) -> list[dict]:
    """Return hook types ranked by avg views (only types with ≥2 videos included)."""
    hook_stats: dict = defaultdict(lambda: {"total_views": 0, "count": 0})
    for v in videos:
        ht = v.get("hook_type", "").strip()
        if not ht:
            continue
        hook_stats[ht]["total_views"] += v.get("view_count", 0)
        hook_stats[ht]["count"] += 1

    return sorted(
        [
            {
                "hook_type": ht,
                "avg_views": s["total_views"] // max(s["count"], 1),
                "video_count": s["count"],
            }
            for ht, s in hook_stats.items()
            if s["count"] >= 2
        ],
        key=lambda x: x["avg_views"],
        reverse=True,
    )


# ── Public API ────────────────────────────────────────────────────────────────


def fetch_analytics() -> dict:
    """
    Collect all video IDs, fetch stats from YouTube, compute summaries,
    save to output/analytics.json, return the analytics dict.
    Raises on auth failure.
    """
    service = _get_authenticated_service()

    # Always fetch all videos from the channel (source of truth)
    video_meta = _fetch_channel_videos(service)

    # Build enrichment map: video_id → {slug, topic, keyword}
    # Priority: video_registry.json (persistent, committed to git) > local uploads.json
    enrichment: dict[str, dict] = {}
    for v in _collect_uploaded_video_ids():
        enrichment[v["video_id"]] = v
    if config.VIDEO_REGISTRY_PATH.exists():
        try:
            with open(config.VIDEO_REGISTRY_PATH, "r", encoding="utf-8") as f:
                for entry in json.load(f):
                    vid = entry.get("video_id")
                    if vid:
                        enrichment[vid] = entry
        except Exception as e:
            logger.debug(f"[analytics] Could not read video_registry.json: {e}")

    for v in video_meta:
        meta = enrichment.get(v["video_id"])
        if meta:
            v["slug"] = meta.get("slug", v["slug"])
            v["keyword"] = meta.get("keyword", v["keyword"])
            v["topic"] = meta.get("topic", v["topic"])
            v["hook_type"] = meta.get("hook_type", "")
            v["hook"] = meta.get("hook", "")
            v["word_count"] = meta.get("word_count", 0)
            v["estimated_seconds"] = meta.get("estimated_seconds", 0)

    if not video_meta:
        logger.info("[analytics] No videos found on channel.")
        result = {
            "fetched_at": _utcnow(),
            "total_videos": 0,
            "videos": [],
            "top_keywords": [],
            "worst_keywords": [],
        }
        config.ANALYTICS_PATH.parent.mkdir(parents=True, exist_ok=True)
        config.ANALYTICS_PATH.write_text(
            json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        return result

    # Fetch stats in batches of 50
    all_ids = [v["video_id"] for v in video_meta]
    stats_map: dict[str, dict] = {}
    for i in range(0, len(all_ids), _STATS_BATCH_SIZE):
        batch_ids = all_ids[i : i + _STATS_BATCH_SIZE]
        try:
            batch_stats = _fetch_stats_batch(service, batch_ids)
            for s in batch_stats:
                stats_map[s["video_id"]] = s
        except Exception as e:
            logger.warning(f"[analytics] Failed to fetch stats batch {i}: {e}")

    # Merge stats back into video_meta
    enriched = []
    for v in video_meta:
        s = stats_map.get(v["video_id"], {})
        enriched.append(
            {
                **v,
                "view_count": s.get("view_count", 0),
                "like_count": s.get("like_count", 0),
                "comment_count": s.get("comment_count", 0),
            }
        )

    top_kw, worst_kw = _compute_summaries(enriched)
    hook_summaries = _compute_hook_summaries(enriched)

    result = {
        "fetched_at": _utcnow(),
        "total_videos": len(enriched),
        "videos": enriched,
        "top_keywords": top_kw,
        "worst_keywords": worst_kw,
        "hook_type_performance": hook_summaries,
    }

    config.ANALYTICS_PATH.parent.mkdir(parents=True, exist_ok=True)
    config.ANALYTICS_PATH.write_text(
        json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    logger.info(
        f"[analytics] Saved analytics: {len(enriched)} videos, "
        f"top keyword: {top_kw[0]['keyword'] if top_kw else 'n/a'}"
    )
    return result


def get_performance_hints() -> str:
    """
    Load analytics (re-fetching if missing or older than 24h).
    Returns a plain-English summary string for Claude prompts.
    Returns "" on any failure.
    """
    try:
        analytics = None

        if config.ANALYTICS_PATH.exists():
            try:
                with open(config.ANALYTICS_PATH, "r", encoding="utf-8") as f:
                    analytics = json.load(f)
                fetched_at = datetime.fromisoformat(analytics["fetched_at"])
                age = datetime.now(timezone.utc) - fetched_at
                if age > timedelta(hours=_HINTS_MAX_AGE_HOURS):
                    logger.info("[analytics] Analytics cache expired — refreshing.")
                    analytics = None
            except Exception:
                analytics = None

        if analytics is None:
            analytics = fetch_analytics()

        total = analytics.get("total_videos", 0)
        if total == 0:
            return ""

        top = analytics.get("top_keywords", [])
        worst = analytics.get("worst_keywords", [])

        top_str = ", ".join(
            f"{k['keyword']} ({k['avg_views']:,} avg, {k['video_count']} video{'s' if k['video_count'] != 1 else ''})"
            for k in top
        )
        worst_str = ", ".join(
            f"{k['keyword']} ({k['avg_views']:,} avg)"
            for k in worst
            if k["avg_views"] < (top[0]["avg_views"] if top else 0)
        )

        hook_perf = analytics.get("hook_type_performance", [])
        hook_str = ", ".join(
            f"{h['hook_type']} ({h['avg_views']:,} avg, {h['video_count']} videos)"
            for h in hook_perf
        )

        parts = [f"Total channel videos analyzed: {total}."]
        if top_str:
            parts.append(f"Top performing keywords by average views: {top_str}.")
        if worst_str:
            parts.append(f"Worst performing keywords: {worst_str}.")
        if hook_str:
            best_hook = hook_perf[0]["hook_type"] if hook_perf else ""
            parts.append(
                f"Hook type performance (best to worst): {hook_str}. "
                f"Prefer {best_hook} hooks when it fits the story."
            )

        return " ".join(parts)

    except Exception as e:
        logger.warning(
            f"[analytics] get_performance_hints failed (continuing without): {e}"
        )
        return ""
