"""
STEP 1 — EVENT DISCOVERY
Uses Claude API to generate strange/unbelievable historical events.
Saves output to output/<slug>/events.json (resumable).
"""

import json
import logging
import re

import anthropic
from anthropic.types import TextBlock

import config
from pipeline import cost_tracker
from pipeline.retry import with_retry

logger = logging.getLogger(__name__)


@with_retry(max_retries=3, base_delay=2)
def _call_claude(client: anthropic.Anthropic, **kwargs) -> anthropic.types.Message:
    return client.messages.create(**kwargs)


SYSTEM_PROMPT = """You are a historian specializing in strange, unbelievable,
and bizarre real historical events. You generate factually accurate but
jaw-dropping historical facts that sound too strange to be true.
Always respond with valid JSON only — no markdown fences, no extra text."""

USER_PROMPT_TEMPLATE = """Generate {count} strange, unbelievable, and fascinating
historical events related to the keyword "{keyword}" for a YouTube channel called
"Unreal History" with the topic "{topic}".

Each event must:
- Be 100% historically accurate (real event, real year, real location)
- Sound almost impossible to believe
- Be dramatic and visually interesting
- Be concise enough for a 20-30 second video

Return ONLY a JSON array (no markdown, no extra text):
[
  {{
    "event": "Brief description of the unbelievable event (1-2 sentences)",
    "year": "Year or approximate year (e.g. '1347' or 'circa 1200')",
    "location": "City, Country or Region",
    "visual_theme": "A one-line description of what this would look like visually"
  }}
]"""


def discover_events(topic: str, keyword: str, count: int, slug: str) -> list[dict]:
    """
    Generate historical events using Claude API.
    Returns list of event dicts. Skips generation if cached output exists.
    """
    output_path = config.OUTPUT_DIR / slug / "events.json"
    output_path.parent.mkdir(parents=True, exist_ok=True)

    # Resume: load from disk if already generated
    if output_path.exists():
        logger.info(f"[event_discovery] Cache hit — loading events from {output_path}")
        with open(output_path, encoding="utf-8") as f:
            events = json.load(f)
        logger.info(f"[event_discovery] Loaded {len(events)} events from cache.")
        return events

    logger.info(f"[event_discovery] Generating {count} events for topic='{topic}', keyword='{keyword}'")

    client = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)

    prompt = USER_PROMPT_TEMPLATE.format(
        count=count,
        keyword=keyword,
        topic=topic,
    )

    try:
        message = _call_claude(
            client,
            model=config.CLAUDE_MODEL,
            max_tokens=config.CLAUDE_MAX_TOKENS,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": prompt}],
        )
        tracker = cost_tracker.get_active()
        if tracker is not None:
            tracker.record_message("event_discovery", message, model=config.CLAUDE_MODEL)
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
        logger.debug(f"[event_discovery] Raw Claude response:\n{raw_text}")

        events = _parse_json_response(raw_text)

        if not isinstance(events, list) or len(events) == 0:
            raise ValueError(f"Expected a non-empty list, got: {type(events)}")

        # Trim to requested count in case Claude returned more
        events = events[:count]

        # Save to disk
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(events, f, indent=2, ensure_ascii=False)

        logger.info(f"[event_discovery] Saved {len(events)} events to {output_path}")
        return events

    except anthropic.APIError as e:
        logger.error(f"[event_discovery] Anthropic API error: {e}")
        raise
    except (json.JSONDecodeError, ValueError) as e:
        logger.error(f"[event_discovery] Failed to parse Claude response: {e}")
        raise


def _parse_json_response(text: str) -> list:
    """
    Robustly extract JSON from Claude's response.
    Handles cases where Claude wraps JSON in markdown code fences.
    """
    # Strip markdown code fences if present
    text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.MULTILINE)
    text = re.sub(r"\s*```$", "", text, flags=re.MULTILINE)
    text = text.strip()

    # Try direct parse first
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # Try extracting just the JSON array
    match = re.search(r"\[.*\]", text, re.DOTALL)
    if match:
        return json.loads(match.group(0))

    raise json.JSONDecodeError("Could not find valid JSON array", text, 0)
