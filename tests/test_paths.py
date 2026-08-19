from pathlib import Path

from le_agent_coding.paths import LeAgentPaths


def test_le_agent_paths_user_locations(tmp_path: Path) -> None:
    paths = LeAgentPaths(home=tmp_path / ".le-agent", agents_home=tmp_path / ".agents")

    assert paths.sessions_dir == tmp_path / ".le-agent" / "sessions"
    assert paths.user_skills_dir == tmp_path / ".le-agent" / "skills"
    assert paths.user_prompts_dir == tmp_path / ".le-agent" / "prompts"
    assert paths.user_agents_skills_dir == tmp_path / ".agents" / "skills"
    assert paths.user_agents_prompts_dir == tmp_path / ".agents" / "prompts"


def test_le_agent_paths_project_locations(tmp_path: Path) -> None:
    paths = LeAgentPaths(home=tmp_path / "home", agents_home=tmp_path / "agents")
    cwd = tmp_path / "project"

    assert paths.project_le_agent_dir(cwd) == cwd / ".le-agent"
    assert paths.project_agents_dir(cwd) == cwd / ".agents"
    assert paths.project_skills_dir(cwd) == cwd / ".le-agent" / "skills"
    assert paths.project_prompts_dir(cwd) == cwd / ".le-agent" / "prompts"
    assert paths.project_agents_skills_dir(cwd) == cwd / ".agents" / "skills"
    assert paths.project_agents_prompts_dir(cwd) == cwd / ".agents" / "prompts"


def test_default_session_path_uses_home_sessions_and_readable_project_path(
    tmp_path: Path,
) -> None:
    paths = LeAgentPaths(home=tmp_path / "home", agents_home=tmp_path / "agents")
    cwd = tmp_path / "repos" / "exploration" / "le-agent"
    cwd.mkdir(parents=True)

    session_path = paths.default_session_path(cwd)

    assert session_path.name == "default.jsonl"
    assert session_path.parent.parent == tmp_path / "home" / "sessions"
    assert "repos-exploration-le-agent-" in session_path.parent.name
    assert len(session_path.parent.name.rsplit("-", maxsplit=1)[-1]) == 6
    assert session_path.parent.exists()
