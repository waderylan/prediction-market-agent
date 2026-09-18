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

## Milestone 4 — 2026-09-18

- Reviewed official [graph API](https://docs.langchain.com/oss/python/langgraph/graph-api),
  [memory](https://docs.langchain.com/oss/python/langgraph/add-memory),
  [adapter](https://github.com/langchain-ai/langchain-mcp-adapters),
  [FastAPI lifespan](https://fastapi.tiangolo.com/advanced/events/), and
  [GPT-5](https://developers.openai.com/api/docs/models/gpt-5) documentation.
  Inspected installed adapter session/load_mcp_tools/result-conversion APIs, ChatOpenAI fields,
  and HTTP-client caching. Locked LangGraph 1.2.11, adapters 0.3.2, FastAPI 0.141.1,
  langchain-openai 1.6.2, and OpenAI SDK 3.16.1.
- Minimal graph: model reasoning → validated MCP tool execution → model; no keyword router.
  Enforce four tool calls, then synthesize without bound tools. InMemorySaver stores messages
  keyed by session_id. Fixed striped locks serialize same-session requests; a semaphore caps
  concurrent turns at four. Locks contain no conversational data.
- Each turn launches a Polymarket subprocess and shares one initialized session for discovery
  and all calls. Cleanup occurs in the request task, avoiding cross-task AnyIO cancel-scope
  ownership. This costs process startup per turn but provides simple recovery on the next turn.
- Adapter structured artifacts are validated again before model use. Tool execution, transport,
  schema errors, and model errors become controlled messages; log only tool names/statuses and
  exception class names. No prompts, arguments, provider bodies, or secret values are logged.
- User-facing focus: explain a contract's price, YES settlement condition, and consequential caveat,
  with source and retrieval time. This is contract literacy, not an unsupported forecast.
- First real-model run exposed SDK default HTTP pools reused across application event loops
  (2 of 4 checks failed). Explicit lifespan-owned sync/async clients fixed it; all four passed
  on rerun, including recall/isolation, detail with citation, and two search phrasings.
- At the user's request, local LLM verification uses a development-only headless Codex gateway:
  gpt-5.6-sol, medium effort, stdin input, ephemeral execution, schema-constrained decisions.
  Reviewed the user's LectureBriefLawEdition conventions and official
  [noninteractive documentation](https://developers.openai.com/es-419/docs/non-interactive-mode).
  No source dependency or copied project code. The gateway is excluded from Docker and deployment;
  it does not execute MCP calls or own conversational memory. No cloud API key was available.
- Verification: 62 offline tests passed; 2 public provider smoke checks and 1 public MCP stdio
  smoke passed separately; 4 real Sol + public MCP checks passed. Docker built, ran as UID 10001,
  honored PORT=9090, returned health 200 and invalid-body 422, and produced a live sourced contract
  readout through its own Polymarket MCP process using the host Codex gateway.
- Observed readout correctly distinguished the three-news-source agreement condition from the
  inauguration fallback. This demonstrates useful interpretation beyond repeating a headline.
- Limitations: production GPT-5 access/quality still needs a real-key check; memory has no eviction,
  restart durability, or authenticated sessions. SDK v1 remains an adapter compatibility constraint.
  A third-party Starlette/AnyIO deprecation warning remains; all checks pass despite it.
- No architecture pivot; Tavily key validation was deferred until that subsystem exists.

## Milestone 5 — 2026-09-18

- Rechecked official Kalshi [market-data quickstart](https://docs.kalshi.com/getting_started/quick_start_market_data),
  [events](https://docs.kalshi.com/api-reference/events/get-events), and
  [market detail](https://docs.kalshi.com/api-reference/market/get-market) documentation.
  Public GET access, fixed-point dollar fields, event pagination, and market tickers remain supported.
- Added an independent Kalshi FastMCP process around KalshiClient. Shared only canonical projection
  schemas and safe error translation with Polymarket. Each server owns its provider client's lifecycle.
  No order book, account access, authentication subsystem, or provider-logic duplication.
- Kalshi inputs use 1–100 character uppercase tickers, bounded query/limit, and only documented
  event-filter states (canonical resolved maps to settled). Discovery describes its three-page
  coverage. The ordinary client retains all pagination and local ranking logic.
- Added a packaged two-server manifest and request-owned sessions via AsyncExitStack. Separate
  discovery operations yield unique tool names. Partial server failure leaves the other usable.
  Model instructions select platforms semantically and forbid silent substitution.
- Shared results now include platform identity. The graph rejects a result whose platform or
  detail identifier differs from the selected tool/request. This prevents accidental cross-provider
  contamination before later matching.
- Offline checks: 82 passed, including both servers' real protocol paths, scripted zero/one/both
  platform paths, input/output schemas, errors, and actual subprocess partial availability.
  Kalshi public stdio discovery/search/detail passed separately (1 test).
