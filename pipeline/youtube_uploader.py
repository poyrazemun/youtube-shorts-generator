"""
STEP 6 — YOUTUBE UPLOAD
Uses YouTube Data API v3 via google-api-python-client.
OAuth2 flow: first run opens browser to authorize, stores credentials.json.
Subsequent runs use stored credentials (auto-refresh).
"""

import json
import logging
import os
import re
from pathlib import Path

import config
from pipeline.retry import with_retry

# YouTube renders the first 3 hashtags found in the title as a clickable
# "category chip" above the title. We append up to this many normalized
# hashtags to claim that surface for discovery without truncating the title.
_TITLE_HASHTAG_COUNT = 3
_TITLE_MAX_CHARS = 100

logger = logging.getLogger(__name__)


def _get_authenticated_service():
    """
    Build and return an authenticated YouTube API service client.
    Opens browser on first run for OAuth2 consent. Stores token in credentials.json.
    """
    try:
        from google.oauth2.credentials import Credentials
        from google.auth.transport.requests import Request
        from google_auth_oauthlib.flow import InstalledAppFlow
        from googleapiclient.discovery import build
    except ImportError as e:
        raise RuntimeError(
            "Google API packages not installed. Run:\n"
            "  pip install google-api-python-client google-auth-oauthlib google-auth-httplib2"
        ) from e

    creds = None
    creds_path = config.YOUTUBE_CREDENTIALS_FILE
    secrets_path = config.YOUTUBE_CLIENT_SECRETS_FILE

    # Load existing credentials
    if os.path.exists(creds_path):
        try:
            creds = Credentials.from_authorized_user_file(creds_path, config.YOUTUBE_SCOPES)
            logger.info("[youtube_uploader] Loaded existing OAuth credentials.")
        except Exception as e:
            logger.warning(f"[youtube_uploader] Could not load credentials: {e} — re-authorizing.")
            creds = None

    # Refresh or re-authorize
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            try:
                creds.refresh(Request())
                logger.info("[youtube_uploader] Refreshed OAuth token.")
            except Exception as e:
                logger.warning(f"[youtube_uploader] Token refresh failed: {e} — re-authorizing.")
                creds = None

        if not creds:
            if not os.path.exists(secrets_path):
                raise FileNotFoundError(
                    f"YouTube client secrets file not found: {secrets_path}\n"
                    f"Download it from Google Cloud Console → APIs & Services → Credentials\n"
                    f"Enable YouTube Data API v3 and create OAuth 2.0 Client ID (Desktop app)"
                )

            flow = InstalledAppFlow.from_client_secrets_file(secrets_path, config.YOUTUBE_SCOPES)
            creds = flow.run_local_server(port=0, open_browser=True)
            logger.info("[youtube_uploader] New OAuth token obtained via browser.")

        # Save credentials for future runs (owner read/write only). Open with
        # O_CREAT|O_TRUNC + 0o600 atomically so the file is never readable by
        # other users, even momentarily. On Windows the mode bits are ignored.
        flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC
        fd = os.open(creds_path, flags, 0o600)
        with os.fdopen(fd, "w") as f:
            f.write(creds.to_json())
        logger.info(f"[youtube_uploader] Credentials saved to {creds_path}")

    service = build("youtube", "v3", credentials=creds)
    return service


_TAGS_MAX_CHARS = 500  # YouTube's hard limit on the combined tag string


def _build_youtube_tags(
    youtube_tags: list[str], location: str, year: str | int
) -> list[str]:
    """Combine Claude-supplied tags with our standard suffix (location, year,
    "unreal history"), deduplicate case-insensitively, strip empties, and
    clamp the total to YouTube's 500-char ceiling.

    Order is preserved (first occurrence wins) so Claude's keyword-ranked tags
    keep their priority. The suffix tags only land if there's budget left."""
    raw = list(youtube_tags or []) + [
        "unreal history",
        str(location or "").strip(),
        str(year or "").strip(),
    ]

    out: list[str] = []
    seen: set[str] = set()
    total = 0
    for t in raw:
        if not t:
            continue
        norm = str(t).strip()
        if not norm:
            continue
        key = norm.lower()
        if key in seen:
            continue
        # Approximation matching script_generator's clamp: each tag costs
        # len + 1 (separator). Conservative — YouTube also adds quotes for
        # tags containing spaces, but staying under 500 here gives headroom.
        cost = len(norm) + 1
        if total + cost > _TAGS_MAX_CHARS:
            continue
        seen.add(key)
        out.append(norm)
        total += cost
    return out


def _build_description_hashtags(
    claude_hashtags: list[str], suffix_hashtags: list[str]
) -> list[str]:
    """Merge Claude's hashtags with our hardcoded suffix into a single ordered,
    case-insensitive deduplicated list. Each tag is normalized to a YouTube-safe
    CamelCase token. Claude's hashtags come first to preserve their relevance
    ranking; suffix tags only land if not already present."""
    out: list[str] = []
    seen: set[str] = set()
    for raw in list(claude_hashtags or []) + list(suffix_hashtags or []):
        if not raw:
            continue
        norm = _normalize_hashtag(str(raw))
        if not norm:
            continue
        key = norm.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(norm)
    return out


