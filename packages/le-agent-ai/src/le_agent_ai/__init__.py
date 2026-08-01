"""Canonical models, provider protocols and event streams for le-agent."""

from .errors import ErrorCode, normalize_error_code
from .faux import FauxProvider, ScriptedResponse
from .models import (
    AssistantMessage,
    Model,
    ProviderContext,
    TextContent,
    ToolCallContent,
    ToolResultMessage,
    Usage,
    UserMessage,
)
from .registry import ModelRegistry
from .stream import AsyncEventStream

__all__ = [
    "AssistantMessage",
    "AsyncEventStream",
    "FauxProvider",
    "ErrorCode",
    "Model",
    "ModelRegistry",
    "ProviderContext",
    "ScriptedResponse",
    "normalize_error_code",
    "TextContent",
    "ToolCallContent",
    "ToolResultMessage",
    "Usage",
    "UserMessage",
]
