from radioagent.debate.orchestrator import DebateOrchestrator
from radioagent.models import PromptConfig, TurnResultMessage


def test_orchestrator_rotates_turns_and_completes() -> None:
    orchestrator = DebateOrchestrator()
    orchestrator.start_debate(
        "Should AI radios stay local-first?",
        ["agent_a", "agent_b"],
        max_turns=2,
    )

    request = orchestrator.build_next_turn(
        {
            "agent_a": PromptConfig(
                agent_id="agent_a",
                display_name="Alex",
                voice_id="voice_1",
                system_prompt="Prompt A",
            ),
            "agent_b": PromptConfig(
                agent_id="agent_b",
                display_name="Blair",
                voice_id="voice_2",
                system_prompt="Prompt B",
            ),
        }
    )
    assert request.speaker_id == "agent_a"
    assert request.turn_index == 1

    state = orchestrator.apply_turn_result(
        TurnResultMessage(
            session_id=request.session_id,
            turn_index=1,
            speaker_id="agent_a",
            speaker_name="Alex",
            text="Privacy should come first.",
        )
    )
    assert state.next_speaker_id == "agent_b"
    assert state.status == "running"

    orchestrator.inject_user_message("What about convenience?")
    follow_up = orchestrator.build_next_turn(
        {
            "agent_a": PromptConfig(
                agent_id="agent_a",
                display_name="Alex",
                voice_id="voice_1",
                system_prompt="Prompt A",
            ),
            "agent_b": PromptConfig(
                agent_id="agent_b",
                display_name="Blair",
                voice_id="voice_2",
                system_prompt="Prompt B",
            ),
        }
    )
    assert follow_up.speaker_id == "agent_b"
    assert follow_up.history[-1].text == "What about convenience?"

    state = orchestrator.apply_turn_result(
        TurnResultMessage(
            session_id=follow_up.session_id,
            turn_index=2,
            speaker_id="agent_b",
            speaker_name="Blair",
            text="Convenience matters when people can inspect the boundary.",
        )
    )
    assert state.status == "complete"
    assert state.next_speaker_id is None


def test_user_injection_reroutes_to_other_host_during_active_turn() -> None:
    orchestrator = DebateOrchestrator()
    prompts = {
        "agent_a": PromptConfig(
            agent_id="agent_a",
            display_name="Alex",
            voice_id="voice_1",
            system_prompt="Prompt A",
        ),
        "agent_b": PromptConfig(
            agent_id="agent_b",
            display_name="Blair",
            voice_id="voice_2",
            system_prompt="Prompt B",
        ),
    }

    orchestrator.start_debate(
        "Which school is best?",
        ["agent_a", "agent_b"],
        max_turns=4,
    )
    first_request = orchestrator.build_next_turn(prompts)

    assert first_request.speaker_id == "agent_a"
    assert orchestrator.expects_turn_result(first_request.session_id, "agent_a")

    state = orchestrator.inject_user_message("Caller says Northwestern is clearly best")

    assert state.waiting_for_agent_id is None
    assert state.next_speaker_id == "agent_b"
    assert state.history[-1].source == "user"

    rerouted_request = orchestrator.build_next_turn(prompts)

    assert rerouted_request.speaker_id == "agent_b"
    assert rerouted_request.turn_index == 1
    assert rerouted_request.history[-1].text == "Caller says Northwestern is clearly best"

