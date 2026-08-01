from le_agent_ai.models import Model
from le_agent_ai.registry import ModelRegistry


def test_registry_merges_named_models_and_rejects_unknown_context_window() -> None:
    registry = ModelRegistry()
    model = Model(provider="openai", id="custom", context_window=32768, max_output_tokens=4096)
    registry.register("openai/custom", model)

    assert registry.require("openai/custom") == model
