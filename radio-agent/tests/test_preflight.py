from radioagent.preflight import extract_error_message, resolve_anthropic_model_id


def test_extract_error_message_prefers_nested_error_message() -> None:
    payload = {
        "error": {
            "type": "invalid_request_error",
            "message": "Your credit balance is too low.",
        }
    }
    assert extract_error_message(payload) == "Your credit balance is too low."


def test_resolve_anthropic_model_id_prefers_exact_match_then_versioned_match() -> None:
    models = [
        {"id": "claude-sonnet-4-6"},
        {"id": "claude-haiku-4-5-20251001"},
    ]
    assert resolve_anthropic_model_id("claude-haiku-4-5", models) == "claude-haiku-4-5-20251001"
    assert resolve_anthropic_model_id("claude-sonnet-4-6", models) == "claude-sonnet-4-6"

