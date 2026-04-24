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
from anthropic.types import TextBlock

import config
from pipeline.research import research_topic
from pipeline.retry import with_retry

logger = logging.getLogger(__name__)


@with_retry(max_retries=3, base_delay=2)
def _call_claude(client: anthropic.Anthropic, **kwargs) -> anthropic.types.Message:
    return client.messages.create(**kwargs)


SYSTEM_PROMPT = """You are a viral YouTube Shorts scriptwriter specializing in
historical content. You write punchy, engaging scripts that hook viewers in the
first second, rehook them in the middle, and leave them astonished. Scripts must
be exactly 5 parts:
Hook → Context → Rehook → Twist → Ending fact.
Always respond with valid JSON only — no markdown fences, no extra text."""

USER_PROMPT_TEMPLATE = """Write a viral YouTube Shorts script for this historical event:

Event: {event}
Year: {year}
Location: {location}

STRICT REQUIREMENTS:
- Total script: 20-30 seconds when read aloud (~90 words MAX)
- Hook: 1 sentence — must stop the scroll in under 2 seconds. Choose the strongest formula for this event:
  • SHOCKING FACT: Lead with the most unbelievable true detail. "A man once sold the Eiffel Tower — twice."
  • FALSE ASSUMPTION: State what everyone believes, then immediately break it. "Everyone thinks Einstein failed math. He didn't — but his teachers still wanted him gone."
  • CONSEQUENCE FIRST: Start with the dramatic outcome, then explain how. "This one telegram started World War One."
  • SPECIFIC NUMBER: A precise number creates instant credibility. "In 1518, 400 people danced non-stop for 2 months — and couldn't stop."
  • DIRECT ADDRESS: Pull the viewer in personally. "You've used this invention today — but its creator was executed for making it."
- Context: 2 short sentences explaining what happened
- Rehook: 1 short sentence after the context that renews curiosity and makes the viewer need the payoff
- Twist: 1 sentence revealing the most unbelievable part
- Ending fact: 1 sentence with a mind-blowing fact that connects back to the hook so the ending feels loopable on replay

HOOK RULES:
- Never start with "Did you know" — it signals low-quality content
- Never start with "In [year]" — bury the date in context, not the hook
- The hook must work as audio only — no "look at this" or visual references
- Under 15 words is ideal; 20 words absolute max
- The rehook should sound natural, not clickbait, and should land around the midpoint of the story

YouTube SEO requirements:
- title: Under 60 characters. Front-load the most searchable keyword first (e.g. "Tesla's Stolen Invention That Changed the World | 1900"). Factual, no ALL CAPS. No misleading claims.
- description: 150-300 words. MUST start with educational framing: "This educational short explores..." or "Explore the true historical story of...". Then: 2-3 sentence summary → 2-3 sentences of deeper context/why it matters → 1-2 sentences on broader historical significance → call to action ("Follow for more unbelievable history."). Weave in relevant keywords naturally throughout.
- hashtags: 5 general tags (history, shorts, facts, etc.). No # prefix.
- youtube_tags: 20 tags mixing broad ("history", "shorts", "documentary", "facts") and specific (inventor name, invention type, location, year, key themes, related figures). No # prefix. Aim to fill close to 500 characters total.

YouTube Safe Distribution rules (CRITICAL — violations cause demotion):
- NEVER use conspiracy framing: "covered up", "they don't want you to know", "suppressed by", "secret history", "what historians hide", "the truth about"
- NEVER glorify violence, suffering, or death — describe historical facts with context, not shock
- If the event involves violence or death, frame it as a historical record: "historical records show...", "according to accounts from the time..."
- Titles must be accurate — never overpromise what the video contains
- NEVER generate content about: suicide methods, sexual violence, child harm, terrorism glorification

Return ONLY this JSON (no markdown, no extra text):
{{
  "title": "Keyword-first title under 60 chars with year",
  "description": "150-300 word SEO description ending with call to action",
  "hashtags": ["history", "unbelievable", "shorts", "facts", "historical"],
  "youtube_tags": ["history", "shorts", "historical facts", "documentary", "unreal history"],
  "hook_type": "SHOCKING_FACT|FALSE_ASSUMPTION|CONSEQUENCE_FIRST|SPECIFIC_NUMBER|DIRECT_ADDRESS",
  "hook": "Hook sentence here",
  "context": "Context sentences here.",
  "rehook": "Mid-video curiosity reset here.",
  "twist": "Twist sentence here.",
  "ending_fact": "Ending fact sentence here that loops back to the hook.",
  "full_script": "Complete script as one flowing paragraph (hook + context + rehook + twist + ending_fact combined)",
  "pin_comment": "A short engaging question specific to this story that will be pinned as the first comment to drive replies (e.g. 'Did you know about this before? What shocked you most? 👇')",
  "word_count": 0,
  "estimated_seconds": 0
}}"""


def _save_prompt(slug: str, idx: int, prompt: str) -> Path:
    """Write prompt to prompts/<slug>_<idx>.txt and return the path."""
    prompts_dir = config.BASE_DIR / "prompts"
    prompts_dir.mkdir(exist_ok=True)
    prompt_path = prompts_dir / f"{slug}_{idx}.txt"
    prompt_path.write_text(prompt, encoding="utf-8")
    return prompt_path


