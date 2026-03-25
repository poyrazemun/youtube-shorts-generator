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

# How long before an in_progress entry is considered stale and reset to failed
STALE_HOURS = 2

BATCH_SIZE = 25
MAX_COUNT_PER_ENTRY = 1
MIN_VIRALITY_SCORE = 7  # topics scoring below this are discarded

def _load_registry() -> list[dict]:
    """Load video_registry.json entries."""
    try:
        if config.VIDEO_REGISTRY_PATH.exists():
            with open(config.VIDEO_REGISTRY_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
    except Exception as e:
        logger.warning(f"[topic_discovery] Could not load video registry: {e}")
    return []


def _load_used_keywords() -> set[str]:
    """Load keywords already used in uploaded videos from video_registry.json."""
    registry = _load_registry()
    return {
        entry.get("keyword", "").strip().lower()
        for entry in registry
        if entry.get("keyword", "").strip()
    }


def _load_done_keywords() -> set[str]:
    """Load keywords already marked done in the topic queue."""
    queue = load_queue()
    return {
        e["keyword"].strip().lower()
        for e in queue.get("topics", [])
        if e["status"] == "done" and e.get("keyword")
    }


SYSTEM_PROMPT = """You are a YouTube Shorts content strategist specializing in
strange and unbelievable historical events. You generate batches of
(topic, keyword, count, virality_score) combinations designed to maximize engagement.
Always respond with valid JSON only — no markdown fences, no extra text."""

USER_PROMPT_TEMPLATE = """Generate exactly {batch_size} unique YouTube Shorts content ideas
for a history channel called "Unreal History". Each entry must specify:
  - topic: A dramatic framing angle (e.g. "Bizarre Medieval Punishments")
  - keyword: A single concrete discovery keyword (e.g. "torture")
  - count: How many videos to make (integer, 1-{max_count})
  - virality_score: Integer 1-10 rating of viral potential using this rubric:
      9-10: Sounds completely fake but is true. Debunks something everyone believes. Famous person in shocking/absurd context.
      7-8:  Genuinely surprising with strong hook potential. Grotesque or absurd outcome.
      5-6:  Interesting but somewhat predictable. Common historical knowledge.
      1-4:  Dry, purely educational, no shock value or twist.

Rules:
- Vary eras: ancient, medieval, early modern, 19th century, 20th century
- Vary geographies: Asia, Africa, Middle East, Americas — not only Europe/USA
- Make topics sound dramatic and click-worthy for YouTube
- Keywords must be concrete nouns that anchor historical content
- No duplicate keywords across all entries
- Be honest with virality scores — do not inflate them
- NEVER generate topics about: suicide or self-harm methods, sexual violence, child abuse or harm to minors, terrorism glorification, or content targeting real living people with false claims — these cause channel strikes on YouTube
- NEVER frame topics as conspiracy theories: no "covered up by", "suppressed", "they don't want you to know" angles — YouTube's algorithm demotes this framing
{hints_block}{used_keywords_block}
Return ONLY a JSON array (no markdown, no extra text):
[
  {{"topic": "Bizarre Medieval Punishments", "keyword": "torture", "count": 1, "virality_score": 8}},
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


def _build_used_keywords_block(used_keywords: set[str], registry: list[dict]) -> str:
    parts = []
    if registry:
        titles = [entry.get("title", "") for entry in registry if entry.get("title")]
        if titles:
            title_list = "\n".join(f"  - {t}" for t in titles)
            parts.append(
                f"\nALREADY UPLOADED VIDEOS — do NOT generate topics covering the same events, "
                f"people, or stories, even under different keywords or angles:\n"
                f"{title_list}\n"
            )
    if used_keywords:
        kw_list = ", ".join(sorted(used_keywords))
        parts.append(
            f"\nALREADY USED KEYWORDS — do NOT reuse any of these:\n"
            f"{kw_list}\n"
        )
    if parts:
        parts.append("Generate completely different events, keywords, and topics.\n")
    return "".join(parts)


def generate_topic_queue(performance_hints: str = "") -> list[dict]:
    """
    Call Claude to generate BATCH_SIZE topic combos.
    Returns raw list of {topic, keyword, count} dicts. Does NOT write to disk.
    Raises on Claude API error or JSON parse failure.
    """
    registry = _load_registry()
    used_keywords = _load_used_keywords() | _load_done_keywords()

    client = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)
    hints_block = _build_hints_block(performance_hints)
    used_keywords_block = _build_used_keywords_block(used_keywords, registry)
    prompt = USER_PROMPT_TEMPLATE.format(
        batch_size=BATCH_SIZE,
        max_count=MAX_COUNT_PER_ENTRY,
        hints_block=hints_block,
        used_keywords_block=used_keywords_block,
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

    # Validate, score-filter, and sort each entry
    validated = []
    seen_keywords = set()
    discarded = 0
    for t in topics:
        kw = str(t.get("keyword", "")).strip().lower()
        if not kw or kw in seen_keywords:
            continue
        # Skip keywords already used in uploaded videos
        if kw in used_keywords:
            logger.debug(
                f"[topic_discovery] Skipped already-used keyword '{kw}': "
                f"{t.get('topic', '')[:60]}"
            )
            discarded += 1
            continue
        seen_keywords.add(kw)
        score = max(1, min(10, int(t.get("virality_score", 5))))
        if score < MIN_VIRALITY_SCORE:
            discarded += 1
            logger.debug(
                f"[topic_discovery] Discarded low-score topic (score={score}): "
                f"{t.get('topic', '')[:60]}"
            )
            continue
        validated.append({
            "topic": str(t.get("topic", "Strange Moments in History")).strip(),
            "keyword": kw,
            "count": max(1, min(MAX_COUNT_PER_ENTRY, int(t.get("count", 1)))),
            "virality_score": score,
        })

    # Sort best topics first so pick_next_topic always picks the highest scorer
    validated.sort(key=lambda x: x["virality_score"], reverse=True)

    logger.info(
        f"[topic_discovery] Generated {len(validated)} topics "
        f"(score>={MIN_VIRALITY_SCORE}), discarded {discarded} low-score topics."
    )
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
                f"(id={entry['id']}, score={entry.get('virality_score', '?')}, count={entry['count']})"
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
    Generate fresh topics, merge with existing queue (preserve recent in_progress),
    save to disk. Returns count of newly added entries.
    Raises on Claude failure — no partial save.
    """
    new_topics = generate_topic_queue(performance_hints=performance_hints)

    existing = load_queue()

    # Reset stale in_progress entries (crashed/interrupted runs)
    now = datetime.now(timezone.utc)
    still_running = []
    for e in existing.get("topics", []):
        if e["status"] != "in_progress":
            continue
        started = e.get("started_at")
        if started:
            try:
                started_dt = datetime.fromisoformat(started)
                age_hours = (now - started_dt).total_seconds() / 3600
                if age_hours > STALE_HOURS:
                    logger.info(
                        f"[topic_discovery] Resetting stale in_progress entry: "
                        f"'{e['keyword']}' (started {age_hours:.1f}h ago)"
                    )
                    e["status"] = "failed"
                    e["finished_at"] = _utcnow()
                    e["error"] = "stale — reset during refresh"
                    continue
            except (ValueError, TypeError):
                pass
        still_running.append(e)

    in_progress_keywords = {e["keyword"].lower() for e in still_running}
    added = []
    for t in new_topics:
        if t["keyword"].lower() not in in_progress_keywords:
            entry = {
                "id": uuid4().hex[:8],
                "topic": t["topic"],
                "keyword": t["keyword"],
                "count": t["count"],
                "virality_score": t.get("virality_score", 7),
                "status": "pending",
                "created_at": _utcnow(),
                "started_at": None,
                "finished_at": None,
                "slug": None,
                "error": None,
            }
            still_running.append(entry)
            added.append(entry)

    # Keep in_progress entries first, then sort pending by virality_score descending
    in_prog = [e for e in still_running if e["status"] == "in_progress"]
    pending = sorted(
        [e for e in still_running if e["status"] == "pending"],
        key=lambda x: x.get("virality_score", 0),
        reverse=True,
    )
    final_topics = in_prog + pending

    queue = {
        "generated_at": _utcnow(),
        "generated_with_hints": bool(performance_hints),
        "topics": final_topics,
    }
    save_queue(queue)
    logger.info(
        f"[topic_discovery] Queue replaced: {len(added)} new entries, "
        f"{len(in_prog)} in_progress preserved."
    )
    return len(added)
