from radioagent.config import load_settings
from radioagent.prompts.loader import load_prompt_config, load_prompt_configs


def test_prompt_loader_reads_single_prompt() -> None:
    settings = load_settings()
    prompt = load_prompt_config(settings.prompt_dir / "agent_a.yaml")
    assert prompt.agent_id == "agent_a"
    assert prompt.display_name == "Alex"
    assert "live AI debate show" in prompt.system_prompt


def test_prompt_loader_reads_all_prompts() -> None:
    settings = load_settings()
    prompts = load_prompt_configs(settings.prompt_dir)
    assert set(prompts) == {"agent_a", "agent_b"}

