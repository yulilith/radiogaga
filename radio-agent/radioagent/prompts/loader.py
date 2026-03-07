from __future__ import annotations

from pathlib import Path

import yaml

from radioagent.models import PromptConfig


def load_prompt_config(path: Path) -> PromptConfig:
    payload = yaml.safe_load(path.read_text()) or {}
    return PromptConfig(**payload)


def load_prompt_configs(prompt_dir: Path) -> dict[str, PromptConfig]:
    configs: dict[str, PromptConfig] = {}
    for path in sorted(prompt_dir.glob("*.yaml")):
        config = load_prompt_config(path)
        configs[config.agent_id] = config
    return configs

