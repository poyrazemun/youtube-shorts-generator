# PROJECT_CONTEXT.md — Unreal History Bot

> Reference document for resuming development in future sessions.
> Keep this updated whenever architecture changes.

---

## What This Project Does

Automated CLI pipeline that generates and uploads YouTube Shorts about strange,
unbelievable real historical events. Channel: **"Unreal History"**.

**Command:**
```bash
python orchestrator.py --topic "Strange Moments in History" --keyword "war" --count 5
python orchestrator.py --topic "Unbelievable Events" --keyword "plague" --count 3 --no-upload
```

---

## File Map

```
orchestrator.py                 CLI entry point (argparse)
config.py                       All constants, paths, API keys (reads .env)
requirements.txt
.env / .env.example
PROJECT_CONTEXT.md              ← this file

pipeline/
  __init__.py
  event_discovery.py            Step 1  — Claude API → events JSON
  script_generator.py           Step 2  — Claude API → scripts JSON (+ SEO metadata)
  image_generator.py            Step 3  — image generation (multi-backend + per-image fallback)
  tts_generator.py              Step 4  — TTS audio (Piper / Coqui / Edge TTS)
  captions.py                   Step 5a — Whisper word-timestamps → ASS+SRT; estimation fallback
  video_assembler.py            Step 5b — ffmpeg 2-pass video assembly
  youtube_uploader.py           Step 6  — YouTube Data API v3 upload
  retry.py                      @with_retry decorator (exponential backoff, used by all external calls)
  state.py                      PipelineState — per-event stage completion ledger → state.json
  log.py                        Structured logging (daily-rotating file + console) [created, not yet wired]
  research.py                   DuckDuckGo snippet fetcher for anti-hallucination [created, not yet wired]
  music.py                      Background track selector from assets/music/ [created, not yet wired]
  thumbnail.py                  1280×720 YouTube thumbnail generator [created, not yet wired]

utils/
  __init__.py
  subtitle_generator.py         Estimation-based .srt generation (fallback when Whisper absent)

output/                         All pipeline output (auto-created)
logs/                           pipeline.log (auto-created)
assets/
  music/                        Drop royalty-free MP3s here for background music (Tier 2, not wired yet)
  images/ audio/ video/         Legacy dirs (unused)
```

---

## Pipeline Data Flow

```
CLI args (topic, keyword, count)
        │
        ▼
[orchestrator.py] slug = slugify(topic + "_" + keyword)
                  state = PipelineState(slug)
        │
        ├─ Step 1: event_discovery.py
        │    Input:  topic, keyword, count
        │    Output: output/<slug>/events.json
        │    Tool:   Claude API (claude-sonnet-4-6) with @with_retry
        │    Format: [{event, year, location, visual_theme}, ...]
        │    State:  state.complete(0, "events")
        │
        ├─ Step 2: script_generator.py
        │    Input:  events.json
        │    Output: output/<slug>/scripts.json
        │    Tool:   Claude API with @with_retry
        │    Format: [{title, description, hashtags, youtube_tags, hook, context,
        │              twist, ending_fact, full_script, word_count,
        │              estimated_seconds, event_index, source_event}, ...]
        │    State:  state.complete(idx, "scripts") per event
        │
        ├─ Step 3: image_generator.py
        │    Input:  scripts.json
        │    Output: output/<slug>/images/<event_idx>/img_0..4.png
        │    Tool:   See backend priority below (all with @with_retry)
        │    Count:  5 images per event (IMAGES_PER_EVENT = 5)
        │    Size:   608×1080 source (scaled to 1080×1920 in ffmpeg)
        │
        ├─ Step 4: tts_generator.py
        │    Input:  scripts.json (full_script field)
        │    Output: output/<slug>/audio/<event_idx>.wav
        │    Tool:   Piper / Coqui / Edge TTS (with @with_retry on Edge TTS)
        │
        ├─ Step 5a: captions.py
        │    Input:  audio WAV + script
        │    Output: output/<slug>/subtitles/<idx>_captions.ass  (Whisper path)
        │            output/<slug>/subtitles/<idx>_captions.srt  (Whisper path)
        │            output/<slug>/subtitles/<idx>.srt           (estimation fallback)
        │    Logic:  if openai-whisper installed → real word timestamps
        │            else → proportional word-timing estimation (subtitle_generator.py)
        │    State:  state.complete(idx, "captions")
        │
        ├─ Step 5b: video_assembler.py
        │    Input:  5 PNGs + WAV + (ASS or SRT) per event
        │    Output: output/<slug>/video/<event_idx>.mp4
        │            output/<slug>_<idx>_<safe_title>.mp4   ← final copy
        │    Tool:   ffmpeg (2-pass: slideshow+audio → subtitle burn)
        │    Spec:   1080×1920, H.264, AAC 128kbps, 24fps
        │    State:  state.complete(idx, "video", [path])
        │
        └─ Step 6: youtube_uploader.py
             Input:  video .mp4 files + scripts.json
             Output: output/<slug>/uploads.json (video IDs + URLs)
             Tool:   YouTube Data API v3, OAuth2, @with_retry on upload
             State:  state.complete(idx, "upload", [url])
```

