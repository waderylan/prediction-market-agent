# Bounded Tavily game research MCP

## Purpose

The Tavily server supplies current public evidence that the market and sports-state providers do
not contain: injuries, lineups, weather, venue or schedule changes, and other news about one exact
game. It is corroborating research only. It cannot establish official game state, contract
identity, contract equivalence, market settlement, an independent probability, or a position.

## MCP surface

The independent `market_agent.mcp.tavily` stdio process exposes one read-only tool:

```text
tavily_search_game_evidence(
  league,
  team_a,
  team_b,
  game_date,
  scheduled_start,
  focus
)
```

`league` is `mlb`, `nfl`, or `ncaa_football`. `focus` is `injuries`, `lineups`, `weather`,
`venue_or_schedule`, or `other_game_news`. Both teams, the local game date, and timezone-aware
scheduled start must be copied from a typed market-detail or game-state result. The server, not the
model, constructs the web query.

The result contains at most five sources. Each source preserves title, public HTTPS URL,
publication date when Tavily supplies one, retrieval time, bounded snippet, relevance score, and
an `authority_tier` of `league_official`, `established_sports_media`, or `other`, plus the explicit
`same_matchup_date` relationship. The tier is a small hostname-based ordering heuristic, not proof
that a claim is correct. Retained title/snippet text must also contain terms relevant to the
requested focus; a ticket, hotel, or generic event page does not become injury or lineup evidence
merely because it names the matchup and date. `other_game_news` remains broad after the
identity/date check. The relationship is deliberately weaker than proof of one game when the teams
have a same-day doubleheader; the copied scheduled start remains visible for that distinction. The
result also reports rejected-result count, coverage, request ID, and an untrusted-content notice.

`game_date` is the provider-established local calendar date, while `scheduled_start` is the exact
UTC instant used by the host identity gate. The tool does not receive the game's IANA timezone, so
the generated search query uses the local date and deliberately omits the UTC clock. Combining a
local September 22 date with `02:10 UTC` would describe neither the local start nor the correct UTC
date for a September 22 evening game. The exact UTC start remains present in the typed output.

## Safety and budgets

- The graph permits four market/state attempts plus at most two Tavily searches per turn.
- Research is rejected before MCP invocation unless league, the unordered participant pair, local
  game date, and scheduled start match a typed detail observation from the same turn.
- One tool call makes one basic Tavily request for exactly five results. Raw page content, generated
  answers, images, crawl, map, research, and arbitrary extraction are disabled.
- The server retains only HTTPS results that mention both teams and the exact requested date and
  whose title/snippet matches the requested evidence focus. Duplicate URLs, private IP URLs,
  malformed records, wrong opponents, wrong dates, off-focus pages, and empty snippets are rejected.
- Retained sources are ordered by authority tier, then publication time when supplied, then Tavily
  relevance. This ordering never bypasses the identity, date, focus, URL, or untrusted-data checks.
- Titles and snippets are normalized, stripped of control characters, and length bounded. They
  remain untrusted source data and cannot override system or tool policy.
- Provider response bodies, keys, and transport diagnostics do not appear in errors or logs.
- A failed search does not fail the chat request. The agent synthesizes from already verified
  market/game data and states that current research was unavailable.
- Returned sources are listed deterministically in the user response, so citations cannot be
  invented or silently detached from the actual MCP result.

## Why this projection is narrower than the generic Tavily MCP

The official generic server was tested before implementation. Its broad surface included search,
extract, crawl, map, and research. Its search result was formatted as untyped text and omitted
publication dates and relevance scores that the underlying API returned. The local projection
keeps the real MCP boundary required by the assignment while providing the same strict schemas,
sanitized errors, provider limits, and domain checks as the other application servers.

Extraction was removed under Milestone 9's planned pivot. A real same-day game search returned
adequate snippets with source URLs and dates. Exposing arbitrary URL extraction would add a larger
prompt-injection surface and another latency/cost step without demonstrated value. Add extraction
later only behind a searched-URL allowlist and a measured need that snippets cannot satisfy.

The current fix intentionally keeps one request and the existing five-result budget. It does not
add team-only searches, generic crawl/extraction, medical-record parsing, player-status conflict
resolution, or page-update-time inference. Those changes require a new evidence relationship and
measured recall/precision need; they are not hidden inside this bounded matchup search.

## Authentication and cost

`TAVILY_API_KEY` is optional. When set, it is sent only in the authorization header to Tavily. When
unset, the client uses the keyless access headers implemented by Tavily's official MCP server.
Keys remain in environment variables or the ignored `.env`; they never enter MCP results, logs,
source code, Codex configuration, or committed fixtures.

Basic search consumes one credit for keyed accounts under Tavily's current API documentation.
Keyless availability and provider limits are external behavior without an uptime guarantee.

## Direct Codex check

The local Codex configuration registers this stdio server as `tavily`. That user-level setting is
machine-specific and is not committed. Restart Codex after registration, use `/mcp` to confirm the
server is active, then try:

```text
Use tavily_search_game_evidence for MLB, Miami Marlins vs San Diego Padres,
game_date 2026-09-20, scheduled_start 2026-09-20T20:10:00Z, focus injuries.
Show the retained and rejected result counts, then cite every retained URL.
```

A direct MCP call relies on the supplied typed identity. The conversational agent adds the stronger
host check that requires those values to match a market-detail or game-state observation first.

Primary references:

- [Tavily Search API](https://docs.tavily.com/documentation/api-reference/endpoint/search)
- [Official Tavily MCP server](https://github.com/tavily-ai/tavily-mcp)
