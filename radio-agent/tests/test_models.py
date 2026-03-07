from radioagent.models import (
    HistoryEntry,
    InjectUserMessage,
    GenerateTurnMessage,
    InterruptTurnMessage,
    PromptConfig,
    dump_socket_message,
    parse_socket_message,
)


def test_socket_message_round_trip() -> None:
    message = InjectUserMessage(text="What if the user wants to jump in?")
    parsed = parse_socket_message(dump_socket_message(message))
    assert isinstance(parsed, InjectUserMessage)
    assert parsed.text == message.text


def test_generate_turn_message_round_trip() -> None:
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

    parsed = parse_socket_message(dump_socket_message(message))
    assert isinstance(parsed, GenerateTurnMessage)
    assert parsed.turn_index == 1
    assert parsed.prompt.display_name == "Alex"


def test_interrupt_turn_message_round_trip() -> None:
    message = InterruptTurnMessage(session_id="session_123", reason="user_injected")

    parsed = parse_socket_message(dump_socket_message(message))

    assert isinstance(parsed, InterruptTurnMessage)
    assert parsed.session_id == "session_123"
    assert parsed.reason == "user_injected"

