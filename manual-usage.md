# Manual Image Mode

Pause the pipeline after scene planning, generate images yourself in any AI tool
(Midjourney, DALL-E, ChatGPT, Sora, Leonardo, etc.), drop them into a folder,
and resume to finish the video.

Use this when you want to A/B test which image generator drives the most views.

---

## The flow at a glance

1. **Run with `--manual-images`** → pipeline stops after writing `prompts.md`.
2. **Generate 5 images yourself** in whatever AI tool you like.
3. **Save them** as `img_0.png` … `img_4.png` in the folder it printed.
4. **Re-run the same command** → pipeline resumes from TTS through upload.

---

## Step 1 — Start the pipeline (paused)

Pick a topic from the queue:

```
.venv/Scripts/python.exe orchestrator.py --list-topics
.venv/Scripts/python.exe orchestrator.py --pick <id> --manual-images
```

Or run in manual mode:

```
.venv/Scripts/python.exe orchestrator.py --topic "Strange Moments in History" --keyword "war" --manual-images
```

Or pull the next pending topic:

```
.venv/Scripts/python.exe orchestrator.py --auto --manual-images
```

The pipeline runs:

- Step 1: Event discovery (Claude)
- Step 2: Script generation (Claude)
- Step 2.5: Content safety check
- Step 2.6: Scene planning

…then **stops** and prints something like:

```
══════════════════════════════════════════════════════════════
  MANUAL IMAGE MODE — PIPELINE PAUSED
══════════════════════════════════════════════════════════════
  5 image(s) still missing.
  Open `prompts.md` in each event folder, generate the images,
  and save them as `img_0.png` … `img_N.png` next to it.

  Drop folders:
    C:\...\output\<slug>\images\0
```

The topic is **not** marked failed in the queue. It stays `in_progress` until
you resume.

---

## Step 2 — Generate images externally

Open the `prompts.md` file in the printed folder. It contains, for each scene:

- The narration line (so you know what the scene is about)
- A **full prompt** (best for FLUX, SDXL, Stable Diffusion)
- A **short prompt** (best for Midjourney, DALL-E, ChatGPT image gen)
- The scene role (HOOK / CONTEXT / REHOOK / TWIST / ENDING)

Pick whichever prompt format your tool prefers, paste it in, and generate the
image. You can mix tools across scenes — there's no enforcement.

**Target size:** 1080×1920 (9:16 portrait). Other sizes get auto-cropped on
resume, so you don't have to be exact — but anything close to portrait works
best (square will get its sides cropped off).

---

## Step 3 — Save images into the folder

Inside `output/<slug>/images/<event_index>/`, save your finished images as:

```
img_0.png   ← scene 0 (hook)
img_1.png   ← scene 1 (context)
img_2.png   ← scene 2 (rehook)
img_3.png   ← scene 3 (twist)
img_4.png   ← scene 4 (ending)
```

Filenames must match exactly. PNG format. The pipeline checks the file is at
least 1KB before accepting it.

---

## Step 4 — Resume

Run the **exact same command** you started with:

```
.venv/Scripts/python.exe orchestrator.py --pick <id> --manual-images
```

The pipeline:

- Skips Steps 1, 2, 2.5, 2.6 (cached on disk).
- Step 3: verifies all 5 PNGs exist, cover-crops them to 1080×1920 if needed.
- Step 4: TTS (voice).
- Step 5: video assembly with subtitles.
- Step 6: YouTube upload.

If any image is still missing, it pauses again and lists exactly which files
it expected but didn't find.

---

## Tracking which AI generated which video

For your A/B comparison week-by-week, jot it down somewhere outside the
pipeline — a row in a spreadsheet, a note in `improvements.md`, etc. Track:

- Topic ID / slug
- Which AI you used (e.g. "midjourney v7", "sora", "dall-e 3")
- The uploaded video URL
- View count after 7 days

Compare the per-AI averages once you have a few weeks of data.

---

## Common gotchas

- **`--manual-images` flag missing on resume**: if you forget the flag on the
  second run, the pipeline tries to generate images via HF/Replicate/PIL
  instead of using yours. Always include `--manual-images` on the resume.
- **Wrong filename**: `image_0.png`, `img0.png`, `IMG_0.PNG` will all be
  treated as missing. Must be lowercase `img_0.png`.
- **File too small**: anything under 1KB is treated as missing (catches empty
  placeholder files).
- **Wrong aspect**: square or landscape images get center-cropped to 9:16,
  which loses the left/right or top/bottom edges. Generate portrait if you can.
- **Scene count**: scene plans usually have 5 scenes but a preset can change
  this. Check `prompts.md` for the actual count rather than assuming 5.

---

## Reverting to fully automated mode

Just drop the flag:

```
.venv/Scripts/python.exe orchestrator.py --auto
```

Automated mode (HF → Replicate → PIL fallback) still works exactly as before.
