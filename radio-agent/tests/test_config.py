from radioagent.config import load_settings


def test_default_runtime_models_are_pinned(monkeypatch) -> None:
    monkeypatch.setenv("RADIO_ANTHROPIC_MODEL", "test-anthropic-model")
    monkeypatch.setenv("RADIO_ELEVENLABS_MODEL", "test-elevenlabs-model")
    monkeypatch.setenv("RADIO_ELEVENLABS_SPEED", "1.1")
    settings = load_settings()
    assert settings.anthropic_model == "test-anthropic-model"
    assert settings.elevenlabs_model == "test-elevenlabs-model"
    assert settings.elevenlabs_speed == 1.1

