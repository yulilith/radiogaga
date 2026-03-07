import asyncio
import logging
import os

import pytest

from radioagent.agents.runtime import ClaudeSdkTurnGenerator
from radioagent.config import load_settings
from radioagent.models import GenerateTurnMessage, HistoryEntry, PromptConfig


def build_live_sdk_message() -> GenerateTurnMessage:
    return GenerateTurnMessage(
        session_id="session_live_sdk_test",
        topic="Live SDK generation test",
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
                text="Debate topic: Live SDK generation test",
            )
        ],
    )


@pytest.mark.asyncio
@pytest.mark.skipif(
    os.getenv("RUN_LIVE_SDK_TEST") != "1",
    reason="Set RUN_LIVE_SDK_TEST=1 to run the real Claude SDK integration check",
)
async def test_live_claude_sdk_generates_valid_response() -> None:
    settings = load_settings()
    generator = ClaudeSdkTurnGenerator(
        workspace_dir=settings.workspace_dir,
        logger=logging.getLogger("test-sdk-live"),
        default_model=settings.anthropic_model,
    )
    message = build_live_sdk_message()

    try:
        response = await asyncio.wait_for(generator.generate_turn(message), timeout=30)
    except Exception:
        raise
    assert response
    assert "api error" not in response.lower()

