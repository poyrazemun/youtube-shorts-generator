"""
STEP 2 (sub-pass) — HISTORICAL FACT-CHECK CORRECTION

A lightweight, second Claude call that scans a freshly generated script's five
narrative beats (hook / context / rehook / twist / ending_fact) for unverified
"pop-history myths" and logic errors, then returns corrected text grounded in
verified history. Runs inside script_generator BEFORE the script is saved to
scripts.json, so the persisted "pipeline database" only ever holds vetted copy.

This is the Step-2 enforcement half of the accuracy guardrail; the Step-1 half
is the HISTORICAL_ACCURACY_DIRECTIVE injected into the generation system prompt.

Fail-open contract (mirrors content_safety): if the call errors, returns
malformed JSON, or proposes a structurally-invalid correction, the ORIGINAL
script is returned unchanged. A fact-check problem must never block or corrupt a
run — at worst we fall back to the directive-constrained first draft.
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

# The narrative fields the checker is allowed to read and rewrite. Metadata
# (title, description, tags, scene_visuals) is intentionally out of scope — this
# pass guards factual claims in the spoken script, not SEO copy.
_BEAT_FIELDS = ("hook", "context", "rehook", "twist", "ending_fact")

SYSTEM_PROMPT = """You are a meticulous historian and fact-checker for a history
Shorts channel. You scan short, already-written scripts for historical
inaccuracy: fabricated human reactions, invented consequences, pseudo-science
("they thought basic nature was witchcraft"), and unverified internet myths
presented as fact. You FIX only what is inaccurate, preserving the punchy tone,
sentence count, and length of each beat. You never add drama that isn't
historically supported. Always respond with valid JSON only — no markdown
fences, no extra text."""

USER_PROMPT_TEMPLATE = """Fact-check this YouTube Shorts history script. Each beat must be grounded in
verified history — no fabricated reactions, no pseudo-science, no internet myths.

EVENT CONTEXT (ground truth to check against):
Event:    {event}
Year:     {year}
Location: {location}

SCRIPT BEATS TO CHECK:
hook:        {hook}
context:     {context}
rehook:      {rehook}
twist:       {twist}
ending_fact: {ending_fact}

RULES:
- Correct any beat that fabricates/exaggerates reactions or consequences, invokes
  pseudo-science (e.g. a group "baffled by basic physics/nature"), or repeats an
  unverified popular myth. If Europeans called a ritual "sorcery", target the
  pagan worship/deities — NOT the physical food/ingredient.
- If a beat is already accurate, return it UNCHANGED.
- Preserve each beat's tone, role, sentence count, and approximate length.
- Do NOT invent new facts to replace removed ones — re-ground the drama in real
  historical irony, cultural clash, political paranoia, or strategic conflict.

Return ONLY this JSON, no other text:
{{
  "issues_found": ["short description of each correction made"],
  "corrected": {{
    "hook": "corrected or unchanged hook",
    "context": "corrected or unchanged context",
    "rehook": "corrected or unchanged rehook",
    "twist": "corrected or unchanged twist",
    "ending_fact": "corrected or unchanged ending fact"
  }}
}}"""


@with_retry(max_retries=2, base_delay=2)
def _call_claude(client: anthropic.Anthropic, **kwargs) -> anthropic.types.Message:
    return client.messages.create(**kwargs)


def _parse_response(raw: str) -> dict:
    """Strip fences + parse JSON. Raises on malformed input."""
    raw = re.sub(r"^```(?:json)?\s*", "", raw, flags=re.MULTILINE)
    raw = re.sub(r"\s*```$", "", raw, flags=re.MULTILINE).strip()
    return json.loads(raw)


def fact_check_script(
    script: dict, client: anthropic.Anthropic | None = None
) -> dict:
    """
    Scan and correct the five narrative beats of a single script.

    Returns the (possibly corrected) script dict. The returned dict carries a
    `fact_check` sub-dict recording what happened, e.g.:
        {"applied": True, "issues_found": [...], "model": "..."}
        {"applied": False, "issues_found": [], "skipped": "<reason>"}

    Fail-open: any error or structurally-invalid correction leaves the script's
    beats untouched. Mutates and returns the same dict for caller convenience.
    """
    if not config.FACTCHECK_ENABLED:
        script["fact_check"] = {"applied": False, "skipped": "disabled"}
        return script

    source = script.get("source_event", {}) or {}
    prompt = USER_PROMPT_TEMPLATE.format(
        event=source.get("event", "") or script.get("title", ""),
        year=source.get("year", "unknown"),
        location=source.get("location", "unknown"),
        hook=script.get("hook", ""),
        context=script.get("context", ""),
        rehook=script.get("rehook", ""),
        twist=script.get("twist", ""),
        ending_fact=script.get("ending_fact", ""),
    )

    try:
        client = client or anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)
        message = _call_claude(
            client,
            model=config.CLAUDE_FACTCHECK_MODEL,
            max_tokens=1024,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": prompt}],
        )
        tracker = cost_tracker.get_active()
        if tracker is not None:
            tracker.record_message(
                "fact_check", message, model=config.CLAUDE_FACTCHECK_MODEL
            )

        text_parts = [
            block.text for block in message.content if isinstance(block, TextBlock)
        ]
        if not text_parts:
            raise ValueError(f"empty response (stop_reason={message.stop_reason})")
        parsed = _parse_response("".join(text_parts).strip())

        corrected = parsed.get("corrected")
        if not isinstance(corrected, dict):
            raise ValueError("response missing 'corrected' object")

        # Apply only non-empty string corrections for known beats. A blank or
        # missing field means "leave the original" — never wipe a beat.
        applied_fields = []
        for field in _BEAT_FIELDS:
            new_val = corrected.get(field)
            if isinstance(new_val, str) and new_val.strip():
                if new_val.strip() != str(script.get(field, "")).strip():
                    applied_fields.append(field)
                script[field] = new_val.strip()

        issues = [
            str(i).strip()
            for i in parsed.get("issues_found", [])
            if str(i).strip()
        ]

        if applied_fields:
            # Force full_script rebuild from the corrected beats downstream.
            script["full_script"] = ""
            logger.info(
                f"[fact_checker] Corrected {len(applied_fields)} beat(s) "
                f"({', '.join(applied_fields)}): {'; '.join(issues) or 'no detail'}"
            )
        else:
            logger.info("[fact_checker] No corrections needed — script verified.")

        script["fact_check"] = {
            "applied": bool(applied_fields),
            "fields": applied_fields,
            "issues_found": issues,
            "model": config.CLAUDE_FACTCHECK_MODEL,
        }
        return script

    except Exception as e:
        logger.warning(
            f"[fact_checker] Fact-check failed (fail-open, keeping original): {e}"
        )
        script["fact_check"] = {"applied": False, "skipped": f"error: {e}"}
        return script
