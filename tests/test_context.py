from pathlib import Path

from le_agent_coding.context import discover_project_context
from le_agent_coding.paths import LeAgentPaths
from le_agent_coding.resources import LeAgentResourcePaths


def test_discovers_user_project_and_agents_context_files(tmp_path: Path) -> None:
    le_agent_home = tmp_path / "home" / ".le-agent"
    agents_home = tmp_path / "home" / ".agents"
    project = tmp_path / "project"
    nested = project / "pkg"
    nested.mkdir(parents=True)
    (project / "pyproject.toml").write_text("[project]\nname = 'demo'\n", encoding="utf-8")
    (le_agent_home).mkdir(parents=True)
    (agents_home).mkdir(parents=True)
    (project / ".le-agent").mkdir()
    (project / ".agents").mkdir()

    (le_agent_home / "AGENTS.md").write_text("User LeAgent instructions", encoding="utf-8")
    (agents_home / "AGENTS.md").write_text("User agents instructions", encoding="utf-8")
    (project / "AGENTS.md").write_text("Project instructions", encoding="utf-8")
    (nested / "AGENTS.md").write_text("Nested instructions", encoding="utf-8")
    (nested / ".le-agent").mkdir()
    (nested / ".agents").mkdir()
    (nested / ".le-agent" / "AGENTS.md").write_text(
        "Project LeAgent instructions", encoding="utf-8"
    )
    (nested / ".agents" / "AGENTS.md").write_text("Project agents instructions", encoding="utf-8")

    context_files = discover_project_context(
        LeAgentResourcePaths(
            root=le_agent_home,
            agents_root=agents_home,
            cwd=nested,
            paths=LeAgentPaths(home=le_agent_home, agents_home=agents_home),
        )
    )

    assert [Path(context_file.path) for context_file in context_files] == [
        le_agent_home / "AGENTS.md",
        agents_home / "AGENTS.md",
        project / "AGENTS.md",
        nested / "AGENTS.md",
        nested / ".le-agent" / "AGENTS.md",
        nested / ".agents" / "AGENTS.md",
    ]
    assert [context_file.content for context_file in context_files] == [
        "User LeAgent instructions",
        "User agents instructions",
        "Project instructions",
        "Nested instructions",
        "Project LeAgent instructions",
        "Project agents instructions",
    ]
