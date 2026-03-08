# Unreal History Bot

Automated YouTube Shorts generator for the "Unreal History" channel —
real historical events that sound unbelievable.

## Quick Start

```bash
# 1. Install dependencies
pip install -r requirements.txt

# 2. Configure environment
cp .env.example .env
# Edit .env — required: ANTHROPIC_API_KEY
# Optional but recommended: HUGGINGFACE_API_TOKEN (free at huggingface.co)

# 3. Install ffmpeg (required)
# Windows: winget install ffmpeg
# macOS:   brew install ffmpeg
# Linux:   sudo apt install ffmpeg

# 4. Run
python orchestrator.py --topic "Strange Moments in History" --keyword "war" --count 5

# Skip upload while testing
python orchestrator.py --topic "Strange Moments in History" --keyword "war" --count 1 --no-upload
```

## Pipeline Steps

| Step | Module | Description | Tool |
|------|--------|-------------|------|
| 1 | `event_discovery.py` | Generate historical events | Claude API |
| 2 | `script_generator.py` | Write viral Short scripts + SEO metadata | Claude API |
| 3 | `image_generator.py` | Generate 5 images per event | A1111 / ComfyUI / HuggingFace / Pollinations / PIL |
| 4 | `tts_generator.py` | Generate voiceover audio | Piper / Coqui / Edge TTS |
| 5a | `captions.py` | Generate word-timed captions | Whisper / estimation fallback |
| 5b | `video_assembler.py` | Assemble final MP4 with burned subtitles | ffmpeg |
| 6 | `youtube_uploader.py` | Upload to YouTube | YouTube Data API v3 |

## Image Generation (Priority Order)

1. **Automatic1111** (local) — Start with `--api` flag: `python webui.py --api`
2. **ComfyUI** (local) — Start normally, API is enabled by default
3. **HuggingFace** (remote, free tier) — Add `HUGGINGFACE_API_TOKEN` to `.env`
   Model: `black-forest-labs/FLUX.1-schnell`
4. **Pollinations.AI** (remote, no key) — Free but occasionally unreliable
5. **PIL placeholder** (offline, always works) — Dark gradient + event text; guaranteed fallback

Each image is retried up to 3 times with exponential backoff before falling back to the next backend.

## Voice Generation (Priority Order)

1. **Piper TTS** (local) — Download from https://github.com/rhasspy/piper/releases
2. **Coqui TTS** (local) — `pip install TTS`
3. **Edge TTS** (online, free) — Microsoft Neural voices; `en-US-ChristopherNeural`. Installed via requirements.txt.

All backends output WAV. Edge TTS outputs MP3 which is auto-converted to WAV via ffmpeg.

## Captions

Captions are generated in Step 5a and burned into the video in Step 5b.

| Mode | Requirement | Quality |
|------|-------------|---------|
| **Whisper** | `pip install openai-whisper` (~500MB PyTorch) | Real word timestamps, in-sync |
| **Estimation** | Nothing (always available) | Proportional timing, may drift |

The pipeline auto-detects which mode to use. Install Whisper to upgrade:
```bash
pip install openai-whisper
```

## YouTube Setup

1. Go to [Google Cloud Console](https://console.cloud.google.com/)
2. Create a project → Enable **YouTube Data API v3**
3. Create OAuth 2.0 credentials (Desktop app type)
4. Download the JSON → save as `client_secrets.json`
5. First run will open your browser for authorization
6. Token saved to `credentials.json` for future runs

## CLI Options

```
python orchestrator.py --topic TOPIC --keyword KEYWORD [--count N] [--no-upload]

Options:
  --topic      Channel topic / batch description
  --keyword    Keyword to focus event discovery
  --count      Number of videos to generate (default: 3, max: 20)
  --no-upload  Skip YouTube upload, save videos locally only
```

## Resumable Pipeline

Each step saves output to `output/<slug>/` before the next step runs.
If a step fails, simply re-run the same command — completed steps are skipped.
Per-event completion is also tracked in `state.json`.

```
output/
  strange_moments_in_history_war/
    events.json              Step 1 cache
    scripts.json             Step 2 cache
    images/0/img_0..4.png    Step 3 cache (5 images per event)
    audio/0.wav              Step 4 cache
    subtitles/0.srt          Step 5a cache (estimation SRT)
    subtitles/0_captions.ass Step 5a cache (Whisper ASS, if installed)
    video/0.mp4              Step 5b cache
    state.json               Per-event stage completion ledger
    uploads.json             Step 6 upload IDs and URLs
  strange_moments_in_history_war_0_Event_Title.mp4   Final output copy
```

To clear cache and re-run from scratch, delete the slug's folder:
```bash
rm -rf output/strange_moments_in_history_war
```

## Output Format

- **Video**: 1080×1920 (9:16 vertical), H.264, 24fps
- **Audio**: AAC 128kbps, Edge TTS `en-US-ChristopherNeural`
- **Duration**: 20–30 seconds (matches actual audio length)
- **Subtitles**: Burned in, white bold text, 50% transparent black box background

## Resilience

All external API calls use exponential backoff retry (3 attempts, 2s → 4s → 8s delay):
- Claude API (event + script generation)
- HuggingFace Inference API (image generation)
- Pollinations.AI (image fallback)
- Edge TTS (audio generation)
- YouTube upload

## Cost Estimate (per 5 videos, using HuggingFace free tier)

| Service | Cost |
|---------|------|
| Claude API (event + script gen) | ~$0.05–0.15 |
| HuggingFace (image gen, free tier) | Free |
| Edge TTS (voiceover) | Free |
| YouTube upload | Free |
| **Total** | **~$0.05–0.15** |

Using local A1111 or ComfyUI keeps image cost at $0.
