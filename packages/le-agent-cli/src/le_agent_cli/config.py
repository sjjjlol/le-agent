"""Non-secret TOML configuration with explicit model capabilities."""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from le_agent_ai import Model, ModelRegistry


@dataclass(slots=True)
class ProviderConfig:
    kind: str
    base_url: str | None = None
    api_key_env: str | None = None


@dataclass(slots=True)
class AppConfig:
    default_model: str | None = None
    permission: str = "confirm"
    providers: dict[str, ProviderConfig] = field(default_factory=dict)
    models: dict[str, Model] = field(default_factory=dict)


def _merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    result = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _merge(result[key], value)
        else:
            result[key] = value
    return result


def _load(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    with path.open("rb") as handle:
        return tomllib.load(handle)


def load_config(*, user_path: Path, project_path: Path) -> AppConfig:
    data = _merge(_load(user_path), _load(project_path))
    providers = {
        name: ProviderConfig(
            kind=str(value.get("kind", name)),
            base_url=value.get("base_url"),
            api_key_env=value.get("api_key_env"),
        )
        for name, value in data.get("providers", {}).items()
    }
    models: dict[str, Model] = {}
    for name, value in data.get("models", {}).items():
        models[name] = Model(
            provider=str(value["provider"]),
            id=str(value["id"]),
            context_window=int(value["context_window"]),
            max_output_tokens=int(value["max_output_tokens"]),
            supports_tools=bool(value.get("supports_tools", True)),
            supports_thinking=bool(value.get("supports_thinking", False)),
            base_url=value.get("base_url"),
        )
    return AppConfig(
        default_model=data.get("default_model"),
        permission=str(data.get("permission", "confirm")),
        providers=providers,
        models=models,
    )


def registry_from_config(config: AppConfig) -> ModelRegistry:
    registry = ModelRegistry()
    for name, model in config.models.items():
        registry.register(name, model)
    return registry


def api_key_for(config: AppConfig, provider: str) -> str | None:
    provider_config = config.providers.get(provider)
    if provider_config and provider_config.api_key_env:
        return os.environ.get(provider_config.api_key_env)
    defaults = {"openai": "OPENAI_API_KEY", "anthropic": "ANTHROPIC_API_KEY"}
    return os.environ.get(defaults.get(provider, ""))
