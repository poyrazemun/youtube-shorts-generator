"""
STEP 6 — YOUTUBE UPLOAD
Uses YouTube Data API v3 via google-api-python-client.
OAuth2 flow: first run opens browser to authorize, stores credentials.json.
Subsequent runs use stored credentials (auto-refresh).
"""

import json
import logging
import os
from pathlib import Path

import config
from pipeline.retry import with_retry

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

    # Build description with hashtags appended
    hashtag_str = " ".join(f"#{tag}" for tag in hashtags if tag)
    full_description = f"{description}\n\n{hashtag_str}\n\n#UnrealHistory #Shorts #History"

    # Clamp title to YouTube's 100-char limit
    if len(title) > 100:
        title = title[:97] + "..."

    event = script.get("source_event", {})
    # Use dedicated youtube_tags if present (T1-D), otherwise fall back to hashtags
    youtube_tags = script.get("youtube_tags") or hashtags
    tags = youtube_tags + ["unreal history", event.get("location", ""), event.get("year", "")]
    tags = [t for t in tags if t]  # filter empty strings

    body = {
        "snippet": {
            "title": title,
            "description": full_description,
            "tags": tags,
            "categoryId": config.YOUTUBE_CATEGORY_ID,
            "defaultLanguage": "en",
        },
        "status": {
            "privacyStatus": config.YOUTUBE_PRIVACY,
            "selfDeclaredMadeForKids": False,
        },
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
