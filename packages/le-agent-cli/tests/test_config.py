from pathlib import Path

from le_agent_cli.config import load_config, registry_from_config


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
