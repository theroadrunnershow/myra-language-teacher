"""Wikipedia summary lookup — free, no API key required.

The model uses this for factual ``what is X?`` questions where a short
encyclopedia paragraph is the right answer. The Wikipedia REST
``/page/summary/{title}`` endpoint returns a single curated paragraph,
which is kid-safer than a free-form web search and short enough to
read back from speech.

On any failure (network, non-2xx, missing extract) the call returns
a kid-friendly ``ok=False`` and logs the underlying error.
"""

from __future__ import annotations

import asyncio
import logging
import urllib.parse
from typing import Any, Mapping

import requests

from tools.base import ToolResult

logger = logging.getLogger(__name__)


WEB_LOOKUP_TOOL_NAME = "web_lookup"
WIKIPEDIA_SUMMARY_URL = (
    "https://en.wikipedia.org/api/rest_v1/page/summary/{title}"
)
HTTP_TIMEOUT_S = 2.0
USER_AGENT = "myra-language-teacher/1.0 (kids-teacher)"
MAX_EXTRACT_CHARS = 800
KID_FRIENDLY_FAILURE = (
    "I can't look that up right now — please ask a grown-up."
)


class WebLookupTool:
    """Fetch a one-paragraph Wikipedia summary for a topic."""

    name = WEB_LOOKUP_TOOL_NAME

    def spec(self) -> dict:
        return {
            "type": "function",
            "name": WEB_LOOKUP_TOOL_NAME,
            "description": (
                "Look up a short summary of a topic on Wikipedia. Use "
                "this for factual 'what is X?' questions about animals, "
                "places, history, or famous people. Returns one "
                "paragraph — paraphrase before reading aloud."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "topic": {
                        "type": "string",
                        "description": (
                            "Topic to look up. A few words is plenty — "
                            "e.g. 'octopus', 'Mount Everest', "
                            "'Mahatma Gandhi'."
                        ),
                    },
                },
                "required": ["topic"],
                "additionalProperties": False,
            },
        }

    def prompt_block(self) -> str:
        return ""

    async def call(self, arguments: Mapping[str, Any]) -> ToolResult:
        topic = arguments.get("topic")
        if not isinstance(topic, str) or not topic.strip():
            return ToolResult(
                ok=False, detail="topic must be a non-empty string"
            )
        topic = topic.strip()
        try:
            extract = await asyncio.get_event_loop().run_in_executor(
                None, _fetch_summary, topic
            )
        except Exception as exc:
            logger.error("[web_lookup] API error for %r: %s", topic, exc)
            return ToolResult(ok=False, detail=KID_FRIENDLY_FAILURE)
        if not extract:
            return ToolResult(
                ok=False,
                detail=f"I couldn't find anything about {topic}.",
            )
        return ToolResult(
            ok=True,
            detail=extract[:MAX_EXTRACT_CHARS],
            data={"topic": topic, "source": "wikipedia"},
        )


def _fetch_summary(topic: str) -> str:
    title = urllib.parse.quote(topic.replace(" ", "_"), safe="")
    response = requests.get(
        WIKIPEDIA_SUMMARY_URL.format(title=title),
        timeout=HTTP_TIMEOUT_S,
        headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
    )
    if not response.ok:
        raise RuntimeError(f"wikipedia status {response.status_code}")
    body = response.json()
    if not isinstance(body, dict):
        raise RuntimeError("wikipedia returned non-object payload")
    extract = body.get("extract")
    if not isinstance(extract, str):
        return ""
    return extract.strip()


__all__ = [
    "KID_FRIENDLY_FAILURE",
    "MAX_EXTRACT_CHARS",
    "WEB_LOOKUP_TOOL_NAME",
    "WebLookupTool",
]
