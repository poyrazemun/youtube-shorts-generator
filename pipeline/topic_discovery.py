"""
T3-A — Topic Queue
Claude generates a batch of 25 (topic, keyword, count) combos tuned for
YouTube Shorts history content. The queue is saved to topics_queue.json
with a status lifecycle: pending → in_progress → done / failed.
"""
import json
import logging
import re
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import anthropic

import config
from pipeline.retry import with_retry

logger = logging.getLogger(__name__)

BATCH_SIZE = 25
MAX_COUNT_PER_ENTRY = 1

SYSTEM_PROMPT = """You are a YouTube Shorts content strategist specializing in
strange and unbelievable historical events. You generate batches of
(topic, keyword, count) combinations designed to maximize engagement.
Always respond with valid JSON only — no markdown fences, no extra text."""

USER_PROMPT_TEMPLATE = """Generate exactly {batch_size} unique YouTube Shorts content ideas
for a history channel called "Unreal History". Each entry must specify:
  - topic: A dramatic framing angle (e.g. "Bizarre Medieval Punishments")
  - keyword: A single concrete discovery keyword (e.g. "torture")
  - count: How many videos to make (integer, 1-{max_count})

Rules:
- Vary eras: ancient, medieval, early modern, 19th century, 20th century
- Vary geographies: Asia, Africa, Middle East, Americas — not only Europe/USA
- Make topics sound dramatic and click-worthy for YouTube
- Keywords must be concrete nouns that anchor historical content
- No duplicate keywords across all entries
{hints_block}
Return ONLY a JSON array (no markdown, no extra text):
[
  {{"topic": "Bizarre Medieval Punishments", "keyword": "torture", "count": 3}},
  ...
]"""


def _utcnow() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _build_hints_block(performance_hints: str) -> str:
    if not performance_hints:
        return ""
    return (
        "\nPERFORMANCE DATA — bias your selections based on this:\n"
        f"{performance_hints}\n\n"
        "Prefer topics and keywords similar to high-performing ones. "
        "Avoid keywords similar to low-performing ones unless you have a strong creative angle.\n"
    )


@with_retry(max_retries=3, base_delay=2)
def _call_claude(client: anthropic.Anthropic, **kwargs) -> anthropic.types.Message:
    return client.messages.create(**kwargs)


def generate_topic_queue(performance_hints: str = "") -> list[dict]:
    """
    Call Claude to generate BATCH_SIZE topic combos.
    Returns raw list of {topic, keyword, count} dicts. Does NOT write to disk.
    Raises on Claude API error or JSON parse failure.
    """
    client = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)
    hints_block = _build_hints_block(performance_hints)
    prompt = USER_PROMPT_TEMPLATE.format(
        batch_size=BATCH_SIZE,
        max_count=MAX_COUNT_PER_ENTRY,
        hints_block=hints_block,
    )

    logger.info(f"[topic_discovery] Calling Claude to generate {BATCH_SIZE} topic combos...")
    message = _call_claude(
        client,
        model=config.CLAUDE_MODEL,
        max_tokens=2048,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": prompt}],
    )

    raw = message.content[0].text.strip()
    logger.debug(f"[topic_discovery] Raw Claude response:\n{raw[:500]}")

    # Strip markdown fences if present
    raw = re.sub(r"^```(?:json)?\s*", "", raw, flags=re.MULTILINE)
    raw = re.sub(r"\s*```$", "", raw, flags=re.MULTILINE).strip()

    topics = json.loads(raw)
    if not isinstance(topics, list):
        raise ValueError(f"Expected JSON array, got {type(topics)}")

    # Validate and clamp each entry
    validated = []
    seen_keywords = set()
    for t in topics:
        kw = str(t.get("keyword", "")).strip().lower()
        if not kw or kw in seen_keywords:
            continue
        seen_keywords.add(kw)
        validated.append({
            "topic": str(t.get("topic", "Strange Moments in History")).strip(),
            "keyword": kw,
            "count": max(1, min(MAX_COUNT_PER_ENTRY, int(t.get("count", 3)))),
        })

    logger.info(f"[topic_discovery] Generated {len(validated)} unique topic combos.")
    return validated


