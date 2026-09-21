# Runtime architecture

## Request ownership

FastAPI accepts `POST /chat` with `query` and `session_id`, then invokes the LangGraph
reasoning loop. The response is exactly `{"response":"..."}`. The local inspection endpoint
also exposes a bounded activity trace; it does not replace the assignment endpoint.

LangGraph `InMemorySaver` stores conversational state by session ID. Fixed striped locks
serialize overlapping requests for a session; a semaphore limits concurrent turns to four.
Those synchronization objects are not custom conversation storage. Memory is instance-local,
unauthenticated, and not durable or automatically evicted.

## MCP lifecycle

The packaged `mcp/servers.json` manifest describes three separate Python stdio processes:
Kalshi, Polymarket, and sports game state. The client substitutes the running Python interpreter, initializes
each session, discovers tools through `tools/list`, and loads them through
`langchain-mcp-adapters`.

A fourth standalone `market_agent.mcp.jev` process is available to external MCP clients such as
Codex. Its one read-only tool accepts unchanged structured detail results from both market servers
and reuses the deterministic matcher plus `JevReviewer`. It is deliberately absent from the
application manifest because the graph already owns automatic Jev routing.

Sessions are owned by the current request and closed together. Each server lifespan owns
one asynchronous provider client; injected test clients remain caller-owned. A provider
client closes its HTTP resources when the server shuts down. Stdout belongs exclusively
to MCP protocol traffic.

One unavailable server does not disable the others. A new turn attempts connection again.
If all are unavailable, the application returns a controlled response.

The locked dependency set uses the MCP SDK's bundled FastMCP implementation. The project
does not install a second FastMCP framework or emulate JSON-RPC in application code.

## Tool execution and validation

The model chooses tools semantically. The graph permits four tool calls, then performs
a final synthesis without bound tools. Tool arguments are validated by the MCP schema,
and structured results are validated again by the host before model use.

The host checks provider identity and requested detail IDs. Each tool result has bounded
models with decimal strings and explicit nulls. Detail rules are limited to 12,000 characters
with `rules_truncated`; a truncated rule cannot establish full settlement equivalence.

Provider transport failures, HTTP errors, structurally unsafe page roots, incomplete data, and
oversized projections become sanitized tool errors. Malformed or oversized individual records on
an otherwise usable page are discarded independently with bounded structured warnings. Each MCP
tool also has a thirty-second wall-clock budget; the client's per-attempt timeouts and retry bounds
remain active inside it.
External cancellation propagates.

The agent catches tool/transport/schema failures and continues with an explicit inability
to verify that data. It does not substitute fabricated quotes. Logs use safe operational
metadata; provider response bodies and credential values do not become tool-error text.

The sports-state host path validates its distinct discovery/detail schemas, requires the returned
opaque reference, and checks the situation discriminator against the league. When market and game
details coexist, the typed matching report compares league, both participants, scheduled start,
and provider-backed references before model synthesis. Every exact state response names
source/time and states that sporting results do not establish contract equivalence or market
settlement.

## Sports adaptation

The two MCP search tools perform exact sports query resolution before selecting their
provider discovery path. A reviewed packaged team catalog supports canonical names and
provider-specific labels. League and contract scope filter the scan before returned-limit
truncation. Clarification can finish locally without an API call.

Sports search projects provider contracts into game-first results. Each game groups its outcome
contracts and carries localized kickoff labels, lifecycle status, consumer links, quote freshness,
and explicit settlement when available. Exact local dates, inclusive date ranges, next/recent
selectors, and opaque continuation cursors stay inside the existing two search tools. The agent
validates both generic `markets[]` and sports `games[].contracts[]` paths.
The Polymarket client retains verified search event context for 15 minutes because Gamma market
detail can omit its event array. A separate 30-second normalized-observation cache gives immediate
search/detail calls one explicit observation identity and exposes cache hit/age. Provider retrieval
time is never relabeled as quote time.

[Sports MCP design](SPORTS_MCP.md) explains catalog provenance, series selection, Gamma
fallback, output fields, and budgets. [Provider contracts](MARKET_API_FEASIBILITY.md)
maps the external fields and links to primary documentation.

The independent [game-state MCP](GAME_STATE_MCP.md) uses ESPN for bounded one-day discovery and
detail, with exact-identity MLB StatsAPI fallback only after primary failure. It shares the team
catalog but never supplies market identity, price, rules, or settlement.

## Matching path

When both providers' detail snapshots are available, the graph invokes the
bounded deterministic matcher and supplies a `matching_report` before synthesis.
A code-generated notice also keeps comparison eligibility visible independently of model prose.

For supported full-game winners, that engine consumes typed sports identity and named outcome
quotes from both detail results. It separately evaluates market-to-market event identity,
contract settlement equivalence, and optional market-to-game identity. Missing required evidence
stays ambiguous, deterministic conflicts veto comparison by default, and differing close/resolution
clocks do not replace scheduled-game identity. The graph performs bounded semantic Jev review for
eligible ambiguity; the standalone MCP surface exposes that same backend for inspection. The
experimental `JEV_FORCE_REVIEW=true` setting expands both routes to complete same-event pairs for
Milestone 8A evaluation without bypassing event-identity or evidence-completeness guards. See
[Matching](CONTRACT_MATCHING.md).

## Local development and deployment

The local UI starts the HTTP application and an optional host Codex gateway. The gateway
only supplies model decisions. The application still owns memory and MCP calls.
CLI authentication remains outside the container.

The Docker image uses locked runtime dependencies, contains the three application MCP modules, the
standalone Jev inspection module, and their reference data, runs as a non-root user, and reads
`PORT`.
Cloud Run deployment is still required. A cloud-accessible backend, one worker, and one
instance preserve the assignment's instance-lifetime memory expectation.

Primary framework references:
[MCP Python SDK](https://github.com/modelcontextprotocol/python-sdk),
[LangGraph memory](https://docs.langchain.com/oss/python/langgraph/add-memory),
[adapter](https://github.com/langchain-ai/langchain-mcp-adapters),
[FastAPI lifespan](https://fastapi.tiangolo.com/advanced/events/).
