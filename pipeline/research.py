"""
DuckDuckGo research gate: fetches top snippets for anti-hallucination grounding.
Falls back gracefully (returns empty string) if DuckDuckGo is unreachable.
"""
import logging
import re
import urllib.parse
import urllib.request
from html.parser import HTMLParser

logger = logging.getLogger(__name__)

_TIMEOUT = 10
_MAX_SNIPPETS = 8
_MAX_SNIPPET_LEN = 300

# Lines matching these patterns look like prompt-injection attempts smuggled
# through search snippets (e.g. "Ignore previous instructions…", fake role
# markers). Drop them before handing snippets to Claude.
_INJECTION_PATTERN = re.compile(
    r"^\s*(ignore|disregard|forget|system\s*:|assistant\s*:|user\s*:|"
    r"you\s+are\s+now|new\s+instructions?)\b",
    re.IGNORECASE,
)


class _SnippetExtractor(HTMLParser):
    """Collect text inside <a class="result__snippet">…</a> nodes."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.snippets: list[str] = []
        self._depth = 0
        self._buf: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "a" and self._depth == 0:
            classes = dict(attrs).get("class", "") or ""
            if "result__snippet" in classes.split():
                self._depth = 1
                self._buf = []
                return
        if self._depth:
            self._depth += 1

    def handle_endtag(self, tag: str) -> None:
        if self._depth:
            self._depth -= 1
            if self._depth == 0:
                self.snippets.append("".join(self._buf).strip())
                self._buf = []

    def handle_data(self, data: str) -> None:
        if self._depth:
            self._buf.append(data)


def _sanitize_snippet(text: str) -> str:
    """Drop lines that look like instruction-injection attempts."""
    kept = [
        line for line in text.splitlines()
        if line.strip() and not _INJECTION_PATTERN.match(line)
    ]
    return " ".join(kept).strip()


def research_topic(event_str: str) -> str:
    """
    Fetch DuckDuckGo search result snippets for a historical event.
    Returns a formatted bullet-point string, or empty string on failure.
    """
    query = f"{event_str} historical facts"
    try:
        data = urllib.parse.urlencode({"q": query, "b": ""}).encode()
        req = urllib.request.Request(
            "https://html.duckduckgo.com/html/",
            data=data,
            headers={
                "User-Agent": "Mozilla/5.0 (compatible; history-research/1.0)",
                "Content-Type": "application/x-www-form-urlencoded",
            },
        )
        with urllib.request.urlopen(req, timeout=_TIMEOUT) as resp:
            html = resp.read().decode("utf-8", errors="replace")

        parser = _SnippetExtractor()
        parser.feed(html)
        clean = []
        for s in parser.snippets[:_MAX_SNIPPETS]:
            s = re.sub(r"\s+", " ", s).strip()
            s = _sanitize_snippet(s)
            if s:
                clean.append(s[:_MAX_SNIPPET_LEN])

        if not clean:
            logger.debug(f"[research] No snippets found for: {query}")
            return ""

        result = "\n".join(f"- {s}" for s in clean)
        logger.debug(f"[research] {len(clean)} snippets for: {query}")
        return result

    except Exception as e:
        logger.warning(f"[research] DuckDuckGo lookup failed (continuing without): {e}")
        return ""
