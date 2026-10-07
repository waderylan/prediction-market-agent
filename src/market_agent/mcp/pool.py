"""Instance-owned MCP sessions with isolated, recoverable server connections."""

import asyncio
import json
import logging
import sys
from collections.abc import AsyncIterator
from contextlib import AsyncExitStack, asynccontextmanager
from dataclasses import dataclass, field
from datetime import timedelta
from importlib.resources import files
from typing import cast

from langchain_core.tools import BaseTool
from langchain_mcp_adapters.client import MultiServerMCPClient
from langchain_mcp_adapters.sessions import Connection, StdioConnection
from langchain_mcp_adapters.tools import load_mcp_tools

from market_agent.logging import log_event
from market_agent.mcp import server_environment

logger = logging.getLogger(__name__)


@dataclass
class _Server:
    name: str
    tools: list[BaseTool] = field(default_factory=list)
    ready: asyncio.Event = field(default_factory=asyncio.Event)
    changed: asyncio.Event = field(default_factory=asyncio.Event)
    reconnect_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    leases: int = 0
    restart: bool = False


class MCPToolPool:
    """Keep one stdio session per server for the life of a Cloud Run instance."""

    def __init__(self, connections: dict[str, Connection] | None = None) -> None:
        if connections is None:
            connections = json.loads(files("market_agent.mcp").joinpath("servers.json").read_text())
        for name, connection in connections.items():
            if connection["transport"] != "stdio":
                raise ValueError("MCPToolPool requires stdio servers")
            stdio = cast(StdioConnection, connection)
            stdio["command"] = sys.executable
            stdio["env"] = server_environment(name)
            stdio["session_kwargs"] = {"read_timeout_seconds": timedelta(seconds=45)}
        self._client = MultiServerMCPClient(connections)
        self._servers = {name: _Server(name) for name in connections}
        self._tasks: list[asyncio.Task[None]] = []
        self._closing = False

    async def start(self) -> None:
        self._tasks = [
            asyncio.create_task(self._serve(server), name=f"mcp-{server.name}")
            for server in self._servers.values()
        ]
        try:
            await asyncio.wait_for(
                asyncio.gather(*(server.ready.wait() for server in self._servers.values())),
                timeout=20,
            )
        except TimeoutError:
            log_event(logger, "mcp_startup_partial", available=self.available_sources())

    async def close(self) -> None:
        self._closing = True
        for server in self._servers.values():
            server.changed.set()
        if self._tasks:
            try:
                await asyncio.wait_for(asyncio.gather(*self._tasks), timeout=10)
            except TimeoutError:
                for task in self._tasks:
                    task.cancel()
                await asyncio.gather(*self._tasks, return_exceptions=True)

    def available_sources(self) -> list[str]:
        return [name for name, server in self._servers.items() if server.tools]

    def transport_failed(self, name: str) -> None:
        """Hide a failed server immediately; reconnect once its active turns finish."""
        server = self._servers.get(name)
        if server is None or server.restart:
            return
        server.tools = []
        server.restart = True
        server.changed.set()
        log_event(logger, "mcp_transport_failed", server=name)

    @asynccontextmanager
    async def tools(self) -> AsyncIterator[list[BaseTool]]:
        # Retry unavailable sources only when a request arrives. No idle work is needed.
        for server in self._servers.values():
            if not server.tools and not server.restart:
                server.changed.set()
        servers = [server for server in self._servers.values() if server.tools]
        if not servers:
            raise ConnectionError("No MCP data server is available")
        for server in servers:
            server.leases += 1
        try:
            yield [tool for server in servers for tool in server.tools]
        finally:
            for server in servers:
                server.leases -= 1
                if server.restart and server.leases == 0:
                    server.changed.set()

    async def _serve(self, server: _Server) -> None:
        reconnect = True
        while not self._closing:
            if not reconnect:
                await server.changed.wait()
                server.changed.clear()
                if self._closing:
                    break
                if server.leases:
                    continue
            try:
                async with AsyncExitStack() as stack:
                    async with server.reconnect_lock, asyncio.timeout(10):
                        session = await stack.enter_async_context(self._client.session(server.name))
                        server.tools = await load_mcp_tools(session)
                    log_event(logger, "mcp_discovered", server=server.name, count=len(server.tools))
                    server.ready.set()
                    while not self._closing:
                        await server.changed.wait()
                        server.changed.clear()
                        if server.restart and server.leases == 0:
                            break
                    reconnect = not self._closing
            except Exception as error:
                reconnect = False
                log_event(
                    logger,
                    "mcp_unavailable",
                    server=server.name,
                    error_type=type(error).__name__,
                )
            finally:
                server.tools = []
                server.ready.set()
                server.restart = False
