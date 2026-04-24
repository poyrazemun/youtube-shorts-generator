# How to Use — Unreal History Bot

Automated YouTube Shorts pipeline. Generates and uploads short videos about strange historical events with zero manual input after setup.

---

## First-Time Setup

### 1. Install dependencies
```bash
py -3.12 -m pip install -r requirements.txt
```

> **Python version:** Use **Python 3.12** (`py -3.12`). Kokoro TTS (the default voice engine) requires Python 3.10–3.12 and will not work on 3.13+.

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

### 5. Install espeak-ng (required for Kokoro TTS)
Download the `.msi` installer from https://github.com/espeak-ng/espeak-ng/releases and install it.
Then add `C:\Program Files\eSpeak NG` to your system PATH.
Verify with: `espeak-ng --version`

> Windows Defender may flag the installer — this is a false positive. Click **More info → Run anyway**.

---

## Daily Usage

### Step 1 — Generate your topic queue (do once, then weekly)
```bash
py -3.12 orchestrator.py --refresh-topics
```
Claude generates 25 topic+keyword combos, scores each on viral potential (1–10), discards anything scoring below 7, and saves the rest sorted best-first to `topics_queue.json`.

Old pending topics are discarded and replaced with fresh ones. Keywords from already-uploaded videos (tracked in `video_registry.json`) are automatically excluded — Claude is told to avoid them, and any that slip through are filtered out, so you never get duplicate topics.

Stale `in_progress` entries (from crashed or interrupted runs older than 2 hours) are automatically reset to `failed` during refresh.

**First time this runs, a browser opens for YouTube OAuth consent** — click Allow.

### Step 2 — View the queue (optional)
```bash
py -3.12 orchestrator.py --list-topics
```
Shows all topics grouped by status with virality scores:
```
  1. [10/10] The Radium Girls — workers ordered to lick radioactive paintbrushes (radium)
  2. [9/10]  Napoleon Routed by Rabbits at His Own Victory Hunt (napoleon)
  3. [8/10]  The Man Who Accidentally Started WWI (assassination)
```

### Step 3 — Run the pipeline (one video per run)
```bash
py -3.12 orchestrator.py --auto
```
Picks the highest-scoring pending topic, runs all 6 pipeline steps, uploads to YouTube.

Alternatively, pick a specific topic by its ID from `--list-topics`:
```bash
py -3.12 orchestrator.py --pick a3f2
```
Runs the full pipeline on that topic. Works with `--no-upload`, `--no-edit`, `--verbose`. If the topic was already marked done or failed, a warning is shown but it runs anyway.

### Step 4 — Check your analytics (after videos get views)
```bash
py -3.12 orchestrator.py --analytics
```
Fetches view counts from YouTube and prints a performance summary.
When you next run `--refresh-topics`, this data is automatically fed to Claude
so it generates more topics like your best performers.

---

## Other Commands

```bash
# Pick a specific topic by ID and run the full pipeline on it
py -3.12 orchestrator.py --pick a3f2

# Pick a specific topic, skip upload
py -3.12 orchestrator.py --pick a3f2 --no-upload

# Wipe the entire topic queue and immediately generate a fresh one (asks for confirmation)
py -3.12 orchestrator.py --clear-topics

# Remove a single topic from the queue by ID (asks for confirmation)
py -3.12 orchestrator.py --delete-topic a3f2

# Test the pipeline without uploading to YouTube
py -3.12 orchestrator.py --auto --no-upload

# Manual mode — specify topic and keyword yourself
py -3.12 orchestrator.py --topic "The Radium Girls" --keyword "radium"

# Manual mode without upload
py -3.12 orchestrator.py --topic "Weird Science" --keyword "invention" --no-upload

# Verbose debug output (shows all internal steps)
py -3.12 orchestrator.py --auto --verbose

# Skip prompt editing pause (automation mode — behaves like before this feature was added)
py -3.12 orchestrator.py --auto --no-edit

# Re-run after a failure (pipeline is resumable — picks up where it left off)
py -3.12 orchestrator.py --auto
```

---

## Visual Testing (no API calls, no cost)

Use `test_video.py` to instantly preview layout changes — subtitle position, font size, CTA overlay — without running the full pipeline.

```bash
py -3.12 test_video.py
```

Output: `test_output/test_video.mp4`

It uses solid-colour placeholder images and `assets/voice_sample.wav` as audio. No Claude, no Replicate, no TTS — just ffmpeg.

**When to use it:** any time you change subtitle positioning (`margin_v` in `captions.py` / `video_assembler.py`), font size, CTA timing, or any other visual parameter. Edit the constant, run the script, check the video.

---

## Changing the Voice

Voice settings are controlled via your `.env` file:

