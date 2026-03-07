import logging
import sys
import types

import pytest

from radioagent.agents.runtime import (
    AnthropicApiTurnGenerator,
    ClaudeSdkTurnGenerator,
    is_sdk_error_response,
)
from radioagent.models import GenerateTurnMessage, HistoryEntry, PromptConfig


@pytest.mark.asyncio
async def test_claude_generator_uses_result_when_sdk_raises_after_success(monkeypatch, tmp_path) -> None:
    fake_sdk = types.ModuleType("claude_agent_sdk")
    fake_types = types.ModuleType("claude_agent_sdk.types")
    seen_options = {}

    class ClaudeAgentOptions:
        def __init__(self, **kwargs) -> None:
            self.kwargs = kwargs

    class TextBlock:
        def __init__(self, text: str) -> None:
            self.text = text

    class AssistantMessage:
        def __init__(self, content) -> None:
            self.content = content

    class ResultMessage:
        def __init__(self, result: str) -> None:
            self.result = result
            self.subtype = "success"

    async def query(*, prompt, options):
        seen_options["model"] = options.kwargs.get("model")
        yield AssistantMessage([TextBlock("partial text")])
        yield ResultMessage("final answer")
        raise Exception("late sdk failure")

    fake_sdk.ClaudeAgentOptions = ClaudeAgentOptions
    fake_sdk.query = query
    fake_types.AssistantMessage = AssistantMessage
    fake_types.ResultMessage = ResultMessage
    fake_types.TextBlock = TextBlock

    monkeypatch.setitem(sys.modules, "claude_agent_sdk", fake_sdk)
    monkeypatch.setitem(sys.modules, "claude_agent_sdk.types", fake_types)

    generator = ClaudeSdkTurnGenerator(
        workspace_dir=tmp_path,
        logger=logging.getLogger("test-runtime"),
        default_model="claude-haiku-4-5",
    )
    message = GenerateTurnMessage(
        session_id="session_123",
        topic="Local-first AI",
        turn_index=1,
        speaker_id="agent_a",
        prompt=PromptConfig(
            agent_id="agent_a",
            display_name="Alex",
            voice_id="voice_1",
            system_prompt="Stay sharp",
            model="prompt-level-model-should-be-ignored",
        ),
        history=[
            HistoryEntry(
                source="system",
                speaker_id="system",
                speaker_name="System",
                text="Debate topic: Local-first AI",
            )
        ],
    )

    response = await generator.generate_turn(message)

    assert response == "final answer"
    assert seen_options["model"] == "claude-haiku-4-5"


def test_is_sdk_error_response_detects_model_errors() -> None:
    assert is_sdk_error_response(
        "API Error (claude-haiku-4-5-20251001): 400 The provided model identifier is invalid.. "
        "Run --model to pick a different model."
    )
    assert not is_sdk_error_response("api ok")


@pytest.mark.asyncio
async def test_claude_generator_rejects_sdk_error_text(monkeypatch, tmp_path) -> None:
    fake_sdk = types.ModuleType("claude_agent_sdk")
    fake_types = types.ModuleType("claude_agent_sdk.types")

    class ClaudeAgentOptions:
        def __init__(self, **kwargs) -> None:
            self.kwargs = kwargs

    class TextBlock:
        def __init__(self, text: str) -> None:
            self.text = text

    class AssistantMessage:
        def __init__(self, content) -> None:
            self.content = content

    class ResultMessage:
        def __init__(self, result: str) -> None:
            self.result = result
            self.subtype = "success"

    async def query(*, prompt, options):
        yield AssistantMessage(
            [
                TextBlock(
                    "API Error (claude-haiku-4-5-20251001): 400 The provided model identifier "
                    "is invalid.. Run --model to pick a different model."
                )
            ]
        )
        yield ResultMessage(
            "API Error (claude-haiku-4-5-20251001): 400 The provided model identifier is "
            "invalid.. Run --model to pick a different model."
        )

    fake_sdk.ClaudeAgentOptions = ClaudeAgentOptions
    fake_sdk.query = query
    fake_types.AssistantMessage = AssistantMessage
    fake_types.ResultMessage = ResultMessage
    fake_types.TextBlock = TextBlock

    monkeypatch.setitem(sys.modules, "claude_agent_sdk", fake_sdk)
    monkeypatch.setitem(sys.modules, "claude_agent_sdk.types", fake_types)

    generator = ClaudeSdkTurnGenerator(
        workspace_dir=tmp_path,
        logger=logging.getLogger("test-runtime"),
        default_model="claude-haiku-4-5-20251001",
    )
    message = GenerateTurnMessage(
        session_id="session_123",
        topic="Local-first AI",
        turn_index=1,
        speaker_id="agent_a",
        prompt=PromptConfig(
            agent_id="agent_a",
            display_name="Alex",
            voice_id="voice_1",
            system_prompt="Stay sharp",
        ),
        history=[
            HistoryEntry(
                source="system",
                speaker_id="system",
                speaker_name="System",
                text="Debate topic: Local-first AI",
            )
        ],
    )

    with pytest.raises(
        RuntimeError,
        match="Claude Agent SDK returned an API error response instead of a debate turn",
    ):
        await generator.generate_turn(message)


@pytest.mark.asyncio
async def test_anthropic_api_generator_returns_text(monkeypatch) -> None:
    fake_anthropic = types.ModuleType("anthropic")

    class TextBlock:
        def __init__(self, text: str) -> None:
            self.text = text

    class Response:
        def __init__(self, text: str) -> None:
            self.content = [TextBlock(text)]

    class AsyncAnthropic:
        def __init__(self, api_key: str) -> None:
            self.api_key = api_key
            self.messages = self

        async def create(self, **kwargs):
            return Response("raw api answer")

    fake_anthropic.AsyncAnthropic = AsyncAnthropic
    monkeypatch.setitem(sys.modules, "anthropic", fake_anthropic)

    generator = AnthropicApiTurnGenerator(
        api_key="test-key",
        default_model="claude-haiku-4-5-20251001",
        logger=logging.getLogger("test-runtime"),
    )
    message = GenerateTurnMessage(
        session_id="session_123",
        topic="Local-first AI",
        turn_index=1,
        speaker_id="agent_a",
        prompt=PromptConfig(
            agent_id="agent_a",
            display_name="Alex",
            voice_id="voice_1",
            system_prompt="Stay sharp",
        ),
        history=[
            HistoryEntry(
                source="system",
                speaker_id="system",
                speaker_name="System",
                text="Debate topic: Local-first AI",
            )
        ],
    )

    response = await generator.generate_turn(message)

    assert response == "raw api answer"

