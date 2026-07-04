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
REPLICATE_API_TOKEN=r8_...          # ~$0.025/image with FLUX.1-dev (5 images = $0.125 per video)
YOUTUBE_PRIVACY=private             # start private, change to public when ready
LOG_LEVEL=INFO

# Optional — premium hosted voice (else free Kokoro is used). See "Using ElevenLabs" below.
ELEVENLABS_ENABLED=true             # flat $5/mo Starter plan; omit/false to stay on Kokoro
ELEVENLABS_API_KEY=sk_...           # from elevenlabs.io → Settings → API Keys
ELEVENLABS_VOICE_ID=...             # copied from the ElevenLabs voice library
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

> Only needed for the **Kokoro** voice path. If you run with ElevenLabs enabled (see below), espeak-ng
> isn't used — but it's still worth installing so Kokoro works as the offline fallback.

### 6. (Optional) Enable ElevenLabs — premium hosted voice
Kokoro is the free, zero-config default. For the most human-sounding narration, use ElevenLabs:

1. Subscribe to the **Starter plan ($5/mo)** at https://elevenlabs.io (required for commercial rights).
2. Settings → **API Keys** → create a key (`sk_...`, shown once — copy it).
3. **Voices → Voice Library**: filter by *Narrative & Story* / accent / gender, preview voices,
   audition your candidates on a real script, then **Copy Voice ID** of the one you pick.
4. Put all three in `.env`: `ELEVENLABS_ENABLED=true`, `ELEVENLABS_API_KEY`, `ELEVENLABS_VOICE_ID`.

It's **env-gated**: with `ELEVENLABS_ENABLED=true` and a key set, Step 4 uses ElevenLabs; otherwise it
falls back to Kokoro. Model defaults to `eleven_multilingual_v2` (best quality); override with
`ELEVENLABS_MODEL=eleven_flash_v2_5` to halve credit usage at near-equal English quality.

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

**Content safety check (between Step 2 and Step 3):** the pipeline runs every script through one Claude call that evaluates it against `growth/youtube-restriction-rules.md`. If the script would be demoted, age-restricted, or removed by YouTube, the pipeline **halts before any image-generation spend** — the topic is marked `failed` in the queue with the violated rule, and re-running `--auto` simply moves to the next pending topic. The verdicts for each script are saved to `output/<slug>/safety.json`. Safety check failures on infrastructure (Claude API down, malformed JSON) fail-open with a warning logged — only a successful "fail" verdict halts the run.

After each successful run the pipeline prints a one-line cost summary, e.g.:
```
  Pipeline finished in 127s, ~$0.1736 spend (Claude $0.0486, images 5×replicate $0.1250)
```
That Claude figure covers every Claude call: Sonnet (event + script + safety, ~$0.034) plus the two
Haiku passes — the historical fact-check (~$0.0025) and localizations (~$0.012). Those Claude steps
run on every video and total ~$0.05.

On top of that you pick **one** image backend and **one** voice — they're either/or alternatives, not
all required:
- **Image backend:** HuggingFace FLUX.1-schnell is **free**; Replicate FLUX.1-dev is ~$0.125 (5 images).
- **Voice:** Kokoro (and Piper / Coqui / Edge) are **free** and local; ElevenLabs is an optional flat
  $5/mo subscription, **not** a per-render charge.

So a full run is **~$0.05/video** on the free HuggingFace + Kokoro combo, or **~$0.17/video** if you use
Replicate for images. The voice choice doesn't change the per-video number. The bare minimum to run the
pipeline is just an Anthropic API key (plus a YouTube OAuth credential for uploading).

The full per-step breakdown (timings, token counts, image counts per provider) is saved to `output/<slug>/cost.json`, and one row is appended to `output/cost_ledger.txt` so you can scroll back through every video you have ever run with running totals at the bottom. Both files are gitignored.

Alternatively, pick a specific topic by its ID from `--list-topics`:
```bash
py -3.12 orchestrator.py --pick a3f2
```
Runs the full pipeline on that topic. Works with `--no-upload`, `--no-edit`, `--verbose`, `--script-check`. If the topic was already marked done or failed, a warning is shown but it runs anyway.

### Step 4 — Check your analytics (after videos get views)
```bash
py -3.12 orchestrator.py --analytics
```
Fetches view counts from YouTube and prints a performance summary including:

