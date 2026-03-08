"""
DuckDuckGo research gate: fetches top snippets for anti-hallucination grounding.
Falls back gracefully (returns empty string) if DuckDuckGo is unreachable.
"""
import logging
import re
import urllib.parse
import urllib.request

logger = logging.getLogger(__name__)

_TIMEOUT = 10
_MAX_SNIPPETS = 8
_MAX_SNIPPET_LEN = 300


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

        snippets = re.findall(
            r'class="result__snippet"[^>]*>(.*?)</a>', html, re.DOTALL
        )
        clean = []
        for s in snippets[:_MAX_SNIPPETS]:
            s = re.sub(r"<[^>]+>", "", s).strip()
            s = re.sub(r"\s+", " ", s)
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
