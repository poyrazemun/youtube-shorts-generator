"""
STEP 2b — TITLE/DESCRIPTION LOCALIZATION

Translates each script's title and description into the languages listed in
config.LOCALIZATION_LANGUAGES. Returns a dict shaped for YouTube's
`localizations` field on videos.insert.

One Claude call per script, asking for all target languages at once. Failures
return an empty dict — the upload still proceeds with the English-only metadata.
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

_LANGUAGE_NAMES = {
    "es": "Spanish (Latin American — neutral, broadest reach)",
    "pt": "Portuguese (Brazilian)",
    "hi": "Hindi",
    "id": "Indonesian",
}

_TITLE_MAX_CHARS = 100  # YouTube's hard ceiling per locale


@with_retry(max_retries=2, base_delay=2)
def _call(client: anthropic.Anthropic, **kwargs) -> anthropic.types.Message:
    return client.messages.create(**kwargs)


def _build_prompt(title: str, description: str, langs: list[str]) -> str:
    lang_list = "\n".join(
        f"- {code}: {_LANGUAGE_NAMES.get(code, code)}" for code in langs
    )
    return f"""Translate the following YouTube Shorts title and description into the listed languages.

TARGET LANGUAGES:
{lang_list}

REQUIREMENTS:
- Translate accurately, keeping the same factual content and SEO keywords (proper nouns like names of people, places, and inventions stay in their original form).
- Title MUST stay under {_TITLE_MAX_CHARS} characters in every language.
- Preserve hashtags exactly as-is (do not translate "#History" etc.).
- Description should preserve the same paragraph structure and length (~150-300 words).
- Do NOT translate the call to action "Follow for more unbelievable history." literally — adapt it naturally to each language's social-media tone.
- Output valid JSON only, no markdown fences.

ENGLISH SOURCE:
title: {json.dumps(title, ensure_ascii=False)}
description: {json.dumps(description, ensure_ascii=False)}

Return ONLY this JSON shape (one entry per language, no extras, no commentary):
{{
{",".join(f'  "{code}": {{"title": "...", "description": "..."}}' for code in langs)}
}}"""


def _parse_json(text: str) -> dict:
    """Tolerant JSON parse — strip code fences if Claude added them."""
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1:
        raise json.JSONDecodeError("No JSON object found", text, 0)
    return json.loads(text[start : end + 1])


def generate_localizations(
    title: str,
    description: str,
    client: anthropic.Anthropic,
    languages: list[str] | None = None,
) -> dict[str, dict[str, str]]:
    """Return {lang: {"title": ..., "description": ...}} for each requested lang.

    Returns {} on any failure — caller treats this as "no localizations" and
    the upload proceeds with English only."""
    languages = languages or config.LOCALIZATION_LANGUAGES
    if not languages:
        return {}

    prompt = _build_prompt(title, description, languages)
    try:
        message = _call(
            client,
            model=config.CLAUDE_TRANSLATION_MODEL,
            max_tokens=2500,
            messages=[{"role": "user", "content": prompt}],
        )
    except Exception as e:
        logger.warning(f"[localizer] Translation API call failed: {e}")
        return {}

    tracker = cost_tracker.get_active()
    if tracker is not None:
        tracker.record_message(
            "localization", message, model=config.CLAUDE_TRANSLATION_MODEL
        )

    text_parts = [b.text for b in message.content if isinstance(b, TextBlock)]
    if not text_parts:
        logger.warning("[localizer] Translation returned no text content")
        return {}

    try:
        parsed = _parse_json("".join(text_parts))
    except json.JSONDecodeError as e:
        logger.warning(f"[localizer] Could not parse JSON from translator: {e}")
        return {}

    out: dict[str, dict[str, str]] = {}
    for lang in languages:
        entry = parsed.get(lang)
        if not isinstance(entry, dict):
            logger.warning(f"[localizer] Missing or malformed entry for {lang}")
            continue
        loc_title = str(entry.get("title", "")).strip()
        loc_desc = str(entry.get("description", "")).strip()
        if not loc_title or not loc_desc:
            logger.warning(f"[localizer] Empty title/description for {lang}")
            continue
        if len(loc_title) > _TITLE_MAX_CHARS:
            loc_title = loc_title[: _TITLE_MAX_CHARS - 3] + "..."
        out[lang] = {"title": loc_title, "description": loc_desc}

    logger.info(f"[localizer] Generated localizations: {sorted(out.keys())}")
    return out