def load_queue() -> dict:
    """Load topics_queue.json. Returns empty structure on any error."""
    try:
        if config.TOPICS_QUEUE_PATH.exists():
            with open(config.TOPICS_QUEUE_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
    except Exception as e:
        logger.warning(f"[topic_discovery] Could not load queue: {e}")
    return {"generated_at": None, "topics": []}


def save_queue(queue: dict) -> None:
    """Atomically write queue to disk (write-to-tmp then rename)."""
    tmp = config.TOPICS_QUEUE_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(queue, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(config.TOPICS_QUEUE_PATH)
    logger.debug(f"[topic_discovery] Queue saved: {len(queue.get('topics', []))} entries.")


def pick_next_topic() -> dict | None:
    """
    Return the next pending entry and mark it in_progress.
    Returns None if queue is exhausted (no pending entries).
    """
    queue = load_queue()
    for entry in queue["topics"]:
        if entry["status"] == "pending":
            entry["status"] = "in_progress"
            entry["started_at"] = _utcnow()
            save_queue(queue)
            logger.info(
                f"[topic_discovery] Picked: '{entry['topic']}' / '{entry['keyword']}' "
                f"(id={entry['id']}, count={entry['count']})"
            )
            return entry
    return None


def mark_topic_done(topic_id: str, slug: str) -> None:
    """Mark entry as done and record the output slug."""
    queue = load_queue()
    for entry in queue["topics"]:
        if entry["id"] == topic_id:
            entry["status"] = "done"
            entry["finished_at"] = _utcnow()
            entry["slug"] = slug
            save_queue(queue)
            logger.info(f"[topic_discovery] Marked done: {topic_id} (slug={slug})")
            return
    logger.warning(f"[topic_discovery] mark_topic_done: id '{topic_id}' not found in queue.")


def mark_topic_failed(topic_id: str, error: str) -> None:
    """Mark entry as failed and record the error message."""
    queue = load_queue()
    for entry in queue["topics"]:
        if entry["id"] == topic_id:
            entry["status"] = "failed"
            entry["finished_at"] = _utcnow()
            entry["error"] = error[:500]
            save_queue(queue)
            logger.warning(f"[topic_discovery] Marked failed: {topic_id} — {error[:100]}")
            return
    logger.warning(f"[topic_discovery] mark_topic_failed: id '{topic_id}' not found in queue.")


def refresh_queue(performance_hints: str = "") -> int:
    """
    Generate fresh topics, merge with existing queue (preserve pending/in_progress),
    save to disk. Returns count of newly added entries.
    Raises on Claude failure — no partial save.
    """
    new_topics = generate_topic_queue(performance_hints=performance_hints)

    existing = load_queue()
    # Keep entries that haven't been processed yet (don't lose in-progress work)
    kept = [e for e in existing.get("topics", []) if e["status"] in ("pending", "in_progress")]

    existing_keywords = {e["keyword"].lower() for e in kept}
    added = []
    for t in new_topics:
        if t["keyword"].lower() not in existing_keywords:
            entry = {
                "id": uuid4().hex[:8],
                "topic": t["topic"],
                "keyword": t["keyword"],
                "count": t["count"],
                "status": "pending",
                "created_at": _utcnow(),
                "started_at": None,
                "finished_at": None,
                "slug": None,
                "error": None,
            }
            kept.append(entry)
            added.append(entry)

    queue = {
        "generated_at": _utcnow(),
        "generated_with_hints": bool(performance_hints),
        "topics": kept,
    }
    save_queue(queue)
    logger.info(
        f"[topic_discovery] Queue refreshed: {len(added)} new entries added, "
        f"{len(kept)} total pending/in_progress."
    )
    return len(added)
