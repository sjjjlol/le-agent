"""Textual TUI frontend for LeAgent coding sessions."""

from __future__ import annotations

from le_agent_coding.tui.adapter import TuiEventAdapter
from le_agent_coding.tui.app import LeAgentTuiApp, run_tui_app
from le_agent_coding.tui.autocomplete import CompletionOption
from le_agent_coding.tui.config import (
    BUILTIN_TUI_THEME_NAMES,
    HIGH_CONTRAST_THEME,
    LE_AGENT_DARK_THEME,
    LE_AGENT_LIGHT_THEME,
    TuiConfigError,
    TuiKeybindings,
    TuiRoleStyle,
    TuiSettings,
    TuiTheme,
    TuiThemeName,
    TurnNotificationMode,
    get_tui_theme,
    load_tui_settings,
    save_tui_settings,
    tui_settings_path,
)
from le_agent_coding.tui.state import ChatItem, TuiState
from le_agent_coding.tui.widgets import (
    CompactSessionInfo,
    SessionSidebar,
    StreamingTranscriptMessageWidget,
    TranscriptMessageWidget,
    TranscriptView,
    render_chat_item,
    render_compact_session_info,
    render_session_sidebar,
    transcript_item_selection_text,
)

__all__ = [
    "BUILTIN_TUI_THEME_NAMES",
    "ChatItem",
    "CompletionOption",
    "CompactSessionInfo",
    "LeAgentTuiApp",
    "SessionSidebar",
    "LE_AGENT_DARK_THEME",
    "LE_AGENT_LIGHT_THEME",
    "StreamingTranscriptMessageWidget",
    "TranscriptMessageWidget",
    "TranscriptView",
    "TuiEventAdapter",
    "TuiConfigError",
    "HIGH_CONTRAST_THEME",
    "TuiKeybindings",
    "TuiRoleStyle",
    "TuiSettings",
    "TuiTheme",
    "TuiThemeName",
    "TurnNotificationMode",
    "TuiState",
    "get_tui_theme",
    "load_tui_settings",
    "render_chat_item",
    "render_compact_session_info",
    "render_session_sidebar",
    "run_tui_app",
    "save_tui_settings",
    "transcript_item_selection_text",
    "tui_settings_path",
]
