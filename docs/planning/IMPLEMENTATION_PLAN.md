# Implementation Plan: Sports Prediction-Market Research Agent

## 1. Purpose

- Build the assignment as a sequence of independently testable milestones.
- Address high-risk assumptions before adding optional features.
- Maintain a working vertical slice throughout development.
- Preserve room to change APIs, schemas, prompts, thresholds, and internal module boundaries when testing provides better evidence.
- Keep the implementation aligned with `../assignment/Assignment_1_Description.md`, the focused
  scope in `PROJECT_PROPOSAL.md`, and the provider contract in `../research/SPORTS_MCP.md`.
- Center the initial product on MLB, NFL, and NCAA Division I full-game winners while retaining
  the generic market lookup path for compatibility.
- Treat on-demand questions and event-aware watches as two entry modes into the same identity,
  MCP, validation, and evidence pipeline rather than separate products.

### Current Milestone Status

| Milestone | Status |
|---|---|
| 0. Repository and test foundation | Complete |
| 1. Provider feasibility | Complete for the current sports scope |
| 2. Domain layer and clients | Complete for the current sports scope |
| 3. Polymarket MCP | Complete for generic and supported sports discovery |
| 4. Local vertical slice | Complete locally |
| 5. Kalshi MCP, routing, and sports discovery | Complete at the MCP/provider layer |
| 6. Sports game-state MCP | Complete locally |
| 7. Sports-aware event identity and deterministic contract matching | Complete; simplified by Milestone 8A |
| 8. Jev sports-contract equivalence | Removed by Milestone 8A |
| 8A. Contract-matching value evaluation and retention decision | Complete locally |
| 9. Bounded sports evidence research | Complete locally |
| 9A. Multi-sport exact-game box scores | Complete locally |
| 9B. Exact-game player and play history tools | Complete locally |
| 9C. Agent-readable presentation and evidence semantics | Complete locally |
| 10. Unified multi-MCP sports information assistant | Complete locally |
| 11. Unified sports intelligence brief | Integrated into Milestone 10 |
| 12. Optional sports-research snapshot ledger | Deferred until after deployment |
| 13. Failure handling and verification | Complete locally |
| 14. Base Cloud Run deployment and acceptance | Required; not started |
| 14A. Cloud watch automation and final submission acceptance | Blocked on Milestones 14, 16B, and 16C |
| 15. Event-aware natural-language watches | Complete locally; cloud acceptance pending Milestone 14A |
| 16. External watch alerts | Complete locally; cloud acceptance pending Milestone 14A |
| 16A. Watch activation acknowledgements and operational visibility | Complete locally; `/chat` and live Cloud Run acceptance deferred |
| 16B. Conversational watch operations and agent acceptance | Planned after Milestone 16A |
| 16C. Inbound Telegram watch status and help | Planned after Milestone 16B; local first |
| 17. Polymarket US migration evaluation | Optional after deployment |

- Remaining execution order: Milestone 14 establishes the live base service; Milestone 16A proves
  local watch runtime and notification behavior; Milestone 16B proves the local agent surface;
  Milestone 16C adds local Telegram status and help commands; and Milestone 14A adds watch
  infrastructure and the Telegram webhook to the accepted Cloud Run service before final submission.

## 2. Fixed Decisions

- Use Python, FastAPI, LangGraph, and `langchain-mcp-adapters`.
- Expose `POST /chat` with:

  ```json
  {
    "query": "string",
    "session_id": "string"
  }
  ```

- Return:

  ```json
  {
    "response": "string"
  }
  ```

- Deploy one containerized service to Google Cloud Run.
- Use a LangGraph checkpointer keyed by `session_id` for conversational memory.
- Keep Polymarket and Kalshi as two separate MCP servers.
- Let the agent select the platform-specific MCP based on the request:
  - Polymarket-only request: invoke only Polymarket tools.
  - Kalshi-only request: invoke only Kalshi tools.
  - Cross-market comparison: invoke both servers.
- Use real MCP discovery and invocation through `tools/list` and `tools/call`.
- Keep cross-platform equivalence deterministic. Require typed event identity, named outcome,
  game-winner type, core postponement/cancellation terms, and exact full-rule agreement before a
  price comparison is allowed. Unsupported wording remains ambiguous.
- Keep the service read-only with respect to prediction-market platforms.
- Never place trades or require exchange-account credentials.
- Compile natural-language watch requests into one versioned, typed `WatchRule`; never execute
  free-form model output as polling logic.
- Keep recurring collection and trigger evaluation deterministic. No LLM call occurs merely
  because a polling interval elapsed.
- Use the existing market and sports-state MCP tools for watch observations. Do not add provider
  reads that bypass or duplicate those tool contracts.
- Do not add a watch MCP server. Keep watch state and trigger evaluation in validated host
  application code so the MCP topology remains the four implemented evidence servers.
- Keep LangGraph with a cloud-accessible API-key model as the primary Cloud Run compiler and
  investigator. Support local Codex through the repository skill and the same MCP tools,
  `WatchRule` schema, validator, and evaluator.
- Complete the watch engine with an in-product alert inbox and deterministic template messages
  before adding external delivery.
- Telegram is the first external alert channel and consumes only stored, deduplicated triggers.

## 3. Decisions That May Change

- Exact provider endpoints used for sports discovery, metadata, rules, prices, and optional
  order books.
- Whether a supported sports comparison benefits from additional order-book data beyond the
  current provider snapshots and last-trade fields.
- Search-query construction and candidate-ranking logic.
- Canonical market-model fields beyond the minimum required fields.
- Exact MCP tool arguments and result schemas after API experiments.
- Tavily query and result limits within the bounded-research requirement.
- Internal Python module layout.
- Whether the SQLite sports-research snapshot ledger is completed for Assignment 1 or retained as a
  stretch milestone.
- LLM model configuration, provided the deployed model remains cloud-accessible.
- Watch polling cadence and active-game windows after measuring provider limits, latency, and quote
  update frequency.
- External notification channels beyond the Milestone 16 Telegram integration.

Changes to fixed decisions require an explicit update to this plan. Changes to flexible decisions may be made at a milestone gate when tests provide a reason.

## 4. Target Architecture

```text
Client
  |
  +--> FastAPI POST /chat --> LangGraph agent + session checkpointer
  |                              |--> Primary LLM reasoning and synthesis
  |                              `--> Watch compiler and investigator
  |
Cloud Scheduler / local runner --> Deterministic watch coordinator
                                  |
                                  v
Multi-server MCP client
  |-- Polymarket MCP ------> Polymarket public APIs
  |-- Kalshi MCP ----------> Kalshi public APIs
  |-- Sports-state MCP ----> ESPN public JSON / MLB StatsAPI fallback
  |-- Tavily MCP ----------> Bounded game-scoped search
  `-- SQLite MCP ----------> Optional deferred research snapshot ledger

Watch compiler + deterministic coordinator
  |-- Watch repository ----> SQLite locally / Firestore on Cloud Run
  `-- Delivery adapter ----> Telegram Bot API for stored WatchTrigger delivery
