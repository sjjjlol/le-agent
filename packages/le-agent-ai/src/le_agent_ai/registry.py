"""Explicit model registry; callers must provide context-window capabilities."""

from __future__ import annotations

from .models import Model


class ModelRegistry:
    def __init__(self) -> None:
        self._models: dict[str, Model] = {}

    def register(self, name: str, model: Model) -> None:
        if not name.strip():
            raise ValueError("model name must not be empty")
        self._models[name] = model

    def get(self, name: str) -> Model | None:
        return self._models.get(name)

    def require(self, name: str) -> Model:
        model = self.get(name)
        if model is None:
            raise KeyError(f"model is not configured: {name}")
        return model

    def all(self) -> dict[str, Model]:
        return dict(self._models)
