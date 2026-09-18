# MCP implementation decisions

## Milestone 3 — 2026-09-18

- Reviewed the official [Python SDK](https://github.com/modelcontextprotocol/python-sdk),
  its [v1 server guide](https://github.com/modelcontextprotocol/python-sdk/blob/v1.x/docs/server.md),
  [protocol testing guide](https://github.com/modelcontextprotocol/python-sdk/blob/v1.x/docs/testing.md),
  and the [adapter dependency declaration](https://github.com/langchain-ai/langchain-mcp-adapters/blob/main/pyproject.toml).
- The current SDK main branch is v2, but the adapter requires `mcp>=1.24,<2`.
  Use the supported v1 maintenance line, locked to 1.30.0, and its bundled FastMCP.
  No separate FastMCP distribution or MCP CLI extra is required.
- Inspected installed FastMCP constructor, tool decorator, session testing helper, and call dispatch.
  Pydantic return models produce declared output schemas and structuredContent.
- Select stdio: one independently launched Python subprocess, no listening MCP port,
  compatible with the adapter and a single-container deployment. Stdout belongs to MCP;
  SDK logging is suppressed to avoid validation payloads in logs.
- Start with `uv run python -m market_agent.mcp.polymarket`. The MCP lifespan owns one
  ordinary PolymarketClient and closes it on shutdown. Injected clients remain caller-owned.
  Existing client bounds remain 10 seconds per HTTP attempt and at most two retries.
- Search: 1–200 characters with non-whitespace content, canonical status or null, 1–10 results.
  Detail: numeric Gamma market ID, 1–20 digits. Search includes a coverage caveat because the
  existing provider search examines only the first page. No claim of exhaustive discovery.
- Results project canonical values into summary/detail schemas; raw data and duplicate descriptions
  are omitted. Strings, result counts, outcome counts, and price precision are bounded. Rules are
  capped at 12,000 characters with a visible truncation flag; other oversized fields fail safely.
  Decimal prices serialize as strings. Null values remain unknown, not zero.
- Controlled MCP errors distinguish transport, HTTP, missing-data, and malformed-data failures.
  Provider bodies and validation payloads are not copied into errors.
- Verification: 45 deterministic tests passed; Ruff lint/format, strict mypy, and diff whitespace
  checks passed. Real MCP memory-stream sessions exercise tools/list and tools/call; fixture HTTP
  is the only mocked layer. Opt-in public stdio subprocess discovery/search/detail passed (1 test).
- No order-book evidence or architecture pivot. SDK v2 migration should wait for adapter support.
