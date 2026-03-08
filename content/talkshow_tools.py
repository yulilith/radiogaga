"""Anthropic tool schemas and dispatcher for talk show agent tool use."""

from __future__ import annotations

from typing import TYPE_CHECKING

from log import get_logger

if TYPE_CHECKING:
    from context.exa_search import ExaSearchService

logger = get_logger(__name__)


INTERRUPT_TOOL = {
    "name": "interrupt",
    "description": (
        "Jump into the conversation because you have something compelling to say. "
        "Use this SPARINGLY — only when you genuinely disagree, have a great joke, "
        "or a fact that cannot wait. Do not interrupt just to agree."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "what_i_want_to_say": {
                "type": "string",
                "description": "A one-sentence preview of what you want to jump in with.",
            },
        },
        "required": ["what_i_want_to_say"],
    },
}

INTROSPECT_TOOL = {
    "name": "introspect",
    "description": (
        "Think privately before speaking. Your thought is NOT broadcast on air. "
        "Use this to plan your angle, recall facts, or decide your position."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "thought": {
                "type": "string",
                "description": "Your private reasoning or planning thought.",
            },
        },
        "required": ["thought"],
    },
}

WEB_SEARCH_TOOL = {
    "name": "web_search",
    "description": (
        "Search the internet for current facts to back up your point. "
        "Results are private — weave them into your speech naturally."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "A concise search query.",
            },
        },
        "required": ["query"],
    },
}

SPEAKER_TOOLS = [INTROSPECT_TOOL, WEB_SEARCH_TOOL]
LISTENER_TOOLS = [INTERRUPT_TOOL, INTROSPECT_TOOL, WEB_SEARCH_TOOL]


async def handle_tool_call(
    name: str,
    tool_input: dict,
    exa_service: ExaSearchService | None = None,
) -> str:
    """Dispatch a tool call and return the result text."""
    if name == "introspect":
        thought = tool_input.get("thought", "")
        logger.debug("tool.introspect", extra={"thought": thought[:80]})
        return "Thought noted."

    if name == "web_search":
        query = tool_input.get("query", "")
        logger.info("tool.web_search", extra={"query": query[:60]})
        if not exa_service or not exa_service.available:
            return "Search unavailable."
        results = await exa_service.search(query)
        if not results:
            return "No results found."
        formatted = []
        for r in results:
            formatted.append(f"- {r['title']}: {r['snippet']}")
        return "\n".join(formatted)

    if name == "interrupt":
        preview = tool_input.get("what_i_want_to_say", "")
        logger.info("tool.interrupt", extra={"preview": preview[:60]})
        return "Interrupt registered."

    logger.warning("tool.unknown: %s", name)
    return "Unknown tool."