---

## Resumability

**Two-layer resumability:**

1. **File-based caching** — every module checks for existing output files before running.
   Re-running the same command skips already-completed steps automatically.

2. **State ledger** — `PipelineState` writes `output/<slug>/state.json` tracking which
   (event_idx, stage) pairs are complete with timestamps and artifact paths.
   Failures are also recorded with error messages for debugging.

Cache invalidation: delete the relevant output file/folder and re-run.

---

## Retry Behavior

All external API calls use `@with_retry(max_retries=3, base_delay=2)` from `pipeline/retry.py`:
- Delay formula: `base_delay * 2^attempt` → 2s, 4s, 8s
- Logs WARNING on each retry, ERROR after final failure
- Applied to: Claude (events + scripts), HuggingFace, Pollinations, Edge TTS, YouTube upload

---

## Image Generation — Backend Priority

| Priority | Backend | Requirement | Notes |
|----------|---------|-------------|-------|
| 1 | Automatic1111 | Local at :7860, started with `--api` | Best quality |
| 2 | ComfyUI | Local at :8188 | Good quality |
| 3 | **HuggingFace** | `HUGGINGFACE_API_TOKEN` in `.env` (free tier) | FLUX.1-schnell, primary remote |
| 4 | Pollinations.AI | Internet only, no key | Free, occasionally unreliable |
| **5** | **PIL placeholder** | **Always available** | **Guaranteed offline fallback** |

Detection: `detect_backend()` in `image_generator.py` returns a string.
Per-image fallback chain: `[primary, "pollinations", "pil"]` — each with `@with_retry`.

HuggingFace endpoint: `https://router.huggingface.co/hf-inference/models/black-forest-labs/FLUX.1-schnell`
Request: `POST {"inputs": prompt, "parameters": {"width": 608, "height": 1080}}`

---

## TTS — Backend Priority

| Priority | Backend | Requirement | Notes |
|----------|---------|-------------|-------|
| 1 | Piper TTS | Binary on PATH | Best quality, fast, offline |
| 2 | Coqui TTS | `pip install TTS` | Good quality, offline |
| **3** | **Edge TTS** | `pip install edge-tts` | **Default fallback; Microsoft Neural, free** |

Edge TTS voice: `en-US-ChristopherNeural`
Edge TTS is async (`asyncio.run()`) → outputs MP3 → converted to WAV via ffmpeg subprocess.
Detection: `detect_tts_backend()` in `tts_generator.py`.

---

## Captions — Mode Selection

| Mode | Requirement | Output | Quality |
|------|-------------|--------|---------|
| **Whisper** | `pip install openai-whisper` | `*_captions.ass` + `*_captions.srt` | Real word timestamps |
| **Estimation** | Nothing extra | `*.srt` (via subtitle_generator.py) | Proportional, may drift |

`captions.py::generate_captions()` auto-detects via `_has_whisper()`.
Whisper model used: `base` (balance of speed vs. accuracy).
ASS format uses `PlayResX=1080, PlayResY=1920` for pixel-accurate subtitle sizing.

When Whisper provides an ASS file, `video_assembler.py` uses it directly (skips SRT→ASS conversion).

---

## Key Configuration (`config.py`)

| Constant | Value | Description |
|----------|-------|-------------|
| `CLAUDE_MODEL` | `claude-sonnet-4-6` | Model for event + script gen |
| `IMAGES_PER_EVENT` | `5` | Images per video |
| `IMAGE_WIDTH/HEIGHT` | `608×1080` | SD source size (multiple of 64) |
| `VIDEO_WIDTH/HEIGHT` | `1080×1920` | Final output (9:16 vertical) |
| `MUSIC_DIR` | `assets/music/` | Drop royalty-free MP3s here |
| `YOUTUBE_PRIVACY` | `private` | Default upload privacy |
| `YOUTUBE_CATEGORY_ID` | `27` | Education |

