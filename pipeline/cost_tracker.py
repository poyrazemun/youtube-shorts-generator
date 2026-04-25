"""
Per-run cost + timing tracker.

Records wall-clock per pipeline step, Claude token usage, and image-generation
counts per provider. On successful runs, writes a detailed `cost.json` into the
slug's output directory and appends a one-line summary to `output/cost_ledger.txt`.

The tracker is a process-global singleton: orchestrator instantiates one per
run and calls `set_active(tracker)`. Pipeline modules then call
`get_active()` and record into it if non-None — so steps work fine outside the
orchestrator (tests, ad-hoc invocations) without any tracker plumbing.
"""

import json
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import config

logger = logging.getLogger(__name__)


# ── Pricing helpers (pure functions, easy to test) ────────────────────────────


def claude_cost_usd(model: str, input_tokens: int, output_tokens: int) -> float:
    """Compute USD cost for a single Claude call given token counts."""
    rates = config.CLAUDE_PRICING.get(model)
    if not rates:
        # Unknown model — log once, treat as $0 rather than crash a real run.
        logger.warning(
            f"[cost] No pricing entry for model '{model}' — recorded as $0"
        )
        return 0.0
    return (input_tokens / 1_000_000) * rates["input"] + (
        output_tokens / 1_000_000
    ) * rates["output"]


def image_cost_usd(provider: str, count: int = 1) -> float:
    """Compute USD cost for `count` images from a given provider."""
    rate = config.IMAGE_PRICING.get(provider, 0.0)
    return rate * count


# ── Tracker ───────────────────────────────────────────────────────────────────


@dataclass
class _StepBucket:
    seconds: float = 0.0
    claude_input_tokens: int = 0
    claude_output_tokens: int = 0
    claude_calls: int = 0
    claude_cost_usd: float = 0.0
    images: dict[str, int] = field(default_factory=dict)  # provider → count
    image_cost_usd: float = 0.0


