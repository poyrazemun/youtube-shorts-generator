# Unreal History Bot

A fully automated YouTube Shorts pipeline that discovers strange real historical events, generates scripts, creates images, records voiceover, assembles videos, and uploads them to YouTube — all without human input after setup.

Built entirely with **[Claude Code](https://claude.ai/claude-code)**, using its agent and skill system for architecture planning, codebase exploration, and iterative implementation across multiple sessions.



---

## How It Works

```
--refresh-topics        Claude generates 25 topics, scores each for viral potential (1-10),
                        discards anything below 7, filters out already-used keywords,
                        resets stale runs, sorts by score — best topics queued first
       ↓
--auto (run daily via Windows Task Scheduler)
       ↓
  Step 1  event_discovery.py    Claude → 1 strange historical event
  Step 2  script_generator.py   Claude + DuckDuckGo research → viral script with hook formula + rehook + loopable ending + SEO metadata
  Step 3  image_generator.py    Replicate FLUX.1-dev → 5 cinematic 9:16 images
  Step 4  tts_generator.py      Kokoro neural TTS → narration audio (fallback: Piper → Coqui → Edge TTS)
  Step 5a captions.py           Whisper / estimation → word-timed subtitles
  Step 5b video_assembler.py    ffmpeg → 1080×1920 MP4 with burned captions + CTA overlay + background music
  Step 6  youtube_uploader.py   YouTube Data API v3 → upload with thumbnail
       ↓
--analytics             Fetches view counts, feeds performance data back into topic generation
```

Every step is **resumable** — output is cached to disk, so re-running picks up where it left off.

---

## Requirements

- Python 3.12 (Kokoro TTS requires 3.10–3.12)
- ffmpeg on PATH
- espeak-ng on PATH (required by Kokoro)
- Anthropic API key (Claude)
- YouTube Data API v3 OAuth credentials
- Replicate API token (for image generation, ~$0.003/image)

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
REPLICATE_API_TOKEN=r8_...      # for image generation (~$0.003/image)
YOUTUBE_PRIVACY=private
```

**2. YouTube API credentials**

- Google Cloud Console → Enable YouTube Data API v3
- Create OAuth 2.0 Client ID (Desktop app) → download as `client_secrets.json`
- Place in project root

**3. First run (opens browser for YouTube OAuth)**

```bash
py -3.12 orchestrator.py --analytics
```

---

## CLI

```bash
# Automated mode — picks highest-scoring topic from queue, runs full pipeline
py -3.12 orchestrator.py --auto

# Regenerate topic queue (Claude scores + filters 25 topics; run weekly to keep queue fresh)
py -3.12 orchestrator.py --refresh-topics

# View current topic queue with virality scores
py -3.12 orchestrator.py --list-topics

# Fetch YouTube analytics + print performance summary
py -3.12 orchestrator.py --analytics

# Manual mode
py -3.12 orchestrator.py --topic "Strange War Stories" --keyword "battle" [--count N] [--no-upload]

# Pick a specific topic from the queue by ID (shown in --list-topics)
py -3.12 orchestrator.py --pick a3f2

# Wipe entire queue and generate a fresh one (asks for confirmation)
py -3.12 orchestrator.py --clear-topics

# Remove a single topic by ID (asks for confirmation)
py -3.12 orchestrator.py --delete-topic a3f2

# Flags available on all modes
--no-upload    skip YouTube upload, save videos locally
--no-edit      skip prompt editing pause (automation mode)
--verbose      DEBUG-level console logging
```

---

## Topic Virality Scoring

Every time `--refresh-topics` runs, Claude rates each generated topic on a 1–10 virality scale:

| Score | Meaning |
|---|---|
| 9–10 | Sounds completely fake but is true. Debunks a widely-held belief. Famous person in shocking context. |
| 7–8 | Genuinely surprising with strong hook potential. |
| < 7 | **Discarded** — not queued |

Topics are sorted highest score first, so `--auto` always produces the most viral-potential video available.

### Deduplication

Keywords from already-uploaded videos (tracked in `video_registry.json`) are automatically excluded during topic generation — both via the Claude prompt and a post-generation filter. This prevents the pipeline from regenerating videos on topics you've already covered.

### Stale Run Recovery

If a pipeline run crashes or is interrupted, the topic stays `in_progress`. On the next `--refresh-topics`, any `in_progress` entry older than 2 hours is automatically reset to `failed` and replaced with fresh topics.

---

## Hook Formulas

Every script uses one of 5 proven hook formulas chosen by Claude for that specific event:

| Formula | Example |
|---|---|
| SHOCKING_FACT | "A man once sold the Eiffel Tower — twice." |
| FALSE_ASSUMPTION | "Everyone thinks Einstein failed math. He didn't — but his teachers still wanted him gone." |
| CONSEQUENCE_FIRST | "This one telegram started World War One." |
| SPECIFIC_NUMBER | "In 1518, 400 people danced non-stop for 2 months — and couldn't stop." |
| DIRECT_ADDRESS | "You've used this invention today — but its creator was executed for making it." |

Hard-banned openers: "Did you know", "In [year]...", any visual reference.

Scripts now follow a 5-beat retention structure: **Hook → Context → Rehook → Twist → Ending fact**.
The rehook is designed to reset curiosity midway through the short, and the ending fact is prompted to connect back to the opener for better loopability.

## Daily Automation (Windows Task Scheduler)

Run `--auto` daily and `--refresh-topics` weekly via Windows Task Scheduler. See [HOW_TO_USE.md](HOW_TO_USE.md) for the full setup guide.

---

## Image Generation (Priority Order)

| Priority | Backend | Requirement |
|---|---|---|
| 1 | HuggingFace (FLUX.1-schnell) | `HUGGINGFACE_API_TOKEN` in `.env` |
| 2 | **Replicate** (FLUX.1-dev) | `REPLICATE_API_TOKEN` in `.env` (~$0.003/img) |
| 3 | PIL placeholder | Always available (offline fallback) |

Each image is retried up to 3 times with exponential backoff before falling back to the next backend.

---

## Voice Generation (Priority Order)

| Priority | Backend | Requirement |
|---|---|---|
| 1 | **Kokoro** (open-weight neural TTS) | `pip install kokoro>=0.9.4 soundfile` + espeak-ng + Python 3.10–3.12 |
| 2 | Piper TTS (local) | Binary on PATH |
| 3 | Coqui TTS (local) | `pip install TTS` |
| 4 | **Edge TTS** | Included in requirements.txt (always available fallback) |

Default Kokoro voice: `af_heart` (American Female, warm tone).

### Customising Voice

Set in `.env` — no code changes needed:

```env
KOKORO_VOICE=af_heart      # af_heart / am_echo / bf_emma / bm_george / am_liam ...
KOKORO_LANG_CODE=a         # a=American EN, b=British EN, e=Spanish, f=French, h=Hindi, i=Italian, p=Portuguese
KOKORO_SPEED=1.15          # 0.5=slow · 1.15=default Shorts pacing · 1.5=fast
```

> Voice and lang code must match — e.g. `bm_george` requires `KOKORO_LANG_CODE=b`. See [HOW_TO_USE.md](HOW_TO_USE.md) for the full voice list.

---

## Scene Planning & Presets

Between script generation and rendering, a lightweight **scene planning layer**
converts each script into an explicit, inspectable `ScenePlan`. Each narrative
beat becomes its own scene with a role-aware image prompt and its own duration.

```
script  →  scene plan  →  image / render inputs  →  video assembly
```

Videos are rendered as clean static slideshows — no zoompan/motion, and no
on-screen text beyond the burned-in subtitles and the subscribe CTA in the
final seconds.

### Scene roles

The five semantic parts of every script each become a scene with a distinct
visual treatment:

| Role      | Intent                                          |
|-----------|-------------------------------------------------|
| `hook`    | Strong, immediate establishing frame            |
| `context` | Explanatory, informative                        |
| `rehook`  | Mid-story curiosity reset                       |
| `twist`   | Heightened contrast / drama                     |
| `ending`  | Clean closing frame with negative space for CTA |

Each scene carries: `role`, `text`, `duration`, `image_prompt`, and
`visual_hints`. Plans are saved to `output/<slug>/scene_plans/<idx>.json`
and can be hand-edited between runs (the video step reads them back).

### Presets (`--preset`)

Presets bundle per-role prompt-style tokens and duration weights.

| Preset              | Feel                                                         |
|---------------------|--------------------------------------------------------------|
| `documentary_clean` | Archival, restrained palette (default)                       |
| `dramatic_history`  | Chiaroscuro, bold contrast, cinematic shadows                |
| `viral_fact_card`   | Saturated, TikTok-style punchy grading                       |

```bash
python orchestrator.py --auto --preset dramatic_history
python orchestrator.py --topic "Strange Moments" --keyword war --preset viral_fact_card
```

Omitting `--preset` uses `config.DEFAULT_SCENE_PRESET` (defaults to
`documentary_clean`). All existing CLI flags continue to work unchanged.

### Extending

- **New preset** — add a `Preset` to `pipeline/presets.py` and register it in
  `PRESETS`. It's picked up by `--preset` automatically.

---

## Output Format

- **Resolution**: 1080×1920 (9:16 vertical)
- **Codec**: H.264, AAC 128kbps, 24fps
- **Duration**: 20–30 seconds
- **Subtitles**: Burned in, white bold text, semi-transparent background box, positioned above YouTube Shorts UI. Whisper captions use shorter 3-word cards for faster pacing, while the estimation fallback keeps larger cards for smoother reading.
- **CTA Overlay**: "Follow @ThatActuallyHappened11" — white text, top-center, appears in last 3 seconds
- **Thumbnail**: 1280×720 PNG, uploaded to YouTube

---

## Output Structure

```
output/
  <slug>/
    events.json       step 1 — discovered event
    scripts.json      step 2 — script + SEO metadata + hook_type
    scene_plans/      step 2.5 — per-event ScenePlan JSON (role-aware scenes + prompts)
    images/           step 3 — one PNG per scene (role-aware prompts)
    audio/            step 4 — narration WAV
    subtitles/        step 5a — .ass + .srt caption files
    video/            step 5b — assembled MP4
    thumbnails/       step 6 — YouTube thumbnail
    uploads.json      step 6 — video IDs + URLs
    state.json        per-step completion ledger

topics_queue.json     topic queue with virality scores (persisted in repo for CI)
video_registry.json   persistent record of all uploaded videos (for analytics)
logs/                 daily rotating logs (14-day retention)
assets/music/         drop royalty-free .mp3 files here for background music
growth/               marketing strategy guides (Reddit strategy, etc.)
```

---

## Cost Estimate (per video)

| Service | Cost |
|---|---|
| Claude API (topic scoring + event + script) | ~$0.02–0.04 |
| Replicate FLUX.1-dev (5 images) | ~$0.015 |
| Kokoro TTS (voice) | Free |
| YouTube upload | Free |
| **Total** | **~$0.03–0.05** |

---

## Built With Claude Code

This project was built entirely using [Claude Code](https://claude.ai/claude-code):

- **Agents** — Plan agents for architecture design, Explore agents for codebase analysis, and general-purpose agents for parallelising research across multiple files simultaneously
- **Skills** — `/commit` for structured git commits, `claude-api` skill for Anthropic SDK patterns
- **Multi-session memory** — persistent `MEMORY.md` tracking architecture decisions, tier completion status, and implementation patterns across all development sessions
