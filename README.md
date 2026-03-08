# Unreal History Bot

A fully automated YouTube Shorts pipeline that discovers strange real historical events, generates scripts, creates images, records voiceover, assembles videos, and uploads them to YouTube — all without human input after setup.

Built entirely with **[Claude Code](https://claude.ai/claude-code)**, using its agent and skill system for architecture planning, codebase exploration, and iterative implementation across multiple sessions.

---

## How It Works

```
--refresh-topics        Claude generates a queue of topic/keyword combos
       ↓
--auto (daily, via GitHub Actions)
       ↓
  Step 1  event_discovery.py    Claude → 1 strange historical event
  Step 2  script_generator.py   Claude + DuckDuckGo research → viral script + SEO metadata
  Step 3  image_generator.py    HuggingFace FLUX.1-schnell → 5 cinematic images
  Step 4  tts_generator.py      Edge TTS (Microsoft Neural) → narration audio
  Step 5a captions.py           Whisper / estimation → word-timed subtitles
  Step 5b video_assembler.py    ffmpeg → 1080×1920 MP4 with burned captions + background music
  Step 6  youtube_uploader.py   YouTube Data API v3 → upload with thumbnail
       ↓
--analytics             Fetches view counts, feeds performance data back into topic generation
```

Every step is **resumable** — output is cached to disk, so re-running picks up where it left off.

---

## Requirements

- Python 3.10+
- ffmpeg on PATH
- Anthropic API key (Claude)
- YouTube Data API v3 OAuth credentials
- HuggingFace API token (optional — free tier, for better images)

```bash
pip install -r requirements.txt
```

```bash
# Windows
winget install ffmpeg

# macOS
brew install ffmpeg

# Linux
sudo apt install ffmpeg
```

---

## Setup

**1. Environment**

```bash
cp .env.example .env
```

```env
ANTHROPIC_API_KEY=sk-ant-...
HUGGINGFACE_API_TOKEN=hf_...     # optional
YOUTUBE_PRIVACY=private
```

**2. YouTube API credentials**

- Google Cloud Console → Enable YouTube Data API v3
- Create OAuth 2.0 Client ID (Desktop app) → download as `client_secrets.json`
- Place in project root

**3. First run (opens browser for YouTube OAuth)**

```bash
python orchestrator.py --analytics
```

---

## CLI

```bash
# Automated mode — picks next topic from queue, runs full pipeline
python orchestrator.py --auto

# Regenerate topic queue (Claude generates 25 combos; auto-refreshes on Mondays via Actions)
python orchestrator.py --refresh-topics

# Fetch YouTube analytics + print performance summary
python orchestrator.py --analytics

# Manual mode
python orchestrator.py --topic "Strange War Stories" --keyword "battle" [--count N] [--no-upload]

# Flags available on all modes
--no-upload    skip YouTube upload, save videos locally
--verbose      DEBUG-level console logging
```

---

## GitHub Actions (Daily Automation)

The included `.github/workflows/daily.yml` runs `--auto` every day at 09:00 UTC.

**Required GitHub Secrets:**

| Secret | Value |
|---|---|
| `ANTHROPIC_API_KEY` | Anthropic API key |
| `HUGGINGFACE_API_TOKEN` | HuggingFace token |
| `YOUTUBE_CLIENT_SECRETS_B64` | `base64(client_secrets.json)` |
| `YOUTUBE_CREDENTIALS_B64` | `base64(credentials.json)` |

```bash
# Encode credentials for GitHub Secrets
python -c "import base64; print(base64.b64encode(open('client_secrets.json','rb').read()).decode())"
python -c "import base64; print(base64.b64encode(open('credentials.json','rb').read()).decode())"
```

The workflow automatically:
- Installs ffmpeg
- Decodes credentials from secrets
- Runs `--refresh-topics` if the queue is empty or it's Monday
- Commits updated `topics_queue.json` back to the repo
- Uploads generated `.mp4` files as downloadable artifacts (7-day retention)

See [HOW_TO_USE.md](HOW_TO_USE.md) for the full step-by-step setup guide.

---

## Image Generation (Priority Order)

| Priority | Backend | Requirement |
|---|---|---|
| 1 | Automatic1111 (local) | Running with `--api` flag |
| 2 | ComfyUI (local) | Running normally |
| 3 | **HuggingFace** | `HUGGINGFACE_API_TOKEN` in `.env` |
| 4 | Pollinations.AI | Internet only, no key needed |
| 5 | PIL placeholder | Always available (offline fallback) |

Each image is retried up to 3 times with exponential backoff before falling back to the next backend.

---

## Voice Generation (Priority Order)

| Priority | Backend | Requirement |
|---|---|---|
| 1 | Piper TTS (local) | Binary on PATH |
| 2 | Coqui TTS (local) | `pip install TTS` |
| 3 | **Edge TTS** | Included in requirements.txt |

Default voice: `en-US-ChristopherNeural`

---

## Output Format

- **Resolution**: 1080×1920 (9:16 vertical)
- **Codec**: H.264, AAC 128kbps, 24fps
- **Duration**: 20–30 seconds
- **Subtitles**: Burned in, white bold text, semi-transparent background box
- **Thumbnail**: 1280×720 PNG, uploaded to YouTube

---

## Output Structure

```
output/
  <slug>/
    events.json       step 1 — discovered event
    scripts.json      step 2 — script + SEO metadata
    images/           step 3 — 5 PNG images
    audio/            step 4 — narration WAV
    subtitles/        step 5a — .ass + .srt caption files
    video/            step 5b — assembled MP4
    thumbnails/       step 6 — YouTube thumbnail
    uploads.json      step 6 — video IDs + URLs
    state.json        per-step completion ledger

topics_queue.json     topic queue (persisted in repo for CI)
logs/                 daily rotating logs (14-day retention)
```

---

## Cost Estimate (per video, HuggingFace free tier)

| Service | Cost |
|---|---|
| Claude API (event + script) | ~$0.01–0.03 |
| HuggingFace (images, free tier) | Free |
| Edge TTS (voice) | Free |
| YouTube upload | Free |
| **Total** | **~$0.01–0.03** |

---

## Built With Claude Code

This project was built entirely using [Claude Code](https://claude.ai/claude-code):

- **Agents** — Plan agents for architecture design, Explore agents for codebase analysis, and general-purpose agents for parallelising research across multiple files simultaneously
- **Skills** — `/commit` for structured git commits, `claude-api` skill for Anthropic SDK patterns
- **Multi-session memory** — persistent `MEMORY.md` tracking architecture decisions, tier completion status, and implementation patterns across all development sessions
