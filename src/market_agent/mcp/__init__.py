"""Student-authored market MCP servers."""

import os


def server_environment(name: str) -> dict[str, str] | None:
    """Explicit environment for one stdio server.

    The MCP stdio client passes child processes only a small allowlist of variables, so a Cloud
    Run environment variable never reaches a server unless it is forwarded here. Locally, the
    server also reads the ignored .env file.
    """
    key = os.environ.get("TAVILY_API_KEY", "").strip()
    if name == "tavily" and key:
        return {"TAVILY_API_KEY": key}
    return None
