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

The packaged `mcp/servers.json` manifest describes four separate Python stdio processes:
Kalshi, Polymarket, sports game state, and bounded Tavily game research. The client substitutes the
running Python interpreter, initializes each session, discovers tools through `tools/list`, and
loads them through `langchain-mcp-adapters`.

Sessions are owned by the current request and closed together. Each server lifespan owns
one asynchronous provider client; injected test clients remain caller-owned. A provider
client closes its HTTP resources when the server shuts down. Stdout belongs exclusively
to MCP protocol traffic.

One unavailable server does not disable the others. A new turn attempts connection again.
If all are unavailable, the application returns a controlled response.

The locked dependency set uses the MCP SDK's bundled FastMCP implementation. The project
does not install a second FastMCP framework or emulate JSON-RPC in application code.

## Tool execution and validation

The model chooses tools semantically. The graph permits four market/state attempts plus two Tavily
searches, then performs a final synthesis without bound tools. Separate per-turn counters prevent
repeated model requests from bypassing either budget. Tool arguments are validated by the MCP
schema, and structured results are validated again by the host before model use.

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

The sports-state host path validates distinct discovery, current-state, box-score-view, player-list,
single-player, and play-by-play schemas; requires returned game/player/play identifiers; checks the
requested view and team side; and checks each sport discriminator against the league. Default
box-score calls transfer scoring and compact leaders, while full and section calls project from the
same cached normalized observation. Baseball inning participation, field-level completeness,
pre/post-event outs, and substitution event kinds remain typed through host validation.
When market and game details coexist, the typed matching report compares league, both participants,
scheduled start, and provider-backed references before model synthesis. Every exact detail response names
source/time and states that sporting results do not establish contract equivalence or market
settlement.

## Sports adaptation

The two MCP search tools perform exact sports query resolution before selecting their
provider discovery path. A reviewed packaged team catalog supports canonical names and
provider-specific labels. League and contract scope filter the scan before returned-limit
truncation. Clarification can finish locally without an API call.

Sports search projects provider contracts into game-first results. Each game groups its outcome
contracts and carries localized kickoff labels, lifecycle status, consumer links, quote freshness,
and explicit settlement when available. Result-kind metadata identifies `games[].contracts` for
sports and `markets[]` for generic topics. Quote freshness distinguishes unavailable timestamps
from authoritative stale timestamps, and unusual close timing remains a separate warning. Exact
local dates, inclusive date ranges, next/recent
selectors, and opaque continuation cursors stay inside the existing two search tools. The agent
validates both generic `markets[]` and sports `games[].contracts[]` paths.
The Polymarket client retains verified search event context for 15 minutes because Gamma market
detail can omit its event array. A separate 30-second normalized-observation cache gives immediate
search/detail calls one explicit observation identity and exposes cache hit/age. Provider retrieval
time is never relabeled as quote time.

[Sports MCP design](SPORTS_MCP.md) explains catalog provenance, series selection, Gamma
fallback, output fields, and budgets. [Provider contracts](MARKET_API_FEASIBILITY.md)
maps the external fields and links to primary documentation.

The independent [game-state MCP](GAME_STATE_MCP.md) uses ESPN for bounded one-day discovery,
current situations, game-only box scores, compact player detail, and stable-ID play windows, with
exact-identity MLB StatsAPI fallback only after primary failure. Player listing and single-player
detail reuse the normalized box-score observation without exposing unrelated stat lines. It shares
the team catalog but never supplies market identity, price, rules, or settlement.

## Research path

The independent [Tavily research MCP](WEB_RESEARCH_MCP.md) exposes one game-scoped search tool.
The host blocks it until its league, participant pair, date, and scheduled start exactly match a
typed market or exact-game sports detail observed in the same turn. The server constructs the query,
inspects exactly five results, and retains only HTTPS sources naming both teams and the exact date.
It supports a league-official-only policy and postgame-recap focus. Typed output preserves
source title, URL, optional publication date, retrieval time, snippet, relevance, relationship, and
rejection count, plus explicit empty status and corroboration cautions for player return/activation
claims. Source text is bounded and untrusted.

Generic Tavily extraction, crawl, map, and research are intentionally absent. Search failure is a
tool-level error; it does not discard already verified market or sports-state data. The response
lists only URLs actually returned by successful research calls.

## Matching path

When both providers' detail snapshots are available, the graph invokes the
bounded deterministic matcher and supplies a `matching_report` before synthesis.
A code-generated notice also keeps comparison eligibility visible independently of model prose.

For supported full-game winners, that engine consumes typed sports identity and named outcome
quotes from both detail results. It separately evaluates market-to-market event identity,
contract settlement equivalence, and optional market-to-game identity. Missing required evidence
stays ambiguous, deterministic conflicts veto comparison, and differing close/resolution clocks do
not replace scheduled-game identity. Milestone 8A removed model adjudication after it added no
real-market value; the primary model may explain but cannot upgrade an ambiguous report. See
[Matching](CONTRACT_MATCHING.md) and the
[value evaluation](MATCHING_VALUE_EVALUATION.md).

## Local development and deployment

The local UI starts the HTTP application and an optional host Codex gateway. The gateway
only supplies model decisions. The application still owns memory and MCP calls.
CLI authentication remains outside the container.

The Docker image uses locked runtime dependencies, contains the four application MCP modules and
their reference data, runs as a non-root user, and reads `PORT`.
Cloud Run deployment is still required. A cloud-accessible backend, one worker, and one
instance preserve the assignment's instance-lifetime memory expectation.

Primary framework references:
[MCP Python SDK](https://github.com/modelcontextprotocol/python-sdk),
[LangGraph memory](https://docs.langchain.com/oss/python/langgraph/add-memory),
[adapter](https://github.com/langchain-ai/langchain-mcp-adapters),
[FastAPI lifespan](https://fastapi.tiangolo.com/advanced/events/).
