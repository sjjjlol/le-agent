"""Project and user Skill discovery compatible with the Agent Skills convention."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml


@dataclass(frozen=True, slots=True)
class Skill:
    name: str
    description: str
    path: Path
    body: str


def _parse_skill(path: Path) -> Skill | None:
    text = path.read_text(encoding="utf-8")
    if not text.startswith("---\n"):
        return None
    _, frontmatter, body = text.split("---\n", 2)
    metadata = yaml.safe_load(frontmatter) or {}
    name = metadata.get("name")
    description = metadata.get("description")
    if not isinstance(name, str) or not name.strip() or not isinstance(description, str):
        return None
    return Skill(name=name.strip(), description=description.strip(), path=path, body=body.strip())


def _discover(root: Path) -> dict[str, Skill]:
    if not root.is_dir():
        return {}
    found: dict[str, Skill] = {}
    for path in sorted(root.glob("**/SKILL.md")):
        skill = _parse_skill(path)
        if skill:
            found[skill.name] = skill
    return found


def load_skills(user_root: Path, project_root: Path) -> dict[str, Skill]:
    """Project scope wins on name collisions, mirroring pi's local customization precedence."""
    return _discover(user_root) | _discover(project_root)


def skill_catalog_prompt(skills: dict[str, Skill]) -> str:
    if not skills:
        return ""
    lines = ["Available skills (use the read tool to load a skill when needed):"]
    lines.extend(f"- {skill.name}: {skill.description} ({skill.path})" for skill in skills.values())
    return "\n".join(lines)