def _normalize_hashtag(tag: str) -> str:
    """Convert a free-form tag into a YouTube-safe hashtag token (CamelCase,
    alphanumeric only). Returns "" if nothing usable remains."""
    parts = re.findall(r"[A-Za-z0-9]+", tag)
    if not parts:
        return ""

    def _shape(p: str) -> str:
        if p.isupper():
            return p  # acronym: "WW2", "NASA"
        if p[0].isupper() and any(c.isupper() for c in p[1:]):
            return p  # already CamelCase: "UnrealHistory"
        return p.capitalize()

    return "".join(_shape(p) for p in parts)


def _build_title_with_hashtags(title: str, hashtags: list[str]) -> str:
    """Append up to _TITLE_HASHTAG_COUNT normalized hashtags to the title to
    claim YouTube's title hashtag chip. Drops tags as needed to fit the 100-char
    ceiling; the original title is preserved and never truncated to make room
    for hashtags."""
    seen: set[str] = set()
    candidates: list[str] = []
    for raw in hashtags:
        if not raw:
            continue
        norm = _normalize_hashtag(str(raw))
        key = norm.lower()
        if not norm or key in seen:
            continue
        seen.add(key)
        candidates.append(norm)
        if len(candidates) >= _TITLE_HASHTAG_COUNT:
            break

    if len(title) > _TITLE_MAX_CHARS:
        return title[: _TITLE_MAX_CHARS - 3] + "..."

    # Try the largest hashtag set that fits after the title.
    for n in range(len(candidates), 0, -1):
        suffix = " " + " ".join(f"#{t}" for t in candidates[:n])
        if len(title) + len(suffix) <= _TITLE_MAX_CHARS:
            return title + suffix

    return title


@with_retry(max_retries=3, base_delay=2)
def _upload_video(service, video_path: Path, script: dict) -> dict:
    """
    Upload a single video to YouTube.
    Returns the YouTube API response dict with video ID.
    """
    try:
        from googleapiclient.http import MediaFileUpload
    except ImportError as e:
        raise RuntimeError("google-api-python-client not installed.") from e

    title = script.get("title", "Unreal History Short")
    description = script.get("description", "")
    hashtags = script.get("hashtags", [])

    # Build description hashtag block. Combine Claude's suggestions with our
    # standard suffix, normalize each tag (CamelCase, alnum-only), and de-dupe
    # case-insensitively so we never ship "#history #History" or "#shorts #Shorts".
    description_hashtags = _build_description_hashtags(
        hashtags, ["UnrealHistory", "Shorts", "History"]
    )
    hashtag_str = " ".join(f"#{t}" for t in description_hashtags)
    full_description = f"{description}\n\n{hashtag_str}"

    title = _build_title_with_hashtags(title, hashtags)

    event = script.get("source_event", {})
    # Use dedicated youtube_tags if present (T1-D), otherwise fall back to hashtags
    youtube_tags = script.get("youtube_tags") or hashtags
    tags = _build_youtube_tags(
        youtube_tags, event.get("location", ""), event.get("year", "")
    )

    body = {
        "snippet": {
            "title": title,
            "description": full_description,
            "tags": tags,
            "categoryId": config.YOUTUBE_CATEGORY_ID,
            "defaultLanguage": "en",
            "defaultAudioLanguage": "en",
        },
        "status": {
            "privacyStatus": config.YOUTUBE_PRIVACY,
            "selfDeclaredMadeForKids": False,
            "license": "creativeCommon",
        },
    }

    # Attach per-language title/description so YouTube serves the localized
    # version to viewers in matching locales. Defaults to {} if the localizer
    # step failed — YouTube simply falls back to the English snippet.
    localizations = script.get("localizations") or {}
    if localizations:
        body["localizations"] = {
            lang: {"title": entry["title"], "description": entry["description"]}
            for lang, entry in localizations.items()
            if isinstance(entry, dict) and entry.get("title") and entry.get("description")
        }

    media = MediaFileUpload(
        str(video_path),
        mimetype="video/mp4",
        resumable=True,
        chunksize=1024 * 1024 * 5,  # 5MB chunks
    )

    logger.info(f"[youtube_uploader] Uploading: '{title}' ({video_path.stat().st_size // 1024}KB)...")

    request = service.videos().insert(
        part=",".join(body.keys()),
        body=body,
        media_body=media,
    )

    response = None
    while response is None:
        status, response = request.next_chunk()
        if status:
            pct = int(status.progress() * 100)
            logger.info(f"[youtube_uploader] Upload progress: {pct}%")

    video_id = response.get("id", "unknown")
    video_url = f"https://www.youtube.com/watch?v={video_id}"
    logger.info(f"[youtube_uploader] Upload complete! Video ID: {video_id} → {video_url}")

    return {
        "video_id": video_id,
        "url": video_url,
        "title": title,
        "privacy": config.YOUTUBE_PRIVACY,
    }


