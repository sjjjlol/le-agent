"""Provider-neutral web search tool backed by Tavily."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from os import environ
from typing import TYPE_CHECKING, Any, Protocol, cast
from urllib.parse import urlsplit

from tavily import AsyncTavilyClient, TavilyKeylessLimitError

from le_agent.messages import TextContent
from le_agent.tools import AgentToolResult, ToolCancellationToken
from le_agent.types import JSONValue

if TYPE_CHECKING:
    from le_agent_coding.tools import ToolDefinition


class WebSearchError(RuntimeError):
    """Raised when a web-search backend cannot return a valid result set."""


@dataclass(frozen=True, slots=True)
class SearchResult:
    """One normalized web-search result."""

    title: str
    url: str
    snippet: str
    published_at: str | None = None
    score: float | None = None

    def to_json(self) -> dict[str, JSONValue]:
        return {
            "title": self.title,
            "url": self.url,
            "snippet": self.snippet,
            "published_at": self.published_at,
            "score": self.score,
        }


@dataclass(frozen=True, slots=True)
class SearchResponse:
    """Normalized response shared by search backends and renderers."""

    backend: str
    request_id: str | None
    results: tuple[SearchResult, ...]
    keyless: bool = False


class WebSearchBackend(Protocol):
    async def search(
        self,
        query: str,
        *,
        max_results: int,
        freshness: str | None,
        include_domains: tuple[str, ...],
        exclude_domains: tuple[str, ...],
    ) -> SearchResponse:
        """Search the web and return normalized results."""


@dataclass(frozen=True, slots=True)
class TavilyBackend:
    """Tavily Search adapter supporting keyed and keyless SDK modes."""

    api_key: str | None = None
    timeout_seconds: float = 30.0

    @property
    def keyless(self) -> bool:
        return not (self.api_key or environ.get("TAVILY_API_KEY"))

    async def search(
        self,
        query: str,
        *,
        max_results: int,
        freshness: str | None,
        include_domains: tuple[str, ...],
        exclude_domains: tuple[str, ...],
    ) -> SearchResponse:
        client = AsyncTavilyClient(
            api_key=self.api_key,
            client_source="le-agent-keyless" if self.keyless else "le-agent",
            client_name="le-agent",
        )
        try:
            raw = await client.search(
                query=query,
                search_depth="basic",
                max_results=max_results,
                time_range=cast(Any, freshness),
                include_domains=include_domains or None,
                exclude_domains=exclude_domains or None,
                include_answer=False,
                include_raw_content=False,
                include_images=False,
                timeout=self.timeout_seconds,
            )
        except TavilyKeylessLimitError as exc:
            retry_after = getattr(exc, "retry_after_seconds", None)
            retry_hint = f" Retry after {retry_after} seconds." if retry_after else ""
            raise WebSearchError(
                "Tavily keyless search is temporarily unavailable or rate-limited."
                f"{retry_hint} Set TAVILY_API_KEY for a higher, predictable limit."
            ) from exc
        except Exception as exc:
            mode = "keyless" if self.keyless else "API"
            raise WebSearchError(f"Tavily {mode} search failed: {exc}") from exc
        finally:
            await client.close()
        return _normalize_tavily_response(raw, keyless=self.keyless)


def create_web_search_tool_definition(
    backend: WebSearchBackend | None = None,
) -> ToolDefinition:
    """Create the coding-layer web_search definition."""
    from le_agent_coding.tools import ToolDefinition

    search_backend = backend or TavilyBackend()

    async def execute(
        arguments: Mapping[str, JSONValue],
        signal: ToolCancellationToken | None = None,
    ) -> AgentToolResult:
        if signal is not None and signal.is_cancelled():
            raise WebSearchError("Web search was cancelled")
        query = _required_string(arguments, "query")
        max_results = _optional_int(arguments, "max_results", default=5)
        if not 1 <= max_results <= 10:
            raise WebSearchError("max_results must be between 1 and 10")
        freshness = _optional_freshness(arguments.get("freshness"))
        include_domains = _string_tuple(arguments.get("include_domains"), "include_domains")
        exclude_domains = _string_tuple(arguments.get("exclude_domains"), "exclude_domains")
        response = await search_backend.search(
            query,
            max_results=max_results,
            freshness=freshness,
            include_domains=include_domains,
            exclude_domains=exclude_domains,
        )
        if signal is not None and signal.is_cancelled():
            raise WebSearchError("Web search was cancelled")
        mode = " keyless best-effort" if response.keyless else ""
        header = f"Web search results from {response.backend}{mode}:"
        if not response.results:
            text = f"{header}\n\nNo results found."
        else:
            blocks = [header]
            for index, result in enumerate(response.results, start=1):
                published = f"\nPublished: {result.published_at}" if result.published_at else ""
                blocks.append(
                    f"[{index}] {result.title}\nURL: {result.url}{published}\n{result.snippet}"
                )
            text = "\n\n".join(blocks)
        return AgentToolResult(
            content=[TextContent(text=text)],
            details={
                "backend": response.backend,
                "request_id": response.request_id,
                "keyless": response.keyless,
                "results": [result.to_json() for result in response.results],
            },
        )

    return ToolDefinition(
        name="web_search",
        description=(
            "Search the public web with Tavily. Returns source titles, URLs, snippets, "
            "publication dates when available, and relevance scores."
        ),
        prompt_snippet="Search the web for current or external information",
        prompt_guidelines=(
            "Use web_search when the answer depends on current or external information.",
            "Cite result URLs when using information returned by web_search.",
        ),
        input_schema={
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "The search query."},
                "max_results": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 10,
                    "default": 5,
                },
                "freshness": {
                    "type": "string",
                    "enum": ["day", "week", "month", "year"],
                },
                "include_domains": {
                    "type": "array",
                    "items": {"type": "string"},
                },
                "exclude_domains": {
                    "type": "array",
                    "items": {"type": "string"},
                },
            },
            "required": ["query"],
            "additionalProperties": False,
        },
        executor=execute,
        execution_mode="parallel",
    )


def _normalize_tavily_response(raw: object, *, keyless: bool) -> SearchResponse:
    if not isinstance(raw, dict):
        raise WebSearchError("Tavily returned a non-object response")
    request_id = raw.get("request_id")
    if request_id is not None and not isinstance(request_id, str):
        request_id = None
    raw_results = raw.get("results", [])
    if not isinstance(raw_results, list):
        raise WebSearchError("Tavily returned an invalid results field")
    results: list[SearchResult] = []
    for raw_result in raw_results:
        if not isinstance(raw_result, dict):
            continue
        title = raw_result.get("title")
        url = raw_result.get("url")
        content = raw_result.get("content")
        if not isinstance(title, str) or not isinstance(url, str) or not _is_http_url(url):
            continue
        snippet = content if isinstance(content, str) else ""
        published_at = raw_result.get("published_date")
        if not isinstance(published_at, str):
            published_at = None
        score = raw_result.get("score")
        if not isinstance(score, (int, float)) or isinstance(score, bool):
            score = None
        results.append(
            SearchResult(
                title=title.strip() or url,
                url=url,
                snippet=snippet.strip(),
                published_at=published_at,
                score=float(score) if score is not None else None,
            )
        )
    return SearchResponse(
        backend="Tavily",
        request_id=request_id,
        results=tuple(results),
        keyless=keyless,
    )


def _required_string(arguments: Mapping[str, JSONValue], name: str) -> str:
    value = arguments.get(name)
    if not isinstance(value, str) or not value.strip():
        raise WebSearchError(f"{name} must be a non-empty string")
    return value.strip()


def _optional_int(arguments: Mapping[str, JSONValue], name: str, *, default: int) -> int:
    value = arguments.get(name, default)
    if not isinstance(value, int) or isinstance(value, bool):
        raise WebSearchError(f"{name} must be an integer")
    return value


def _optional_freshness(value: JSONValue | None) -> str | None:
    if value is None:
        return None
    if value not in {"day", "week", "month", "year"}:
        raise WebSearchError("freshness must be day, week, month, or year")
    return value


def _string_tuple(value: JSONValue | None, name: str) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, list) or not all(
        isinstance(item, str) and item.strip() for item in value
    ):
        raise WebSearchError(f"{name} must be a list of non-empty strings")
    return tuple(cast(str, item).strip() for item in value)


def _is_http_url(value: str) -> bool:
    parsed = urlsplit(value)
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


__all__ = [
    "SearchResponse",
    "SearchResult",
    "TavilyBackend",
    "WebSearchBackend",
    "WebSearchError",
    "create_web_search_tool_definition",
]
