# How to Use — Unreal History Bot

Automated YouTube Shorts pipeline. Generates and uploads short videos about strange historical events with zero manual input after setup.

---

## First-Time Setup

### 1. Install dependencies
```bash
pip install -r requirements.txt
```

### 2. Create your `.env` file in the project root
```env
ANTHROPIC_API_KEY=sk-ant-...
REPLICATE_API_TOKEN=r8_...          # for image generation (~$0.003/image)
YOUTUBE_PRIVACY=private             # start private, change to public when ready
LOG_LEVEL=INFO
```

### 3. Set up YouTube API (one-time)
1. Go to https://console.cloud.google.com
2. Create a project → Enable **YouTube Data API v3**
3. Create **OAuth 2.0 Client ID** (Desktop app) → Download as `client_secrets.json`
4. Place `client_secrets.json` in the project root

### 4. Install ffmpeg
Download from https://ffmpeg.org/download.html and make sure `ffmpeg` is on your PATH.

---

## Daily Usage

### Step 1 — Generate your topic queue (do once, then weekly)
```bash
python orchestrator.py --refresh-topics
```
Claude generates 25 topic+keyword combos, scores each on viral potential (1–10), discards anything scoring below 7, and saves the rest sorted best-first to `topics_queue.json`.

Old pending topics are discarded and replaced with fresh ones. Keywords from already-uploaded videos (tracked in `video_registry.json`) are automatically excluded — Claude is told to avoid them, and any that slip through are filtered out, so you never get duplicate topics.

Stale `in_progress` entries (from crashed or interrupted runs older than 2 hours) are automatically reset to `failed` during refresh.

**First time this runs, a browser opens for YouTube OAuth consent** — click Allow.

### Step 2 — View the queue (optional)
```bash
python orchestrator.py --list-topics
```
Shows all topics grouped by status with virality scores:
```
  1. [10/10] The Radium Girls — workers ordered to lick radioactive paintbrushes (radium)
  2. [9/10]  Napoleon Routed by Rabbits at His Own Victory Hunt (napoleon)
  3. [8/10]  The Man Who Accidentally Started WWI (assassination)
```

### Step 3 — Run the pipeline (one video per run)
```bash
python orchestrator.py --auto
```
Picks the highest-scoring pending topic, runs all 6 pipeline steps, uploads to YouTube.

### Step 4 — Check your analytics (after videos get views)
```bash
python orchestrator.py --analytics
```
Fetches view counts from YouTube and prints a performance summary.
When you next run `--refresh-topics`, this data is automatically fed to Claude
so it generates more topics like your best performers.

---

## Other Commands

```bash
# Test the pipeline without uploading to YouTube
python orchestrator.py --auto --no-upload

# Manual mode — specify topic and keyword yourself
python orchestrator.py --topic "The Radium Girls" --keyword "radium"

# Manual mode without upload
python orchestrator.py --topic "Weird Science" --keyword "invention" --no-upload

# Verbose debug output (shows all internal steps)
python orchestrator.py --auto --verbose

# Re-run after a failure (pipeline is resumable — picks up where it left off)
python orchestrator.py --auto
```

---

## Add Background Music (optional)
Drop royalty-free `.mp3` files into `assets/music/`.
The pipeline randomly picks one track per batch and mixes it into every video at low volume.

Good free sources:
- https://pixabay.com/music/
- https://freemusicarchive.org/

---

## Automate with Windows Task Scheduler
To run daily without touching your PC:

1. Open **Task Scheduler** → Create Basic Task
2. Set trigger to **Daily** at your preferred time
3. Set action to run:
   ```
   python C:\path\to\unreal-history-bot\orchestrator.py --auto
   ```
4. Add a **weekly** task for `--refresh-topics` to keep the topic queue fresh

---

## Pipeline Steps (what happens when you run --auto)

| Step | Name              | What it does                                                    |
|------|-------------------|-----------------------------------------------------------------|
| 1    | Event Discovery   | Claude finds 1 strange real historical event                    |
| 2    | Script Generation | Claude writes a viral 20–30s script using one of 5 hook formulas |
| 3    | Image Generation  | Replicate FLUX.1-schnell generates 5 cinematic 9:16 images      |
| 4    | Voice Generation  | Edge TTS (en-US-ChristopherNeural) narration                    |
| 5a   | Captions          | Whisper word timestamps or estimation-based SRT                 |
| 5b   | Video Assembly    | ffmpeg: images + audio + captions + "Follow @ThatActuallyHappened11" overlay |
| 6    | YouTube Upload    | Uploads video + thumbnail to your channel                       |

All steps are **resumable** — if a step fails, re-run the same command and it continues from where it stopped.

---

## What Every Video Includes

- **Hook formula**: One of 5 proven formulas (Shocking Fact, False Assumption, Consequence First, Specific Number, Direct Address) chosen by Claude for maximum scroll-stopping power
- **Subtitles**: Burned in, white bold text with dark background box, positioned above the YouTube Shorts phone UI
- **CTA overlay**: "Follow @ThatActuallyHappened11" — white text, top-center, visible in the last 3 seconds of every video
- **Background music**: Mixed at low volume if `.mp3` files are present in `assets/music/`

---

## Output Files

```
output/
  <slug>/
    events.json       ← discovered historical events
    scripts.json      ← generated video scripts (includes hook_type field)
    images/           ← AI-generated images per event
    audio/            ← TTS narration audio
    subtitles/        ← captions (.ass + .srt)
    video/            ← final .mp4 files
    thumbnails/       ← YouTube thumbnails (1280x720)
    uploads.json      ← upload results with YouTube video URLs

topics_queue.json     ← topic queue with virality scores (generated by --refresh-topics)
video_registry.json   ← persistent record of all uploaded videos
logs/                 ← daily rotating log files (kept 14 days)
growth/               ← marketing guides (Reddit strategy)
```

---

## Troubleshooting

| Problem | Fix |
|---|---|
| `ANTHROPIC_API_KEY is not set` | Add your key to `.env` |
| `YouTube client secrets not found` | Download `client_secrets.json` from Google Cloud Console |
| `ffmpeg not found` | Install ffmpeg and add it to your system PATH |
| Topic queue exhausted | Run `python orchestrator.py --refresh-topics` |
| Duplicate video generated | Already fixed — `--refresh-topics` now excludes keywords from `video_registry.json` |
| Topics stuck as `in_progress` | Automatically reset after 2 hours on next `--refresh-topics` |
| Images failing to generate | Check `REPLICATE_API_TOKEN` in `.env` — PIL placeholder used as fallback |
| Step fails mid-pipeline | Just re-run — completed steps are cached and skipped |
| CTA overlay missing or clipped | Check ffmpeg version supports drawtext filter (`ffmpeg -filters \| grep drawtext`) |
| Script cached with old format | Delete `output/<slug>/scripts.json` and re-run to regenerate |