---

## ffmpeg Video Assembly — Technical Details

Two-pass approach in `video_assembler.py::assemble_video()`:

**Pass 1** — Slideshow + Audio (writes `tmp/nosub.mp4`):
- Each image: `-loop 1 -t <audio_duration/n_images>` input
- Scale filter per image: `scale=1080:1920:force_original_aspect_ratio=decrease,pad=1080:1920:(ow-iw)/2:(oh-ih)/2:color=black,setsar=1,fps=24`
- Concat filter joins all scaled images
- Audio mapped from WAV input → AAC 128kbps

**Pass 2** — Subtitle burn (writes final `.mp4`):
- If Whisper ASS provided: uses it directly as `ass='<path>'` filter
- Otherwise: calls `_convert_srt_to_ass()` to convert SRT → ASS in tmpdir, then burns
- ASS styling: `PlayResX=1080, PlayResY=1920`, `BorderStyle=3` (box background), `Alignment=2` (bottom-center)
- Font size: `int(1920 * 0.032)` ≈ 61px; margin from bottom: `int(1920 * 0.09)` ≈ 173px
- Windows path escaping: backslashes → `/`, colons → `\:`
- If subtitle burn fails: silently copies no-subtitle version

---

## Script Metadata (T1-D Enhanced)

Scripts now include a dedicated `youtube_tags` field for upload:
- `title`: ≤60 chars, format "Hook or question | Year"
- `description`: 2-sentence summary + "Follow for more unbelievable history."
- `hashtags`: 5 general tags
- `youtube_tags`: 10–15 tags mixing broad ("history", "shorts") + specific (location, year, topic)

`youtube_uploader.py` uses `youtube_tags` if present, falls back to `hashtags`.

---

## YouTube Upload — Setup

1. Google Cloud Console → Enable YouTube Data API v3
2. Create OAuth 2.0 Client ID (Desktop app type)
3. Download JSON → save as `client_secrets.json` in project root
4. First run: browser opens for auth → `credentials.json` saved automatically
5. Subsequent runs: token auto-refreshed from `credentials.json`

Upload result saved to `output/<slug>/uploads.json`. Already-uploaded events
(by `event_index`) are skipped on re-run.

---

## Dependencies

```
# Python 3.10+ required (uses X | Y union type hints)
anthropic>=0.40.0             Claude API
python-dotenv>=1.0.0          .env loading
edge-tts>=6.1.0               TTS (Microsoft Neural, free)
Pillow>=10.0.0                Image PIL fallback + thumbnail
google-api-python-client      YouTube upload
google-auth-oauthlib          YouTube OAuth
google-auth-httplib2          YouTube auth transport

# System (must be on PATH):
ffmpeg                        Video assembly, MP3→WAV conversion
ffprobe                       Audio duration detection (bundled with ffmpeg)

# Optional — better TTS:
piper                         Binary from github.com/rhasspy/piper/releases
TTS (pip install TTS)         Coqui TTS

# Optional — real caption timing:
openai-whisper (pip)          ~500MB PyTorch; upgrades captions from estimated to word-accurate
```

---

## Tier 2 Modules (Created, Not Yet Wired)

These files exist in `pipeline/` but are not called by the orchestrator yet:

| File | Purpose | Activates when |
|------|---------|----------------|
| `log.py` | Daily-rotating file log + console handler | Wire into orchestrator (replace `_setup_logging`) |
| `research.py` | DuckDuckGo snippet fetcher for Claude grounding | Wire into `script_generator.py` |
| `music.py` | Random track selector from `assets/music/` | Wire into `video_assembler.py` |
| `thumbnail.py` | 1280×720 HuggingFace + Pillow thumbnail | Wire into orchestrator + youtube_uploader |

---

## Known Issues / Technical Debt

- Python 3.10+ required for `X | Y` union syntax in type hints; not explicitly enforced.
- `replicate` Python package removed (Pydantic v1 broken on Python 3.14+);
  Replicate calls use `urllib.request` against REST API directly.
- Windows console: `print()` with Unicode characters may raise `UnicodeEncodeError`
  on cp1254 terminals — cosmetic only, does not affect execution.
- PIL placeholder images are stylized text cards, not AI art — acceptable for
  testing but should be replaced with real images for production uploads.
- Whisper `base` model may mis-transcribe proper nouns or historical names;
  use `small` or `medium` model for higher accuracy (edit `captions.py`).
