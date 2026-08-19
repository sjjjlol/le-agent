from dataclasses import dataclass, field

import pytest

from le_agent_coding.tools import create_coding_tools
from le_agent_coding.web_search import (
    SearchResponse,
    SearchResult,
    WebSearchError,
    _normalize_tavily_response,
    create_web_search_tool_definition,
)


@dataclass
class FakeBackend:
    calls: list[dict[str, object]] = field(default_factory=list)

    async def search(
        self,
        query: str,
        *,
        max_results: int,
        freshness: str | None,
        include_domains: tuple[str, ...],
        exclude_domains: tuple[str, ...],
    ) -> SearchResponse:
        self.calls.append(
            {
                "query": query,
                "max_results": max_results,
                "freshness": freshness,
                "include_domains": include_domains,
                "exclude_domains": exclude_domains,
            }
        )
        return SearchResponse(
            backend="Tavily",
            request_id="request-1",
            keyless=True,
            results=(
                SearchResult(
                    title="LeAgent",
                    url="https://example.com/le-agent",
                    snippet="A coding agent.",
                    published_at="2026-08-20",
                    score=0.9,
                ),
            ),
        )


@pytest.mark.anyio
async def test_web_search_tool_returns_text_and_structured_citations() -> None:
    backend = FakeBackend()
    tool = create_web_search_tool_definition(backend).to_agent_tool()

    result = await tool.execute(
        "call-1",
        {
            "query": "latest le-agent news",
            "max_results": 3,
            "freshness": "week",
            "include_domains": ["example.com"],
        },
    )

    assert tool.execution_mode == "parallel"
    assert "Tavily keyless best-effort" in result.text
    assert "https://example.com/le-agent" in result.text
    assert result.details == {
        "backend": "Tavily",
        "request_id": "request-1",
        "keyless": True,
        "results": [
            {
                "title": "LeAgent",
                "url": "https://example.com/le-agent",
                "snippet": "A coding agent.",
                "published_at": "2026-08-20",
                "score": 0.9,
            }
        ],
    }
    assert backend.calls == [
        {
            "query": "latest le-agent news",
            "max_results": 3,
            "freshness": "week",
            "include_domains": ("example.com",),
            "exclude_domains": (),
        }
    ]


@pytest.mark.anyio
async def test_web_search_tool_validates_result_limit() -> None:
    tool = create_web_search_tool_definition(FakeBackend()).to_agent_tool()

    with pytest.raises(WebSearchError, match="between 1 and 10"):
        await tool.execute("call-1", {"query": "test", "max_results": 11})


def test_tavily_normalization_rejects_non_http_urls() -> None:
    response = _normalize_tavily_response(
        {
            "request_id": "request-1",
            "results": [
                {"title": "Good", "url": "https://example.com", "content": "ok"},
                {"title": "Bad", "url": "javascript:alert(1)", "content": "bad"},
            ],
        },
        keyless=False,
    )

    assert [result.title for result in response.results] == ["Good"]


def test_default_coding_tools_register_web_search_and_allow_disabling(tmp_path) -> None:
    assert [tool.name for tool in create_coding_tools(cwd=tmp_path)] == [
        "read",
        "write",
        "edit",
        "bash",
        "web_search",
    ]
    assert [tool.name for tool in create_coding_tools(cwd=tmp_path, web_search_enabled=False)] == [
        "read",
        "write",
        "edit",
        "bash",
    ]