```env
KOKORO_VOICE=bm_george       # voice name (see table below)
KOKORO_LANG_CODE=b           # language code (see table below)
KOKORO_SPEED=1.1             # speaking speed (0.5 = slow, 1.1 = default, 1.5 = fast)
```

### Available Voices

| Voice | Gender | Accent |
|-------|--------|--------|
| `af_heart` | Female | American (warm) |
| `af_bella` | Female | American |
| `af_nova` | Female | American |
| `am_echo` | Male | American |
| `am_eric` | Male | American |
| `am_liam` | Male | American |
| `bf_emma` | Female | British |
| `bf_isabella` | Female | British |
| `bm_george` | Male | British (default) |
| `bm_lewis` | Male | British |

### Available Language Codes

| Code | Language |
|------|----------|
| `a` | American English |
| `b` | British English (default) |
| `e` | Spanish |
| `f` | French |
| `h` | Hindi |
| `i` | Italian |
| `p` | Brazilian Portuguese |
| `j` | Japanese (`pip install misaki[ja]` required) |
| `z` | Mandarin Chinese (`pip install misaki[zh]` required) |

> Make sure `KOKORO_VOICE` and `KOKORO_LANG_CODE` match — e.g. a British voice (`bm_george`) must use lang code `b`.

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
| 2    | Script Generation | Claude writes a viral 20–30s script using one of 5 hook formulas plus a rehook and loop-aware ending |
| 3    | Image Generation  | FLUX generates 5 cinematic 9:16 images (HuggingFace schnell if token set, else Replicate dev, else PIL fallback) |
| 4    | Voice Generation  | Kokoro neural TTS (auto-fallback: Piper → Coqui → Edge TTS)     |
| 5a   | Captions          | Whisper word timestamps or estimation-based SRT                 |
| 5b   | Video Assembly    | ffmpeg: images + audio + captions + "Follow @ThatActuallyHappened11" overlay |
| 6    | YouTube Upload    | Uploads video + thumbnail to your channel                       |

All steps are **resumable** — if a step fails, re-run the same command and it continues from where it stopped.

---

## What Every Video Includes

- **Hook formula**: One of 5 proven formulas (Shocking Fact, False Assumption, Consequence First, Specific Number, Direct Address) chosen by Claude for maximum scroll-stopping power
- **Mid-video rehook**: Claude now writes a retention reset midway through the script so the story re-engages before the payoff
- **Subtitles**: Burned in, white bold text with dark background box, positioned above the YouTube Shorts phone UI. Whisper captions use shorter 3-word cards for faster pacing, while the estimation fallback keeps larger cards for smoother reading
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

## Editing Prompts Before Claude Runs

By default, before each script is generated the pipeline saves the full user prompt to `prompts/<slug>_<event_index>.txt` and pauses:

```
  Prompt saved: prompts/the_radium_girls_radium_0.txt
  Edit it now, then press Enter to send to Claude (Enter without editing uses it as-is)...
```

Open the file in any editor, adjust the wording, add extra constraints, inject context the research missed — then press Enter. The pipeline reads the file back and sends your edited version to Claude. If you leave the file empty, it falls back to the original automatically.

**To skip the pause entirely** (e.g. for unattended automation):
```bash
py -3.12 orchestrator.py --auto --no-edit
```

The `prompts/` folder is gitignored — prompt files are local only.

---

## Troubleshooting

| Problem | Fix |
|---|---|
| `ANTHROPIC_API_KEY is not set` | Add your key to `.env` |
| `YouTube client secrets not found` | Download `client_secrets.json` from Google Cloud Console |
| `ffmpeg not found` | Install ffmpeg and add it to your system PATH |
| `ModuleNotFoundError: kokoro` | Run `py -3.12 -m pip install kokoro>=0.9.4 soundfile` |
| Kokoro falls back to Edge TTS | Make sure you're running with `py -3.12` — Kokoro requires Python 3.10–3.12 |
| `espeak-ng not found` | Install from https://github.com/espeak-ng/espeak-ng/releases and add to PATH |
| Topic queue exhausted | Run `py -3.12 orchestrator.py --refresh-topics` |
| Duplicate video generated | Already fixed — `--refresh-topics` now excludes keywords from `video_registry.json` |
| Topics stuck as `in_progress` | Automatically reset after 2 hours on next `--refresh-topics` |
| Images failing to generate | Check `HUGGINGFACE_API_TOKEN` or `REPLICATE_API_TOKEN` in `.env` — PIL placeholder used as final fallback |
| Step fails mid-pipeline | Just re-run — completed steps are cached and skipped |
| CTA overlay missing or clipped | Check ffmpeg version supports drawtext filter (`ffmpeg -filters \| grep drawtext`) |
| Script cached with old format | Delete `output/<slug>/scripts.json` and re-run to regenerate |
| Want to skip prompt editing | Add `--no-edit` flag — pipeline skips the pause and sends prompts straight to Claude |
