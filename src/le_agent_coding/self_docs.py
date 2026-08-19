"""Locations of LeAgent's packaged self-documentation and examples."""

from __future__ import annotations

from pathlib import Path

_PACKAGE_ROOT = Path(__file__).resolve().parent
_DATA_ROOT = _PACKAGE_ROOT / "data"


def le_agent_readme_path() -> Path:
    """Return the installed overview document for LeAgent-aware tasks."""
    return _DATA_ROOT / "docs" / "README.md"


def le_agent_docs_path() -> Path:
    """Return the installed LeAgent self-documentation directory."""
    return _DATA_ROOT / "docs"


def le_agent_examples_path() -> Path:
    """Return the installed LeAgent example directory."""
    return _DATA_ROOT / "examples"