```

- The two market servers remain separate processes and separate MCP configurations.
- The sports-state server remains a third independent process and never becomes a fallback for
  market identity, prices, rules, or settlement.
- Market servers share Python domain types where useful, but they do not call each other.
- Each server has unique tool names to avoid collisions after tool aggregation.
- Tavily remains a separate MCP integration.
- The primary LLM remains responsible for tool selection, ambiguous-case review, and final synthesis.
- The watch compiler resolves intent through the same MCP client and emits a validated `WatchRule`.
  The deterministic coordinator later invokes the same exact-detail and game-state tools without
  placing an LLM in the polling loop.
- Watch persistence is an application service, not another MCP server. MCP remains the boundary for
  external evidence; the host owns rules, observations, trigger fingerprints, and alert history.
- Local Codex, local LangGraph, and deployed LangGraph share the watch schema, validator, evaluator,
  and repository contract. Only the reasoning backend and storage driver differ.
- External delivery consumes stored deterministic triggers. It does not change MCP routing, watch
  evaluation, or the in-product alert record.

## 5. Tool Surface

### Polymarket MCP

- `polymarket_search_markets`
  - Input: query, optional status and league, local date or inclusive date range, IANA timezone,
    next/most-recent selector, opaque continuation cursor, and game limit.
  - Output: clarification or bounded game groups containing contracts, localized schedule labels,
    lifecycle, quote freshness, settlement, URLs, and discovery coverage.
- `polymarket_get_market`
  - Input: stable numeric Polymarket market identifier returned by discovery.
  - Output: normalized market detail, rules, prices, sports context, settlement, and provenance.
- Add an order-book tool only if measured product value justifies its request cost.

### Kalshi MCP

- `kalshi_search_markets`
  - Input: query, optional status, league, and verified series precision control, local date or
    inclusive date range, IANA timezone, next/most-recent selector, opaque continuation cursor,
    and game limit.
  - Output: clarification or bounded game groups containing contracts, localized schedule labels,
    lifecycle, quote freshness, settlement, URLs, and discovery coverage.
- `kalshi_get_market`
  - Input: an exact Kalshi market ticker returned by discovery or supplied by the user.
  - Output: normalized market detail, rules, prices, sports context, settlement, and provenance.
- `kalshi_search_series`
  - Input: bounded category, tag, and status filters.
  - Output: provider series metadata for generic precision lookup; sports users do not need it.
- Add an order-book tool only if measured product value justifies its request cost.

### Tavily MCP

- `tavily_search_game_evidence`
  - Input: league, both exact canonical team names, one game date, scheduled start, one evidence
    focus, and an all-sources or league-official-only source policy.
  - Output: at most five typed same-matchup/date sources with title, HTTPS URL, publication date when
    supplied, retrieval time, snippet, relevance score, relationship, explicit empty status, and
    corroboration cautions for return/activation claims.
- Construct queries in the server instead of accepting arbitrary web queries.
- Enforce a maximum of two searches in application state rather than relying only on the prompt.
- Do not expose Tavily crawl, map, research, or arbitrary extraction tools.

### Sports Game-State MCP

- `sports_state_find_games`
- `sports_state_get_game_state`
- `sports_state_get_box_score`
  - Input: discovery `game_ref`, summary/full/section view, and optional team-side filter.
  - Output: only the requested projection, available views, a follow-up tip, inning participation,
    field-level completeness, cache identity, and provenance.
- `sports_state_list_players`
- `sports_state_get_player_stats`
- `sports_state_get_play_by_play`
- Keep the public surface limited to supported-game discovery, current normalized state, game-only
  statistics, compact player detail, and bounded chronological plays.

### Optional SQLite MCP

- `save_research_snapshot`
- `get_research_snapshot`
- `list_research_snapshots`
- Add scoring tools only after save and retrieval work reliably.

### Watch Application Service (Milestone 15; Not an MCP Server)

- Validate and save a versioned `WatchRule` only after exact game and market references are
  established and the user confirms its compiled preview.
- List, revise, pause, and inspect watches through the `/chat` workflow and shared host functions.
- Expose the same host functions to local Codex through a bounded `scripts/watch_cli.py` interface;
  do not add model-facing filesystem or database tools.
- Use SQLite for the explicit local watcher process and Firestore through the same repository
  interface on Cloud Run. This operational state remains separate from the deferred Milestone 12
  research-snapshot ledger.
- Keep every state mutation inside validated application code. The agent supplies typed intent but
  cannot execute arbitrary queries, write provider observations, or select its own storage backend.

## 6. Canonical Sports and Contract Models

- Keep one normalized contract model after provider-specific parsing. Core fields include:
  - `platform`
  - `market_id`
  - `event_id`
  - `title`
  - `description`
  - `outcomes`
  - named outcomes and provider price semantics
  - `yes_price` and `no_price` only when the provider contract supports those labels safely
  - `yes_bid`
  - `yes_ask`
  - `scheduled_start`
  - `open_time`
  - `close_time`
  - `expected_resolution_time`
  - `resolution_deadline`
  - `resolution_source`
  - `rules`
  - `status`
  - `liquidity`
  - `volume`
  - consumer-facing `market_url`
  - provider API provenance URL
  - nullable authoritative `quote_as_of`, `retrieved_at`, `provider_updated_at`, typed freshness,
    and freshness reason
  - `observation_id`, `cache_hit`, and `cache_age_ms`
  - `settlement_value`, `winning_outcome`, and `resolved_at`
  - `retrieved_at`
- Attach a typed sports event containing league, provider event identity, raw and canonical
  participants, NCAA divisions when known, full-game winner type, schedule evidence, and
  comparison eligibility with its reason.
- Project search results as games with contracts nested beneath each game. A sports search limit
  counts games, not contracts. `result_kind` and `contracts_location` tell clients whether to read
  `games[].contracts`, `markets[]`, or clarification fields.
- Keep scheduled start, trading close, expected resolution, and resolution deadline independent.
- Distinguish current, stale, timestamp-unavailable, and not-trading quote states. Missing quote
  time never proves staleness. Warn when provider trading close is unusually far after kickoff.
- Omit inconsistent optional timing rather than presenting it as authoritative.
- Allow unavailable fields to be null rather than inventing values, links, prices, times, or IDs.
- Retain a bounded raw-provider payload inside the provider layer for debugging when appropriate;
  do not expose it to the model as the public MCP contract.
- Normalize timestamps to UTC and probabilities to the range `[0, 1]`.
- Keep exact numeric parsing in ordinary Python code.

## 7. Milestones

### Milestone 0: Repository and Test Foundation

#### Work

- Select dependency and packaging files.
- Create the initial source and test layout.
- Add `.env.example` with placeholders only.
- Confirm `.env`, caches, virtual environments, local databases, and generated artifacts are ignored.
- Add configuration validation for required environment variables.
- Establish unit, integration, and live-smoke test markers.
- Add structured logging without secrets or full prompt dumps.

#### Exit Criteria

- A clean environment can install the project.
- The test command runs successfully with an initial smoke test.
- Missing configuration produces a clear startup error.
- No credentials or local secret files are tracked.

#### Pivot Point

- Adjust package layout or dependency tooling before application code depends on it.

#### Current State

- **Status:** Complete.
- The repository uses a Python 3.12 `src/` package, `uv` dependency workflow and lockfile,
  environment template, validated settings, safe JSON logging, pytest markers, generated-file
  ignores, and documented local commands.
- Installation, smoke tests, Ruff, strict mypy, diff checks, and secret-file ignore behavior form
  the continuing verification gate.

### Milestone 1: Market API Feasibility Spike

#### Work

- Test Polymarket discovery, market detail, rule text, price, pagination, and error behavior.
- Test Kalshi discovery, market detail, rule text, price, pagination, and error behavior.
- Use MLB, NFL, and NCAA football across active and settled examples rather than one hard-coded
  game.
- Include likely cross-platform game pairs, doubleheaders, wrong opponents, and settlement-rule
  near-matches.
- Record response fields and API limitations in development notes.
- Save sanitized response fixtures for automated tests.
- Determine whether order-book calls are necessary for the initial version.

#### Questions to Resolve

- Can natural-language team and matchup queries produce useful candidates on each platform?
- Where are complete resolution rules exposed?
- Which price fields are stable and comparable?
- How are team-named outcomes, YES/NO polarity, and two-contract games represented?
- How are active, closed, resolved, and invalid markets represented?
- Which provider fields describe scheduled kickoff, trading close, expected resolution, quote
  observation, and final settlement?
- What pagination, timeout, and rate-limit behavior must the clients handle?

#### Exit Criteria

- Both APIs return enough information to build the canonical market model.
- At least one plausible sports contract pair can be retrieved from both platforms.
- Major missing fields and workarounds are documented.
- Sanitized fixtures cover success, empty results, and malformed or incomplete data.

#### Pivot Point

- If reliable team search is unavailable, use provider-backed league discovery with bounded local
  identity and participant filtering.
- If full rules cannot be retrieved reliably, narrow the supported market categories or reconsider the matching workflow before building MCP servers.

#### Current State

- **Status:** Complete.
- `../research/MARKET_API_FEASIBILITY.md` defines provider endpoints, field semantics, bounded
  discovery behavior, and known limitations.
- Sanitized fixtures cover success, empty, incomplete, malformed, paginated, and error responses.
- Sports discovery uses provider-backed league scopes and bounded local filtering. Order-book depth
  remains outside the initial product because summary and detail responses support the current read
  path.

### Milestone 2: Shared Domain Layer and API Clients

#### Work

- Implement provider-specific asynchronous HTTP clients.
- Add explicit request timeouts and bounded retries.
- Parse provider responses into the canonical market model.
- Add deterministic normalization for sports identity, timestamps, local calendars, probabilities,
  named outcomes, lifecycle, quote freshness, settlement, units, and exchange status.
- Define typed provider exceptions for transport, HTTP, validation, and missing-data failures.
- Test parsing primarily against saved fixtures.
- Keep a small set of opt-in live smoke tests.

#### Exit Criteria

- Each client can search for grouped sports games and fetch one contract in normalized form.
- Unit tests do not require network access.
- Live smoke tests can be run independently when APIs are available.
- Invalid responses fail with typed, inspectable errors.

#### Pivot Point

- Change internal models and API boundaries before MCP schemas make them externally visible.

#### Current State

- **Status:** Complete for the current provider and sports scope.
- Separate asynchronous clients, isolated parsers, explicit timeouts, bounded retries, typed
  failures, UTC/probability normalization, sports identity metadata, and opt-in live tests remain
  the provider boundary.
- Tests cover success, empty search, incomplete fields, malformed data, missing identity, HTTP
  failures, retries, timeouts, sports aliases, wrong opponents, pagination, and price semantics.

### Milestone 3: Polymarket MCP Server

#### Work

- Wrap the Polymarket client in a student-authored Python MCP server.
- Expose narrow tools with valid JSON Schema inputs.
- Return structured, bounded results.
- Validate limits and identifiers before making API calls.
- Convert client failures into useful MCP tool errors.
- Add tests for tool discovery, successful calls, empty results, invalid arguments, upstream failures, and malformed upstream responses.

#### Exit Criteria

- An MCP client discovers the Polymarket tools through `tools/list`.
- An MCP client invokes every required tool through `tools/call`.
- Tool results match their declared schemas.
- The server does not crash on the three rubric failure classes.

#### Pivot Point

- Revise tool granularity before the agent depends on the interface.

#### Current State

- **Status:** Complete for generic and supported sports discovery.
- The student-authored stdio FastMCP server exposes typed search/detail tools, bounded results,
  explicit rule truncation, sanitized errors, and lifespan-owned HTTP resources.
- Sports search supports exact team resolution, game grouping, local calendar filters, lifecycle,
  freshness, settlement, opaque continuation, and detail-context preservation.
- Polymarket matchup search sends both resolved participants using MLB full names, NFL nicknames,
  or college school names. Supplied dates are strict timezone-aware `gameStartTime` filters, so
  historical games do not depend on a broad single-team result ranking.
- Real protocol tests cover discovery, declared schemas, successful calls, invalid arguments,
  provider failures, malformed data, and bounded public smoke checks.

### Milestone 4: Early Vertical Slice

#### Work

- Create the FastAPI application and required `/chat` contract.
- Create a minimal LangGraph reasoning and tool loop.
- Connect the Polymarket MCP through `langchain-mcp-adapters`.
- Add a framework-native in-memory checkpointer keyed by `session_id`.
- Support a Polymarket sports query, a no-tool query, and a memory follow-up.
- Run the slice locally and inside the initial Docker container.

#### Exit Criteria

- `/chat` returns the required response shape.
- The agent makes a real Polymarket MCP call for an appropriate sports request.
- The agent can answer an appropriate no-tool request without forcing a tool call.
- Two turns with the same session recall prior context.
- Two different session IDs remain isolated.
- The Dockerized slice starts as a non-root user and reads `PORT`.

#### Pivot Point

- Resolve MCP lifecycle, LangGraph state, and container-process issues before adding more servers.

#### Current State

- **Status:** Complete locally.
- FastAPI, the LangGraph tool loop, adapter-backed stdio MCP sessions, native session memory,
  bounded tool calls, controlled dependency errors, safe tool observability, and the non-root
  container are implemented.
- Local model, protocol, subprocess, memory-isolation, container, and public-provider checks cover
  the vertical slice. A cloud-accessible production model and deployed Cloud Run behavior remain
  deployment gates.

### Milestone 5: Kalshi MCP, Platform Routing, and Sports Discovery

#### Work

- Build the Kalshi MCP using the same interface discipline as the Polymarket MCP.
- Register both servers with the multi-server MCP client.
- Ensure all aggregated tool names remain unique.
- Describe tools clearly enough for semantic selection.
- Add routing tests that inspect actual tool calls.
- Resolve MLB, NFL, and NCAA Division I teams and matchups from reviewed exact aliases.
- Discover full-game winner contracts without requiring provider taxonomy knowledge.
- Group contracts by provider game and expose local schedule labels, lifecycle, freshness,
  settlement, consumer URLs, and bounded coverage.
- Reject ambiguous teams, unsupported contract types, wrong opponents, and invented identifiers.

#### Required Routing Cases

- “Find this market on Polymarket” invokes Polymarket only.
- “Find this market on Kalshi” invokes Kalshi only.
- “Compare this event on Polymarket and Kalshi” invokes both.
- A general explanation invokes neither market server.
- A follow-up such as “What was the Kalshi price?” uses session context and invokes a tool only if fresh data is needed.

#### Exit Criteria

- Both servers are discovered independently.
- Both servers are reached through real MCP calls.
- Platform-specific requests select the correct server.
- Cross-platform requests can chain calls to both servers.
- Incorrect or unnecessary cross-platform calls are covered by tests.
- Supported sports searches accept local dates, inclusive ranges, IANA timezones, next/most-recent
  selection, and opaque continuation cursors.
- A sports limit counts games rather than individual team contracts.
- Detail calls preserve sports identity and the search observation where the provider omits it.

#### Pivot Point

- Improve tool descriptions, system instructions, or graph routing if the LLM repeatedly selects the wrong platform.
- Do not replace semantic selection with hard-coded keyword routing solely to make tests pass.

#### Current State

- **Status:** Complete at the MCP/provider layer; agent-specific sports behavior continues in
  Milestone 10.
- The independent Kalshi server and shared projections preserve partial availability, unique tool
  names, provider/identifier validation, and model-driven platform selection.
- Both servers support MLB, NFL, and NCAA Division I full-game winner discovery using reviewed
  identity metadata and provider-backed scope resolution.
- Deterministic, real MCP, agent-consumption, and bounded live tests cover common names, ambiguous
  aliases, wrong opponents, doubleheaders, pagination, grouped games, named prices, timing,
  lifecycle, freshness, settlement, links, and identifier safety.
- `../research/SPORTS_MCP.md` is the source of truth for algorithms, schemas, budgets, catalog
  maintenance, and known provider boundaries.

### Milestone 6: Sports Game-State MCP

#### Purpose and Confirmed Feasibility

- Maintain one narrow, read-only MCP server for exact-game situations, statistics, and plays.
- Support the same initial leagues as market discovery: MLB, NFL, and NCAA Division I football.
- Keep game state separate from prediction-market price, contract equivalence, and settlement.
- Use ESPN's unauthenticated public JSON scoreboard/summary surface as the primary cross-sport
  source. It is free and requires no account or API key, but it is undocumented and has no SLA.
- Use MLB StatsAPI as a fallback for MLB only. NFL and NCAA football return controlled
  unavailability when ESPN fails.
- Current provider verification covers ESPN scoreboards and summaries for all three leagues,
  completed box scores for MLB/NFL/NCAA, live MLB state and box scores, and exact MLB StatsAPI
  fallback data. The undocumented ESPN interface has no stability guarantee.

#### Fixed Architecture Decisions

- Implement a student-authored Python stdio MCP server in the existing package and container.
- Register it as a third independent process beside Kalshi and Polymarket. Give every tool a
  `sports_state_` prefix so aggregated tool names cannot collide.
- Reuse the existing asynchronous client, typed-error, bounded-response, logging, and FastMCP
  patterns. The server lifespan owns one asynchronous HTTP client; injected test transports remain
  caller-owned.
- Keep all provider URLs in code-owned allowlists. Tool inputs never accept a URL, hostname, path,
  arbitrary provider name, or HTTP headers.
- Use only fixed ESPN routes under `site.api.espn.com` for the three supported league scoreboards
  and event summaries. Use only fixed MLB StatsAPI schedule/live-feed routes under
  `statsapi.mlb.com` for the MLB fallback.
- Do not spoof a browser user agent. ESPN rejected browser-shaped clients during feasibility
  testing while the default `httpx` client succeeded. Treat a future 403 as provider
  unavailability rather than rotating identities or attempting to evade access controls.
- Do not add Node, a hosted MCP dependency, Apify, a paid sports API, background workers,
  WebSockets, or continuous polling.
- Do not import a broad ESPN MCP. Broad servers expose unrelated news, standings, odds, roster,
  and betting surfaces or introduce metered hosting. This server exposes only bounded exact-game
  discovery, state, statistics, player detail, and play history.
- Do not expose provider odds, provider win probability, player projections, fantasy data, news,
  season statistics, or unbounded play feeds. Game-only sports detail remains separate from
  market and web-research evidence.

#### Tool Surface

- `sports_state_find_games`
  - Inputs: `query`, required `league` and IANA `timezone`, optional exact `local_date`, and game
    `limit` from 1 through 10. The exact reserved query `all` returns one bounded same-day slate;
    optional `compact` projects selection fields only. Do not add date ranges, season scans,
    pagination, or an exhaustive mode.
  - Purpose: resolve a supported team or matchup, or list one bounded same-day slate, and return
    provider-backed game choices.
  - Output: zero to ten normalized game summaries plus explicit coverage, clarification, and
    selection evidence. The limit counts games.
  - The default calendar window is the requested local day. If no date is supplied, use the
    current day in the supplied timezone and disclose that derived date in the result.
- `sports_state_get_game_state`
  - Input: one opaque `game_ref` copied unchanged from `sports_state_find_games`.
  - Purpose: return one current normalized snapshot for the exact discovered game.
  - Output: common game state plus exactly one league-specific situation object.
  - Reject edited, malformed, cross-league, or unsupported-source references before
    provider I/O. Never accept a guessed ESPN event ID or MLB `gamePk` as a substitute.
- `sports_state_get_box_score`
  - Input: the same opaque `game_ref`; no team, date, provider, or mode parameters.
  - Purpose: return line scoring plus game-only team and player performance for MLB, NFL, or NCAA
    football.
  - Output: sport-specific baseball or football sections, completeness, warnings, provenance,
    cache metadata, and content-derived observation identity.
- `sports_state_list_players`
  - Input: the exact opaque `game_ref`.
  - Output: a compact directory of players with game-stat lines, stable IDs, teams, sides, and
    available stat groups.
- `sports_state_get_player_stats`
  - Input: the exact opaque `game_ref` and one unchanged ID from the player directory.
  - Output: only that player's game-stat groups and exact-game provenance.
- `sports_state_get_play_by_play`
  - Input: the exact opaque `game_ref`, bounded limit, mutually exclusive before/after anchor,
    and optional scoring, period, and team filters.
  - Output: a chronological window with stable play IDs, paging/resume checkpoints, sport-specific
    context, cache metadata, and content-derived observation identity.
- Require discovery before detail. The agent must not construct identifiers from team names,
  dates, market IDs, or prior knowledge.
- Return MCP tool errors with compact JSON containing stable `error.code`, `message`, and
  caller-correctable `fields`, matching the existing market-server convention.

#### Identity and Game Reference

- Reuse the reviewed team catalog and exact alias resolver. Do not create a second fuzzy identity
  system for ESPN names.
- Add reviewed ESPN identifiers or labels to the shared catalog only when fixtures or live data
  prove them. Preserve the raw ESPN labels beside canonical participants.
- A candidate must match the requested league and one or two resolved participants. Unknown or
  conflicting opponents produce clarification, not the known team's unrelated game.
- Preserve home/away roles and the provider event ID. Two games with the same participants remain
  distinct by event ID and scheduled start; doubleheaders are never merged.
- `game_ref` is an opaque, versioned, URL-safe token containing only the minimum validated lookup
  context: source namespace, league, provider event ID, scheduled start, requested timezone, and
  canonical participants. It is an identifier carrier, not an authentication credential.
- Include a domain-separated SHA-256 checksum of the canonical serialized payload to detect
  truncation and accidental model edits. This checksum is not an authentication boundary; the
  strict allowlist, bounded lookup, and response-identity checks provide the security boundary.
- References remain valid across MCP subprocess and Cloud Run instance restarts and require no
  deployment secret. Reject invalid encoding, schema, version, checksum, or field values before
  provider I/O.
- Validate the detail response against every reference field. A provider response cannot silently
  substitute another game, league, date, or participant pair.
- When ESPN fails for an MLB reference, locate the MLB StatsAPI game using league, scheduled date,
  both canonical participants, and start-time evidence. If one same-team game exists on that local
  date, it is the fallback candidate. If a doubleheader or multiple candidates exist, require a
  unique start within 30 minutes and matching game number when available. Otherwise return
  unavailable or clarification rather than guessing.

#### Normalized Output Contract

- Common game fields:
  - `league`, `game_ref`, `source`, and raw provider game ID.
  - Canonical and raw home/away participants.
  - UTC scheduled start, requested timezone, local date, and localized start.
  - Home and away score as nonnegative integers or null when the provider does not supply them.
  - Lifecycle: `scheduled`, `pregame`, `live`, `halftime`, `delayed`, `suspended`, `postponed`,
    `cancelled`, `final`, or `unknown`.
  - Period/inning number and provider display label.
  - Game clock when meaningful.
  - Bounded last-play text when supplied.
  - `retrieved_at`, nullable authoritative `provider_updated_at`, cache metadata, source URL, and
    warnings.
  - A bounded usage note distinguishing lightweight discovery from authoritative normalized detail.
- Football situation fields:
  - Nullable possession team, down, distance, field position, red-zone flag, and home/away
    timeouts.
  - Preserve a bounded provider down-and-distance label for explanation.
- Baseball situation fields:
  - Semantic phase (`not_started`, `active`, `transition`, `complete`, or `unavailable`), nullable
    inning, top/bottom/unknown half, balls, strikes, outs, first/second/third-base occupancy,
    batter, and pitcher.
- Missing situation fields remain null. Halftime, inning transitions, reviews, delays, and provider
  update races commonly omit fields; never derive possession from the last-play team or infer base
  occupancy from prose.
- Resolve ESPN batter/pitcher IDs only from explicit names in the same summary response. Treat
  sibling bases as empty only when the response supplies at least one explicit current-base key.
- `retrieved_at` records when this service observed the response. It is not a provider update
  clock. Leave `provider_updated_at` null unless the provider supplies an authoritative state
  timestamp.
- Return bounded warnings for internally inconsistent but usable state. Reject impossible values
  such as negative scores, football downs outside 1-4, baseball outs outside 0-3, nonfinite
  numbers, conflicting participants, or a response identity mismatch.
- Do not return raw provider payloads to the model.

#### Lifecycle and State Rules

- Normalize lifecycle from explicit provider status codes first. Display text may refine a known
  state, such as recognizing halftime, but must not override an explicit final, postponed,
  cancelled, or delayed status.
- Scores alone never determine lifecycle. A tied or lopsided score can occur in any phase.
- A nonzero score is not evidence that a game is live. A `0-0` score is not evidence that a game
  has not started.
- A provider's final state describes the sporting event only. It does not establish prediction-
  market settlement, contract equivalence, cancellation treatment, or winning market outcome.
- When ESPN and the MLB fallback disagree, return the primary observation plus a structured
  conflict warning. Do not average scores or select whichever state appears more favorable.

#### Request, Cache, and Size Budgets

- Discovery performs one ESPN scoreboard request for the requested local date. Convert every
  returned start to the requested timezone and filter by that local calendar date.
- If the primary calendar page returns no matching team after filtering and the requested local
  day overlaps an adjacent provider/UTC calendar day, discovery may request the immediately
  adjacent date pages. The hard maximum is three scoreboard requests total; stop as soon as a
  qualifying game is found. This boundary check prevents UTC rollover misses without scanning a
  season or week.
- Detail performs one ESPN event-summary request. MLB fallback permits at most one date-scoped
  schedule request and one exact live-feed request after an ESPN transport, HTTP, or schema
  failure.
- Allow at most two attempts per logical HTTP request. Retry only transport failures, HTTP 429,
  and retryable 5xx responses with bounded backoff. Do not retry 4xx validation failures or an
  unchanged malformed payload.
- Use a ten-second timeout per HTTP attempt and a thirty-second MCP tool wall-clock budget.
  External cancellation propagates.
- Cap any upstream response at 5 MiB, a scoreboard page at 200 events, and returned candidates at
  ten games. NCAA scoreboards measured about 1.2 MiB in feasibility testing, so the limit leaves
  headroom without accepting unbounded payloads.
- Maintain a bounded in-process cache of at most 256 normalized snapshots. Respect the provider's
  cache header within conservative caps: at most five seconds for live state, thirty seconds for
  scheduled/pregame state, and five minutes for terminal state.
- Expose `observation_id`, `cache_hit`, and `cache_age_ms`. Cached snapshots retain their original
  `retrieved_at`; never restamp cached data as newly observed.
- Make no background requests. One user request may trigger only the bounded work above.

#### Failure and Security Behavior

- Handle the three rubric failure classes independently: connection/transport failure, tool
  execution error, and malformed or schema-invalid response.
- One malformed event record must not discard valid sibling games after the scoreboard envelope
  and pagination structure are known safe. Report bounded discard counts and warnings.
- A malformed root, oversized response, conflicting response identity, or unusable exact detail
  fails the call safely.
- ESPN failure may use MLB StatsAPI only for MLB. Never substitute a different sport, league,
  provider game, or market MCP.
- NFL or NCAA provider failure returns a controlled unavailable result with the requested game
  identity preserved; it does not fabricate a score or fall back to web search.
- Logs contain operation name, league, response status, latency, result counts, cache status, and
  sanitized error class only. Do not log full provider payloads, user prompts, or opaque refs.
- Stdout remains exclusively MCP protocol traffic. Operational logs go to stderr through the
  existing safe logger.
- The server is read-only and exposes no arbitrary fetch, order, account, credential, polling,
  notification, or provider-disconnect tool.

#### Agent Integration Rules

- Use this MCP only when the user requests a current/recent game score, lifecycle, or in-game
  situation, or when such state is necessary for an explicitly requested analysis.
- Do not call it for ordinary market discovery, historical contract rules, general sports
  knowledge, or a no-tool question.
- Market MCPs remain authoritative for contract identity, prices, rules, and settlement fields.
  The game-state MCP remains authoritative only for its attributed sporting-event observation.
- Before combining game state with a market result, deterministically verify league, both
  participants, and scheduled-game identity. If more than one event remains plausible, ask the
  user to select a game.
- State observations may explain context but cannot certify equivalent contracts, calculate a
  trading edge, declare a market resolved, or override the deterministic matcher.
- Final responses must name the data source and observation time and disclose stale, missing, or
  conflicting state.

#### Documentation Alignment After Implementation

- Update all project documentation only after the sports-state MCP is implemented and its behavior
  is verified. Do not describe planned behavior as currently available.
- Update `README.md` with the third server's purpose, supported leagues, two-tool reference, setup
  and run behavior, free-provider attribution, source limitations, observation freshness, and
  example current-score/scenario requests.
- Update all three required README diagrams so they show the sports-state MCP process, ESPN primary
  source, MLB StatsAPI fallback, agent routing, and Cloud Run deployment path accurately.
- Update `PROJECT_PROPOSAL.md` and this implementation plan's status table to distinguish the
  implemented game-state capability from future comparison, research, and unified synthesis work.
- Update `SPORTS_MCP.md` or add one focused game-state design document, then link it from the README
  documentation map. Keep provider contracts, field provenance, identity rules, budgets, cache
  semantics, error codes, and known limitations in one canonical research document rather than
  duplicating them across files.
- Update `MCP_VERTICAL_SLICE.md`, `MARKET_API_FEASIBILITY.md`, `CONTRACT_MATCHING.md`, and
  `TESTING.md` wherever their architecture, provider boundary, test map, or completion claims are
  affected.
- Update `.env.example`, deployment instructions, server-manifest examples, cost disclosure, MCP
  attribution, and submission checklist only if the final implementation changes them. The planned
  design requires no new secret or paid service, so do not invent configuration requirements.
- Search the repository for stale counts and claims such as "two MCP servers," "third server,"
  "future work," and architecture lists. Preserve statements that intentionally describe the two
  market servers; revise only statements whose total-server or current-capability meaning changed.
- Verify every documented command, tool name, schema field, provider fallback, request budget,
  diagram edge, and limitation against the implemented code and observed verification results.
- Leave `PROCESS_LOG.md` to Rylan Wade's personal authorship. Documentation alignment must not
  fabricate personal prompts, lessons, or reflections.

#### Verification Plan

- Provider fixtures must cover each league and these phases where applicable: scheduled, live,
  halftime or inning transition, delayed/suspended, postponed/cancelled, final, and unknown.
- Unit tests cover exact team resolution, NCAA ambiguity, doubleheaders, timezone/date boundaries,
  lifecycle normalization, null situation fields, score bounds, reference tampering, response
  identity conflicts, cache identity, response limits, and MLB fallback matching.
- Provider-client tests inject timeout, 403, 429, 503, malformed roots, malformed sibling records,
  oversized responses, cancellation, and conflicting primary/fallback observations.
- Real MCP tests prove `tools/list`, strict JSON Schemas, `tools/call`, stable errors, bounded
  outputs, discovery-before-detail, and independent process startup.
- Agent tests prove correct tool selection for current-state questions, no call for irrelevant
  questions, market/game identity verification, ambiguous-game clarification, and explicit
  separation between final score and market settlement.
- Opt-in live checks perform bounded discovery for all three leagues and exact detail for one
  returned game when available. Empty live slates are valid and must not cause fabricated test
  expectations.
- Container verification proves the third stdio process starts as the non-root runtime user and
  needs no secret or additional runtime.
- Documentation checks prove every project document and required diagram matches the implemented
  third-server architecture and contains no premature or stale capability claim.

#### Exit Criteria

- All three supported leagues return normalized scheduled, live, or final game state through real
  MCP discovery and invocation.
- At least one observed live football response exposes the available scenario fields without
  requiring them all, and at least one observed live MLB response exposes inning/count/out/base
  state.
- Ambiguous games and missing situation data produce explicit choices or nulls rather than guesses.
- ESPN failures are controlled; the MLB fallback works only after exact identity matching; NFL and
  NCAA fail honestly without substitution.
- Request counts, retries, timeouts, response sizes, candidate counts, cache lifetimes, and output
  text are bounded and verified.
- The agent can answer a current-score/scenario question, cite observation time and source, and
  keep the sporting result separate from prediction-market settlement.
- All project documentation, diagrams, setup/deployment instructions, provider attribution, cost
  disclosure, limitations, and test maps describe the verified sports-state MCP accurately.
- Offline tests, Ruff, formatting, strict mypy, real MCP tests, bounded live checks, and container
  checks pass before sports-aware contract matching resumes.

#### Current State

- **Status:** Complete locally.
- The third independent stdio server exposes strict discovery/detail schemas, opaque checksummed
  game references, ESPN normalization for all three leagues, and exact-identity MLB StatsAPI
  fallback without a secret or paid dependency.
- Bounded caches preserve observation identity and time. The agent validates league, participants,
  and scheduled start before combining game and market evidence, names source/time, and keeps final
  sporting state separate from prediction-market settlement.
- Deterministic, real MCP, agent, bounded public-provider, and non-root container checks cover the
  documented request, retry, size, cache, identity, situation, fallback, and failure contracts.
- Cloud Run deployment remains Milestone 14 work; local/container verification does not claim a
  deployed third-server runtime.

#### Pivot Point

- If ESPN becomes unavailable to the deployed `httpx` client, its schema cannot be bounded safely,
  or live checks show unreliable identity/state, do not scrape HTML or weaken validation. Keep the
  typed MCP interface and pause NFL/NCAA support until another verified free source exists.
- If MLB StatsAPI is substantially more reliable in measured live checks, make it the primary MLB
  source while keeping the same public MCP schema.
- Do not add a paid dependency merely to preserve this optional product capability; the assignment
  already satisfies its minimum two-server requirement with Kalshi and Polymarket.

### Milestone 7: Sports-Aware Event Identity and Deterministic Contract Matching

#### Work

- Extend the existing bounded matching report rather than adding a separate MCP comparison tool.
- Consume typed sports identity and outcome quotes from both market-detail results instead of
  dropping those fields when constructing matcher inputs.
- Use three explicitly separate deterministic checks:
  1. **Market-to-market event identity:** compare league, season, both canonical participants,
     provider event identity evidence, game number when available, scheduled start in a shared
     timezone, and full-game market type.
  2. **Market-to-game identity:** when a sports-state observation is already required by the user
     or the requested analysis, compare each market with that observation's league, both
     participants, scheduled start, and provider-backed game reference.
  3. **Contract-to-contract equivalence:** compare named outcome mapping, line or threshold,
     resolution authority, official-result requirements, postponement windows, cancellation
     payouts, overtime, ties, shortened or abandoned games, and material exclusions.
- Make sports-state evidence optional corroboration. An unavailable sports-state server must not
  prevent market-to-market matching, and an ordinary contract comparison must not call it solely
  to satisfy the matcher.
- Allow sports-state evidence to confirm or reject real-world game identity, but never to establish
  contract equivalence, market settlement, or payout semantics and never to override conflicting
  market terms.
- Keep scheduled start, trading close, expected resolution, and resolution deadline as separate
  clocks. A shifted trading or resolution clock is not automatically a different sporting event.
- Reject clear identity or settlement mismatches in code before any model call.
- Treat missing or inconsistent identity, outcome, or settlement evidence as insufficient evidence,
  not equivalence.
- Never assume that one Kalshi team's NO outcome equals the opponent's YES contract without
  settlement evidence.
- Separate primary equivalent candidates from related contextual contracts and unrelated games.
- Return one typed report with explicit dimension-level evidence, provenance, and reasons for
  rejection or ambiguity. Preserve the independent code-generated eligibility notice in the final
  response.

#### Decision Rules

- Any definite event-identity, outcome-mapping, or settlement-rule conflict produces `different`.
- Missing required identity or settlement evidence produces `ambiguous`; the primary model may
  explain it but cannot upgrade it.
- `equivalent` requires all required market-to-market identity, outcome, and settlement checks to
  pass. Sports-state corroboration can strengthen the report but is not a prerequisite.
- A final sports score or completed lifecycle never upgrades a contract pair to `equivalent` and
  never marks either market settled. Only explicit market settlement fields can establish the
  latter.

#### Exit Criteria

- Obvious mismatches are rejected deterministically.
- Related markets are labeled as context, not equivalent contracts.
- A typed market-to-game report replaces the current loose dictionary and distinguishes `match`,
  `different`, and `insufficient_evidence` for every checked dimension.
- Equivalent market contracts can be identified without requiring the sports-state MCP.
- Optional sports-state evidence is combined only after exact identity validation and is presented
  with its source and observation time.
- Ambiguous sports-rule cases remain non-comparable.
- Tests include dangerous near-matches, not only easy positive pairs.
- Tests keep doubleheaders, wrong opponents, different game numbers, and different settlement
  treatment distinct even when titles are similar.
- Tests cover matching and conflicting sports-state observations, acceptable and unacceptable
  start-time drift, sports-state unavailability, and a completed game whose markets remain
  unsettled or non-equivalent.

#### Pivot Point

- Keep the matcher limited to supported full-game winners if additional sports or contract types
  lack the typed identity and settlement evidence required for safe comparison.
- If live sports-state availability or identity quality is unreliable, keep it as optional context;
  do not weaken validation or make contract comparison depend on it.

#### Current State

- **Status:** Complete locally for supported full-game winners; simplified by Milestone 8A.
- Market-detail sports identity and named outcome quotes survive host validation in typed
  `ContractEvidence` inputs. The bounded matcher returns separate typed event-identity,
  contract-equivalence, and optional market-to-game assessments with dimension provenance.
- Event checks cover league, derived or supplied season, both canonical participants, each
  provider's internal event identity, scheduled-start drift, full-game type, and game number when
  available. The retained settlement checks cover affirmative named-outcome mapping, line,
  postponement, cancellation, and exact complete supplied rule text.
- Trading close, expected resolution, and final resolution deadline remain separate informational
  clocks and do not identify the sporting event. Missing or unsupported required evidence stays
  ambiguous; explicit identity, outcome, or settlement conflicts remain deterministic vetoes.
- Verified live MLB, NFL, and NCAA templates distinguish wait-until-complete from bounded
  postponement windows and 50/50 from fair-price cancellation payouts. Other semantic differences
  are covered conservatively by exact full-rule agreement rather than specialized parsers.
- Milestone 6 game state is incorporated only when already retrieved for the user's request. Typed
  market-to-game checks expose source and observation time, and cannot alter contract equivalence
  or market settlement. Market-only equivalence works without the sports-state MCP.
- Unit and real-MCP scripted-agent regressions cover positive pairs, named outcomes, wrong
  opponents, doubleheaders/game numbers, acceptable and unacceptable start drift, rule conflicts,
  missing evidence, sports-state conflicts/unavailability, and final games with non-equivalent or
  unsettled markets.
- A 2026-09-20 live replay covered six shared NFL/MLB games and 12 cross-platform pairs. Authority
  phrasing produced no false conflicts; actual postponement and MLB cancellation differences
  remained deterministic vetoes.
- Ambiguous sports semantics remain non-comparable; the primary model can explain but not upgrade
  the deterministic verdict.
- `../research/CONTRACT_MATCHING.md` defines the matcher boundary and evidence policy.

### Milestone 8: Jev Sports-Contract Equivalence Evaluation

#### Work

- Define a small, typed Jev interface independent of the graph and provider implementations.
- Send only normalized sports identity, named outcomes, complete settlement terms, and the semantic
  questions left unresolved after deterministic checks.
- Ask bounded questions about same-game identity, equivalent winner meaning, postponement and
  cancellation treatment, overtime, ties, shortened or abandoned games, and settlement authority.
- Produce `equivalent`, `ambiguous`, or `different` with a probability distribution and confidence.
- Build a hand-labeled sports fixture set containing true matches, doubleheaders, wrong opponents,
  shifted dates, missing rules, truncated rules, and settlement near-misses.
- Compare deterministic-only, deterministic-plus-Jev, and main-LLM-review behavior.
- Add a short timeout, at most one retry, and a primary-LLM fallback.
- Place Jev behind a feature flag so it can be disabled without changing the workflow.

#### Exit Criteria

- Jev improves or usefully accelerates sports contract classification on the labeled fixture set.
- Low-confidence and unavailable-model cases fall back cleanly.
- Deterministic conflicts always veto equivalence regardless of Jev output in the default safe
  operating mode.
- Jev never predicts a game, performs arithmetic, recommends a position, or produces the final
  user response.
- The entire request still succeeds when Jev is disabled.

#### Pivot Point

- If Jev does not improve the sports evaluation set, retain the typed interface but disable
  automatic routing and use the primary LLM for ambiguous pairs.

#### Current State

- **Status:** Removed by Milestone 8A after failing the real-market retention thresholds.
- The Jev graph node, reviewer, MCP server, feature flags, gateway configuration, prompts, fixtures,
  and dedicated tests were removed rather than left dormant.
- Historical implementation results remain recorded here and in version control. The final
  evidence and tradeoffs are in `../research/MATCHING_VALUE_EVALUATION.md`.

### Milestone 8A: Contract-Matching Value Evaluation and Retention Decision

This required decision gate governs further product work. Milestones 7
and 8 must earn their ongoing complexity with measured results; neither is retained by default
only because it has already been implemented.

#### Work

- Build a manually reviewed evaluation set from real cross-platform MLB, NFL, and NCAA searches
  across multiple dates. Record shared games, candidate contracts, actual rule compatibility,
  actionable price differences, and cases where no safe comparison exists.
- Establish evaluation thresholds before scoring the set. Measure classification precision and
  recall, false-equivalence and false-rejection rates, ambiguous rate, useful-pair yield, latency,
  model cost, maintenance burden, and explanation quality.
- Compare at least these configurations on the same held-out examples:
  - Milestone 7 deterministic matching without Jev.
  - Milestone 7 followed by Milestone 8 Jev review for eligible ambiguous pairs.
  - Milestone 7 followed by forced Jev review for every complete same-event pair, with deterministic
    settlement output retained separately for audit.
  - A simpler baseline that refuses cross-platform equivalence unless a small set of essential
    identity and settlement facts agree.
- Identify which Milestone 7 checks provide demonstrated safety or useful coverage and which are
  brittle, redundant, or unused. Evaluate Jev only on ambiguity that survives those checks.
- Produce a written keep, simplify, or remove decision for Milestone 7 and a separate decision for
  Milestone 8. Include the measured evidence, accepted tradeoffs, and retained operating boundary.
- Implement the decision: remove unused code, dependencies, prompts, feature flags, tests, and
  documentation rather than leaving a disabled or unproven subsystem in the repository.
- If either milestone is removed, preserve a safe fallback: decline unsupported cross-platform
  equivalence instead of reverting to title similarity or unconstrained model judgment.

#### Exit Criteria

- The evaluation uses held-out, manually labeled real-market examples and reports raw counts as
  well as rates; synthetic fixtures alone are insufficient.
- Milestone 7 and Milestone 8 each have an explicit evidence-backed keep, simplify, or remove
  decision.
- Any retained component clears its predeclared usefulness, correctness, latency, and cost
  thresholds and has a concrete user-visible benefit.
- Any component that does not clear those thresholds is removed or reduced to the smallest proven
  subset, with its dead paths and documentation removed in the same milestone.
- The selected design passes the repository quality gate and retains the Milestone 6 boundary:
  sports-state data is optional corroboration, never proof of equivalence or settlement.

#### Pivot Point

- Remove Milestone 8 if Jev does not materially improve ambiguous-case decisions over Milestone 7
  alone after accounting for latency, cost, and new failure modes.
- Simplify or remove Milestone 7 if real markets rarely produce comparable contracts or its
  deterministic rules do not improve safety and useful-pair yield over the simpler baseline.
- Remove both matching layers if cross-platform equivalence does not provide enough demonstrated
  user value. Keep the market MCPs and platform-specific analysis, and refuse cross-platform price
  comparisons until a better-supported approach exists.

#### Current State

- **Status:** Complete locally.
- A predeclared evaluation sampled 25 real games across MLB, NFL, and NCAA on three dates. Seventeen
  games existed on both platforms; 15 had complete detail evidence suitable for evaluation.
- All 15 manually reviewed pairs were materially different because Polymarket waited for completion
  and paid 50/50 on cancellation while Kalshi imposed a two-day/48-hour window and fair-price
  settlement. No pair supported an actionable cross-platform price comparison.
- Deterministic M7 and the simple baseline classified 15/15 correctly. Normal Jev made no calls.
  Forced Jev made 12/15 automatic decisions, downgraded three correct NFL rejections to ambiguous,
  consumed 29,254 input tokens, and had 308 ms median provider latency.
- **M7 decision:** simplify and retain typed event identity, named outcomes, game-winner type,
  postponement, cancellation, and exact full-rule agreement. Remove unproven authority, official-
  result, overtime, tie, shortened-game, abandoned-game, and exclusion parsers.
- **M8 decision:** remove. It added no correct coverage or useful pair, introduced external cost and
  nondeterminism, and reduced forced-review coverage.
- **Why removal instead of disabling:** the normal route invoked Jev 0/15 times because retained
  deterministic checks had already resolved every case. Forcing Jev consumed 29,254 input tokens
  without adding a decision and changed three correct NFL rejections to ambiguity. Its 308 ms
  median latency met the speed threshold, but speed alone did not justify an unused credential,
  fourth MCP process, model drift, gateway failure mode, or ongoing maintenance surface.
- **Accepted tradeoff:** the system no longer attempts model-based equivalence for differently
  worded rules. Those pairs remain safely ambiguous and non-comparable. Reintroducing semantic
  review requires a balanced held-out set containing real equivalent pairs, measurable additional
  safe-match yield, and zero false equivalences over the retained deterministic baseline.
- No Jev or AI-gateway credential is required by the resulting runtime.
- Differently worded or incomplete full rules remain ambiguous unless the retained core checks
  prove a conflict. The primary LLM cannot upgrade that result. Sports state remains optional
  corroboration and never proves equivalence or settlement.

### Milestone 9: Bounded Sports Evidence Research

#### Work

- Connect the supported Tavily MCP as a fourth server after the sports-state MCP.
- Run research only after one sports event is identified and a viable primary pair or clearly
  scoped single-platform request exists.
- In the conversational agent, Tavily must never be the first MCP call. The same turn must first
  obtain typed game detail through either a market search followed by exact market detail or
  sports-state discovery followed by exact game-state detail. Discovery alone and conversation
  memory do not authorize research.
- Before any Tavily network request, host code must verify that the requested league, unordered
  participant pair, local game date, and scheduled start match that same-turn typed detail. A
  missing or altered identity skips the Tavily call rather than searching a guessed game.
- Keep this mandatory agent call-order gate distinct from the standalone Tavily MCP. A direct MCP
  client may call the structured tool itself, but it does not receive the conversational host's
  same-turn identity guarantee.
- Track search and extraction counts in per-request graph state.
- Start with these provisional limits:
  - At most two searches.
  - At most five results inspected per search.
  - At most three article extracts.
- Preserve source title, URL, publication date, retrieval time, and evidence relationship.
- Focus evidence on injuries, confirmed lineups, weather, venue changes, postponements, and other
  event-specific information that can materially affect the requested game analysis.
- Bind every evidence item to the identified league, participants, and scheduled game; reject
  same-team articles about another game as unrelated.
- Keep supporting and conflicting evidence distinct.
- Treat retrieved text as untrusted data and prevent it from overriding system instructions.

#### Exit Criteria

- The selected research tools are discovered and invoked through MCP.
- Agent tests prove Tavily cannot run first, cannot run from discovery or memory alone, and makes
  no provider request when its identity differs from same-turn typed detail.
- Search budgets cannot be exceeded by repeated model requests.
- Responses cite the evidence actually retrieved.
- Search failure produces a useful partial answer rather than a server error.

#### Pivot Point

- Adjust research limits using observed latency, cost, and answer quality.
- Remove extraction if search snippets provide enough evidence for the assignment demonstration.

#### Current State

- **Status:** Complete locally.
- The fourth application server is a student-authored Tavily MCP projection with one read-only
  `tavily_search_game_evidence` tool. The agent performs real MCP discovery and invocation; the
  server performs one bounded Tavily Search API request.
- The original generic Tavily MCP was evaluated through real discovery and a live call. It exposed
  broad crawl, map, research, and extraction tools, returned search output as untyped text, and
  omitted available publication dates and relevance scores. The implemented projection preserves
  those fields in a strict schema and matches the safety style of the other application servers.
- Research is host-blocked until supplied league, teams, game date, and scheduled start exactly
  match a typed market detail or game-state detail from the same turn. Each server call constructs
  its query, requests exactly five results, retains only HTTPS results naming both teams and the
  exact date with text relevant to the requested focus, and returns bounded snippets as explicitly
  untrusted data. Same-matchup ticket, hotel, and generic event pages are rejected for injuries,
  lineups, weather, and venue/schedule evidence when their text does not match that focus.
- The generated query uses the established local `game_date` without incorrectly pairing it with
  `scheduled_start`'s UTC clock. The UTC start remains typed identity evidence. Retained sources
  expose and sort by a bounded league-official/established-media/other authority heuristic, then
  publication time and provider relevance; the tier is not a correctness claim.
- Therefore the conversational path always has a prerequisite MCP sequence: market search plus
  market detail, or sports-state discovery plus game-state detail, before Tavily. Tavily cannot be
  the agent's first MCP call, and prior-session memory does not bypass this same-turn requirement.
  The standalone MCP remains directly callable for inspection, without that host-level guarantee.
- Per-turn graph state permits at most two research searches in addition to the existing four
  market/state calls. Search failures remain tool-level errors and the agent answers with already
  verified market or game evidence.
- Extraction was removed at the documented pivot point. Live search snippets contained enough
  event-specific evidence, while arbitrary URL extraction would add latency and prompt-injection
  surface without a demonstrated Milestone 9 need. The effective extraction budget is therefore
  zero, stricter than the provisional maximum of three.
- Tavily supports an optional API key. With no key, the server uses the official Tavily MCP's
  keyless search mode; no credential is required for local verification.
- A final 2026-09-20 real stdio MCP check for Marlins–Padres injuries returned one verified
  same-matchup/date source and rejected four results that failed the identity/date filter.
- Deterministic fixtures cover strict schemas, malformed responses, HTTP/rate failures, secret
  redaction, same-city opponent rejection, identity filtering, pre-detail blocking, the two-search
  budget, source citations, and useful partial answers when research fails.
- `../research/WEB_RESEARCH_MCP.md` defines the implemented contract and extension boundary.

### Milestone 9A: Multi-Sport Exact-Game Box Scores

#### Work

- Expose `sports_state_get_box_score(game_ref)` beside discovery and current game state.
- Reuse the opaque discovery reference without accepting team, date, provider, or mode selectors.
- Normalize MLB inning scoring, team totals, batting lines, and pitching lines with stable player
  IDs and integer `outs_recorded`.
- Normalize NFL and NCAA football period scoring, named team statistics, and categorized player
  statistics through one shared football contract.
- Use ESPN as the primary provider and the existing exact-identity MLB StatsAPI path as the MLB
  fallback. Keep structured game statistics independent of Tavily.
- Omit provider-unavailable optional statistics, give null inning values explicit participation
  semantics, separate required and optional field coverage, exclude season statistics and
  play-by-play, and expose bounded field-specific warnings.
- Cache live box scores for at most 10 seconds and final box scores for at most five minutes. Bind
  observation IDs to normalized snapshot content.
- Teach the agent to choose state for the current situation and box score for team/player game
  performance, while preserving deterministic market-to-game identity checks.

#### Exit Criteria

- The MCP tool accepts only a `game_ref` returned by `sports_state_find_games`.
- Completed MLB, NFL, and NCAA games return their supported line score and player/team statistics.
- Live MLB preserves not-yet-batted inning semantics and returns changing player game lines.
- Pitching display notation and integer outs agree; unavailable fields are absent and documented.
- Deterministic tests cover provider normalization, fallback, cache identity, MCP schemas, and agent
  consumption. Public MCP verification covers completed football/baseball and live baseball.

#### Current State

- **Status:** Complete locally.
- The box-score tool runs in the independent sports-state stdio process and consumes the same
  checksummed game reference as current-state detail.
- Baseball and football responses use sport-specific sections instead of irrelevant nullable
  branches. Completeness metadata identifies partial and unavailable provider sections.
- ESPN supplies all supported leagues. MLB StatsAPI remains a fallback only after an ESPN
  transport, HTTP, or schema failure and only after exact participant/date/start matching.
- The host validates returned identity and sport, stores typed box-score observations, permits them
  as exact research context, and keeps prediction-market settlement separate.
- Deterministic and real MCP checks exercise MLB, NFL, NCAA football, completed games, live MLB,
  stable player IDs, game-only statistics, outs normalization, caching, and observation changes.

### Milestone 9B: Exact-Game Player and Play History Tools

#### Work

- Expose `sports_state_list_players(game_ref)` as a compact directory of stable player IDs, names,
  teams, sides, and available game-stat groups.
- Expose `sports_state_get_player_stats(game_ref, player_id)` with only one selected player's
  game statistics, exact-game identity, completeness, warnings, and provenance.
- Expose `sports_state_get_play_by_play(game_ref, ...)` with a 1-50 play limit, chronological
  windows, stable provider-backed play IDs, mutually exclusive before/after anchors, and optional
  scoring, period, and home/away filters.
- Return paging and resume checkpoints plus earlier/later evidence so clients can inspect history
  or request only plays after their last observed ID.
- Normalize ESPN pitch/action plays for MLB and drive plays for NFL and NCAA football. Use
  exact-identity MLB StatsAPI at-bats only after eligible ESPN failure and disclose granularity.
- Reuse normalized box-score observations for player tools. Maintain a lifecycle-aware play-feed
  cache with content-derived observation IDs and a 1,000-play normalization bound.
- Validate all tool arguments before provider I/O, discard malformed individual plays with bounded
  warnings, and reject enclosing identity or schema conflicts.
- Validate every result in the agent host, route requests semantically, preserve exact-game
  matching safeguards, and keep sports detail separate from market settlement.

#### Exit Criteria

- All three tools accept only discovery-issued game references; player detail accepts only a stable
  listed ID, and play anchors accept only stable IDs from the exact game observation.
- Player list/detail responses avoid transferring unrelated full-box-score rows.
- Play windows remain chronological across latest, backward, and later-unseen requests, with no
  duplicate or fabricated IDs.
- Deterministic tests cover MLB, NFL, and NCAA football normalization, strict MCP schemas, agent
  routing and validation, cache reuse, filters, paging, invalid inputs, and MLB fallback.
- Bounded provider checks retrieve player directories, individual lines, and play windows from
  completed MLB, NFL, and NCAA football games.

#### Current State

- **Status:** Complete locally.
- The independent sports-state MCP exposes six read-only tools for discovery, current state, box
  scores, player lookup, player detail, and bounded play history.
- Player requests reuse one box-score cache entry and observation identity while projecting only
  the directory or selected player.
- Play responses expose provider granularity, stable IDs, sequence numbers, filtered/matching
  counts, navigation checkpoints, completeness, bounded warnings, and source provenance.
- ESPN supplies all supported leagues; exact-identity MLB StatsAPI fallback supplies at-bat-level
  play history when eligible.
- Unit, real MCP, stdio, scripted-agent, and bounded public-provider checks exercise the complete
  surface and its safety constraints.

### Milestone 9C: Agent-Readable Presentation and Evidence Semantics

#### Work

- Give `sports_state_get_box_score` a compact default summary, an explicit full view, and
  sport-applicable section views with optional away/home filtering.
- Return a machine-readable follow-up tip so the agent presents the default summary and tells the
  user that full and section-specific layouts are available without printing them automatically.
- Represent each baseball inning half as played, not played, not reached, or unknown. Treat an
  unnecessary final home half as complete line-score semantics.
- Report missing required and optional box-score fields separately. Optional omissions never make
  an otherwise complete section partial.
- Expose baseball `outs_before` and `outs_after`; classify pitches, plate appearances,
  substitutions, and other game actions; attach structured substitution participants when the
  provider supplies them.
- Declare market search result shape with `result_kind` and `contracts_location`.
- Declare quote freshness as current, stale, timestamp unavailable, or not trading. Reserve stale
  for an old authoritative quote timestamp and flag unusually late provider close timing.
- Support postgame-recap research and an official-only source policy. Return an explicit
  no-qualifying-sources status and require structured corroboration for snippet claims about a
  player return or activation.
- Validate each projection in the host and cover the behavior through unit, MCP schema, stdio, and
  scripted-agent tests.

#### Exit Criteria

- An ordinary box-score request transfers only the summary and directs the agent to offer full or
  section-specific follow-ups; explicit full/section calls return exactly the requested content.
- A final skipped bottom half is rendered as not played and does not mark the line score partial.
- Baseball play consumers cannot confuse post-event outs with pre-event state, and substitutions
  are distinguishable without interpreting at-bat prose.
- Completeness warnings name exact fields and separate required from optional omissions.
- Sports and generic market searches identify their contract container without inference.
- Missing quote timestamps are not labeled stale; truly old authoritative timestamps are.
- Official-only and postgame-recap research retain the existing identity, URL, result, and search
  budgets, and flagged return claims cannot be presented as confirmed without corroboration.
- The offline quality gate passes with real MCP discovery/call coverage.

#### Current State

- **Status:** Complete locally.
- Box-score, play-by-play, market, and research MCP schemas expose the presentation and evidence
  semantics directly to any consuming agent.
- Provider caches retain full normalized observations while public box-score calls project only the
  requested view, preserving player lookup and observation identity.
- Deterministic fixtures cover summary/full/section views, team filtering, skipped home halves,
  required/optional field coverage, pre/post outs, substitutions, quote freshness, close timing,
  result containers, official-only sources, empty evidence, recap focus, and corroboration cautions.

### Milestone 10: Unified Multi-MCP Sports Information Assistant

#### Product Boundary

- Build an all-in-one conversational information getter for supported MLB, NFL, and NCAA Division I
  football games and their Kalshi and Polymarket full-game-winner contracts.
- Answer with verified sports, market, and current web information. Do not produce an independent
  win probability, betting recommendation, or generic `YES`, `NO`, or `NO POSITION` conclusion.
- Treat market prices as provider observations, not as the agent's forecast. Keep sporting state,
  market trading state, contract equivalence, and market settlement separate.
- Prefer a direct answer tailored to the question over a mandatory report template. Produce a
  fuller game brief only when the user asks broadly or when several sources are materially useful.
- Keep the FastAPI and LangGraph application as the primary product and assignment implementation.
  A repository Codex skill provides a secondary direct-MCP test surface for interactive and
  headless checks. Direct Codex sessions do not claim LangGraph memory, host-side matching, tool
  budgets, or HTTP endpoint coverage.

#### Work

- Define and test an explicit intent-to-source routing matrix:
  - General sports explanations and unrelated knowledge questions preserve the no-tool path.
  - Schedule, score, lifecycle, situation, box score, player-stat, and play-by-play questions use
    only the sports-state MCP tools needed for the answer.
  - A named prediction-market platform uses only that platform's MCP unless the user requests a
    comparison or broader game brief.
  - Cross-market comparison uses both market MCPs and retrieves exact contract detail before
    comparing prices, outcomes, rules, or settlement terms.
  - Current injuries, lineups, weather, venue or schedule changes, game news, and postgame recap
    questions use bounded Tavily research only after one exact game is established.
  - A broad all-in-one game request may use sports state, both relevant market MCPs, and bounded
    research when each source contributes material information.
- Resolve one canonical game before combining sources. Bind every game-state observation, market
  contract, and research result to the league, participants, date, and scheduled start. Ask the
  user to choose when more than one game or provider event remains plausible.
- Retrieve exact detail through returned identifiers and opaque references. Never construct or
  guess a ticker, numeric market ID, `game_ref`, player ID, schedule, or consumer URL.
- Synthesize only the sections supported by the request and available evidence:
  - Direct answer and exact game identity with localized start time.
  - Current lifecycle, score, sport-specific situation, and observation time.
  - Requested box-score, player-stat, or play-by-play detail.
  - Kalshi and Polymarket contract identities, named outcomes, provider prices, quote times,
    freshness warnings, links, rules, and explicit settlement when available.
  - Deterministic equivalence, mismatch, or insufficient-evidence result before any cross-market
    price comparison.
  - Bounded current web evidence with source links, retrieval times, and corroboration cautions.
  - Missing sources, snapshot drift, uncertainty, and limitations that affect the answer.
- Keep each fact attached to its source and observation time. Never present separate MCP calls as
  one transactionally consistent snapshot or use a final sporting result as proof of market
  settlement.
- Support session-scoped follow-ups without silently presenting remembered observations as fresh.
  Reuse stable identity from memory, then refresh only the current sources required by the new
  question.
- Return a useful partial answer when one MCP or upstream provider is unavailable. Name the missing
  source, retain verified results from other sources, and do not substitute a different provider's
  facts.
- Run independent calls concurrently only after exact event identity and requested scope are known.
  Preserve bounded tool budgets, deterministic output ordering, and controlled failure behavior.
- Add scripted-model and real-MCP integration tests that assert the exact servers and tools called,
  their dependencies, the normalized evidence supplied to synthesis, and irrelevant sources that
  were intentionally not called.

#### Exit Criteria

- A representative all-in-one game request invokes sports state, Kalshi, and Polymarket through
  real MCP sessions and invokes bounded Tavily research only when current external evidence is
  relevant.
- Sports-state-only, Kalshi-only, Polymarket-only, cross-market, full-game-brief, follow-up, and
  no-tool prompts route to the minimum correct source set.
- The agent verifies exact event identity before combining sources and asks for clarification when
  multiple games, contracts, or player identities remain plausible.
- Direct questions receive concise direct answers; broad requests receive a coherent sourced brief
  rather than a dump of every available field.
- Local-date requests use the requested IANA timezone rather than treating a UTC calendar date as
  the user's date.
- Responses identify sources and observation or quote times, distinguish independently retrieved
  snapshots, and keep game result, market price, rules, equivalence, and settlement separate.
- Same-session follow-ups retain the correct game context and cross-session tests prove isolation.
- Partial server failure produces an explicitly incomplete but still useful answer without an
  unhandled error or unsupported substitution.
- The agent handles arbitrary reasonable in-scope queries rather than memorized examples.
- Responses never present an agent-generated betting pick or independent win probability.
- A fresh headless Codex session discovers the repository skill and exercises a configured MCP
  using the same identity-first routing rules, with the direct-MCP limitations stated explicitly.

#### Current State

- **Status:** Complete locally.
- The LangGraph prompt routes narrow questions to the minimum source set and broad game briefs to
  sports state, both market platforms, and bounded research when those sources add evidence.
- Each turn supports eight market/state calls and two research searches. Independent calls in one
  reasoning step execute concurrently; identifier-dependent calls remain ordered across steps.
- The host identifies available and unavailable MCP sources per request, preserves partial
  answers, validates typed results, and produces deterministic contract and game matching reports.
- Real-MCP integration coverage exercises all four servers in one sourced brief and verifies the
  exact call sequence, research gate, matching report, provenance notices, and call budgets.
- `.agents/skills/sports-information/SKILL.md` supplies the direct Codex workflow without
  representing it as a substitute for the application path.

#### Pivot Point

- If a full brief exceeds latency or context budgets, make external research opt-in and use compact
  discovery projections before removing identity, provenance, freshness, or rule evidence.
- If model-selected routing remains inconsistent, add a small deterministic intent classifier for
  source eligibility while leaving exact tool choice and synthesis to the agent.
- Reduce response breadth before weakening correct tool selection, session memory, MCP correctness,
  event identity, or failure behavior.
- Never collapse the independent MCP servers into one provider facade merely to simplify routing.

### Milestone 11: Integrated into Milestone 10

- Milestone 10 contains the unified game brief as one response mode within the broader
  question-routing workflow.
- No separate implementation milestone or duplicate workflow is required for this capability.

### Milestone 12: Optional SQLite Sports-Research Snapshot Ledger

#### Current State

- **Status:** Deferred until after deployment.
- No ledger server, storage model, or save/retrieval tools are part of the current application.
- Deployment and required assignment verification take priority over optional persistence.

#### Work

- Build the ledger only after the complete research workflow is stable.
- Store a research snapshot only after an explicit user request.
- Save league, participants, provider event and contract references, local kickoff, quote timestamps,
  provider prices, evidence URLs, equivalence decision, and explicit settlement when available.
- Support basic retrieval in a later turn.
- Document that local Cloud Run storage is not durable across instance replacement.

#### Exit Criteria

- The SQLite server is independently discovered and invoked through MCP.
- “Save this research” uses conversational context and creates one record.
- A different session cannot accidentally overwrite the record through ambiguous context.
- Ledger failure does not destroy the generated information response.

#### Pivot Point

- Cut or defer this milestone if core rubric work, deployment, or verification is incomplete.
- Do not introduce managed database infrastructure for Assignment 1.

### Milestone 13: Sports Failure Handling and Verification

#### Work

- Consolidate tests added during earlier milestones.
- Add deliberate failure injection for every MCP server:
  - Server unavailable or transport closed.
  - Tool execution error.
  - Malformed or schema-invalid response.
- Test upstream HTTP timeouts, rate limits, empty results, and partial data.
- Test invalid `/chat` payloads and missing session IDs.
- Test session recall and isolation across multiple turns.
- Test tool routing by recording invoked server and tool names.
- Test research-budget enforcement.
- Test ambiguous aliases, unknown opponents, unsupported spreads/totals/props, invalid timezones,
  conflicting date filters, reversed ranges, mutually exclusive selectors, and invalid limits.
- Test malformed, provider-mismatched, and query-mismatched continuation cursors.
- Preserve stable validation codes and offending fields, and prove semantic/cursor rejection occurs
  before provider I/O.
- Parse provider records independently, return bounded discard warnings with partial results, and
  enforce page/record size limits.
- Give cached normalized observations an identity and explicit hit/age metadata; never relabel
  retrieval time as quote provenance.
- Test bounded empty coverage, stale quotes, awaiting-resolution lifecycle, explicit settlement,
  inconsistent provider timing, and missing sports detail context.
- Run formatting, linting, type checking, unit tests, and integration tests.

#### Exit Criteria

- All three rubric-required MCP failure classes return controlled responses.
- The HTTP service does not expose unhandled stack traces.
- Tests demonstrate no-tool, single-tool, and multi-tool reasoning.
- Tests demonstrate clarification without an upstream call and prove that no component guesses a
  ticker, numeric market ID, team identity, schedule, or consumer URL.
- Verification commands and observed results are documented truthfully.

#### Pivot Point

- Fix correctness and failure behavior before adding presentation features.

#### Current State

- **Status:** Complete locally.
- All four configured MCP servers are exercised under process-unavailable conditions while healthy
  servers remain usable. Provider and MCP tests cover transport failures, tool errors, malformed
  responses, timeouts, rate limits, bounded empty results, and partial records.
- The HTTP and LangGraph paths cover invalid requests, memory isolation, no-tool, single-tool,
  multi-tool, source-specific failure, final-model failure, and per-turn budget enforcement.
- Excess parallel tool requests produce protocol-complete tool results while the public activity
  record remains bounded by its response schema.
- The deterministic suite and retained unit-test contracts are recorded in
  `../research/ADVERSARIAL_TESTING_REPORT.md`.

### Milestone 14: Base Cloud Run Deployment and Acceptance

#### Product Boundary

- Put the existing non-watch conversational agent on Cloud Run as a complete, independently
  accepted vertical slice before provisioning any watch infrastructure.
- Deploy FastAPI, LangGraph, the configured cloud model, session-scoped memory, and all four MCP
  integrations behind the required public `POST /chat` contract.
- Prove arbitrary sports-information, market, research, memory, no-tool, and partial-failure
  behavior on the live URL. A container that starts but cannot complete real tool calls does not
  satisfy this milestone.
- Exclude Firestore watch state, Cloud Scheduler, the internal polling route, Telegram secrets, and
  live watch automation. Those belong to Milestone 14A after the base service and Milestone 16B
  agent behavior are accepted.
- Keep unaccepted watch automation disabled in the deployed environment. The live service must not
  imply that a saved watch will continue running until Milestone 14A passes.

#### Decisions Locked by This Plan

- **One base service:** deploy one Cloud Run service for the assignment agent. Do not introduce a
  second service, worker, queue, or database merely to obtain the first live URL.
- **Exact public contract:** preserve `POST /chat` with `{query, session_id}` and `{response}`.
  Deployment metadata, tool traces, and internal errors stay outside that response.
- **Cloud model only:** use the configured cloud-accessible model backend. Do not package or call
  Ollama from Cloud Run.
- **MCPs run for real:** include every runtime dependency needed to start the configured MCP
  servers, perform live `tools/list`, and complete live `tools/call` operations. No deployment test
  may substitute hard-coded tool responses.
- **Instance-local memory is honest:** retain `--max-instances 1` for the assignment's in-memory
  checkpointer, test same-session and cross-session behavior, and document that scale-to-zero or
  revision replacement can erase conversations.
- **Secrets remain external:** inject model and provider credentials at deployment time from the
  shell or Secret Manager. Never copy `.env` into the image, build context, logs, README, or
  committed command history.
- **Production process contract:** bind to `0.0.0.0:$PORT`, run as a non-root user, handle SIGTERM,
  place bounded timeouts around model and MCP work, and return controlled errors instead of
  restarting on an ordinary upstream failure.
- **Observable acceptance:** structured logs identify request correlation, selected source classes,
  MCP availability, latency, and sanitized failure category without recording prompts, responses,
  session IDs, credentials, or provider payloads.
- **Reproducible release:** record the image digest, Cloud Run revision, region, model configuration,
  MCP configuration, and exact smoke-test commands. Keep the previous healthy revision available
  for rollback until acceptance passes.
- **Cost boundary:** deploy at zero minimum instances and one maximum instance, set bounded request
  concurrency and timeout, inspect billing after live tests, and document scale-to-zero and external
  API costs.

#### Base Deployment Workflow

1. Run the complete local quality gate and build the production image from a clean source context.
2. Run the image locally with deployment-shaped environment values and verify health, `/chat`, all
   MCP processes, memory, shutdown, and secret absence.
3. Enable the required Google APIs, select the project and `us-west1`, and create the image
   repository and least-privilege runtime identity.
4. Build and publish the immutable image, then deploy one Cloud Run revision with watch automation
   disabled and only base-agent secrets configured.
5. Verify the live URL with no-tool, single-source, multi-source, same-session memory,
   cross-session isolation, malformed-request, and partial-provider-failure probes.
6. Inspect Cloud Run logs, revision health, instance behavior, response bounds, and billing. Fix the
   service and repeat the same acceptance set before leaving traffic on the revision.
7. Record only observed deployment instructions and results, then prepare draft deployment
   documentation for the accepted base revision. Milestone 14A owns final submission packaging.

#### Work

- Finalize the multi-stage Dockerfile with a non-root runtime user.
- Confirm the application binds to `0.0.0.0:$PORT`.
- Configure secrets through environment variables or Secret Manager.
- Deploy with `--max-instances 1` to support instance-local memory.
- Run live tests against the deployed `/chat` endpoint.
- Verify same-session recall while the instance remains active.
- Verify Polymarket-only, Kalshi-only, sports comparison, local-date discovery, research, no-tool,
  memory, and failure responses.
- Complete draft README setup, run, base deployment, cost, limitations, and MCP attribution sections.
- Update all three required architecture diagrams to match the accepted base revision and label
  automatic cloud watches as pending Milestone 14A.
- Add a repeatable source-ZIP validation command, but defer the final ZIP until Milestone 14A so it
  contains the accepted watch deployment rather than this intermediate base revision.
- Leave `PROCESS_LOG.md` for Rylan Wade's accurate personal narrative and lessons learned.
- Audit the production dependency graph, Docker build context, executable paths, MCP subprocess
  startup, writable temporary locations, health behavior, and SIGTERM handling.
- Add deployment configuration that disables watch automation without disabling ordinary
  sports-information behavior or compiling a second application variant.
- Add preflight checks for required environment variables, safe configuration summaries, MCP
  dependency availability, and forbidden secret files in the image context.
- Create idempotent base deployment commands or a script for API enablement, image publication,
  Cloud Run configuration, revision inspection, traffic promotion, and rollback. Keep literal
  credentials out of every command file.
- Add a live acceptance harness that targets an explicit URL and session prefix, applies bounded
  timeouts, records sanitized results, and never runs accidentally as part of the unit suite.
- Verify live MCP discovery and invocation evidence for all four servers rather than inferring tool
  use from final prose.

#### Verification Plan

- Container tests inspect the effective user, `PORT` binding, environment, image contents,
  installed MCP runtimes, writable paths, signal shutdown, health response, and absence of local
  secret files.
- Deployment tests verify service identity, revision/image digest, region, min/max instances,
  concurrency, timeout, environment names, secret references, ingress, and unauthenticated access
  to only the assignment-required public surface.
- Live `/chat` tests use unique session IDs and verify request validation, stable response schema,
  same-session recall, cross-session isolation, no-tool routing, every MCP independently, a chained
  multi-MCP query, and a broad four-server query.
- Failure probes make one upstream or MCP source unavailable at a time and confirm the live service
  returns a bounded partial answer without leaking internals or destabilizing later requests.
- Log inspection confirms each acceptance request can be correlated while prompts, responses,
  sessions, tokens, credentials, and private provider data remain absent.
- Scale-to-zero and revision tests confirm a cold request recovers, memory limitations are described
  honestly, the previous revision can receive traffic again, and no watch polling or Telegram
  delivery occurs.
- Cost inspection records actual Cloud Run and external-provider usage produced by the acceptance
  run and confirms the configured ceilings match the README.

#### Exit Criteria

- The live Cloud Run URL answers arbitrary reasonable non-watch queries in the supported product
  scope.
- Deployment uses the required request and response contract.
- README diagrams match the code that was actually deployed.
- Draft submission documentation describes only verified base behavior and contains no secrets.
- The base service remains available until it is replaced by an accepted Milestone 14A revision.
- The deployed image runs non-root, binds to the injected port, shuts down cleanly, contains no
  secrets, and exposes no unaccepted watch-automation promise.
- Logs, revision metadata, rollback, cost evidence, README instructions, and diagrams match the
  deployment that was actually tested.
- Milestone 14 completion authorizes Milestone 14A infrastructure work; it does not by itself claim
  persistent watches, Scheduler polling, Firestore state, or Telegram delivery.

#### Current State

- **Status:** Not started; no live Cloud Run acceptance evidence is recorded.
- Local application, container configuration, MCP integrations, memory, and failure tests provide
  inputs to deployment but do not satisfy any live exit criterion.

#### Pivot Point

- Reduce response breadth, container size, or optional research before weakening real MCP
  invocation, session isolation, secret handling, or the required public contract.
- Roll traffic back to the previous healthy revision when a deployment fails acceptance; do not
  patch a live container or substitute localhost evidence.
- Keep watch automation disabled until the base deployment is stable and Milestone 14A dependencies
  are complete.

### Milestone 14A: Cloud Watch Automation and Final Submission Acceptance

#### Execution Gate

- Start only after Milestone 14 has an accepted live base revision and Milestones 16A, 16B, and 16C
  pass their local lifecycle, notification, conversational-agent, and Telegram-command exit criteria.
- Treat this as a deployment submilestone, not as the place to redesign watch rules, agent prompts,
  runtime states, event wording, polling arithmetic, or delivery semantics.

#### Product Boundary

- Extend the proven Cloud Run agent with durable, automatic watch operation.
- Use Firestore as the deployed watch repository, authenticated Cloud Scheduler as the polling
  clock, and the configured Telegram bot as the live external delivery channel.
- Preserve `/chat` as the only surface for creating or changing watches. Cloud Scheduler and the
  internal polling endpoint are infrastructure; the same Telegram bot accepts only read-only
  `/help` and `/status` commands after Milestone 16C. No second app or service is deployed.
- Remove the user's need to run or supervise a foreground process. A confirmed deployed watch must
  continue polling after the chat request completes and across Cloud Run instance replacement.
- Preserve exactly the watch schemas, runtime transitions, lifecycle events, notification policy,
  and agent behavior accepted locally.

#### Decisions Locked by This Plan

- **Extend the accepted service:** add watch infrastructure to the same Cloud Run service and image
  lineage accepted in Milestone 14. Do not create an unrelated deployment with different agent or
  watch behavior.
- **Firestore is authoritative in cloud:** no deployed watch state, observation, event, lease,
  trigger, or delivery record relies on the container filesystem or process memory.
- **Scheduler is the only polling clock:** create one Cloud Scheduler job that invokes the internal
  poll route once per minute. The coordinator applies each watch's due-time policy and returns
  quickly when nothing is due.
- **Private polling surface:** require Google-issued OIDC, the exact service URL audience, and a
  dedicated least-privilege Scheduler service account. Public `/chat` access does not make the
  polling route public.
- **Real Telegram configuration:** load the bot token from Secret Manager and keep the destination
  outside source control and model context. A live message is required for acceptance; a fake
  transport proves code behavior but not deployed delivery.
- **No daemon or resident runner:** Cloud Run requests perform bounded claim, poll, persist, and
  delivery work. Do not launch `run_watches.py` inside the web container.
- **One inbound route on the existing service:** switch the locally accepted Telegram command
  handler from `getUpdates` to a bounded webhook route on the same Cloud Run service. Validate the
  configured Telegram webhook secret and allowed chat before any watch read. Telegram webhooks and
  `getUpdates` must not consume the same bot's updates at the same time.
- **Atomic and idempotent recovery:** Firestore transactions, leases, fingerprints, and outbox state
  must tolerate Scheduler retries, request overlap, instance termination, revision replacement, and
  delivery retry without duplicate logical events or messages.
- **Safe enablement:** deploy schema-compatible code first, verify Firestore and private-route
  health, then create or enable the Scheduler job and Telegram destination. Provide one switch that
  stops new claims without deleting watches or history.
- **Base service remains protected:** watch failures, Firestore outages, Scheduler authentication
  errors, and Telegram failures must not break ordinary `/chat` sports-information requests.
- **Cloud truth is observable:** expose sanitized poll counters and classifications in logs and
  durable watch status while keeping session IDs, destinations, tokens, prompts, provider payloads,
  and private Firestore data out of logs.
- **Cost remains bounded:** retain scale-to-zero where compatible, one maximum instance for
  assignment memory, bounded poll work, grouped observations, no-token polling, and zero Tavily use
  on the polling path. Measure the cost effect of one-minute Scheduler traffic.

#### Production Watch Flow

1. A user creates and confirms a watch through the accepted `/chat` agent flow.
2. The application service commits the rule and `awaiting_first_poll` runtime state to Firestore and
   returns immediately; it does not wait for Scheduler.
3. Cloud Scheduler sends an authenticated request to the private polling route once per minute.
4. The coordinator transactionally claims due watches, groups shared evidence, invokes the existing
   MCP tools, persists observations, and evaluates deterministic conditions.
5. The first usable poll records the appropriate lifecycle event and Telegram outbox item; later
   polls record only real state transitions or condition triggers.
6. The delivery worker claims due outbox records, sends Telegram, and records sent, retry, or
   terminal failure without changing the underlying watch event.
7. `/chat` list, inspect, and inbox requests read Firestore runtime truth and delivery status. They
   never infer monitoring from Scheduler existence or Telegram success.
8. Instance replacement, Scheduler retry, or deployment of a new revision resumes from Firestore
   leases and state without duplicate activation or trigger messages.

#### Work

- Provision Firestore in the selected project and region, required indexes, the Cloud Run runtime
  identity, the Scheduler identity, Secret Manager entries, and minimum IAM grants.
- Configure production repository selection and fail startup or watch operations clearly when
  required cloud configuration is incomplete; never fall back silently to SQLite.
- Deploy schema-compatible Firestore readers/writers and verify migration behavior with existing
  documents before enabling automatic polling.
- Configure and verify the authenticated internal polling route, audience, service-account identity,
  request timeout, Scheduler retry policy, and one-minute job.
- Configure the real Telegram secret and destination, validate them without logging values, and
  preserve inbox-only behavior when external delivery is deliberately disabled.
- Configure the Telegram webhook and command menu on the existing bot, verify the secret header,
  allowed chat, and chat-to-session binding, and disable local `getUpdates` for the deployed bot.
- Add deployment enable/disable and rollback procedures that pause new claims safely, retain watch
  history, drain or preserve outbox records, and avoid duplicate delivery after re-enable.
- Add dashboards or bounded log queries for Scheduler invocations, claimed watches, source readiness,
  lifecycle events, triggers, delivery results, retry exhaustion, latency, and sanitized errors.
- Update deployment scripts, environment templates, architecture diagrams, operator commands, cost
  guidance, incident steps, and grading evidence from actual cloud resources.
- Run the complete final quality gate and build the submission ZIP from the accepted revision,
  excluding `.env`, credentials, caches, virtual environments, local databases, test artifacts, and
  machine-specific files.
- Complete the final README, diagrams, cost disclosure, live URL instructions, limitations, MCP
  attribution, and submission checklist. Leave `PROCESS_LOG.md` content to Rylan Wade and validate
  only its presence and required structure.

#### Verification Plan

- IAM tests prove the Scheduler identity can invoke only the intended service route, unauthenticated
  requests fail, wrong audience and wrong identity fail, and the runtime identity has only required
  Firestore and secret access.
- Firestore acceptance creates, reads, revises, pauses, resumes, lists, inspects, and deletes test
  watches through application services while preserving session isolation and schema meaning.
- Scheduler acceptance covers no-due, not-yet-active, due, multiple grouped watches, delayed game,
  partial source failure, total source failure, terminal state, and bounded request timeout cases.
- Idempotency acceptance repeats the same Scheduler request, overlaps two requests, expires a lease,
  terminates an instance mid-cycle, deploys a replacement revision, and proves one logical event
  and one Telegram message per fingerprint.
- Live Telegram acceptance creates a uniquely labeled opted-in watch, receives the correct
  monitoring lifecycle message, verifies its inbox and delivery record, and confirms that missing
  or invalid Telegram configuration does not disable watch polling or stored events.
- Inbound Telegram acceptance sends `/help`, `/status`, and `/status <watch_id>` through the real
  bot to the existing Cloud Run service; verifies Firestore-backed answers and rejects unauthorized
  chats, invalid webhook secrets, and duplicate updates without affecting polling or `/chat`.
- Agent acceptance repeats the Milestone 16B create, confirm, list, inspect, inbox, pause, resume,
  revise, delete, and trigger-investigation flows against Firestore on the live `/chat` URL.
- Base regression reruns Milestone 14 no-tool, MCP routing, memory, and partial-failure probes while
  Scheduler traffic is active.
- Cost and operations acceptance records Scheduler, Cloud Run, Firestore, Secret Manager, Telegram,
  model, MCP, and Tavily behavior; ordinary polling must still use zero model and Tavily calls.

#### Documentation Alignment

- Rewrite final README, watch explanation, deployment instructions, architecture diagrams, testing
  report, and assignment alignment in present tense as one deployed system.
- Describe `/chat` management, automatic Scheduler polling, Firestore durability, lifecycle inbox,
  Telegram delivery, and Telegram status/help replies as one continuous workflow. Do not frame
  cloud watches as an add-on or narrate the order in which milestones were built.
- Include exact setup, disable, rollback, secret-rotation, status-inspection, and cost commands only
  after they have been executed successfully against the accepted project.
- State limitations honestly: instance-local conversation memory, session IDs are not
  authentication, one configured Telegram recipient, polling latency, provider freshness, and
  temporal correlation does not prove causation.

#### Exit Criteria

- The already accepted base `/chat` service remains healthy with Scheduler traffic active.
- A user can manage the complete watch lifecycle through `/chat` without starting a local runner or
  knowing about Firestore, Scheduler, leases, outbox records, or Cloud Run revisions.
- Confirmed watches persist in Firestore, poll automatically through authenticated Scheduler calls,
  survive instance and revision replacement, and expose truthful runtime health.
- One real first-poll lifecycle event and one real condition trigger reach the configured Telegram
  recipient exactly once and remain available in the authoritative inbox.
- The same Telegram bot answers read-only help and watch-status commands through the deployed
  service, using Firestore runtime summaries without starting a new poll or exposing other sessions.
- Failures in sources, Firestore, Scheduler authentication, or Telegram are bounded, observable,
  recoverable where appropriate, and do not create false monitoring claims or break ordinary chat.
- Deployed agent, watch, memory, MCP, documentation, security, cost, and submission acceptance all
  pass against the same live revision left available for grading.
- The final source ZIP, README, three diagrams, live URL, and Rylan Wade-authored process log are
  present, internally consistent, free of secrets, and ready for Brightspace submission.

#### Current State

- **Status:** Not started; blocked on live Milestone 14 acceptance and local Milestones 16A/16B/16C.
- Firestore, Scheduler, OIDC, and Telegram behavior currently have local or controlled-substitute
  coverage only; no production watch claim is permitted.

#### Pivot Point

- Disable Scheduler claims or Telegram projection before weakening Firestore authority,
  authentication, event idempotency, secret handling, or the base `/chat` service.
- Reduce polling scope or message detail before moving deterministic watch evaluation into the
  model or relying on Cloud Run process memory.

### Milestone 15: Event-Aware Natural-Language Watches

#### Product Boundary

- Let a user describe a price watch conversationally, resolve the exact game and contracts through
  the existing MCP tools, preview one typed rule, and activate it after confirmation.
- Combine prediction-market movement with verified game events. Initial triggers cover absolute
  price movement, cross-platform divergence, scoring plays, lifecycle changes, and price movement
  with no tracked scoring event inside a bounded time window.
- Treat time alignment as correlation evidence, not proof that a game event caused a price move.
  Preserve provider quote time, retrieval time, sports observation time, and play time separately.
- Keep the service read-only. Watches observe and alert; they never place, recommend, or prepare a
  trade.
- Make watches part of the primary product flow. A user creates and investigates them through the
  same conversational agent used for game and market questions.

#### Decisions Locked by This Plan

- **One rule contract:** LangGraph and local Codex emit the same versioned `WatchRule` JSON schema.
  Pydantic validation and deterministic semantic checks are authoritative; model prose is not
  executable configuration.
- **Existing observation tools:** compilation and polling use `sports_state_find_games`,
  `sports_state_get_game_state`, `sports_state_get_play_by_play`, the two platform search tools,
  and exact market-detail tools. Tavily is excluded from routine polling and remains available only
  for a bounded triggered investigation.
- **Pinned identity:** an active rule stores canonical league, participants, scheduled start,
  opaque `game_ref`, platform market identifiers, named outcome mapping, and contract type. The
  coordinator never guesses or constructs identifiers during a poll.
- **No-token polling:** the coordinator performs MCP calls, timestamp alignment, arithmetic,
  windowing, deduplication, and rule evaluation in ordinary Python. Creating or revising a rule is
  the only required model operation.
- **Edge-triggered alerts:** one condition transition creates one trigger fingerprint. Cooldowns,
  re-arm conditions, and idempotency keys prevent repeated alerts from an unchanged state.
- **Shared execution core:** Cloud Scheduler calls an authenticated internal Cloud Run polling
  endpoint once per minute; the coordinator returns immediately when no watch is due. A foreground
  local runner calls the same coordinator. Local Codex does not pretend a chat process can remain
  alive after the session ends.
- **Scheduler authentication:** the internal polling endpoint verifies a Google-issued OIDC token,
  expected audience, and dedicated Scheduler service-account identity even though public `/chat`
  remains reachable for assignment grading.
- **Portable state:** SQLite backs local development; Firestore backs Cloud Run. Both implement one
  repository contract used by the watch application service. Cloud Run never relies on its ephemeral
  filesystem for active watches.
- **Primary and secondary agent paths:** the Cloud Run product uses the LangGraph compiler with the
  configured cloud model API key. The repository Codex skill provides a secondary local compiler
  and investigator using the same MCP tool order, schemas, validation, and watch CLI.
- **Minimal default alert:** the evaluator produces a complete template alert without an LLM. A
  model explanation is optional and receives only the triggering rule, bounded price deltas,
  relevant plays, contract metadata, and missing-source flags.
- **Separate persistence purpose:** watch state is operational data required to schedule and dedupe
  monitoring. It does not implement or reactivate the deferred Milestone 12 research ledger.

#### Canonical Watch Models

- `WatchRule` contains:
  - schema version, watch ID, lifecycle, creation source, and session scope;
  - exact game identity and activation window;
  - one or more exact market references with platform and named outcome;
  - typed conditions, thresholds, rolling windows, cooldowns, and re-arm behavior;
  - allowed game-event classes and explicit `no_tracked_event` semantics;
  - polling policy reference rather than an unconstrained model-generated interval;
  - explanation policy and bounded delivery policy;
  - compiler preview, user confirmation time, and provenance.
- `WatchObservation` contains exact rule and event identity, provider observation IDs, quote and
  retrieval clocks, normalized prices, game lifecycle and situation, bounded new play IDs, cache
  metadata, and per-source availability.
- `WatchTrigger` contains the deterministic condition result, before/after values, correlated game
  events, stale or missing-source warnings, trigger fingerprint, delivery state, and optional
  investigation reference.

#### Cloud Run Workflow

1. A user describes a watch through `POST /chat` using the existing `session_id` contract.
2. The LangGraph compiler asks for clarification when the team, game, platform, outcome, threshold,
   time window, or event relationship is ambiguous.
3. The agent resolves the exact game and market references through existing MCP discovery and detail
   calls, emits a `WatchRule`, and passes it to the host validator.
4. The response presents a concise rule preview, estimated polling behavior, and token policy. The
   rule remains inactive until the user confirms it in the same session.
5. The host watch service persists the confirmed rule. Cloud Scheduler calls an authenticated
   `/internal/watches/poll` endpoint once per minute; the endpoint loads and claims only due watches.
6. The deterministic coordinator groups compatible watches by game and market reference, performs
   one shared observation set, evaluates every rule, and records the poll through the shared state
   repository without exposing a model-callable mutation tool.
7. A trigger creates a template alert. Automatic explanations are disabled; a user requests a
   bounded investigation of a stored trigger through `/chat`.
8. The user lists alerts or asks a follow-up through `/chat`; the agent retrieves bounded event
   history and refreshes only evidence needed for the answer.

#### Local Codex Workflow

1. The repository sports-information skill recognizes create, revise, pause, list, and investigate
   watch intents.
2. Codex resolves exact games and contracts through the existing configured MCPs, generates the
   canonical rule, and submits it to `scripts/watch_cli.py validate` through JSON standard input.
3. The user confirms the preview before persistence.
4. `uv run --env-file .env python scripts/run_watches.py` runs the shared deterministic
   coordinator in the foreground against SQLite. Process shutdown stops polling cleanly; saved
   rules remain available.
5. Codex inspects triggers through the bounded watch CLI and uses existing MCP data tools for
   optional investigation. Direct Codex does not claim LangGraph checkpoint memory or Cloud
   Scheduler behavior.

#### Token and Provider-Cost Controls

- Compile once on creation and again only when the user changes semantic rule fields.
- Perform zero model calls during ordinary polling and zero Tavily calls unless a unique trigger is
  explicitly configured for investigation.
- Group watches that reference the same game or contract so one MCP observation serves all of them.
- Store deltas and referenced observation IDs rather than duplicating complete payloads on every
  interval.
- Stop polling when all referenced games and contracts reach terminal states or the activation
  window expires.
- Reject model-proposed cadences outside an application allowlist. Set the allowlist only after live
  rate-limit and latency measurements for every provider.
- Enforce per-watch and service-wide ceilings for polls, automatic explanations, tool calls, Tavily
  searches, and stored events.

#### Initial Defaults

- **Activation window:** start fifteen minutes before the scheduled game and stop fifteen minutes
  after every referenced game and contract reaches a terminal state.
- **Polling cadence:** poll due watches once per minute while a game is active, once every five
  minutes before the game or during a delay, and once after a terminal transition. Provider
  measurements can only make this policy more conservative before release.
- **Explanation policy:** automatic model explanations are off. The evaluator writes a complete
  deterministic template alert, and the user requests an agent investigation when wanted.
- **Delivery:** write every alert to the in-product inbox exposed through `/chat` and the bounded
  local CLI. An explicitly opted-in watch also creates one Telegram outbox record.
- **Event vocabulary:** support scoring and lifecycle changes plus
  `no_tracked_scoring_event`. Turnovers, substitutions, pitching changes, injuries, and other
  causal-looking categories wait for tested cross-league normalization.
- **Retention:** retain ordinary non-triggering observations for seven days, trigger evidence for
  thirty days, and watch definitions until the user deletes them. Implement explicit cleanup rather
  than relying on billable Firestore TTL deletes.
- **Ownership boundary:** scope watches to the creating session and use unguessable watch IDs, but
  state clearly that session IDs are not authentication. Milestone 15 stores no external delivery
  destination or sensitive personal information.

#### Implementation Gates

- Measure provider limits, latency, and quote update frequency before enabling the default cadence.
- Keep the assignment `/chat` endpoint publicly gradeable while requiring verified Google OIDC for
  the scheduler endpoint.
- Require an authentication design before supporting private multi-user history or user-supplied
  delivery destinations.

#### Work

- Define the canonical models, JSON Schemas, stable validation errors, and schema-migration policy.
- Implement a compiler subgraph that separates intent extraction, MCP identity resolution, rule
  validation, preview, confirmation, and persistence.
- Implement the watch application service, bounded local CLI, and one repository contract with
  SQLite and Firestore drivers.
- Implement the shared deterministic coordinator, lifecycle-aware scheduling, observation grouping,
  trigger evaluator, idempotent recording, cooldowns, and re-arm semantics.
- Add the authenticated internal polling endpoint without changing the assignment's public
  `POST /chat` request or response contract.
- Extend the repository Codex skill and add the explicit local foreground runner.
- Add bounded template alerts and optional agent investigation using the normal host validation,
  tool budgets, source notices, and provenance rules.
- Add replay fixtures that feed identical timestamped observations to both local and Cloud storage
  drivers and produce identical trigger decisions.
- Document provider polling limits, Firestore and model costs, local operation, Cloud Scheduler,
  authentication boundary, and shutdown behavior.

#### Verification Plan

- Compiler evals cover paraphrases, implicit thresholds, omitted time windows, contradictory rules,
  unsupported contract types, ambiguous teams, doubleheaders, and multi-platform requests.
- Contract tests prove invalid rules never persist and compilation never guesses a market ID,
  `game_ref`, outcome mapping, timezone, or polling interval.
- Replay tests cover threshold boundaries, quote staleness, out-of-order observations, delayed sports
  feeds, duplicate plays, cache hits, process restarts, missed intervals, final games, and market
  settlement lag.
- Token instrumentation proves inactive intervals and non-triggering polls make zero model calls;
  duplicate trigger fingerprints make zero additional explanation calls.
- Integration tests prove polling invokes the existing MCP tools, shares observations across
  compatible watches, avoids Tavily in the hot path, and remains useful when one source fails.
- Parity tests compile and validate representative watches through LangGraph and headless Codex,
  then run the same deterministic replay and compare normalized rule and trigger semantics.
- Cloud tests verify authenticated scheduler invocation, Firestore persistence across instance
  replacement, idempotent retries, bounded concurrency, and no cross-session watch mutation.

#### Exit Criteria

- A user creates, previews, confirms, lists, pauses, revises, and investigates a watch through the
  primary `/chat` workflow.
- The same representative watch works through local Codex and the foreground SQLite runner without
  changing its schema or trigger meaning.
- Active Cloud Run watches survive instance replacement and execute through authenticated scheduler
  calls using Firestore-backed state.
- Routine polling consumes no model tokens and no Tavily credits.
- Alerts identify the exact game, contract, before/after price, evaluation window, correlated game
  events, quote freshness, unavailable sources, and whether an explanation used a model.
- Historical replay produces deterministic, idempotent trigger results with measured false-positive
  and missed-trigger cases documented.
- The assignment's existing chat, memory, MCP, failure-handling, and deployment contracts continue
  to pass unchanged.

#### Current State

- **Status:** Complete locally; cloud watch acceptance pending Milestone 14A.
- LangGraph exposes validated preview, confirmation, lifecycle, inbox, and investigation flows.
  SQLite, the Firestore adapter, deterministic coordinator, authenticated scheduler endpoint,
  bounded CLI, foreground runner, and replay fixture share one versioned schema and evaluator.
- Controlled substitutes verify Firestore transactions, Google OIDC, Secret Manager, restart
  recovery, concurrent leases, zero-model polling, and zero-Tavily polling. Cloud Run, live
  Firestore, Cloud Scheduler, and Google-issued OIDC remain unverified until deployment.

#### Pivot Point

- Keep rule compilation and historical replay even if provider limits make live polling too slow.
- Reduce event vocabulary, cadence, retention, and automatic explanation before weakening identity,
  deduplication, provenance, or no-token polling.
- Do not replace the deterministic evaluator with an LLM loop.

### Milestone 16: External Watch Alerts

#### Product Boundary

- Deliver an already-created `WatchTrigger` outside the application without changing how the watch
  compiles, polls, or decides to fire.
- Use Telegram as the first delivery channel because one HTTPS bot request works from the local
  foreground runner and Cloud Run without a paid messaging provider.
- Keep the in-product inbox authoritative. Telegram is a delivery projection of the stored trigger,
  not the only copy of an alert.
- Send deterministic template alerts by default. External delivery never creates an automatic model
  call or Tavily request.
- Keep alerts informational and read-only. They contain no trading action, recommendation, or
  prefilled order workflow.

#### Decisions Locked by This Plan

- **Telegram first:** implement one `TelegramDelivery` adapter. Design a small delivery interface so
  Discord or web push can follow without changing watch or trigger models, but do not implement
  those channels in this milestone.
- **No new MCP server:** delivery is validated application I/O after deterministic trigger creation.
  The existing MCP tools remain the only sports, market, and research evidence sources.
- **Single configured recipient:** the first release sends to one deployment-owned Telegram chat.
  It does not accept arbitrary chat IDs from `/chat` or model output.
- **Secret boundary:** read `TELEGRAM_BOT_TOKEN` from the ignored local environment and Google Secret
  Manager on Cloud Run. Configure `TELEGRAM_CHAT_ID` outside source control. Never place either value
  in prompts, tool results, logs, watch rules, alert bodies, or exception messages.
- **Explicit opt-in:** a watch defaults to in-product delivery. The user enables Telegram delivery
  in the compiled preview or a later revision; missing Telegram configuration leaves the watch
  active with inbox-only delivery.
- **Outbox delivery:** commit the trigger and one idempotent delivery record before making the HTTPS
  request. A retry reuses the trigger fingerprint and cannot create a second logical message.
- **Bounded message:** send one plain-text message under 1,500 characters containing game identity,
  contract and platform, before/after price, window, correlated event, freshness warning, trigger
  time, and a short non-causation notice.
- **Controlled retries:** honor Telegram rate-limit retry metadata, retry timeouts and server errors
  with bounded backoff, and mark persistent client or authorization errors terminal and visible in
  the inbox.

#### Shared Local and Cloud Workflow

1. Milestone 15 creates and stores one deterministic `WatchTrigger` and outbox record.
2. The coordinator claims due outbox records with a lease so concurrent or retried poll requests do
   not deliver the same trigger twice.
3. `TelegramDelivery` formats the stored trigger without invoking a model and sends one HTTPS Bot
   API request.
4. The repository records `sent`, `retry_scheduled`, or `failed_terminal`, the attempt count, and a
   sanitized provider error classification.
5. Local SQLite and Cloud Firestore use the same outbox state machine. The local foreground runner
   reads credentials from `.env`; Cloud Run reads the bot token from Secret Manager.
6. `/chat` and the local watch CLI report delivery status from stored state. They never query
   Telegram merely to reconstruct whether an alert fired.

#### Work

- Add typed delivery policy and outbox models without changing the canonical trigger decision.
- Implement the delivery interface, Telegram formatter, bounded HTTPS client, timeouts, retries,
  rate-limit handling, and sanitized errors.
- Extend SQLite and Firestore repositories with atomic outbox creation, leasing, retry scheduling,
  and terminal status updates.
- Extend watch compilation and revision so Telegram is an allowlisted delivery choice that requires
  explicit confirmation and available deployment configuration.
- Add local environment placeholders and Cloud Run Secret Manager wiring without committing a bot
  token or chat ID.
- Add inbox and CLI views for delivery state, retry time, and terminal failure reason.
- Document bot setup, local configuration, Cloud configuration, revocation, cost boundaries, and
  how to disable external delivery without deleting a watch.

#### Verification Plan

- Unit tests cover exact formatting, length bounds, escaping, missing optional evidence, stale quote
  warnings, correlation wording, and secret redaction.
- Transport tests inject timeouts, disconnects, rate limits, server errors, malformed responses,
  authorization failures, and a recipient that blocks the bot.
- Repository replay tests prove retries and concurrent claims cannot send two logical alerts for one
  trigger fingerprint.
- Integration tests prove a Telegram-enabled watch and an inbox-only watch share the same trigger
  semantics and differ only at the delivery boundary.
- Token instrumentation proves successful, retried, and failed deliveries make zero model calls.
- Local tests run the SQLite foreground watcher against a fake Telegram endpoint. Cloud tests verify
  Secret Manager access, Firestore outbox recovery after instance replacement, and scheduled retry.
- One explicit live smoke test sends a uniquely identified test alert to the configured Telegram
  chat and remains excluded from the default suite.

#### Exit Criteria

- A confirmed watch can opt into Telegram from both LangGraph and local Codex workflows.
- One stored trigger produces at most one logical Telegram alert across retries, concurrency, and
  process restarts.
- The same alert remains available in the in-product inbox whether delivery succeeds or fails.
- Missing or invalid Telegram configuration never disables polling or trigger storage.
- Telegram messages contain sufficient evidence to understand why the deterministic rule fired and
  state that temporal alignment does not prove causation.
- External alert delivery consumes no model tokens and exposes no credentials or private destination
  identifiers in logs or agent context.

#### Current State

- **Status:** Complete locally; cloud watch acceptance pending Milestone 14A.
- The transactional SQLite/Firestore outbox, bounded Telegram adapter, retry classifier, inbox
  delivery status, Secret Manager boundary, fake transport end-to-end replay, and opt-in live smoke
  test are implemented. Fake delivery and live delivery to the configured chat are verified locally.

#### Pivot Point

- Keep the in-product inbox as the complete fallback when Telegram is unavailable.
- Disable external delivery before weakening idempotency, secret handling, or trigger evidence.
- Add another free channel only after Telegram delivery and recovery behavior are measured.

### Milestone 16A: Watch Activation Acknowledgements and Operational Visibility

#### Product Boundary

- Make the watch lifecycle observable without confusing a saved rule with a running monitor.
- Treat the primary product outcome as confidence: Rylan Wade and a grader can determine that a
  specific watch is actually collecting evidence, not merely present in a database.
- A confirmation response proves only that a validated rule is durable. A monitoring-started event
  proves that a runner claimed the due rule, collected and stored usable evidence, and established
  the baseline needed for at least one configured condition.
- Record watch lifecycle events in the authoritative in-product inbox and project them to Telegram
  only when that watch explicitly enables Telegram delivery.
- Expose concise runtime health through the local CLI and shared SQLite/Firestore repository
  contracts. Do not add a separate status service, notification script, or MCP server.
- Keep every lifecycle event and status projection independent of its initiating interface so a
  Milestone 16B can consume the same contracts without changing runner or notification
  behavior. Do not implement or revise the `/chat` watch tools in this milestone.
- Keep acknowledgement, status, and delivery deterministic. This milestone adds no model call,
  Tavily call, provider request, or polling interval beyond the work already required by the watch.

#### Product Experience

- **Trust, control, and simplicity are co-equal goals.** The product must say whether a watch is
  saved, waiting, monitoring, degraded, paused, or terminal; let the owner inspect and manage it;
  and avoid requiring an ordinary user to understand runners, polling, databases, Scheduler, or
  deployment.
- **This milestone is local-first.** Rylan Wade deliberately owns the foreground runner and uses
  CLI commands to create, inspect, and manage watches. Telegram provides outbound lifecycle and
  condition notifications. This produces direct sanity checks and grading evidence before any
  conversational endpoint depends on the behavior.
- **The later user experience begins and ends at `/chat`.** The eventual user creates, lists,
  inspects, pauses, resumes, revises, and deletes watches through conversation. Telegram remains an
  outbound notification channel, not a second management interface. That integration is explicitly
  outside this milestone and belongs to Milestone 16B.
- **Target cloud operation is automatic.** In the deployed product, authenticated Cloud Scheduler
  polling replaces the manually managed foreground runner. Final cloud integration must preserve
  the state transitions and messages proven locally.
- **One-minute polling sets the responsiveness expectation.** Timely delivery after each completed
  poll matters, but low-latency or real-time alerting is not a product claim. Do not sacrifice clear
  state, correctness, or delivery idempotency to imply sub-minute speed.

#### Decisions Locked by This Plan

- **One product promise applies to every control surface.** Creating a watch means the system has
  accepted responsibility for tracking its lifecycle and reporting whether that responsibility is
  currently fulfilled. The local CLI establishes that contract. Milestone 16B and Cloud Run work
  must reuse it rather than introduce weaker meanings or a separate state machine.
- **Creation and activation are separate moments.** The initial experience returns an immediate
  CLI receipt when the rule is saved. A later lifecycle event confirms that evidence-backed
  monitoring has started. Milestone 16B must preserve the same separation and must
  not compress those moments into one success claim unless activation has actually occurred.
- **Status is visible without reading infrastructure.** Telegram supplies outbound lifecycle and
  condition notices. The local operator obtains the same facts through bounded CLI list and inspect
  commands. Database inspection and log reading are diagnostic fallbacks, not product workflows.
- **The notification boundary is the first usable poll, not confirmation.** `watch_cli.py` persists
  the rule in an `awaiting_first_poll` runtime state. It must not send a
  monitoring-started Telegram message. The local foreground runner or authenticated Cloud
  Scheduler invocation creates that message only after the coordinator completes the first usable
  observation cycle for the due watch.
- **Process startup is not evidence of monitoring.** Printing `runner=started`, opening a database,
  or accepting an authenticated Scheduler request does not qualify. The coordinator must claim the
  exact rule, persist its observation, and determine condition readiness before it can announce
  that monitoring started.
- **The existing activation window remains authoritative.** A watch confirmed well before its
  activation window stays `awaiting_first_poll`; it does not send an early acknowledgement merely
  because a runner is open. The first eligible check remains fifteen minutes before scheduled
  start, with the existing five-minute pregame/delay and one-minute active cadence.
- **Usability is evaluated from typed condition requirements.** A price-move condition is usable
  when at least one pinned market has a nonterminal, non-stale price observation. Cross-platform
  divergence requires usable observations from both referenced platforms. Lifecycle conditions
  require an available sports-state observation. Scoring and no-tracked-scoring relationships
  require available sports state and play evidence in addition to the applicable price evidence.
- **Healthy, degraded, and unavailable starts remain distinct.** If every configured condition has
  its required evidence, runtime becomes `monitoring`. If at least one condition is usable while
  another configured source or condition is unavailable, runtime becomes `degraded` and the start
  event names the missing coverage. If no condition is usable, runtime becomes `awaiting_sources`,
  no success notification is created, and ordinary polling retries on the existing cadence.
- **Terminal evidence cannot create a false start.** A game that is already final or cancelled and
  whose pinned contracts are terminal moves through terminal handling without a
  monitoring-started notification. A terminal watch cannot be resumed or assigned a new activation
  epoch.
- **The baseline is not a movement trigger.** The first usable observation establishes the starting
  point for later comparisons. It can create a lifecycle acknowledgement, but it cannot by itself
  satisfy a price-move or divergence condition that requires earlier evidence.
- **Lifecycle events are first-class records, not fabricated triggers.** Add a typed watch event
  envelope with discriminated kinds for waiting, started, started-degraded, degraded, interrupted,
  recovered, resumed, updated, completed, and `condition_triggered`. Do not create fake price
  deltas, reserved condition IDs, or synthetic provider evidence merely to reuse `WatchTrigger`.
- **The inbox remains authoritative.** Persist the lifecycle event before external delivery and
  expose it beside condition alerts in chronological inbox results. Telegram remains a projection
  of the stored event. Missing or failed Telegram delivery cannot erase the event or change runtime
  state.
- **One activation epoch produces at most one start event.** Initial confirmation creates the first
  epoch. A successful resume creates a new epoch labeled `monitoring_resumed`; a confirmed material
  revision creates a new epoch labeled `monitoring_updated`. Runner restarts, delivery retries,
  concurrent claims, Cloud Run instance replacement, and repeated identical observations do not
  create another start event for the same epoch.
- **Activation state is durable and storage-neutral.** Store activation epoch, runtime state,
  first-success time, last-attempt time, last-success time, next due time, source readiness, and
  acknowledgement fingerprint in SQLite or Firestore through the shared repository interface.
  Process memory may cache these values but is never authoritative.
- **Event and outbox creation are atomic.** The transition to `monitoring` or `degraded`, insertion
  of the lifecycle event, and insertion of an opted-in Telegram outbox record occur in one
  repository transaction. A crash cannot leave a delivered start message without a durable event,
  or a durable monitoring state without its intended outbox record.
- **The existing delivery worker sends the event in the same cycle.** `run_watches.py` and the Cloud
  polling endpoint already run the delivery worker after coordinator polling. A newly created
  lifecycle outbox item is eligible immediately, uses the same leases and retry classifier as a
  condition alert, and makes zero model or Tavily calls.
- **Messages describe evidence and a code-owned execution origin.** A start message includes the
  exact matchup, watched outcomes and platforms, rule summary, effective cadence, source readiness,
  start time in the game's timezone, watch ID, and whether the poll came from the local foreground
  runner or authenticated Scheduler endpoint. The runner and endpoint pass that typed origin to
  the coordinator; the model and watch payload cannot choose it. A message names Cloud Run only
  when Cloud Run's injected service/revision metadata is present, so a locally invoked endpoint
  cannot impersonate deployed acceptance.
- **Runtime health is separate from desired rule status.** `WatchStatus.ACTIVE` continues to mean
  that the owner wants the rule enabled. Runtime state separately reports
  `awaiting_first_poll`, `awaiting_sources`, `monitoring`, `degraded`, or `terminal`. This prevents
  an active database row from being presented as proof that a runner is healthy.
- **Local management commands are returned at confirmation, not embedded in cloud Telegram.** A
  local CLI confirmation returns the exact runner, active-list, and inspect commands for its
  database and session. Telegram includes the watch ID but does not expose the session ID,
  filesystem path, machine-specific shell command, or instructions for a chat capability that has
  not been implemented. Milestone 16B may add provider-neutral management guidance.
- **Active-list output is operational, not a rule dump.** Add a status filter and concise projection
  containing watch ID, matchup, condition summary, desired status, runtime state, monitoring start,
  last attempt, last successful poll, next due time, source readiness, and delivery state. Full
  `inspect` remains available for the complete versioned rule.
- **The same semantics apply locally and in Cloud Run.** The local second command is
  `run_watches.py`; the cloud equivalent is the first authenticated Scheduler invocation that
  claims the due watch. Both paths call the same coordinator and repository transition. No cloud-
  only shortcut may announce monitoring before evidence collection.
- **Current data survives the schema change.** SQLite migration must preserve existing rules,
  observations, triggers, outbox state, and delivery history. Firestore readers accept records
  written before this milestone and initialize absent runtime fields conservatively as
  `awaiting_first_poll` without resending historical start notices unless the rule begins a new
  activation epoch.
- **Session and secret boundaries do not change.** Session IDs remain unauthenticated routing keys
  and never appear in Telegram messages. Tokens and recipient IDs remain outside rules, events,
  prompts, MCP results, logs, and command output.

#### Notification Behavior Contract

- Event creation is independent of delivery. Every asynchronous watch event is stored in the
  authoritative inbox before any Telegram attempt. Telegram opt-in controls projection, not whether
  the event exists or what state transition occurred.
- Command acknowledgements are synchronous receipts, not external notifications. Save, pause, and
  delete return a CLI receipt and do not send Telegram messages merely to echo the operator's own
  action.
- State-transition notifications are edge-triggered. Repeated polls in the same state update health
  timestamps but do not repeat a message. Recovery is sent only after a corresponding waiting,
  degraded, or interrupted episode.
- Each lifecycle event has one semantic payload and channel-specific rendering. Telegram uses a
  bounded plain-text renderer. CLI list/inspect use concise and full status projections. Later
  `/chat` work reads the same typed event and runtime fields; it does not reconstruct state from
  message text.

| Situation | Durable event and runtime effect | Telegram policy |
|---|---|---|
| Rule saved before any poll | No asynchronous lifecycle event; runtime is `awaiting_first_poll` | No message |
| First eligible poll has no usable condition | `monitoring_waiting_for_sources`; runtime is `awaiting_sources` | Send one warning for the waiting episode |
| First eligible poll has complete coverage | `monitoring_started`; runtime is `monitoring` | Send once for the activation epoch |
| First eligible poll has partial but usable coverage | `monitoring_started_degraded`; runtime is `degraded` | Send once and name missing coverage |
| A running watch loses some required coverage | `monitoring_degraded`; runtime is `degraded` | Send once for the degradation episode |
| A running watch loses all usable coverage | `monitoring_interrupted`; runtime is `awaiting_sources` | Send one operational warning |
| Coverage returns after waiting, degradation, or interruption | `monitoring_recovered`; runtime returns to `monitoring` or remains explicitly `degraded` | Send once and identify restored and still-missing coverage |
| A watch condition becomes true | Existing `condition_triggered` event and trigger evidence remain authoritative | Send the existing deterministic alert |
| Operator pauses a watch | Synchronous receipt; desired state becomes `paused`; no future polls are due | No echo message |
| Operator resumes a watch | Synchronous saved receipt; new activation epoch waits for usable evidence | Send `monitoring_resumed` only after a usable poll |
| Operator materially revises a watch | Synchronous saved receipt; new activation epoch waits for usable evidence | Send `monitoring_updated` only after a usable poll |
| Game and contracts reach a supported terminal state | `monitoring_completed`; runtime is `terminal` and no more polls are due | Send one completion message with the stop reason |
| Telegram delivery fails | Delivery record becomes retryable or terminal without changing watch runtime | Retry by policy; terminal failure remains visible in inbox and CLI because Telegram cannot report its own failure |
| Operator deletes a watch | Synchronous receipt; cancel unclaimed deliveries and apply retention policy | No echo message |

- Waiting, degraded, interrupted, recovered, resumed, updated, completed, and condition messages use
  distinct event kinds and stable fingerprints. They are never encoded as fake price triggers.
- A notification contains the watch ID, matchup, watched outcome and platform, concise rule summary,
  runtime state, source readiness, event time, effective cadence, and a plain statement of what will
  happen next. Condition alerts retain their before/after evidence and non-causation notice.
- Messages do not contain session IDs, database paths, shell commands, Telegram identifiers, secret
  values, provider payloads, or deployment claims that the execution origin cannot prove.

#### Deferred Interaction Trial

- The local acceptance baseline is a saved CLI receipt followed by a Telegram monitoring-started
  message after the first usable poll. It proves the path from durable rule through runner,
  evidence, lifecycle event, outbox, and Telegram.
- Whether the Milestone 16B `/chat` experience also projects activation into conversation history
  is an interaction decision that requires a working chat prototype. Defer that comparison instead
  of building endpoint behavior here.
- The Milestone 16B trial may change channel presentation. It must not change the durable event vocabulary,
  runtime states, notification timing, on-demand status data, or exactly-once semantics defined in
  this milestone.

#### Lifecycle Event Contract

- Introduce a versioned `WatchLifecycleEvent` containing:
  - event ID, event kind, watch ID, session scope, activation epoch, and stable fingerprint;
  - exact game identity and bounded market/outcome labels;
  - deterministic condition summary and effective polling cadence;
  - runtime state and source-readiness entries with sanitized warnings;
  - persisted observation IDs that established readiness;
  - event time and game-local display time;
  - one deterministic message under the existing 1,500-character Telegram ceiling.
- Generalize inbox and outbox records around a typed watch-event envelope while preserving the
  complete `WatchTrigger` evidence contract for condition alerts. Existing trigger IDs and
  fingerprints remain valid; migration does not relabel old price alerts as lifecycle events.
- Use a fingerprint derived from watch ID, activation epoch, lifecycle-event kind, and baseline
  observation identity. Do not use timestamps alone as idempotency keys.
- Retain lifecycle events with alert history for 30 days. Keep the current rule and latest runtime
  state until deletion so active-list and inspect remain useful after event retention cleanup.

#### Local and Cloud Workflow

1. Preview and explicit confirmation persist the rule and activation epoch with runtime
   `awaiting_first_poll`.
2. The confirmation response says that the watch is saved but monitoring has not yet been proven.
   Local CLI output includes exact commands to start the runner, list active watches, and inspect
   the new watch.
3. A local runner or authenticated Scheduler call claims the watch when it is due and collects the
   existing bounded MCP evidence.
4. The coordinator persists the observation and calculates typed readiness for each condition.
5. No usable condition leaves runtime at `awaiting_sources` and creates one waiting event for that
   episode. At least one usable condition creates a healthy or degraded lifecycle event exactly
   once for the activation epoch.
6. The repository atomically records runtime state, the inbox event, its evidence links, and an
   opted-in Telegram outbox record.
7. The existing delivery worker attempts the Telegram projection in the same runner or Scheduler
   cycle and records sent, retry, or terminal failure without changing the inbox event.
8. Later polls update last-attempt, last-success, next-due, and source-readiness fields without
   repeating the current state event. A real state transition creates the corresponding edge event;
   condition triggers continue through their existing evaluator.

#### Local CLI Surface and Future Interface Contract

- Extend `watch_cli.py --operation list` with
  `--status active|paused|terminal|all`, defaulting to `all` for backward compatibility.
- Return a concise runtime projection by default and retain a typed full `inspect` operation.
- After local confirmation, return commands in this shape with values safely quoted from the
  invocation rather than model-generated:

  ```powershell
  uv run --env-file .env python scripts/run_watches.py --db <database>
  uv run --env-file .env python scripts/watch_cli.py --db <database> --operation list --session-id <session> --status active
  uv run --env-file .env python scripts/watch_cli.py --db <database> --operation inspect --session-id <session> --watch-id <watch>
  ```

- Do not change LangGraph watch tools, `/chat` request handling, or conversation copy in this
  milestone. Define `WatchRuntimeSummary` and event-query results as typed application contracts so
  Milestone 16B can expose them without importing CLI formatting or parsing Telegram
  text.
- Milestone 16B must support natural-language list and inspect requests from the
  same session, but that routing and response behavior require separate endpoint acceptance tests.
- Telegram messages remain outbound and provide the watch ID and concise state. Local evaluator
  receipts contain the commands needed to manage that watch; Telegram never contains machine-local
  paths or shell commands.
- Foreground runner output adds bounded counts for lifecycle events and awaiting/degraded watches;
  it does not print session IDs, Telegram destinations, or full provider data.

#### Work

- Add typed runtime-state, source-readiness, lifecycle-event, generalized inbox, and generalized
  outbox models with an explicit schema-migration policy.
- Extend the repository interface and both storage adapters with activation-epoch management,
  runtime updates, atomic lifecycle event/outbox insertion, event retrieval, and status-filtered
  watch summaries.
- Add a forward-only SQLite migration that preserves the current developer database and a
  backward-compatible Firestore read path for documents lacking the new fields.
- Add condition-aware readiness evaluation beside coordinator scheduling. Reuse validated
  `WatchObservation` fields; do not interpret provider prose or duplicate evaluator arithmetic.
- Update confirmation, revision, pause, resume, terminal, and deletion flows to maintain activation
  epochs and runtime state consistently.
- Generalize `DeliveryWorker` lookup from trigger-only messages to the typed event envelope while
  preserving retry, lease, error, and provider-message behavior.
- Add concise CLI status filtering and confirmation receipts. Add a typed, interface-neutral runtime
  summary that Milestone 16B LangGraph watch tools can consume without changing this milestone's endpoint
  code.
- Add deterministic waiting/start/degradation/interruption/recovery/resume/update/completion message
  formatting and document exactly which evidence established each transition.
- Defer chat-specific message-policy comparison and conversation copy to Milestone 16B, where a
  working endpoint can exercise the interaction. This milestone freezes event semantics, not
  future chat presentation.
- Revise the local runner, Cloud endpoint result counters, product documentation, architecture
  diagrams, test map, and repository skill to present one coherent watch lifecycle.

#### Milestone 14A Boundary

- Keep lifecycle, repository, outbox, Scheduler-handler, and delivery contracts portable to Cloud
  Run, but do not configure or claim deployed watch operation in this milestone.
- Local acceptance uses the real configured Telegram recipient to prove end-to-end delivery.
  Firestore, Cloud endpoint, and delivery behavior use controlled substitutes and parity tests.
- Milestone 14A exclusively owns production Firestore configuration, authenticated Cloud Scheduler,
  Cloud Run secrets and environment, the real deployed Telegram destination, and live end-to-end
  watch acceptance.

#### Documentation Alignment

- Rewrite documentation for behavior that actually exists in present tense. Revise the existing
  watch narrative in place; do not append a feature announcement, changelog, migration story, or
  separate "activation acknowledgement" add-on section.
- Remove temporal backpointers from final-state documentation, including phrases such as "new,"
  "now," "previously," "was changed," "after Milestone 16A," "this follow-up adds," and "the old
  behavior." State the product contract directly: confirmation saves a watch, runtime state shows
  whether evidence collection operates, and the first usable poll records and delivers a lifecycle
  event.
- Present watches as an integrated Market Lens workflow rather than an optional layer attached to
  the product. Assignment-mapping text may distinguish rubric requirements from additional product
  scope, but product, setup, architecture, and operator documentation describe one system.
- Keep current instructions accurate: foreground-runner and direct CLI commands form the local
  evaluation/operator path. Do not document conversational watch management as implemented during
  this milestone.
- Preserve a surface-neutral product explanation and schema contract so Milestone 16B
  can rewrite the final journey as chat-first and automatic without historical backpointers or
  describing lifecycle visibility as an add-on.
- Update `README.md` in place:
  - introduce saved, awaiting, monitoring, degraded, paused, and terminal states in the main product
    workflow;
  - make local confirmation, first-poll acknowledgement, condition alerts, inbox, CLI status, and
    Telegram one continuous evaluator journey;
  - document the exact local confirm, runner, active-list, inspect, pause, resume, and inbox
    commands;
  - explain that the runner command proves only process startup until a usable poll records runtime
    health;
  - update the system and deployment diagrams with durable runtime state, lifecycle events, the
    generalized inbox/outbox, local foreground polling, authenticated Scheduler polling, and
    same-cycle Telegram delivery;
  - label `/chat` watch management and automatic Cloud operation as later integration work rather
    than claiming either has passed acceptance;
  - include cost and failure semantics for lifecycle events without implying extra model, Tavily,
    or provider calls;
  - state cloud behavior only from deployed acceptance evidence.
- Update `docs/WATCHES_EXPLAINED.md` in place:
  - make the save-versus-monitor distinction part of the basic explanation before runner details;
  - show the confirmation receipt, first usable poll, start message, active-list command, ordinary
    trigger, and terminal transition in one end-to-end example;
  - explain healthy, degraded, and awaiting-source states in plain language;
  - distinguish a runner heartbeat from evidence-backed monitoring without introducing milestone
    terminology.
- Update `docs/research/WATCH_MONITORING_AND_ALERTS.md` as the canonical technical contract:
  - define runtime-state, source-readiness, activation-epoch, lifecycle-event, generalized inbox,
    and generalized outbox schemas;
  - specify atomicity, fingerprints, delivery leases, retention, SQLite/Firestore parity, typed
    execution origin, Cloud Run metadata, and migration safety;
  - document exact readiness rules for every condition, the no-success-message boundary during
    total evidence failure, and the single waiting warning for that episode;
  - include the final local and Cloud sequence diagrams.
- Update `docs/planning/PROJECT_PROPOSAL.md` so the target-product and workflow sections treat
  lifecycle visibility as a normal part of creating and operating a watch. Distinguish verified
  local behavior from planned chat and cloud integration without presenting the feature as an
  optional add-on.
- Update `docs/assignment/IMPLEMENTATION_ALIGNMENT.md` so repository deliverables and submission
  checks name runtime health, lifecycle inbox events, and Scheduler-delivered acknowledgement where
  relevant. Keep the assignment's required core contract distinct without calling the product
  behavior an add-on.
- Update `docs/research/TESTING.md` and
  `docs/research/ADVERSARIAL_TESTING_REPORT.md` with the verified readiness, idempotency, migration,
  local/Cloud parity, and delivery failure cases. Record only commands and results that were
  actually observed.
- Update `.agents/skills/sports-information/SKILL.md` so its local create, confirm, list, inspect,
  resume, and inbox workflows use runtime health correctly. This local Codex workflow does not imply
  that the deployed `/chat` endpoint already supports the same operations.
- Update CLI `--help` text and command examples from the same canonical command shapes returned by
  confirmation. Keep session IDs out of Telegram, logs, screenshots, and generic documentation
  examples.
- Update documentation regression tests to reject stale add-on framing, temporal backpointers,
  trigger-only inbox/outbox claims, confirmation-as-monitoring claims, and diagrams that omit
  runtime state or the first-poll boundary.
- Leave `PROCESS_LOG.md` to Rylan Wade's personal authorship. Documentation work records technical
  behavior and verification without inventing personal prompts, lessons, or reflection.

#### Verification Plan

- Confirmation tests prove that persistence alone creates no lifecycle event, outbox item, or
  Telegram attempt and returns an `awaiting_first_poll` receipt.
- Coordinator tests cover every condition's readiness requirements, healthy and degraded starts,
  initial source waiting, later degradation, total interruption, recovery, terminal-first evidence,
  baseline non-triggering behavior, and supported completion.
- Idempotency tests run repeated polls, two concurrent runners, lease expiry, process restart, and
  Cloud Run instance replacement against one activation epoch and produce one logical event per
  state-transition episode.
- Resume and revision tests produce one correctly labeled new event after usable evidence while
  pause, repeated resume, and terminal watches cannot create spurious epochs.
- SQLite migration tests start from a copy of the pre-milestone schema with rules, observations,
  triggers, and sent/retry outbox records and prove that every record remains readable and retains
  its meaning.
- SQLite/Firestore parity tests compare runtime transitions, atomic event/outbox writes, event
  ordering, retention, delivery state, and filtered active-list projections.
- Delivery tests cover inbox-only watches, Telegram success, missing configuration, timeout,
  disconnect, `429`, `5xx`, malformed response, authorization failure, blocked recipient, retry
  exhaustion, and secret redaction for lifecycle messages.
- CLI tests execute the returned runner/list/inspect command shapes against paths containing spaces,
  verify `--status` filtering, and ensure concise output does not expose session IDs outside the
  explicit local command receipt.
- Interface-boundary tests prove runtime summaries and lifecycle event queries contain no CLI-only
  formatting and that this milestone does not change `/chat` schemas, routing, or watch-tool
  behavior.
- Cloud endpoint tests prove an authenticated no-due invocation sends nothing, a first usable due
  invocation creates and attempts one message through a controlled delivery adapter, and a retried
  Scheduler request does not duplicate it.
- Replay and instrumentation tests continue to prove zero model calls and zero Tavily calls for
  acknowledgement, ordinary polling, retries, and status inspection.
- One opt-in live run confirms a newly activated watch produces one real Telegram start message,
  appears in the active-list command, survives runner restart without another message, and later
  produces ordinary alerts without changing their semantics. This live milestone run is local.
- Milestone 14A separately repeats the activation path through the deployed Scheduler and real
  Telegram configuration before the project claims cloud delivery.

#### Exit Criteria

- Local confirmation clearly distinguishes saved configuration from verified monitoring and
  returns the appropriate runner, list, and inspect commands.
- The first usable local or Cloud poll creates one durable healthy/degraded lifecycle event and, for
  a Telegram-enabled watch, one logical external message.
- No start message is created by confirmation, runner startup, an idle poll, total source failure,
  terminal-first evidence, retry, restart, concurrency, or instance replacement.
- Active-list and inspect show desired status, actual runtime state, source readiness, last success,
  next due time, and delivery state from durable storage without requiring `/chat` changes.
- Existing local data upgrades without destructive reset, and SQLite and Firestore satisfy the
  same lifecycle and idempotency contract.
- Condition evaluation, alert formatting, inbox authority, Telegram retry behavior, zero-model
  polling, and the public assignment `/chat` contract remain unchanged.
- Local milestone completion requires quality gates, deterministic replay, controlled delivery
  tests, one opt-in live local Telegram activation, and clear CLI status evidence.
- Milestone 14A additionally requires automatic Cloud Scheduler operation, real Cloud Run Telegram
  configuration, and deployed end-to-end acceptance. Until then, documentation must describe
  Telegram-on-Cloud-Run setup as pending final deployment work.

#### Current State

- **Status:** Complete locally. SQLite and the controlled Firestore adapter persist runtime
  summaries, lifecycle events, and opted-in outbox records through the shared repository contract.
- Confirmation returns `awaiting_first_poll` and the exact local runner, active-list, and inspect
  commands. A first usable poll creates the start event; waiting, degradation, interruption,
  recovery, resume, update, and completion use deterministic state transitions.
- The local first-poll activation path delivered a real Telegram notice using recorded game
  evidence. Firestore, the Scheduler endpoint, and Cloud Run remain under controlled or pending
  cloud acceptance; Milestone 14A owns the deployed end-to-end proof.

#### Pivot Point

- If generalizing the inbox/outbox would risk current trigger history, add a backward-compatible
  typed lifecycle-event store and shared delivery lookup before attempting a destructive table
  rename. Do not encode lifecycle acknowledgements as fake price triggers.
- If a source cannot establish complete readiness, send an explicitly degraded acknowledgement
  only when at least one configured condition is genuinely evaluable. Never improve apparent UX by
  claiming healthy monitoring during total evidence failure.
- Reduce message detail or CLI presentation before weakening durable idempotency, inbox authority,
  source readiness, session secrecy, or local/cloud semantic parity.

### Milestone 16B: Conversational Watch Operations and Agent Acceptance

#### Execution Gate

- Start after Milestone 16A has accepted runtime states, lifecycle events, interface-neutral
  summaries, inbox behavior, and local Telegram notifications.
- Complete locally against SQLite and the foreground runner. Do not require Firestore, Cloud
  Scheduler, deployed Telegram configuration, or a live Cloud Run service; those belong to
  Milestone 14A.

#### Product Boundary

- Make the complete watch lifecycle usable through the existing `POST /chat` agent without making
  users understand CLI commands, runner processes, database paths, polling leases, or outbox state.
- Cover create, preview, confirm, cancel, list, filter, inspect, revise, pause, resume, delete, inbox,
  delivery status, lifecycle explanation, and trigger investigation through natural language.
- Use the typed watch application service, `WatchRuntimeSummary`, and lifecycle-event queries from
  Milestones 15-16A. The agent is a conversational control surface over those contracts; it is not
  a second repository, scheduler, evaluator, formatter, or delivery worker.
- Keep asynchronous behavior honest. `/chat` confirms synchronous commands and reads durable state;
  it does not remain open waiting for a poll and cannot claim monitoring before the runner records
  usable evidence.
- Keep Telegram outbound-only in this milestone; Milestone 16C separately adds read-only status and
  help commands. The agent can enable or disable the allowlisted delivery policy for a watch, but
  it never reads Telegram messages, accepts chat IDs, exposes destination details, or treats
  Telegram delivery as proof that monitoring is healthy.

#### Decisions Locked by This Plan

- **Host functions are authoritative:** expose a small set of typed LangGraph tools backed by the
  existing watch application service. Tools accept semantic watch inputs and identifiers, not SQL,
  filesystem paths, provider payloads, session IDs chosen by the model, or preformatted messages.
- **The request owns the session:** derive watch ownership from the validated `/chat` request and
  inject it into host calls. Ignore and reject any model-generated attempt to select another
  session. Session IDs remain routing keys, not authentication.
- **Two-phase destructive intent:** create, material revision, and delete require a bounded preview
  followed by explicit confirmation in the same session. Pause and resume may execute from an
  explicit command because they are reversible, but vague language produces a confirmation prompt
  instead of mutation.
- **Confirmation is version-bound:** a confirmation references the exact preview fingerprint and
  rule version. Replayed, stale, cross-session, already-consumed, or text-only confirmations cannot
  create or mutate a watch.
- **The agent never compiles executable prose:** the model proposes typed intent; host validation,
  exact game/contract identity, allowlisted conditions, cadence policy, versioning, and semantic
  checks decide whether a rule is valid.
- **Saved is not monitoring:** creation, revision, and resume responses say `saved` and
  `awaiting_first_poll`. Only a durable lifecycle event permits `monitoring`, `degraded`,
  `awaiting_sources`, or `terminal` language.
- **Status reads do not poll:** list, inspect, inbox, and delivery-status requests read durable state
  and display last-attempt, last-success, next-due, source readiness, and observation time. They do
  not trigger provider calls merely to make status look fresher.
- **Natural references are memory-assisted, not guessed:** same-session phrases such as "that
  Yankees watch," "the second one," or "pause it" may resolve from checkpointed watch summaries.
  Multiple plausible matches require a short choice; the agent never selects by hidden list order.
- **Conversation memory is not watch storage:** checkpoints may retain pending previews and recent
  references, but durable watch state, ownership, versions, events, and deliveries come from the
  repository. Lost conversation memory cannot delete or silently recreate a watch.
- **Read paths are bounded:** list results use concise summaries, default to active watches, support
  explicit status filters and pagination, and avoid loading full rules or event history until the
  user selects one watch.
- **Inbox semantics stay unified:** condition triggers and lifecycle events appear in chronological
  order with event kind, watch ID, matchup, runtime state, evidence time, and delivery state. The
  agent never fabricates an alert from logs or Telegram history.
- **Investigations are explicit and bounded:** only a user request about a stored trigger may invoke
  fresh MCP evidence or Tavily. Runtime status, lifecycle explanation, ordinary inbox reads, and
  delivery troubleshooting make zero model-selected provider refreshes beyond the current chat
  turn's synthesis.
- **Tool failure is not watch failure:** a malformed agent tool call, unavailable repository, or
  formatter error returns a controlled response and does not mutate the rule. The agent distinguishes
  application-command failure from a stored watch runtime problem.
- **No hidden retries for mutations:** transport retries reuse idempotency keys and return the
  existing result. The agent does not silently issue a second create, resume, revision, or delete
  after an uncertain response.
- **Notification presentation remains channel-correct:** `/chat` returns command receipts and
  on-demand inbox/status views; Telegram pushes opted-in asynchronous lifecycle and condition
  events. The HTTP response never promises a future push through the already-completed chat turn.
- **Ordinary agent behavior remains intact:** watch routing must not hijack normal sports questions,
  game briefs, market comparisons, research, no-tool knowledge, or unrelated conversation.

#### Conversational Operation Contract

| User intent | Agent action | Required result |
|---|---|---|
| "Watch the Yankees price and alert me if it moves 4%" | Resolve exact game and contract, compile, validate, and preview | No persistence; show rule, cadence, delivery choice, ambiguity, and confirmation request |
| "Confirm" | Consume the matching preview fingerprint | Persist once; return watch ID, `saved`, and `awaiting_first_poll` |
| "Cancel that" before confirmation | Discard the pending preview | No rule or event is created |
| "What are you watching?" | List session-owned summaries | Show active watches first with runtime state, freshness, next due time, and concise condition |
| "Is the Yankees watch running?" | Resolve and inspect one watch | Explain desired status separately from observed runtime health and delivery status |
| "Change it to 6%" | Build a versioned revision preview | Preserve the current rule until explicit confirmation |
| "Pause that watch" | Resolve one watch and pause idempotently | Return paused receipt; no future poll is due |
| "Resume it" | Create one new activation epoch | Return saved/awaiting receipt; monitoring is announced only after usable evidence |
| "Delete it" | Preview the destructive target and consequences | Delete only after exact confirmation; report retention/cancel behavior |
| "Show my alerts" | Query the unified inbox | Return bounded lifecycle and condition events in chronological order |
| "Why did this alert fire?" | Load stored trigger evidence; optionally run bounded investigation | Separate deterministic trigger facts, refreshed evidence, uncertainty, and model synthesis |
| "Did Telegram send it?" | Read delivery state | Report sent, retry scheduled, or terminal failure without querying Telegram as authority |
| Ambiguous watch reference | Return bounded candidates | Make no mutation until the user selects one |
| Repository or tool unavailable | Return a controlled incomplete result | Make no unsupported claim about saved state, runtime health, or delivery |

#### Agent Workflow

1. Classify the turn as ordinary information, watch command, watch status, inbox, or trigger
   investigation without forcing watch tools on unrelated prompts.
2. Resolve remembered context only within the request session, then use bounded list/inspect calls
   when a natural watch reference needs grounding.
3. For creation or revision, resolve exact game and market identities through existing MCP tools,
   construct typed intent, and let the host validator produce either stable errors or a preview.
4. Store only the bounded preview fingerprint and display data in conversation state. Persist
   nothing until exact confirmation.
5. Execute a confirmed application command with an idempotency key derived outside model prose and
   render the typed result using state-specific language.
6. For reads, retrieve the minimum durable summary, event page, rule version, or trigger evidence
   required by the question and preserve provider timestamps and missing-source warnings.
7. Return a concise direct answer. Include next actions only when they are actually available on the
   current surface; never return local shell commands from `/chat`.

#### Work

- Define bounded LangGraph tool schemas and result types for preview, confirm, cancel-preview, list,
  inspect, revise-preview, pause, resume, delete-preview, delete-confirm, inbox, event inspection,
  delivery status, and trigger investigation.
- Route every tool through the shared watch application service and request-derived session context.
  Add no direct repository, Telegram, provider-client, or coordinator access to model-callable code.
- Extend graph state with versioned pending actions, consumed confirmation fingerprints, selected
  watch references, pagination cursors, and bounded recent summaries using the framework
  checkpointer rather than a custom global dictionary.
- Add deterministic result formatters for saved, awaiting, monitoring, degraded, paused, terminal,
  waiting-for-sources, delivery failure, ambiguity, validation failure, stale confirmation, and
  repository failure responses.
- Update the routing prompt and tool descriptions with explicit negative cases so ordinary sports
  questions and "watch" used as a verb outside monitoring do not enter mutation flows.
- Add host-side authorization checks, version checks, idempotency keys, pagination limits, event
  bounds, investigation budgets, and secret-field exclusion independent of model behavior.
- Integrate lifecycle events into inbox and status responses without changing the event store or
  notification policy accepted in Milestone 16A.
- Prototype whether unread lifecycle events should be summarized on the next watch-related turn or
  shown only when requested. Select one behavior from observed interaction tests; do not attempt an
  asynchronous push through `/chat`.
- Preserve existing public request and response schemas, ordinary sports-agent routing, MCP budgets,
  memory behavior, and failure handling.

#### Documentation Alignment

- Rewrite the README and watch explanation as one conversational workflow: describe, preview,
  confirm, await monitoring, receive Telegram events, ask status, manage the watch, and investigate
  an alert.
- Document natural-language examples for every operation and state without exposing internal tool
  names, CLI commands, session IDs, database paths, or milestone history in the product narrative.
- Keep a separate local evaluator section for the foreground runner and CLI; explain that these test
  the same application contracts but are not required by the eventual user.
- Update architecture and sequence diagrams so LangGraph performs intent and presentation, host
  tools enforce commands, the repository owns truth, the runner owns polling, and Telegram projects
  stored events.
- Update the repository skill and testing docs with the accepted behavior while clearly separating
  direct Codex verification from the application `/chat` path.
- Leave cloud automation described as pending Milestone 14A until deployed acceptance exists. The
  final Milestone 14A documentation rewrite presents the accepted cloud system without temporal
  backpointers or add-on framing.

#### Verification Plan

- Scripted-model graph tests assert exact tool selection, argument shapes, call order, result
  rendering, and non-use of watch tools for ordinary prompts.
- Real-model evals cover paraphrases and multi-turn flows for every operation in the conversational
  contract, including vague confirmations, corrections, cancellations, pronouns, multiple watches,
  stale previews, and a lost checkpoint.
- Mutation tests prove create, revision, resume, pause, and delete are session-scoped, version-bound,
  idempotent, and safe under repeated HTTP requests and final-model failure after tool success.
- State-language tests cover every runtime and delivery state and reject responses that equate
  `active` with monitoring, runner startup with monitoring, Telegram success with monitoring, or a
  stale timestamp with current health.
- Memory tests prove same-session preview confirmation and natural references work while
  cross-session attempts cannot view, confirm, mutate, or infer another session's watches.
- Inbox tests mix waiting, start, degradation, recovery, trigger, completion, and delivery-failure
  events; verify pagination, ordering, concise summaries, and exact drill-down evidence.
- Investigation tests prove only stored triggers unlock bounded refresh/research, preserve original
  evidence, mark later observations separately, and never rewrite the trigger explanation.
- Failure tests inject repository unavailability, malformed typed results, MCP failures during
  compilation, model failure before and after mutation, and Telegram delivery failure without
  duplicate commands or unsupported success claims.
- Prompt-injection tests place hostile text in provider fields, event labels, and tool errors and
  prove it remains quoted data that cannot choose tools, sessions, watch IDs, destinations, or
  mutation arguments.
- Regression tests rerun the existing no-tool, sports-only, market-only, multi-MCP, research,
  memory, budget, and public-schema suites with watch tools enabled.
- One local end-to-end run creates a real watch through `/chat`, confirms it, starts the separately
  managed foreground runner, receives the Telegram activation event, reads status and inbox through
  `/chat`, revises and resumes it, then pauses and deletes it without using the CLI for management.

#### Exit Criteria

- Every operation in the conversational contract works through the local `/chat` endpoint with no
  CLI command required for user management.
- Agent language always distinguishes accepted configuration, desired status, observed runtime
  health, event evidence, and delivery result.
- The agent consumes the exact Milestone 16A summaries and events without duplicating runtime logic,
  parsing presentation text, or creating a second notification policy.
- Confirmation, ownership, versioning, idempotency, ambiguity, memory isolation, pagination, and
  failure behavior pass adversarial tests.
- Ordinary sports-information and assignment-required agent behavior remain unchanged with watch
  tools enabled.
- The local end-to-end run proves `/chat` management, external foreground polling, inbox authority,
  and real Telegram delivery as one coherent workflow.
- Milestone 14A can deploy these accepted contracts without designing new agent or watch behavior.

#### Current State

- **Status:** Planned after Milestone 16A.
- Milestone 15 already provides basic LangGraph preview, confirmation, lifecycle, inbox, and
  investigation flows. Those flows do not satisfy this milestone until they consume the complete
  runtime-health, lifecycle-event, notification, and interface-boundary contracts from Milestone
  16A and pass the expanded operation matrix.

#### Pivot Point

- Reduce conversational shortcuts or response detail before weakening host validation, explicit
  confirmation, session isolation, durable-state authority, or truthful runtime language.
- Keep a complex operation CLI-only until its typed host contract is safe; do not let the model
  bypass the application service to achieve surface completeness.
- Defer proactive unread-event presentation if it creates noise. Preserve the inbox and on-demand
  status path rather than coupling asynchronous delivery to an HTTP response.

### Milestone 16C: Inbound Telegram Watch Status and Help

#### Execution Gate

- Start after Milestone 16B accepts the shared watch-status wording and the local `/chat` operation
  contract. Complete the local command path before Milestone 14A configures its cloud webhook.
- This milestone does not depend on a live Cloud Run service. Milestone 14A owns deployment and
  real webhook acceptance against Firestore.

#### Product Boundary

- Let the existing Telegram bot answer questions about saved watches in the same chat where it
  sends opted-in lifecycle and condition alerts. Provide `/help` and read-only `/status` commands.
- Keep `/chat` and the local CLI as the places to create, revise, pause, resume, and delete watches.
  Telegram does not become a general agent conversation or watch-management surface.
- Reuse `WatchService` and its typed `WatchRuntimeSummary` for status. Do not add a second status
  service, repository, bot, hosted app, model call, MCP request, or provider refresh.
- Local operation uses the existing `scripts/run_watches.py` foreground process, existing SQLite
  database, and existing `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID` configuration. No local HTTP
  server, public URL, tunnel, or additional always-running process is required.

#### Telegram Command Contract

| Message | Reply |
|---|---|
| `/help` | Short explanation of `/status`, `/status <watch_id>`, status meanings, and where watch changes are made. |
| `/start` | The same help text; no registration or mutation. |
| `/status` | Bounded list of watches in the configured session, active first, with watch ID, matchup, concise condition, desired state, runtime state, last successful check, and next due time. |
| `/status <watch_id>` | One watch's stored runtime summary, including source readiness, last attempt/success, observation time, next due time, and latest delivery state when present. |
| Unknown command or invalid watch ID | Brief usage guidance without revealing another watch or session. |

- Say `saved` or `awaiting_first_poll` until a usable poll records monitoring evidence. Distinguish
  `monitoring`, `degraded`, `awaiting_sources`, `paused`, and `terminal`; show timestamps and stale
  evidence plainly. A running process or successful Telegram send is not proof of healthy monitoring.
- `/status` reads persisted data only. It does not poll a game, call an MCP server, ask the model,
  revise a watch, or wait for a future update. Keep replies short enough for one Telegram message;
  limit list size and explain how to inspect a specific watch when there are more results.
- Keep session IDs, database paths, bot credentials, Telegram identifiers, and raw provider payloads
  out of replies. The watch ID in existing alerts is the handle for `/status <watch_id>`.

#### Decisions Locked by This Plan

- **One trusted chat:** accept commands only from the already configured `TELEGRAM_CHAT_ID`, in a
  private chat with the bot. Ignore all other chats, groups, forwarded commands, unsupported update
  types, and non-text messages without reading watch data. Do not interpret message text as an
  authorization credential.
- **One explicit session binding:** configure the watch session associated with that chat outside
  source control, for example as `TELEGRAM_WATCH_SESSION_ID` in the ignored local `.env`. Use that
  binding for all `WatchService` reads; never accept a session ID from the Telegram message. Watches
  created under another session remain outside this bot's status view. Session IDs are routing keys,
  not authentication; the chat allowlist is the access boundary for this single-user scope.
- **One local runner:** add a bounded `getUpdates` loop alongside the existing once-per-minute
  coordinator and outbound delivery loop in `run_watches.py`. Use the same bot token and database;
  keep exactly one runner consuming that bot's updates. The two loops must stop cleanly together.
- **No local webhook:** Telegram long polling is an outbound HTTPS connection from the local
  runner. A configured Telegram webhook prevents `getUpdates` from receiving updates, so local
  startup must detect or clearly report that conflict. When Cloud Run later enables its webhook,
  stop the local inbound loop for that bot.
- **Bounded and independent:** apply short request deadlines, backoff, message-length limits, and
  concise sanitized errors. Telegram receive/reply failures must not stop watch evaluation or
  outbound alert delivery; watch-source failures must not block stored status replies.
- **Read-only duplicate handling:** track Telegram `update_id` progress across local restarts so
  ordinary redelivery does not flood replies. A reply with an uncertain Telegram send result may
  be repeated; do not claim exactly-once message delivery. Never create a watch event or mutate a
  rule in response to an inbound command.
- **One future cloud service:** Milestone 14A replaces local `getUpdates` with a Telegram webhook
  route on the already deployed Cloud Run agent. The route verifies Telegram's configured secret
  header and the allowed chat, then calls the same deterministic command handler and Firestore-
  backed service. It is an inbound route, not a second hosted interface for users.

#### Local Workflow

1. Configure the existing bot token and allowed chat ID plus the bound watch session in the
   ignored `.env`. Create watches under that same session through the CLI or local `/chat` flow.
2. Start one `uv run --env-file .env python scripts/run_watches.py --db artifacts/watches.db`
   process. It checks due watches, delivers queued alerts, and receives Telegram commands while
   the terminal and computer remain running.
3. Send `/help` or `/status` to the existing bot. The command handler validates the chat and
   parses the allowlisted command without invoking LangGraph or the polling coordinator.
4. Read the SQLite-backed `WatchService` summary for the bound session and send a concise reply
   using the existing Telegram send boundary. The stored timestamps make old evidence visible.
5. Stopping the foreground process stops both watch checks and Telegram replies. Saved rules and
   runtime history remain in SQLite; restarting resumes from stored state.

#### Work

- Extract a deterministic command parser and formatter shared by local long polling and the
  future cloud webhook. Route status reads through `WatchService`, not CLI output parsing or raw SQL
  in the Telegram adapter.
- Add single-chat and session-binding configuration with startup validation, secret-safe diagnostics,
  and no silent cross-session fallback when the binding is missing or invalid.
- Extend the existing runner with a supervised inbound loop, bounded Telegram `getUpdates` calls,
  restart-safe update progress, controlled replies, and clean shutdown. Keep its existing poll and
  outbound delivery cadence intact.
- Register `/help` and `/status` in the bot command menu when configured. Keep `/start` as a help
  alias and document that Telegram supports status reads only.

#### Documentation Alignment

- Audit every project document and help surface that describes watches, Telegram, setup, runtime
  behavior, architecture, verification, or deployment. Update each relevant description as part of
  this milestone; a working command with stale project documentation does not meet the exit gate.
- Cover `README.md`, `docs/WATCHES_EXPLAINED.md`, `docs/WATCHES_NEXT_STEPS.md`, the watch monitoring
  and testing documents under `docs/research/`, `docs/planning/PROJECT_PROPOSAL.md`,
  `docs/assignment/IMPLEMENTATION_ALIGNMENT.md`, `.env.example`, the sports-information skill,
  CLI `--help`, and every affected architecture or sequence diagram. Keep the implementation plan
  and its status table consistent with the accepted local and pending cloud boundaries.
- Describe one continuous user workflow: create a watch through `/chat` or the local CLI, run the
  local foreground monitor, receive alerts from the existing bot, and send `/help` or `/status` to
  that same bot for an on-demand stored status. Give exact local commands, required configuration,
  the single-runner rule, the same-session binding, and the behavior when the runner is stopped.
- State the actual command contract and examples for `/help`, `/start`, `/status`, and
  `/status <watch_id>`. Explain that status reads stored observations, shows their timestamps and
  source health, and does not request a fresh poll. Keep creation and mutations on `/chat` and the
  CLI; keep Telegram identifiers, session IDs, and credentials out of product examples.
- Show the local `getUpdates` path and outbound delivery path in diagrams as parts of the same
  runner. Show the Cloud Run webhook on the existing service only in a clearly labeled deployment
  plan until Milestone 14A has live acceptance; then update diagrams and setup text to the observed
  deployed behavior.
- Write product and operator documentation in present tense as a coherent system. Remove temporal
  backpointers such as "previously," "now," "was changed," "new in 16C," or "the old behavior."
  Do not append a changelog-style feature announcement or describe inbound Telegram as an add-on.
  Preserve historical test evidence as evidence, while keeping current instructions and claims
  aligned with behavior that has actually been verified.
- Update the final Milestone 14A documentation pass with the same voice and include the webhook
  secret, allowed-chat configuration, command menu, deployment enablement, and live status checks
  only after those cloud steps have been exercised.

#### Verification Plan

- Parser and formatter checks cover both commands, `/start`, exact watch IDs, unknown commands,
  extra arguments, long inputs, empty lists, multiple watches, and bounded reply lengths.
- Status checks cover every runtime state, stale timestamps, source warnings, next-due time,
  delivery failures, and a saved watch that has never had a usable poll. No status request may
  increase provider, MCP, model, or polling counts.
- Access checks cover wrong chat, group chat, mismatched session binding, guessed watch ID, and
  malformed Telegram updates. None may reveal another session's watches or sensitive values.
- Runner checks simulate Telegram timeouts, rate limits, malformed responses, duplicate update IDs,
  process restart, and shutdown while confirming the once-per-minute watch loop still runs.
- One opt-in local live check sends `/help`, `/status`, and `/status <watch_id>` to the real bot while
  the runner is open, then confirms replies stop when the runner is closed. Milestone 14A repeats
  the command path through the deployed webhook and Firestore.
- Documentation review checks every relevant file and diagram against the implemented command
  surface, local process requirements, session scope, and deployment status; product text uses
  present-tense descriptions without temporal backpointers or unverified cloud claims.

#### Exit Criteria

- The existing local runner answers `/help` and `/status` from the allowed chat with no additional
  process or locally hosted endpoint, while continuing normal monitoring and outbound alerts.
- Status replies are bounded, read-only, session-scoped, and consistent with the same durable
  runtime state and language shown by the CLI and `/chat`.
- Wrong chats and guessed IDs expose no watch data; missing configuration and Telegram failures
  produce controlled behavior without stopping the watch coordinator.
- Local tests and an opt-in real bot check establish the command contract. Cloud webhook setup and
  deployed acceptance remain Milestone 14A work.
- All affected project documentation, setup examples, help text, and diagrams describe the accepted
  local Telegram behavior in present tense and distinguish planned cloud operation accurately.

#### Current State

- **Status:** Planned after Milestone 16B; no inbound Telegram command handler exists yet.
- The current runner polls watches and sends Telegram alerts. It does not call `getUpdates`, receive
  bot messages, or reply to status/help commands.

#### Pivot Point

- Keep only `/help` and `/status` if richer bot interactions threaten reliable watch monitoring,
  session isolation, or the local single-runner workflow. Leave mutations in `/chat` and the CLI.

### Milestone 17: Optional Polymarket US Migration Evaluation

#### Work

- Evaluate replacing the international Gamma adapter with a read-only Polymarket US adapter only
  after deployment and submission requirements are satisfied.
- Use documented US routes, including `/v1/search`, `/v1/markets`, market detail, and league-event
  discovery; do not treat the migration as a base-URL substitution.
- Keep US market IDs, event IDs, slugs, prices, statuses, and settlement terms in a distinct
  provider namespace. Never relabel Gamma results or links as US contracts.
- Parse `marketSides`, `bestBidQuote`, `bestAskQuote`, `sportsMarketTypeV2`, and wrapped detail
  responses according to verified US semantics.
- Verify MLB, NFL, and NCAA football taxonomy independently.
- Preserve the public MCP interface only where the US provider can supply equivalent evidence.

#### Exit Criteria

- Search and detail use verified US identifiers, links, price semantics, sports identity, and
  settlement fields.
- MLB, NFL, and NCAA football each have independent taxonomy and bounded live evidence.
- Deterministic fixtures, identity and quote regressions, protocol tests, agent-consumption tests,
  and documentation pass without weakening the current provider contract.
- The integration remains read-only and uses no authenticated order, account, or portfolio API.

#### Pivot Point

- Keep the Gamma adapter when the US API cannot satisfy the supported league coverage, sports
  identity, detail, settlement, or consumer-link contract.

## 8. Verification Strategy

- Unit tests:
  - Provider response parsing.
  - Sports identity, schedule, lifecycle, quote, settlement, and canonical normalization.
  - Sports-aware deterministic equivalence checks and dangerous near-matches.
  - Local-date/range validation, game selection, continuation, and game-limit semantics.
  - Research-budget counters.
  - Response formatting.
- MCP contract tests:
  - Tool discovery.
  - Input-schema validation.
  - Tool invocation.
  - Error conversion.
  - Grouped sports response schemas and search/detail metadata preservation.
- Agent integration tests:
  - Correct platform selection.
  - Both-platform chaining.
  - No-tool behavior.
  - Tavily use after matching.
  - Clarification choices and localized multiple-game selection.
  - Named outcomes, stale warnings, lifecycle, settlement, and comparison eligibility.
- Memory tests:
  - Same-session recall.
  - Cross-session isolation.
  - Context-dependent save request.
- Watch engine tests:
  - Natural-language compilation into the versioned rule schema.
  - Exact identity resolution and confirmation before persistence.
  - Zero-token deterministic polling, edge-triggered evaluation, cooldowns, and idempotent retries.
  - SQLite/Firestore replay parity and LangGraph/headless-Codex semantic parity.
  - Runtime-state, source-readiness, lifecycle-event, inbox, and Telegram notification transitions.
  - Full conversational create, confirm, cancel, list, inspect, revise, pause, resume, delete,
    delivery-status, inbox, and investigation behavior.
  - Session ownership, stale confirmation, mutation idempotency, ambiguous references, prompt
    injection, and ordinary-agent routing regression.
  - Inbound Telegram help/status parsing, configured-chat and session checks, read-only runtime
    summaries, local runner coexistence, and duplicate-update handling.
- Container tests:
  - Non-root execution.
  - MCP subprocess startup.
  - Environment validation.
  - Graceful shutdown.
- Live tests:
  - Small, opt-in provider and real-MCP smoke suite across supported leagues.
  - Base Cloud Run acceptance suite with watch automation disabled.
  - Firestore, authenticated Scheduler, real Telegram, agent-watch, restart, revision, and duplicate
    delivery acceptance on the final deployed service.
  - Opt-in local Telegram command check, then webhook-backed help/status acceptance on the same
    final Cloud Run service.

## 9. Scope-Priority Order

- Protect these first:
  1. Real MCP discovery and invocation.
  2. Correct sports identity, named outcomes, timing, freshness, lifecycle, and settlement.
  3. Correct selection between separate Polymarket and Kalshi MCP servers.
  4. Framework-native, session-scoped memory.
  5. Multi-step reasoning and bounded tool use.
  6. Graceful failure handling.
  7. Truthful, deterministic, idempotent watch state and notification behavior.
  8. Session-scoped conversational watch management through validated host contracts.
  9. Working base Cloud Run deployment followed by durable automated cloud watches.
  10. Read-only Telegram status and help through the existing bot and watch service.
  11. Accurate documentation and required diagrams.
- Reduce these first if time is constrained:
  1. SQLite calibration summaries.
  2. SQLite sports-research snapshot ledger.
  3. Order-book depth.
  4. Additional leagues, spreads, totals, props, and futures.
  5. Saved research snapshots and expanded brief presentation.
  6. Polymarket US migration.

## 10. Completion Definition

- The deployed agent handles Polymarket-only, Kalshi-only, cross-market sports comparison,
  research, memory, and no-tool requests.
- The agent chooses between the two separate market MCP servers based on request intent.
- All required servers are reached through real MCP operations.
- Sports-equivalent contracts are distinguished from related, incompatible, and
  insufficient-evidence contracts using identity, outcome, schedule, and settlement evidence.
- Users can search supported games by team, matchup, local date or range, next game, or most recent
  game without knowing provider taxonomy or guessing identifiers.
- Responses separate kickoff, trading close, quote observation, lifecycle, and settlement.
- Research and tool use remain bounded.
- Memory works across at least two turns under the same `session_id` and remains isolated between sessions.
- The service handles required failure cases without crashing.
- A user can create, confirm, list, inspect, revise, pause, resume, delete, and investigate watches
  through `/chat` without using local operator commands.
- Saved, awaiting, monitoring, degraded, paused, and terminal states remain distinct and are backed
  by durable evidence rather than agent wording, process startup, or Telegram delivery.
- Cloud Scheduler polls confirmed watches automatically, Firestore preserves operational state
  across instance replacement, and ordinary polling uses no model or Tavily calls.
- The authoritative inbox retains lifecycle and condition events, while opted-in Telegram delivery
  sends each logical event at most once and exposes failures without disabling monitoring.
- The same Telegram bot answers `/help` and `/status` for its configured chat using the stored watch
  runtime state, first through the local runner and then through the deployed service webhook.
- The deployed watch workflow survives Scheduler retries, overlapping requests, instance
  termination, revision replacement, source outages, and Telegram errors without false health
  claims or duplicate logical events.
- Automated and manual verification results are documented.
- Cloud Run, README, diagrams, source ZIP, and Rylan Wade's personal process log are ready for submission.
