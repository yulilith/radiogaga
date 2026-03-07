import asyncio
import logging
import os

import pytest

from radioagent.agents.runtime import AnthropicApiTurnGenerator
from radioagent.config import load_settings
from radioagent.models import GenerateTurnMessage, HistoryEntry, PromptConfig


def build_live_anthropic_message() -> GenerateTurnMessage:
    return GenerateTurnMessage(
        session_id="session_live_anthropic_test",
        topic="Live Anthropic API generation test",
        turn_index=1,
        speaker_id="agent_a",
        prompt=PromptConfig(
            agent_id="agent_a",
            display_name="Alex",
            voice_id="voice_unused",
            system_prompt=(
                "Reply in one short natural sentence. Do not mention tools, APIs, or model names."
            ),
        ),
        history=[
            HistoryEntry(
                source="system",
                speaker_id="system",
                speaker_name="System",
                text="Debate topic: Live Anthropic API generation test",
            )
        ],
    )


@pytest.mark.asyncio
@pytest.mark.skipif(
    os.getenv("RUN_LIVE_ANTHROPIC_TEST") != "1",
    reason="Set RUN_LIVE_ANTHROPIC_TEST=1 to run the real Anthropic SDK integration check",
)
async def test_live_anthropic_api_generates_valid_response() -> None:
    settings = load_settings()
    generator = AnthropicApiTurnGenerator(
        api_key=settings.anthropic_api_key or "",
        default_model=settings.anthropic_model,
        logger=logging.getLogger("test-anthropic-live"),
    )
    message = build_live_anthropic_message()

    response = await asyncio.wait_for(generator.generate_turn(message), timeout=30)
    assert response
    assert "api error" not in response.lower()

