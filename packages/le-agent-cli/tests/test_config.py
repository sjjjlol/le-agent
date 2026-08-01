from pathlib import Path

import pytest
from le_agent_cli.config import load_config, registry_from_config, resolve_model_name


def test_project_config_overrides_user_default_model_and_builds_registry(tmp_path: Path) -> None:
    user = tmp_path / "user.toml"
    project = tmp_path / "project.toml"
    user.write_text(
        (
            "default_model = 'old'\n[models.old]\nprovider = 'openai'\nid = 'old'\n"
            "context_window = 1000\nmax_output_tokens = 100\n"
        ),
        encoding="utf-8",
    )
    project.write_text(
        (
            "default_model = 'new'\n[models.new]\nprovider = 'anthropic'\nid = 'new'\n"
            "context_window = 2000\nmax_output_tokens = 200\n"
        ),
        encoding="utf-8",
    )

    config = load_config(user_path=user, project_path=project)

    assert config.default_model == "new"
    assert registry_from_config(config).require("new").context_window == 2000


def test_builtin_model_catalog_has_concrete_openai_models_and_default(tmp_path: Path) -> None:
    config = load_config(user_path=tmp_path / "missing-user.toml", project_path=tmp_path / "missing-project.toml")

    assert config.default_model == "gpt-5.4-mini"
    expected = {
        "gpt-5.6-sol": (1_050_000, 128_000),
        "gpt-5.6-terra": (1_050_000, 128_000),
        "gpt-5.6-luna": (1_050_000, 128_000),
        "gpt-5.5": (1_050_000, 128_000),
        "gpt-5.4": (1_050_000, 128_000),
        "gpt-5.4-mini": (400_000, 128_000),
    }
    actual = {
        name: (config.models[name].context_window, config.models[name].max_output_tokens)
        for name in expected
    }
    assert actual == expected
    assert "gpt" not in config.models
    assert "claude" in config.models


def test_resolve_model_name_accepts_alias_unique_id_and_provider_id_and_rejects_ambiguity(tmp_path: Path) -> None:
    config = load_config(user_path=tmp_path / "user.toml", project_path=tmp_path / "project.toml")

    assert resolve_model_name(config, "gpt-5.4-mini") == "gpt-5.4-mini"
    assert resolve_model_name(config, "openai/gpt-5.4-mini") == "gpt-5.4-mini"

    duplicate = config.models["gpt-5.4-mini"].model_copy(update={"provider": "proxy"})
    config.models["mini-via-proxy"] = duplicate
    assert resolve_model_name(config, "gpt-5.4-mini") == "gpt-5.4-mini"
    config.models["shared-a"] = duplicate.model_copy(update={"id": "shared-id"})
    config.models["shared-b"] = duplicate.model_copy(update={"provider": "openai", "id": "shared-id"})
    with pytest.raises(ValueError, match="不明确"):
        resolve_model_name(config, "shared-id")
    assert resolve_model_name(config, "proxy/gpt-5.4-mini") == "mini-via-proxy"


def test_provider_api_key_env_rejects_literal_secrets_without_echoing_them(tmp_path: Path) -> None:
    project = tmp_path / "project.toml"
    literal_secret = "sk-proj-sensitive-value"
    project.write_text(
        (
            "[providers.openai]\n"
            "kind = 'openai'\n"
            f"api_key_env = '{literal_secret}'\n"
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError) as captured:
        load_config(user_path=tmp_path / "missing.toml", project_path=project)

    assert "api_key_env 必须是环境变量名称" in str(captured.value)
    assert literal_secret not in str(captured.value)
