"""Shared visual tokens for the compact hybrid terminal theme."""

APP_CSS = """
Screen {
    background: #101419;
    color: #d8dee9;
}

#transcript {
    height: 1fr;
    padding: 1 2;
    scrollbar-size: 1 1;
}

.message {
    width: 100%;
    padding: 0 1 1 1;
}

.user-message { color: #88c0d0; }
.assistant-message { color: #d8dee9; }
.system-message { color: #8f9baa; }
.error-message { color: #bf616a; }

#command-palette {
    display: none;
    max-height: 9;
    margin: 0 2;
    border-left: solid #4c566a;
    background: #17202a;
    color: #d8dee9;
}

#status {
    height: 1;
    padding: 0 2;
    color: #8f9baa;
}

#composer {
    min-height: 3;
    max-height: 8;
    margin: 0 2;
    border: none;
    border-top: solid #2d3642;
    background: #101419;
    color: #eceff4;
}

#hints {
    height: 1;
    padding: 0 2;
    color: #6f7a89;
}

#approval-dialog {
    width: 68;
    height: auto;
    padding: 1 2;
    border: thick #ebcb8b;
    background: #17202a;
}
"""
