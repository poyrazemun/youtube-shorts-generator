# Unreal History Bot

[![CI](https://github.com/poyrazemun/youtube-shorts-generator/actions/workflows/ci.yml/badge.svg)](https://github.com/poyrazemun/youtube-shorts-generator/actions/workflows/ci.yml) ![Python](https://img.shields.io/badge/python-3.12-blue.svg) ![Ruff](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/ruff/main/assets/badge/v2.json) [![License: MIT](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE) ![Last commit](https://img.shields.io/github/last-commit/poyrazemun/youtube-shorts-generator)

> **An autonomous YouTube Shorts channel about strange real history.** Run it on a daily schedule and the channel runs itself — topics, scripts, images, voiceover, subtitles, upload.

📺 **See it in action:** [@ThatActuallyHappened11 on YouTube](https://www.youtube.com/@ThatActuallyHappened11) — the live channel this repo runs. **Click the thumbnail to watch a sample Short produced end-to-end by this pipeline.**

You set it up once. From then on, one command per day publishes one short. Topics are picked from a Claude-generated queue scored for virality, scripts use proven hook formulas, images come from FLUX, the voice is Kokoro TTS, and YouTube performance data feeds back into the next batch of topics so the channel learns what works.

---

## Highlights

- **Virality-scored topic queue** — Claude rates 25 topic ideas 1–10; only ≥7 ship, sorted best-first.
- **Content-safety pre-check** — every script is evaluated against YouTube's demotion rules before any image-generation spend; failed scripts halt the pipeline and are marked failed in the queue.
- **Scene-aware image prompts** — role + preset system; not 5 generic pictures, but a planned shot list (intro / context / twist / payoff).
- **Whisper-timed subtitles** — burned-in 3-word cards synced to the audio, with a CTA overlay in the last 3 seconds.
- **Analytics feedback loop** — `--analytics` summarises which keywords and hook types perform best; those signals are injected into the next `--refresh-topics` prompt.
- **Per-run cost + timing tracker** — `output/<slug>/cost.json` + a chronological `cost_ledger.txt` with running totals.
- **Fully resumable** — every step caches its output. Re-run after any failure and the pipeline picks up where it stopped.

---

## Table of Contents

- [Quick Start](#quick-start)
- [How It Works](#how-it-works)
- [Requirements](#requirements)
- [Setup](#setup)
- [CLI](#cli)
- [Topic Virality Scoring](#topic-virality-scoring)
  - [Content Safety Check (Step 2.5)](#content-safety-check-step-25)
  - [Cost & Timing Tracking](#cost--timing-tracking)
  - [Analytics Feedback Loop](#analytics-feedback-loop)
- [Hook Formulas](#hook-formulas)
- [Daily Automation](#daily-automation-windows-task-scheduler)
- [Image / Voice Generation](#image-generation-priority-order)
- [Scene Planning & Presets](#scene-planning--presets)
- [Output Format & Structure](#output-format)
- [Built With Claude Code](#built-with-claude-code)

---

## Quick Start

```bash
git clone https://github.com/poyrazemun/youtube-shorts-generator && cd youtube-shorts-generator
py -3.12 -m pip install -r requirements.txt
cp .env.example .env                              # then add ANTHROPIC_API_KEY + REPLICATE_API_TOKEN
py -3.12 orchestrator.py --refresh-topics         # generate the topic queue
py -3.12 orchestrator.py --auto                   # publish one video
```

Full setup (ffmpeg, espeak-ng, YouTube OAuth) is in [Setup](#setup) below.

> The commands use `py -3.12` (the Windows Python launcher). On macOS or Linux, replace it with `python3.12` everywhere.

---

## How It Works

The pipeline is a six-step CLI: pick a topic from the scored queue, write the script, generate images, synthesise voice, burn captions, upload. Every step caches its output, so re-running picks up where it stopped.

**Cost per video — measured from a real run** (`output/<slug>/cost.json`):

| Component | Cost |
|---|---|
| Claude (event + script + content safety, Sonnet 4.6) | ~$0.03 |
| Image generation — HuggingFace FLUX.1-schnell (`HUGGINGFACE_API_TOKEN` set) | **Free** |
| Image generation — Replicate FLUX.1-dev (5 × $0.025) | $0.125 |
| Kokoro TTS, captions, ffmpeg assembly, YouTube upload | Free |
| **Total: HuggingFace path** | **~$0.03** |
| **Total: Replicate path** | **~$0.16** |

Switching to HuggingFace saves about 80% per video. The Replicate path is the safer fallback (no rate limits, consistent quality on FLUX.1-dev) but you pay per image.

<details>
<summary><b>Detailed pipeline diagram</b></summary>

```
--refresh-topics        Claude generates 25 topics, scores each for viral potential (1-10),
                        discards anything below 7, filters out already-used keywords,
                        resets stale runs, sorts by score — best topics queued first
       ↓
--auto (run daily via Windows Task Scheduler)
       ↓
  Step 1   event_discovery.py    Claude → 1 strange historical event
  Step 2   script_generator.py   Claude + DuckDuckGo research → viral script with hook formula + rehook + loopable ending + SEO metadata
  Step 2.5 content_safety.py     Claude evaluates the script vs. YouTube demotion rules; halts before image spend on a fail
  Step 3   image_generator.py    FLUX (HuggingFace schnell, or Replicate dev) → 5 cinematic 9:16 images
  Step 4   tts_generator.py      Kokoro neural TTS → narration audio (fallback: Piper → Coqui → Edge TTS)
  Step 5a  captions.py           Whisper / estimation → word-timed subtitles
  Step 5b  video_assembler.py    ffmpeg → 1080×1920 MP4 with burned captions + CTA overlay + background music
  Step 6   youtube_uploader.py   YouTube Data API v3 → upload with thumbnail
       ↓
--analytics             Fetches view counts, feeds performance data back into topic generation
```

</details>

---

## Requirements

- Python 3.12 (Kokoro TTS requires 3.10–3.12)
- ffmpeg on PATH
- espeak-ng on PATH (required by Kokoro)
- Anthropic API key (Claude)
- YouTube Data API v3 OAuth credentials
- Replicate API token (for image generation, ~$0.025/image with FLUX.1-dev — see [Cost](#how-it-works))

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
REPLICATE_API_TOKEN=r8_...      # ~$0.025/image with FLUX.1-dev (5 images = $0.125 per video)
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

# Dry run — validate pipeline wiring end-to-end with zero API spend
# (skips Claude, forces PIL images, skips YouTube upload; topic/keyword optional)
py -3.12 orchestrator.py --dry-run

# Flags available on all modes
--no-upload    skip YouTube upload, save videos locally
--no-edit      skip prompt editing pause (automation mode)
--verbose      DEBUG-level console logging
--dry-run      skip Claude + force PIL images + skip upload (no API spend)
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

### Content Safety Check (Step 2.5)

Between script generation and image generation, every script is run through one Claude call that evaluates it against `growth/youtube-restriction-rules.md`. If the script would be demoted, age-restricted, demonetized, or removed by YouTube — most importantly Rule 4 (conspiracy framing), Rule 5 (forbidden categories: suicide methods, sexual violence, harm to minors, terrorism glorification, false claims about living people), and Rule 6 (graphic gore as focal point) — the pipeline halts **before** spending on image generation. The topic is marked `failed` in the queue with the violated rule, so re-running `--auto` picks the next pending topic.

The full per-script verdict is saved to `output/<slug>/safety.json` for audit. Safety check failures on infrastructure (Claude API down, malformed JSON response) fail-open with a warning — only a successful `"fail"` verdict from Claude halts the run, so an offline Claude doesn't block your pipeline. The check is skipped under `--dry-run` (zero API spend stays zero).

### Cost & Timing Tracking

Every successful run records per-step wall-clock + Claude token usage + image-generation counts (per provider) and writes two artifacts:

- **`output/<slug>/cost.json`** — full breakdown for that one video (steps, timings, tokens, image counts, USD totals).
- **`output/cost_ledger.txt`** — chronological one-line-per-video append-only log with a `TOTAL` footer recomputed each run. Re-running the same slug replaces the existing row instead of duplicating it.

Both files are gitignored. After each successful run a one-line summary prints to the console:

```
Pipeline finished in 200s, ~$0.1552 spend (Claude $0.0302, images 5×replicate $0.1250)
```

**Updating pricing when rates change**

Pricing rates live in `config.py`:

- `CLAUDE_PRICING` — `{model_id: {"input": $/MTok, "output": $/MTok}}`. If Anthropic changes prices or you switch models, edit the dict directly. Unknown models are recorded as `$0` (with a warning logged), so an unset model won't crash a run.
- `IMAGE_PRICING` — `{provider: $/image}`. Replicate and HuggingFace rates are also env-overridable via `IMAGE_COST_REPLICATE` and `IMAGE_COST_HUGGINGFACE` in `.env`, so you can tweak rates without touching code.

**Switching image providers**

If you swap providers (e.g. Replicate → HuggingFace, or add a new one), update both:

1. The actual provider call in `pipeline/image_generator.py` (`detect_backend()` and the per-backend functions).
2. `IMAGE_PRICING` in `config.py` — add the new provider's per-image cost so cost tracking stays accurate. The provider key recorded into `cost.json` is whatever string `image_generator` passes to `tracker.record_image()`, so keep the names aligned.

### Analytics Feedback Loop

`--analytics` fetches view/like counts for every uploaded video and saves them to `output/analytics.json` along with two derived signals (each requires ≥ 2 videos to be considered, so single uploads can't poison the ranking):

- **Top / worst keywords** — average views grouped by `keyword`
- **Hook type performance** — average views grouped by the `hook_type` Claude tagged on each script

The next time `--refresh-topics` runs, those signals are flattened into a plain-English hint string and injected into Claude's topic-generation prompt — for example: _"Top performing keywords by average views: napoleon (12,400 avg, 3 videos). Hook type performance: FALSE_ASSUMPTION (9,800 avg, 4 videos). Prefer FALSE_ASSUMPTION hooks when it fits the story."_ Claude is told to bias new topics toward winning patterns and avoid losing ones.

The exact hint string Claude received is persisted into `topics_queue.json` as `performance_hints_used`, so you can audit afterwards which signal shaped the queue. Both `--analytics` and `--refresh-topics` now print the hint string they are about to send so the loop is visible from the CLI.

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
| 2 | **Replicate** (FLUX.1-dev) | `REPLICATE_API_TOKEN` in `.env` (~$0.025/img) |
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

Default Kokoro voice: `bm_george` (British Male) at `KOKORO_SPEED=1.1`.

### Customising Voice

Set in `.env` — no code changes needed:

```env
KOKORO_VOICE=bm_george     # af_heart / am_echo / bf_emma / bm_george / am_liam ...
KOKORO_LANG_CODE=b         # a=American EN, b=British EN, e=Spanish, f=French, h=Hindi, i=Italian, p=Portuguese
KOKORO_SPEED=1.1           # 0.5=slow · 1.1=default Shorts pacing · 1.5=fast
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
    images/           step 3 — one PNG per scene + img_N.txt sidecar with the exact prompt sent to the backend
    audio/            step 4 — narration WAV
    subtitles/        step 5a — .ass + .srt caption files
    video/            step 5b — assembled MP4
    thumbnails/       step 6 — YouTube thumbnail
    uploads.json      step 6 — video IDs + URLs
    state.json        per-step completion ledger

topics_queue.json     topic queue with virality scores (local-only, git-ignored)
video_registry.json   persistent record of all uploaded videos (local-only, git-ignored)
logs/                 daily rotating logs (14-day retention)
assets/music/         drop royalty-free .mp3 files here for background music
growth/               marketing strategy guides (Reddit strategy, etc.)
```

---

## Built With Claude Code

This project was built entirely using [Claude Code](https://claude.ai/claude-code):

- **Agents** — Plan agents for architecture design, Explore agents for codebase analysis, and general-purpose agents for parallelising research across multiple files simultaneously
- **Multi-session memory** — persistent `MEMORY.md` tracking architecture decisions, tier completion status, and implementation patterns across all development sessions