class CostTracker:
    def __init__(self, slug: str) -> None:
        self.slug = slug
        self._steps: dict[str, _StepBucket] = {}
        self._active_step: str | None = None
        self._step_started_at: float | None = None
        self._run_started_at = time.monotonic()

    # ── timing ───────────────────────────────────────────────────────────────
    def start_step(self, name: str) -> None:
        # End any step still open (defensive — shouldn't normally happen)
        if self._active_step is not None:
            self.end_step(self._active_step)
        self._active_step = name
        self._step_started_at = time.monotonic()
        self._steps.setdefault(name, _StepBucket())

    def end_step(self, name: str) -> None:
        if self._active_step != name or self._step_started_at is None:
            return
        elapsed = time.monotonic() - self._step_started_at
        self._steps[name].seconds += elapsed
        self._active_step = None
        self._step_started_at = None

    # ── recording ────────────────────────────────────────────────────────────
    def record_claude(
        self, step: str, model: str, input_tokens: int, output_tokens: int
    ) -> None:
        bucket = self._steps.setdefault(step, _StepBucket())
        bucket.claude_input_tokens += input_tokens
        bucket.claude_output_tokens += output_tokens
        bucket.claude_calls += 1
        bucket.claude_cost_usd += claude_cost_usd(
            model, input_tokens, output_tokens
        )

    def record_message(self, step: str, message: Any, model: str | None = None) -> None:
        """Convenience: extract usage from an anthropic Message object."""
        try:
            usage = getattr(message, "usage", None)
            if usage is None:
                return
            in_tok = int(getattr(usage, "input_tokens", 0) or 0)
            out_tok = int(getattr(usage, "output_tokens", 0) or 0)
            mdl = model or getattr(message, "model", "") or config.CLAUDE_MODEL
            self.record_claude(step, mdl, in_tok, out_tok)
        except Exception as e:
            logger.debug(f"[cost] record_message failed (ignored): {e}")

    def record_image(self, step: str, provider: str, count: int = 1) -> None:
        bucket = self._steps.setdefault(step, _StepBucket())
        bucket.images[provider] = bucket.images.get(provider, 0) + count
        bucket.image_cost_usd += image_cost_usd(provider, count)

    # ── totals ───────────────────────────────────────────────────────────────
    def total_seconds(self) -> float:
        return sum(s.seconds for s in self._steps.values())

    def total_claude_cost(self) -> float:
        return sum(s.claude_cost_usd for s in self._steps.values())

    def total_image_cost(self) -> float:
        return sum(s.image_cost_usd for s in self._steps.values())

    def total_cost(self) -> float:
        return self.total_claude_cost() + self.total_image_cost()

    def image_provider_counts(self) -> dict[str, int]:
        agg: dict[str, int] = {}
        for s in self._steps.values():
            for prov, n in s.images.items():
                agg[prov] = agg.get(prov, 0) + n
        return agg

    # ── output ───────────────────────────────────────────────────────────────
    def to_dict(self) -> dict:
        return {
            "slug": self.slug,
            "finished_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
            "total_seconds": round(self.total_seconds(), 2),
            "total_cost_usd": round(self.total_cost(), 4),
            "total_claude_cost_usd": round(self.total_claude_cost(), 4),
            "total_image_cost_usd": round(self.total_image_cost(), 4),
            "image_providers": self.image_provider_counts(),
            "steps": {
                name: {
                    "seconds": round(b.seconds, 2),
                    "claude_calls": b.claude_calls,
                    "claude_input_tokens": b.claude_input_tokens,
                    "claude_output_tokens": b.claude_output_tokens,
                    "claude_cost_usd": round(b.claude_cost_usd, 4),
                    "images": dict(b.images),
                    "image_cost_usd": round(b.image_cost_usd, 4),
                }
                for name, b in self._steps.items()
            },
        }

    def write_cost_json(self) -> Path:
        path = config.OUTPUT_DIR / self.slug / "cost.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(self.to_dict(), indent=2, ensure_ascii=False), encoding="utf-8"
        )
        return path

    def append_to_ledger(self, ledger_path: Path | None = None) -> Path:
        """
        Append (or replace, if same slug already present) one line for this
        run, then rewrite the TOTAL footer.
        """
        ledger_path = ledger_path or (config.OUTPUT_DIR / "cost_ledger.txt")
        ledger_path.parent.mkdir(parents=True, exist_ok=True)

        provider_counts = self.image_provider_counts()
        if provider_counts:
            img_part = " + ".join(
                f"{n}×{prov}" for prov, n in sorted(provider_counts.items())
            )
        else:
            img_part = "0 images"
        ts = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
        new_row = (
            f"{ts}  {self.slug:<40s}  {int(self.total_seconds()):>4d}s  "
            f"${self.total_cost():>7.4f}   "
            f"(claude ${self.total_claude_cost():.4f} | "
            f"img {img_part} ${self.total_image_cost():.4f})"
        )

        existing_rows: list[str] = []
        if ledger_path.exists():
            for line in ledger_path.read_text(encoding="utf-8").splitlines():
                stripped = line.strip()
                if not stripped:
                    continue
                if stripped.startswith("─") or stripped.startswith("TOTAL"):
                    continue
                # match by slug (column 2)
                parts = stripped.split()
                if len(parts) >= 2 and parts[1] == self.slug:
                    continue  # drop old row for same slug
                existing_rows.append(line.rstrip())

        existing_rows.append(new_row)

        # Recompute totals from rows
        total_seconds = 0
        total_cost = 0.0
        for row in existing_rows:
            try:
                # row format: "<ts>  <slug>  <NNNs>  $<cost>   (...)"
                tokens = row.split()
                # find "<NNN>s" and "$X.XXXX"
                secs_token = next(t for t in tokens if t.endswith("s") and t[:-1].isdigit())
                cost_token = next(t for t in tokens if t.startswith("$"))
                total_seconds += int(secs_token[:-1])
                total_cost += float(cost_token.lstrip("$"))
            except Exception:
                continue

        footer_sep = "─" * 100
        footer = f"TOTAL  {len(existing_rows)} videos     {total_seconds}s    ${total_cost:.4f}"

        body = "\n".join(existing_rows) + f"\n{footer_sep}\n{footer}\n"
        ledger_path.write_text(body, encoding="utf-8")
        return ledger_path

    def summary_line(self) -> str:
        provider_counts = self.image_provider_counts()
        if provider_counts:
            img_part = ", ".join(
                f"{n}×{prov}" for prov, n in sorted(provider_counts.items())
            )
        else:
            img_part = "no images"
        return (
            f"Pipeline finished in {int(self.total_seconds())}s, "
            f"~${self.total_cost():.4f} spend "
            f"(Claude ${self.total_claude_cost():.4f}, "
            f"images {img_part} ${self.total_image_cost():.4f})"
        )


# ── Module-level "active tracker" singleton ───────────────────────────────────

_active: CostTracker | None = None


def set_active(tracker: CostTracker | None) -> None:
    global _active
    _active = tracker


def get_active() -> CostTracker | None:
    return _active