def _load_edited_prompt(prompt_path: Path, original: str) -> str:
    """Read back the (possibly edited) prompt. Falls back to original if empty or unreadable."""
    try:
        text = prompt_path.read_text(encoding="utf-8").strip()
        return text if text else original
    except Exception:
        return original


def generate_scripts(
    events: list[dict], slug: str, no_edit: bool = False
) -> list[dict]:
    """
    Generate a viral script for each event using Claude API.
    Returns list of script dicts, one per event. Resumable — skips if cached.

    Args:
        events: list of event dicts from event_discovery
        slug: pipeline slug for output paths
        no_edit: if True, skip prompt-editing pause (automation mode)
    """
    output_path = config.OUTPUT_DIR / slug / "scripts.json"
    output_path.parent.mkdir(parents=True, exist_ok=True)

    # Resume: load from disk if already generated
    if output_path.exists():
        logger.info(
            f"[script_generator] Cache hit — loading scripts from {output_path}"
        )
        with open(output_path, encoding="utf-8") as f:
            scripts = json.load(f)
        scripts = [_validate_and_fix_script(script) for script in scripts]
        logger.info(f"[script_generator] Loaded {len(scripts)} scripts from cache.")
        return scripts

    client = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)
    scripts = []

    for idx, event in enumerate(events):
        logger.info(
            f"[script_generator] Generating script {idx + 1}/{len(events)}: {event.get('event', '')[:60]}..."
        )

        snippets = research_topic(event.get("event", ""))
        research_section = (
            f"\n\nRESEARCH SNIPPETS (use for factual grounding, do not copy verbatim):\n{snippets}"
            if snippets
            else ""
        )
        prompt = (
            USER_PROMPT_TEMPLATE.format(
                event=event.get("event", ""),
                year=event.get("year", "unknown"),
                location=event.get("location", "unknown"),
            )
            + research_section
        )

        if not no_edit:
            prompt_path = _save_prompt(slug, idx, prompt)
            print(f"\n  Prompt saved: {prompt_path}")
            print(
                "Edit it now, then press Enter to send to Claude (Enter without editing uses it as-is)..."
            )
            input()
            prompt = _load_edited_prompt(prompt_path, prompt)

        try:
            message = _call_claude(
                client,
                model=config.CLAUDE_MODEL,
                max_tokens=config.CLAUDE_MAX_TOKENS,
                system=SYSTEM_PROMPT,
                messages=[{"role": "user", "content": prompt}],
            )
            text_parts = [
                block.text for block in message.content
                if isinstance(block, TextBlock)
            ]
            if not text_parts:
                raise ValueError(
                    f"Claude returned no text content "
                    f"(stop_reason={message.stop_reason})"
                )
            raw_text = "".join(text_parts).strip()
            logger.debug(
                f"[script_generator] Raw response for event {idx}:\n{raw_text}"
            )

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
    required_keys = [
        "title",
        "description",
        "hashtags",
        "youtube_tags",
        "hook_type",
        "hook",
        "context",
        "rehook",
        "twist",
        "ending_fact",
        "full_script",
        "pin_comment",
    ]

    list_keys = {"hashtags", "youtube_tags"}
    for key in required_keys:
        if key not in script:
            logger.warning(
                f"[script_generator] Missing key '{key}' in script — using fallback."
            )
            script[key] = [] if key in list_keys else ""

    # Recompute word count and estimated duration (avg 130 words/minute for narration)
    full_script = script.get("full_script", "")
    if not full_script:
        # Rebuild from parts if missing
        full_script = " ".join(
            [
                script.get("hook", ""),
                script.get("context", ""),
                script.get("rehook", ""),
                script.get("twist", ""),
                script.get("ending_fact", ""),
            ]
        ).strip()
        script["full_script"] = full_script

    words = full_script.split()
    word_count = len(words)
    words_per_minute = 130 * max(config.KOKORO_SPEED, 0.1)
    estimated_seconds = round((word_count / words_per_minute) * 60)

    script["word_count"] = word_count
    script["estimated_seconds"] = estimated_seconds

    # Clamp title to 60 chars (prompt asks for <60; this is the hard ceiling
    # that keeps it under YouTube's search-result truncation as well).
    if len(script.get("title", "")) > 60:
        script["title"] = script["title"][:57] + "..."

    # Ensure hashtags + youtube_tags are lists of strings without # prefix
    for field in ("hashtags", "youtube_tags"):
        tags = script.get(field, [])
        if isinstance(tags, str):
            tags = [t.strip() for t in tags.split(",")]
        script[field] = [str(t).lstrip("#") for t in tags if t]

    # Clamp description to YouTube's 5000 char limit (safety net)
    if len(script.get("description", "")) > 5000:
        script["description"] = script["description"][:4997] + "..."

    # Clamp total youtube_tags to ~500 chars (YouTube tag limit)
    tags = script.get("youtube_tags", [])
    total = 0
    clamped = []
    for tag in tags:
        if total + len(tag) + 1 <= 500:
            clamped.append(tag)
            total += len(tag) + 1
        else:
            break
    script["youtube_tags"] = clamped

    return script
