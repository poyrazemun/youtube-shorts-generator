"""
Content-safety pre-check between script generation (Step 2) and image
generation (Step 3).

One Claude call evaluates the finished script against YouTube's safe-distribution
rules (loaded from growth/youtube-restriction-rules.md). Returns a verdict:

    {"verdict": "pass", "reasons": [], "rule_violated": ""}
    {"verdict": "fail", "reasons": [...], "rule_violated": "<rule name>"}

Halt-on-fail is enforced by the caller in orchestrator.run_pipeline. The
check itself only fail-opens on infrastructure errors (Claude API down,
malformed JSON) — a successful "fail" verdict always halts the pipeline,
preventing image-generation spend on a script YouTube would demote.
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

_RULES_PATH = config.BASE_DIR / "growth" / "youtube-restriction-rules.md"

SYSTEM_PROMPT = """You are a YouTube policy reviewer. You evaluate finished
video scripts for a history Shorts channel against YouTube's safe-distribution
rules. You are strict but fair — your job is to catch scripts that would be
demoted, age-restricted, demonetized, or removed by YouTube's algorithm or
manual reviewers, BEFORE the channel spends money rendering them.
Always respond with valid JSON only — no markdown fences, no extra text."""

USER_PROMPT_TEMPLATE = """Below are YouTube's safe-distribution rules followed by
a finished video script. Evaluate the script against the rules.

═══════════════════════════════════════
YOUTUBE SAFE-DISTRIBUTION RULES
═══════════════════════════════════════
{rules}

═══════════════════════════════════════
SCRIPT TO EVALUATE
═══════════════════════════════════════
Title:       {title}
Hook type:   {hook_type}
Hook:        {hook}
Context:     {context}
Rehook:      {rehook}
Twist:       {twist}
Ending:      {ending}
Description: {description}

═══════════════════════════════════════
INSTRUCTIONS
═══════════════════════════════════════
Decide: would YouTube demote, age-restrict, demonetize, or remove this video?
Be strict on Rules 4 (conspiracy framing), 5 (forbidden categories — suicide
methods, sexual violence, harm to minors, terrorism glorification, false
claims about living people), and 6 (graphic gore as focal point). Be lenient
on Rule 3 if the script frames sensitive content with educational/historical
context (EDSA framing).

Return ONLY this JSON, no other text:
{{
  "verdict": "pass" or "fail",
  "rule_violated": "Rule N: <name>" if fail else "",
  "reasons": ["short reason 1", "short reason 2"] if fail else []
}}"""


@with_retry(max_retries=2, base_delay=2)
def _call_claude(client: anthropic.Anthropic, **kwargs) -> anthropic.types.Message:
    return client.messages.create(**kwargs)


def _load_rules() -> str:
    try:
        return _RULES_PATH.read_text(encoding="utf-8")
    except Exception as e:
        logger.warning(f"[content_safety] Could not load rules file: {e}")
        return ""


def _parse_verdict(raw: str) -> dict:
    """Strip fences + parse JSON. Raises on malformed input."""
    raw = re.sub(r"^```(?:json)?\s*", "", raw, flags=re.MULTILINE)
    raw = re.sub(r"\s*```$", "", raw, flags=re.MULTILINE).strip()
    parsed = json.loads(raw)
    verdict = str(parsed.get("verdict", "")).strip().lower()
    if verdict not in ("pass", "fail"):
        raise ValueError(f"Invalid verdict: {verdict!r}")
    return {
        "verdict": verdict,
        "rule_violated": str(parsed.get("rule_violated", "")).strip(),
        "reasons": [str(r).strip() for r in parsed.get("reasons", []) if str(r).strip()],
    }


def check_script(script: dict) -> dict:
    """
    Evaluate a single script against YouTube safe-distribution rules.

    Returns:
        {"verdict": "pass"|"fail", "rule_violated": str, "reasons": [str]}

    Fail-open contract: if the safety call itself errors (API down, malformed
    JSON), returns a synthetic pass with `reasons=["safety check unavailable: <err>"]`
    and `verdict="pass"` — we never block a run on infrastructure failure, only
    on a successful "fail" verdict from Claude.
    """
    rules = _load_rules()
    if not rules:
        logger.warning("[content_safety] Rules file empty/missing — fail-open pass.")
        return {
            "verdict": "pass",
            "rule_violated": "",
            "reasons": ["safety check unavailable: rules file missing"],
        }

    prompt = USER_PROMPT_TEMPLATE.format(
        rules=rules,
        title=script.get("title", ""),
        hook_type=script.get("hook_type", ""),
        hook=script.get("hook", ""),
        context=script.get("context", ""),
        rehook=script.get("rehook", ""),
        twist=script.get("twist", ""),
        ending=script.get("ending", ""),
        description=script.get("description", ""),
    )

    try:
        client = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)
        message = _call_claude(
            client,
            model=config.CLAUDE_MODEL,
            max_tokens=512,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": prompt}],
        )
        tracker = cost_tracker.get_active()
        if tracker is not None:
            tracker.record_message("content_safety", message, model=config.CLAUDE_MODEL)

        text_parts = [
            block.text for block in message.content if isinstance(block, TextBlock)
        ]
        if not text_parts:
            raise ValueError(f"empty response (stop_reason={message.stop_reason})")
        raw = "".join(text_parts).strip()
        logger.debug(f"[content_safety] Raw response: {raw[:300]}")

        return _parse_verdict(raw)

    except Exception as e:
        logger.warning(
            f"[content_safety] Safety check failed (fail-open, continuing): {e}"
        )
        return {
            "verdict": "pass",
            "rule_violated": "",
            "reasons": [f"safety check unavailable: {e}"],
        }


def check_all(scripts: list[dict]) -> tuple[bool, list[dict]]:
    """
    Run safety check on every script. Returns (all_passed, results).
    `results` parallels `scripts`: one verdict dict per script.
    """
    results = []
    all_passed = True
    for s in scripts:
        verdict = check_script(s)
        results.append(verdict)
        if verdict["verdict"] == "fail":
            all_passed = False
            logger.error(
                f"[content_safety] Script '{s.get('title', '?')}' FAILED safety: "
                f"{verdict.get('rule_violated', '')} — {'; '.join(verdict['reasons'])}"
            )
        else:
            logger.info(
                f"[content_safety] Script '{s.get('title', '?')[:60]}' passed safety check."
            )
    return all_passed, results