def _find_srt_path(slug: str, event_index: int) -> Path | None:
    """Locate the SRT for a given event. Whisper path emits `{idx}_captions.srt`,
    estimation fallback emits `{idx}.srt`. Prefer the Whisper one if present."""
    subtitle_dir = config.OUTPUT_DIR / slug / "subtitles"
    for name in (f"{event_index}_captions.srt", f"{event_index}.srt"):
        p = subtitle_dir / name
        if p.exists() and p.stat().st_size > 0:
            return p
    return None


@with_retry(max_retries=3, base_delay=2)
def _upload_caption(service, video_id: str, srt_path: Path) -> None:
    """Upload an SRT as a real YouTube caption track via captions.insert."""
    try:
        from googleapiclient.http import MediaFileUpload
    except ImportError as e:
        raise RuntimeError("google-api-python-client not installed.") from e

    body = {
        "snippet": {
            "videoId": video_id,
            "language": "en",
            "name": "English",
            "isDraft": False,
        }
    }
    media = MediaFileUpload(str(srt_path), mimetype="application/octet-stream", resumable=False)
    request = service.captions().insert(part="snippet", body=body, media_body=media)
    response = request.execute()
    logger.info(
        f"[youtube_uploader] Caption track uploaded: id={response.get('id', 'unknown')} "
        f"({srt_path.name})"
    )


def _append_to_registry(entry: dict) -> None:
    """Persist video_id + metadata to video_registry.json (never cleared, committed to git)."""
    registry = []
    if config.VIDEO_REGISTRY_PATH.exists():
        try:
            with open(config.VIDEO_REGISTRY_PATH, encoding="utf-8") as f:
                registry = json.load(f)
        except Exception:
            registry = []

    # Update existing entry or append new one
    existing_ids = {r["video_id"] for r in registry}
    if entry["video_id"] not in existing_ids:
        registry.append(entry)
        with open(config.VIDEO_REGISTRY_PATH, "w", encoding="utf-8") as f:
            json.dump(registry, f, indent=2, ensure_ascii=False)
        logger.debug(f"[youtube_uploader] Registered video {entry['video_id']} in video_registry.json")


def upload_all_videos(
    video_paths: list[Path],
    scripts: list[dict],
    slug: str,
    topic: str = "",
    keyword: str = "",
) -> list[dict]:
    """
    Upload all assembled videos to YouTube.
    Returns list of upload result dicts with video IDs and URLs.
    Saves upload results to output/<slug>/uploads.json for reference.
    """
    results_path = config.OUTPUT_DIR / slug / "uploads.json"

    # Load existing upload results to avoid re-uploading
    existing_results = {}
    if results_path.exists():
        try:
            with open(results_path) as f:
                existing_list = json.load(f)
                existing_results = {r["event_index"]: r for r in existing_list}
        except Exception:
            pass

    try:
        service = _get_authenticated_service()
    except Exception as e:
        logger.error(f"[youtube_uploader] Authentication failed: {e}")
        raise

    upload_results = []

    for loop_i, (video_path, script) in enumerate(zip(video_paths, scripts)):
        idx = script.get("event_index", loop_i)

        # Skip if already uploaded
        if idx in existing_results:
            logger.info(
                f"[youtube_uploader] Event {idx} already uploaded: "
                f"{existing_results[idx].get('url', 'unknown')}"
            )
            upload_results.append(existing_results[idx])
            continue

        if video_path is None or not video_path.exists():
            logger.error(f"[youtube_uploader] Video file not found for event {idx}: {video_path}")
            continue

        try:
            result = _upload_video(service, video_path, script)
            result["event_index"] = idx

            # Upload SRT as a real caption track. Failure here must not abort
            # the run — the video is already up, captions are a nice-to-have.
            srt_path = _find_srt_path(slug, idx)
            if srt_path is None:
                logger.warning(
                    f"[youtube_uploader] No SRT found for event {idx} — skipping caption upload"
                )
            else:
                try:
                    _upload_caption(service, result["video_id"], srt_path)
                    result["caption_uploaded"] = True
                except Exception as e:
                    logger.error(
                        f"[youtube_uploader] Caption upload failed for {result['video_id']}: {e}"
                    )
                    result["caption_uploaded"] = False

            upload_results.append(result)

            # Save after each upload to preserve progress
            with open(results_path, "w") as f:
                json.dump(upload_results, f, indent=2)

            # Persist to the global registry (local-only file, git-ignored — survives across runs)
            _append_to_registry({
                "video_id": result["video_id"],
                "title": result["title"],
                "slug": slug,
                "topic": topic,
                "keyword": keyword,
                "hook_type": script.get("hook_type", ""),
                "hook": script.get("hook", ""),
                "word_count": script.get("word_count", 0),
                "estimated_seconds": script.get("estimated_seconds", 0),
            })

        except Exception as e:
            logger.error(f"[youtube_uploader] Upload failed for event {idx}: {e}")
            raise

    logger.info(f"[youtube_uploader] All uploads complete. Results saved to {results_path}")
    return upload_results
