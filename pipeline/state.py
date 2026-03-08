"""
Per-event stage completion tracker.
Saves state to output/<slug>/state.json.
Enables per-event skip on partial run resumption.
"""
import json
import logging
from datetime import datetime
from pathlib import Path

import config

logger = logging.getLogger(__name__)

STAGES = ["events", "scripts", "images", "audio", "captions", "video", "upload"]


class PipelineState:
    """
    Tracks completion status for each (event_idx, stage) pair.
    State is persisted after every update so partial runs resume correctly.
    """

    def __init__(self, slug: str) -> None:
        self.slug = slug
        self._path = config.OUTPUT_DIR / slug / "state.json"
        self._data: dict = {}  # {str(event_idx): {stage: {status, ts, artifacts}}}
        self._load()

    def _load(self) -> None:
        if self._path.exists():
            try:
                self._data = json.loads(self._path.read_text(encoding="utf-8"))
                logger.debug(f"[state] Loaded state from {self._path}")
            except Exception as e:
                logger.warning(f"[state] Could not load state file: {e} — starting fresh")
                self._data = {}

    def save(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(
            json.dumps(self._data, indent=2, ensure_ascii=False), encoding="utf-8"
        )

    def is_done(self, event_idx: int, stage: str) -> bool:
        """Return True if this (event, stage) already completed successfully."""
        entry = self._data.get(str(event_idx), {}).get(stage, {})
        return entry.get("status") == "done"

    def complete(
        self, event_idx: int, stage: str, artifacts: list[str] | None = None
    ) -> None:
        """Mark a (event, stage) pair as complete and persist."""
        key = str(event_idx)
        if key not in self._data:
            self._data[key] = {}
        self._data[key][stage] = {
            "status": "done",
            "ts": datetime.utcnow().isoformat(),
            "artifacts": artifacts or [],
        }
        self.save()
        logger.debug(f"[state] event={event_idx} stage={stage} → done")

    def fail(self, event_idx: int, stage: str, error: str) -> None:
        """Record a failure for (event, stage) and persist."""
        key = str(event_idx)
        if key not in self._data:
            self._data[key] = {}
        self._data[key][stage] = {
            "status": "failed",
            "ts": datetime.utcnow().isoformat(),
            "error": str(error),
        }
        self.save()
        logger.debug(f"[state] event={event_idx} stage={stage} → failed")
