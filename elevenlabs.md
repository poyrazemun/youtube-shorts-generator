# ElevenLabs TTS Integration — Plan & Decision

**Date:** 2026-06-03
**Decision:** Adopt **ElevenLabs** (Starter plan, $5/mo) as a new, env-gated TTS backend.
Kokoro stays as the free offline fallback. ---->>>>TOTALLY OPTIONAL

> This file supersedes the earlier `voicebox_plan.md`. VoiceBox and EC2-GPU were investigated
> and rejected — see §4. The short version: at Shorts length (~90 words/~550 chars), TTS cost is
> a rounding error, so the decision came down to **voice quality + ease of integration**, where
> ElevenLabs wins.

---

## 1. Why ElevenLabs

- **Most human-sounding voice.** Our scripts are 20–30s hooks where narration quality directly
  drives retention. ElevenLabs is best-in-class vs. Kokoro's recognizably-synthetic 82M voice.
- **Zero infrastructure.** Hosted API — no GPU, no desktop app, no EC2 boot/storage babysitting.
  Works fine on the current CPU-only machine.
- **Commercial rights required anyway.** A monetized YouTube channel needs at least the **Starter
  plan ($5/mo)** for commercial use, so the paid tier isn't "extra" cost.
- **Trivial integration.** API key + ~40 lines in `tts_generator.py`, slotting above Kokoro in the
  existing backend-priority chain. Output converts to our 44.1kHz mono WAV via the existing resampler.

## 2. Cost (at ~550 chars/video — our Shorts length)

| Plan | Monthly | Credits | Videos/mo covered | Per-video |
|---|---|---|---|---|
| Free | $0 | 10,000 | ~18 | ❌ no commercial rights |
| **Starter (chosen)** | **$5** | 30,000 | ~54 (v2) / ~108 (Flash) | flat $5/mo |
| Creator | $22 | 121,000 | ~220 | unlocks Professional Voice Cloning |

- 1 character ≈ 1 credit (Multilingual v2); ~0.5 credit on Flash/Turbo models.
- At a daily cadence (~30 videos/mo) the **$5 Starter plan covers it with headroom.**
- Starter includes **Instant Voice Cloning**; **Professional Voice Cloning** (highest-fidelity clone)
  needs Creator ($22) — only relevant if we later want a bespoke cloned narrator.

## 3. Implementation plan (branch-based, low-risk)

1. **Subscribe** to ElevenLabs Starter; create an API key.
2. **New branch** off `main` (e.g. `feat/elevenlabs-tts`).
3. **Config** (`config.py`, near the `KOKORO_*` block):
   - `ELEVENLABS_API_KEY` (from `.env` — **never commit**)
   - `ELEVENLABS_VOICE_ID`
   - `ELEVENLABS_MODEL` (e.g. `eleven_multilingual_v2` for quality, or a Flash model for cost)
   - `ELEVENLABS_ENABLED` (default off until merged)
4. **`pipeline/tts_generator.py`:**
   - `_check_elevenlabs()` — returns True if key present and `ELEVENLABS_ENABLED`.
   - `_generate_elevenlabs(text, out_path)` — POST to the TTS endpoint, save returned audio
     (MP3/WAV), then reuse `_convert_mp3_to_wav()` → 44.1kHz mono WAV.
   - Register **above Kokoro** in `detect_tts_backend()` and the dispatch in `generate_audio()`.
   - Wrap the network call with the existing `with_retry` decorator.
5. **`.env.example` + README/HOW_TO_USE** — document the new env vars and backend priority.
6. **Test on the branch:** render a couple of videos. Do at least one **direct A/B** (same script
   through Kokoro and ElevenLabs), listen to a full render before publishing.
7. **Release a couple of videos** from the branch. If quality/retention look good → **merge to main**.

Orchestrator (`orchestrator.py:376–401`) needs **no changes** — it just calls `generate_audio()`.

## 4. Rejected alternatives

- **VoiceBox** — open-source local GUI app (localhost API on :17493). Human-like engines (Qwen3-TTS,
  Chatterbox) need a **GPU we don't have**; the CPU-friendly engine is just Kokoro (which we already
  run). It's also a desktop app, awkward to run headless. **No gain, lots of overhead.**
- **EC2 GPU (burst, ~10 min/run)** — compute is pennies (~$0.09–0.17/run), but real cost is
  **storage-at-rest + cold-start model downloads (~$5–10/mo)** plus infra babysitting. The GPU also
  sits idle through our API-bound steps (only TTS needs it). At Shorts length, **all cost, no benefit.**
- **Replicate TTS** — viable runner-up: pay-per-use (~2¢/video, $0 in idle months), reuses an API we
  already have. Rejected for now only because ElevenLabs is more human + we need a paid tier for
  commercial rights regardless. **Keep as the fallback option if we ever want to drop the subscription.**

## 5. Open items to confirm during implementation

- Pick the voice (`ELEVENLABS_VOICE_ID`) — browse the voice library for a documentary-narrator tone.
- Choose model: `eleven_multilingual_v2` (quality) vs a Flash/Turbo model (½ the credits, near-equal
  quality on English). Default to quality; revisit if credit usage gets tight.
- Confirm the API returns audio we can pipe straight through `_convert_mp3_to_wav` (it accepts MP3).
- Decide whether to keep ElevenLabs default-on after merge, or leave it env-gated with Kokoro fallback.