- **Per-video breakdown** sorted by views
- **Top keywords** by average views (only keywords with ≥ 2 videos count, so single uploads can't skew the ranking)
- **Hook type performance** (also gated at ≥ 2 videos)
- **Hints sent to Claude on next `--refresh-topics`** — the exact plain-English string the next refresh will inject into the topic-generation prompt, so you can see what Claude is being told before you regenerate

When you run `--refresh-topics`, that hint string is automatically prepended to Claude's prompt, telling it to bias toward your top keywords/hook types and avoid the worst ones. The string used is also saved into `topics_queue.json` (`performance_hints_used`) so you can audit later which signal shaped a given batch.

```bash
py -3.12 orchestrator.py --refresh-topics
```
Now also prints the hints it just sent to Claude, so the feedback loop is visible end-to-end:

```
  Hints sent to Claude:
  ────────────────────────────────────────────────────────
    Total channel videos analyzed: 12.
    Top performing keywords by average views: napoleon (12,400 avg, 3 videos).
    Hook type performance (best to worst): FALSE_ASSUMPTION (9,800 avg, 4 videos). Prefer FALSE_ASSUMPTION hooks when it fits the story.
```

---

## Other Commands

```bash
# Pick a specific topic by ID and run the full pipeline on it
py -3.12 orchestrator.py --pick a3f2

# Pick a specific topic, skip upload
py -3.12 orchestrator.py --pick a3f2 --no-upload

# Wipe the entire topic queue and immediately generate a fresh one (asks for confirmation).
# Like --refresh-topics, this uses the latest analytics hints and prints them back so you can see what Claude was told.
py -3.12 orchestrator.py --clear-topics

# Remove a single topic from the queue by ID (asks for confirmation)
py -3.12 orchestrator.py --delete-topic a3f2

# Test the pipeline without uploading to YouTube
py -3.12 orchestrator.py --auto --no-upload

# Dry run — skip Claude + force PIL images + skip upload (zero API spend)
py -3.12 orchestrator.py --dry-run

# Manual mode — specify topic and keyword yourself
py -3.12 orchestrator.py --topic "The Radium Girls" --keyword "radium"

# Manual mode without upload
py -3.12 orchestrator.py --topic "Weird Science" --keyword "invention" --no-upload

# Verbose debug output (shows all internal steps)
py -3.12 orchestrator.py --auto --verbose

# Skip prompt editing pause (automation mode — behaves like before this feature was added)
py -3.12 orchestrator.py --auto --no-edit

# Review the spoken narration before any image/TTS spend — pauses after
# scripts.json is written so you can fact-check / edit the text by hand
py -3.12 orchestrator.py --auto --script-check

# Re-run after a failure (pipeline is resumable — picks up where it left off)
py -3.12 orchestrator.py --auto
```

---

## Testing Without API Spend

Two tools, different niches.

### `--dry-run` — full pipeline, zero API spend

```bash
py -3.12 orchestrator.py --dry-run
```

Runs every step end-to-end with no paid calls: skips Claude (uses a hardcoded fixture event + script), forces the PIL image backend, and skips YouTube upload. Topic/keyword default to a fixture value if omitted.

Output goes to `output/dry_run_dryrun/` as with any normal run.

**When to use it:** after any structural change to the orchestrator, a step, or how steps talk to each other. It validates wiring — does the full pipeline still assemble a video end-to-end — without spending on Claude or Replicate.

### `test_video.py` — video assembly only, custom images

```bash
py -3.12 test_video.py
```

Drop PNG/JPG files directly into `test_output/`, then run. It uses your images + `assets/voice_sample.wav` as audio + an estimation-based SRT. Only the ffmpeg assembly step runs — no PIL rendering, no TTS synth. Output: `test_output/test_video.mp4`.

**When to use it:** when you've generated candidate images in another tool (Midjourney, ComfyUI, etc. — using the `img_N.txt` prompts from a prior real run) and want to preview how they'd look assembled into a short. Also fastest for pure layout tweaks (subtitle `margin_v`, font size, CTA timing) since it skips all generation steps.

---

## Changing the Voice

Voice settings are controlled via your `.env` file. There are two backends.

### Option A — ElevenLabs (premium, hosted)

The most human-sounding option. Top of the priority chain when enabled:

```env
ELEVENLABS_ENABLED=true
ELEVENLABS_API_KEY=sk_...                    # from elevenlabs.io → Settings → API Keys
ELEVENLABS_VOICE_ID=...                       # Voice Library → pick a voice → Copy Voice ID
ELEVENLABS_MODEL=eleven_multilingual_v2       # quality; or eleven_flash_v2_5 for ½ the credits
```

To change the voice, browse **Voices → Voice Library** at elevenlabs.io (filter by *Narrative &
Story*, accent, gender), audition candidates on a real script, then copy the new Voice ID into
`ELEVENLABS_VOICE_ID`. No code changes needed. Set `ELEVENLABS_ENABLED=false` (or remove the key) to
fall back to Kokoro.

### Option B — Kokoro (free, offline default)

Used automatically when ElevenLabs isn't enabled:

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
   python C:\path\to\youtube-shorts-generator\orchestrator.py --auto
   ```
4. Add a **weekly** task for `--refresh-topics` to keep the topic queue fresh

---

## Pipeline Steps (what happens when you run --auto)

| Step | Name              | What it does                                                    |
|------|-------------------|-----------------------------------------------------------------|
| 1    | Event Discovery   | Claude finds 1 strange real historical event                    |
| 2    | Script Generation | Claude writes a viral 20–30s script using one of 5 hook formulas plus a rehook and loop-aware ending. Also outputs a `scene_visuals` block — one distinct camera-subject description per beat — so the 5 images aren't visual duplicates of each other. A strict historical-accuracy directive in the system prompt + a swift Haiku fact-check pass (`FACTCHECK_ENABLED`) correct pop-history myths / fabricated drama in the 5 beats before saving. |
| 2b   | Localization      | Haiku 4.5 translates title + description into es / pt / hi / id and caches them in `scripts.json`. Uploaded as YouTube `localizations` so non-English viewers see localized metadata. Failures fall back to English-only. |
| 3    | Image Generation  | FLUX generates 5 cinematic 9:16 images (HuggingFace schnell if token set, else Replicate dev, else PIL fallback) |
| 4    | Voice Generation  | ElevenLabs (if enabled) → Kokoro neural TTS (auto-fallback: Piper → Coqui → Edge TTS) |
| 5a   | Captions          | Whisper word timestamps or estimation-based SRT                 |
| 5b   | Video Assembly    | ffmpeg: images + audio + captions + "Follow @ThatActuallyHappened11" overlay |
| 6    | YouTube Upload    | Uploads video to your channel (Education category, standard YouTube license). Also uploads the SRT as a real caption track via `captions.insert` (selectable in the CC menu, not just burned pixels). |

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
    scripts.json      ← generated video scripts (includes hook_type, scene_visuals, localizations, and a fact_check audit field per script)
    images/           ← AI-generated images per event + img_N.txt sidecar with the exact prompt sent to the backend (paste into other tools to compare output)
    audio/            ← TTS narration audio
    subtitles/        ← captions (.ass + .srt)
    video/            ← final .mp4 files
    uploads.json      ← upload results with YouTube video URLs
    safety.json       ← per-script verdicts from the content-safety pre-check
    cost.json         ← per-step timings + token usage + image counts + USD totals (gitignored)

output/cost_ledger.txt ← chronological one-line-per-video cost log with running totals (gitignored)
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

## Reviewing the Spoken Script Before Render (`--script-check`)

Where `--no-edit` is about the *input* to Claude, `--script-check` is about the *output*. It lets you read and fix the exact narration that will be spoken in the video — the `full_script` and beat fields (`hook`, `context`, `rehook`, `twist`, `ending_fact`) — and confirm it's 100% accurate before any image-generation or TTS spend.

Run with the flag on any mode:
```bash
py -3.12 orchestrator.py --auto --script-check
```

The pipeline generates the scripts, writes them to disk, then pauses:
```
Pipeline paused. Script saved to 'output/<slug>/scripts.json'. Review or edit the file now. Press [ENTER] to reload and continue...
```

Open `output/<slug>/scripts.json` in any editor, read the narration, fix any wording or factual errors, and **save the file**. Then press ENTER — the pipeline re-reads `scripts.json` from disk so every downstream step (content safety, scene planning, images, TTS, subtitles) uses your edited text.

Notes:
- The pause happens **before** the content-safety check, so your edits are safety-checked too.
- It's skipped under `--dry-run` (zero API spend stays zero).
- If you don't edit the file, just press ENTER to continue with the generated script as-is.

---

## Troubleshooting

| Problem | Fix |
|---|---|
| `ANTHROPIC_API_KEY is not set` | Add your key to `.env` |
| `YouTube client secrets not found` | Download `client_secrets.json` from Google Cloud Console |
| `ffmpeg not found` | Install ffmpeg and add it to your system PATH |
| `ModuleNotFoundError: kokoro` | Run `py -3.12 -m pip install kokoro>=0.9.4 soundfile` |
| Kokoro falls back to Edge TTS | Make sure you're running with `py -3.12` — Kokoro requires Python 3.10–3.12 |
| ElevenLabs ignored (uses Kokoro) | Set both `ELEVENLABS_ENABLED=true` **and** `ELEVENLABS_API_KEY` in `.env` — either missing falls back to Kokoro |
| ElevenLabs API 401 / 429 | 401 = bad/rotated key; 429 = out of monthly credits (check Settings → Usage) or rate-limited — the call retries 3× then errors |
| `espeak-ng not found` | Install from https://github.com/espeak-ng/espeak-ng/releases and add to PATH |
| Topic queue exhausted | Run `py -3.12 orchestrator.py --refresh-topics` |
| Duplicate video generated | Already fixed — `--refresh-topics` now excludes keywords from `video_registry.json` |
| Topics stuck as `in_progress` | Automatically reset after 2 hours on next `--refresh-topics` |
| Images failing to generate | Check `HUGGINGFACE_API_TOKEN` or `REPLICATE_API_TOKEN` in `.env` — PIL placeholder used as final fallback |
| Step fails mid-pipeline | Just re-run — completed steps are cached and skipped |
| CTA overlay missing or clipped | Check ffmpeg version supports drawtext filter (`ffmpeg -filters \| grep drawtext`) |
| Script cached with old format | Delete `output/<slug>/scripts.json` and re-run to regenerate |
| Want to skip prompt editing | Add `--no-edit` flag — pipeline skips the pause and sends prompts straight to Claude |
| Want to read/fix the spoken narration before render | Add `--script-check` flag — pipeline pauses after `scripts.json` is written so you can edit it, then reloads on ENTER |
