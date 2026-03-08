"""
STEP 2 — SCRIPT GENERATION
Uses Claude API to generate viral YouTube Shorts scripts for each event.
Saves output to output/<slug>/scripts.json (resumable).
"""

import json
import logging
import re
from pathlib import Path

import anthropic

import config
from pipeline.research import research_topic
from pipeline.retry import with_retry

logger = logging.getLogger(__name__)


@with_retry(max_retries=3, base_delay=2)
def _call_claude(client: anthropic.Anthropic, **kwargs) -> anthropic.types.Message:
    return client.messages.create(**kwargs)


SYSTEM_PROMPT = """You are a viral YouTube Shorts scriptwriter specializing in
historical content. You write punchy, engaging scripts that hook viewers in the
first second and leave them astonished. Scripts must be exactly 4 parts:
Hook → Context → Twist → Ending fact.
Always respond with valid JSON only — no markdown fences, no extra text."""

USER_PROMPT_TEMPLATE = """Write a viral YouTube Shorts script for this historical event:

Event: {event}
Year: {year}
Location: {location}

STRICT REQUIREMENTS:
- Total script: 20-30 seconds when read aloud (~80 words MAX)
- Hook: 1 sentence that grabs attention instantly (pose a shocking question or statement)
- Context: 2 sentences explaining what happened
- Twist: 1 sentence revealing the most unbelievable part
- Ending fact: 1 sentence with a mind-blowing fact to end on

YouTube SEO requirements:
- title: Under 60 characters. Format: shocking hook or question + year (e.g. "The Soldier Who Fought Alone for 29 Years | 1945"). Factual, no ALL CAPS.
- description: Exactly 2 sentences — what happened + why it matters. End with "Follow for more unbelievable history." (150-200 chars total)
- hashtags: 5 general tags (history, shorts, facts, etc.)
- youtube_tags: 10-15 tags mixing broad ("history", "shorts", "documentary") and specific (event location, year, key figures/topics). No # prefix.

Return ONLY this JSON (no markdown, no extra text):
{{
  "title": "Punchy title under 60 chars with year",
  "description": "2-sentence description ending with call to action (150-200 chars)",
  "hashtags": ["history", "unbelievable", "shorts", "facts", "historical"],
  "youtube_tags": ["history", "shorts", "historical facts", "documentary", "unreal history"],
  "hook": "Hook sentence here",
  "context": "Context sentences here.",
  "twist": "Twist sentence here.",
  "ending_fact": "Ending fact sentence here.",
  "full_script": "Complete script as one flowing paragraph (hook + context + twist + ending_fact combined)",
  "word_count": 0,
  "estimated_seconds": 0
}}"""


def generate_scripts(events: list[dict], slug: str) -> list[dict]:
    """
    Generate a viral script for each event using Claude API.
    Returns list of script dicts, one per event. Resumable — skips if cached.
    """
    output_path = config.OUTPUT_DIR / slug / "scripts.json"
    output_path.parent.mkdir(parents=True, exist_ok=True)

    # Resume: load from disk if already generated
    if output_path.exists():
        logger.info(f"[script_generator] Cache hit — loading scripts from {output_path}")
        with open(output_path, "r", encoding="utf-8") as f:
            scripts = json.load(f)
        logger.info(f"[script_generator] Loaded {len(scripts)} scripts from cache.")
        return scripts

    client = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)
    scripts = []

    for idx, event in enumerate(events):
        logger.info(f"[script_generator] Generating script {idx + 1}/{len(events)}: {event.get('event', '')[:60]}...")

        snippets = research_topic(event.get("event", ""))
        research_section = (
            f"\n\nRESEARCH SNIPPETS (use for factual grounding, do not copy verbatim):\n{snippets}"
            if snippets else ""
        )
        prompt = USER_PROMPT_TEMPLATE.format(
            event=event.get("event", ""),
            year=event.get("year", "unknown"),
            location=event.get("location", "unknown"),
        ) + research_section

        try:
            message = _call_claude(
                client,
                model=config.CLAUDE_MODEL,
                max_tokens=config.CLAUDE_MAX_TOKENS,
                system=SYSTEM_PROMPT,
                messages=[{"role": "user", "content": prompt}],
            )
            raw_text = message.content[0].text.strip()
            logger.debug(f"[script_generator] Raw response for event {idx}:\n{raw_text}")

            script = _parse_json_response(raw_text)
            script = _validate_and_fix_script(script)

            # Attach source event metadata
            script["event_index"] = idx
            script["source_event"] = event
            scripts.append(script)

            logger.info(
                f"[script_generator] Script {idx + 1} OK — "
                f"{script.get('word_count', '?')} words, ~{script.get('estimated_seconds', '?')}s"
            )

        except anthropic.APIError as e:
            logger.error(f"[script_generator] API error on event {idx}: {e}")
            raise
        except (json.JSONDecodeError, ValueError, KeyError) as e:
            logger.error(f"[script_generator] Parse error on event {idx}: {e}")
            raise

    # Save all scripts to disk
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(scripts, f, indent=2, ensure_ascii=False)

    logger.info(f"[script_generator] Saved {len(scripts)} scripts to {output_path}")
    return scripts


def _parse_json_response(text: str) -> dict:
    """Robustly extract JSON dict from Claude's response."""
    text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.MULTILINE)
    text = re.sub(r"\s*```$", "", text, flags=re.MULTILINE)
    text = text.strip()

    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    match = re.search(r"\{.*\}", text, re.DOTALL)
    if match:
        return json.loads(match.group(0))

    raise json.JSONDecodeError("Could not find valid JSON object", text, 0)


def _validate_and_fix_script(script: dict) -> dict:
    """Validate script fields and compute word count / estimated duration."""
    required_keys = ["title", "description", "hashtags", "youtube_tags", "hook",
                     "context", "twist", "ending_fact", "full_script"]

    list_keys = {"hashtags", "youtube_tags"}
    for key in required_keys:
        if key not in script:
            logger.warning(f"[script_generator] Missing key '{key}' in script — using fallback.")
            script[key] = [] if key in list_keys else ""

    # Recompute word count and estimated duration (avg 130 words/minute for narration)
    full_script = script.get("full_script", "")
    if not full_script:
        # Rebuild from parts if missing
        full_script = " ".join([
            script.get("hook", ""),
            script.get("context", ""),
            script.get("twist", ""),
            script.get("ending_fact", ""),
        ]).strip()
        script["full_script"] = full_script

    words = full_script.split()
    word_count = len(words)
    estimated_seconds = round((word_count / 130) * 60)

    script["word_count"] = word_count
    script["estimated_seconds"] = estimated_seconds

    # Clamp title length
    if len(script.get("title", "")) > 100:
        script["title"] = script["title"][:97] + "..."

    # Ensure hashtags + youtube_tags are lists of strings without # prefix
    for field in ("hashtags", "youtube_tags"):
        tags = script.get(field, [])
        if isinstance(tags, str):
            tags = [t.strip().lstrip("#") for t in tags.split(",")]
        script[field] = [str(t).lstrip("#") for t in tags if t]

    # Clamp title to 60 chars for CTR
    if len(script.get("title", "")) > 60:
        script["title"] = script["title"][:57] + "..."

    return script
