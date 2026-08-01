from pathlib import Path

from le_agent_cli.skills import load_skills


def test_project_skill_overrides_user_skill_with_same_name(tmp_path: Path) -> None:
    user_root = tmp_path / "user"
    project_root = tmp_path / "project"
    (user_root / "review").mkdir(parents=True)
    (project_root / "review").mkdir(parents=True)
    (user_root / "review" / "SKILL.md").write_text("---\nname: review\ndescription: old\n---\nuser", encoding="utf-8")
    (project_root / "review" / "SKILL.md").write_text(
        "---\nname: review\ndescription: new\n---\nproject", encoding="utf-8"
    )

    skills = load_skills(user_root, project_root)

    assert skills["review"].description == "new"
    assert skills["review"].body == "project"
