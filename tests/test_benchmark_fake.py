import pytest

from le_agent_ai import FakeProvider
from le_agent_coding.benchmark.fake import create_fake_provider, fake_tool_names


@pytest.mark.parametrize(
    ("task_id", "expected_tools"),
    [
        ("repository_lookup", ["read", "read", "read"]),
        ("targeted_edit", ["read", "edit"]),
        ("failing_test_fix", ["bash", "read", "edit", "bash"]),
    ],
)
def test_create_fake_provider_has_expected_real_tool_rounds(
    task_id: str, expected_tools: list[str]
) -> None:
    assert isinstance(create_fake_provider(task_id), FakeProvider)
    assert list(fake_tool_names(task_id)) == expected_tools


def test_create_fake_provider_rejects_unknown_task() -> None:
    with pytest.raises(ValueError, match="未知 fake benchmark task"):
        create_fake_provider("missing")
