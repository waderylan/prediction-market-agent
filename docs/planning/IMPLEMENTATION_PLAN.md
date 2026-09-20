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
| 7. Sports-aware deterministic matching | In progress |
| 8. Jev sports-contract equivalence | Planned |
| 9. Bounded sports evidence research | Planned |
| 10. Complete sports agent and forecast output | Planned |
| 11. Optional sports-research ledger | Optional |
| 12. Failure handling and verification | In progress across implemented layers |
| 13. Cloud Run and submission | Required |
| 14. Polymarket US migration evaluation | Optional after deployment |

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
- Keep Jev outside MCP as a specialized LangGraph decision node.
- Use Jev only for sports contract-equivalence classification after deterministic validation,
  not game prediction, price prediction, or arithmetic.
- Keep the service read-only with respect to prediction-market platforms.
- Never place trades or require exchange-account credentials.

## 3. Decisions That May Change

- Exact provider endpoints used for sports discovery, metadata, rules, prices, and optional
  order books.
- Whether a supported sports comparison benefits from additional order-book data beyond the
  current provider snapshots and last-trade fields.
- Search-query construction and candidate-ranking logic.
- Canonical market-model fields beyond the minimum required fields.
- Exact MCP tool arguments and result schemas after API experiments.
- Jev prompt structure, confidence thresholds, retry policy, and fallback threshold.
- Tavily result and extraction limits within the bounded-research requirement.
- Internal Python module layout.
- Whether the SQLite sports-research ledger is completed for Assignment 1 or retained as a
  stretch milestone.
- LLM model configuration, provided the deployed model remains cloud-accessible.

Changes to fixed decisions require an explicit update to this plan. Changes to flexible decisions may be made at a milestone gate when tests provide a reason.

## 4. Target Architecture

```text
Client
  |
  v
FastAPI POST /chat
  |
  v
LangGraph agent + session checkpointer
  |          |             |
  |          |             +--> Jev equivalence node via Vercel AI Gateway
  |          |
  |          +--> Primary LLM reasoning and synthesis
  |
  v
Multi-server MCP client
  |-- Polymarket MCP ------> Polymarket public APIs
  |-- Kalshi MCP ----------> Kalshi public APIs
  |-- Sports-state MCP ----> ESPN public JSON / MLB StatsAPI fallback
  |-- Tavily MCP ----------> Search and extraction
  `-- SQLite MCP ----------> Optional sports-research ledger
```

- The two market servers remain separate processes and separate MCP configurations.
- The sports-state server remains a third independent process and never becomes a fallback for
  market identity, prices, rules, or settlement.
- Market servers share Python domain types where useful, but they do not call each other.
- Each server has unique tool names to avoid collisions after tool aggregation.
- Tavily remains a separate MCP integration.
- Jev receives normalized sports event identity, outcome mapping, and settlement rules only
  after deterministic conflicts are rejected.
- The primary LLM remains responsible for tool selection, ambiguous-case review, and final synthesis.

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

- Use the supported Tavily search and extraction tools.
- Enforce budgets in application state rather than relying only on the prompt.

### Sports Game-State MCP

- `sports_state_find_games`
- `sports_state_get_game_state`
- Keep the public surface limited to supported-game discovery and a current normalized state
  snapshot. Milestone 6 fixes the exact inputs, outputs, provider boundaries, and budgets.

### Optional SQLite MCP

- `save_forecast`
- `get_forecast`
- `list_forecasts`
- Add scoring tools only after save and retrieval work reliably.

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
  - nullable authoritative `quote_as_of`, `retrieved_at`, `provider_updated_at`, and stale reason
  - `observation_id`, `cache_hit`, and `cache_age_ms`
  - `settlement_value`, `winning_outcome`, and `resolved_at`
  - `retrieved_at`
- Attach a typed sports event containing league, provider event identity, raw and canonical
  participants, NCAA divisions when known, full-game winner type, schedule evidence, and
  comparison eligibility with its reason.
- Project search results as games with contracts nested beneath each game. A sports search limit
  counts games, not contracts.
- Keep scheduled start, trading close, expected resolution, and resolution deadline independent.
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

- Change internal models and API boundaries now, before MCP schemas make them externally visible.

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

- Add one narrow, read-only MCP server that answers what is happening in a supported game now.
- Support the same initial leagues as market discovery: MLB, NFL, and NCAA Division I football.
- Keep game state separate from prediction-market price, contract equivalence, and settlement.
- Use ESPN's unauthenticated public JSON scoreboard/summary surface as the primary cross-sport
  source. It is free and requires no account or API key, but it is undocumented and has no SLA.
- Use MLB StatsAPI as a fallback for MLB only. Do not claim an equivalent official free fallback
  for NFL or NCAA football unless a later feasibility check verifies one.
- A 2026-09-20 feasibility check through the project's `httpx` stack returned HTTP 200 for all
  three ESPN league scoreboards. Live MLB exposed inning, count, outs, bases, pitcher, batter, and
  last play. Live NFL exposed score, period, clock, down, distance, field position, timeouts, last
  play, and possession when supplied. The prior NCAA football slate returned scheduled/final game
  identity and scores. MLB StatsAPI independently returned a matching live game and detailed
  baseball state.
- Treat this feasibility evidence as proof of a workable interface, not a promise that an
  undocumented provider schema will remain stable.

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
- Do not import an existing broad ESPN MCP. Existing servers expose unrelated news, standings,
  odds, roster, and betting surfaces or introduce metered hosting. This server exposes only the
  minimum game-discovery and state tools needed by this product.
- Do not expose provider odds, provider win probability, player projections, fantasy data, news,
  or full box scores. Those fields are outside the game-state requirement and could be mistaken
  for the agent's forecast evidence.

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
  implemented game-state capability from future comparison, research, and forecasting work.
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
- Cloud Run deployment remains Milestone 13 work; local/container verification does not claim a
  deployed third-server runtime.

#### Pivot Point

- If ESPN becomes unavailable to the deployed `httpx` client, its schema cannot be bounded safely,
  or live checks show unreliable identity/state, do not scrape HTML or weaken validation. Keep the
  typed MCP interface and pause NFL/NCAA support until another verified free source exists.
- If MLB StatsAPI is substantially more reliable in measured live checks, make it the primary MLB
  source while keeping the same public MCP schema.
- Do not add a paid dependency merely to preserve this optional product capability; the assignment
  already satisfies its minimum two-server requirement with Kalshi and Polymarket.

### Milestone 7: Sports-Aware Deterministic Contract Matching

#### Work

- Generate bounded candidate pairs from the two market results.
- Reject clear mismatches in code before any model call.
- Compare:
  - League, season, both participants, provider event identity evidence, and game number.
  - Scheduled start in a shared timezone, without substituting trading close or resolution time.
  - Full-game market type, line or threshold, and named outcome mapping.
  - Resolution authority and official-result requirements.
  - Postponement windows, cancellation payouts, overtime, ties, shortened games, abandoned games,
    and material exclusions.
- Treat missing or inconsistent sports evidence as insufficient evidence, not equivalence.
- Never assume that one Kalshi team's NO outcome equals the opponent's YES contract without
  settlement evidence.
- Separate primary equivalent candidates from related contextual contracts.
- Return explicit reasons for rejection or ambiguity.

#### Exit Criteria

- Obvious mismatches are rejected deterministically.
- Related markets are labeled as context, not equivalent contracts.
- Ambiguous sports-rule cases are routed to the Jev milestone for review.
- Tests include dangerous near-matches, not only easy positive pairs.
- Tests keep doubleheaders, wrong opponents, different game numbers, and different settlement
  treatment distinct even when titles are similar.

#### Pivot Point

- Keep the matcher limited to supported full-game winners if additional sports or contract types
  lack the typed identity and settlement evidence required for safe comparison.

#### Current State

- **Status:** In progress.
- The bounded generic matcher, deterministic vetoes, conservative verdicts, contextual labels,
  independent eligibility notice, and pre-synthesis matching report are implemented.
- Provider discovery supplies typed sports identity but intentionally marks comparison eligibility
  as insufficient until the matcher evaluates both contracts' settlement evidence.
- Sports-aware dimensions and regression cases in this milestone remain required before a
  cross-platform sports price difference is presented as like-for-like.
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
- Deterministic conflicts always veto equivalence regardless of Jev output.
- Jev never predicts a game, performs arithmetic, recommends a position, or produces the final
  forecast.
- The entire request still succeeds when Jev is disabled.

#### Pivot Point

- If Jev does not improve the sports evaluation set, retain the typed interface but disable
  automatic routing and use the primary LLM for ambiguous pairs.

### Milestone 9: Bounded Sports Evidence Research

#### Work

- Connect the supported Tavily MCP as a fourth server after the sports-state MCP.
- Run research only after one sports event is identified and a viable primary pair or clearly
  scoped single-platform request exists.
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
- Search budgets cannot be exceeded by repeated model requests.
- Responses cite the evidence actually retrieved.
- Search failure produces a useful partial answer rather than a server error.

#### Pivot Point

- Adjust research limits using observed latency, cost, and answer quality.
- Remove extraction if search snippets provide enough evidence for the assignment demonstration.

### Milestone 10: Complete Sports Agent Workflow and Forecast Output

#### Work

- Assemble the full LangGraph workflow:
  - Interpret request.
  - Select zero, one, or both market MCPs.
  - Resolve the team, matchup, league, local calendar window, and intended game.
  - Retrieve grouped games and exact provider contracts without inventing identifiers.
  - Ask the user to choose when multiple event IDs remain plausible.
  - Normalize and match sports contracts.
  - Run Jev or fallback review only for semantic ambiguity that survives deterministic checks.
  - Retrieve bounded current evidence when appropriate.
  - Synthesize a final response.
- Define a consistent response structure containing:
  - League, participants, local kickoff, and exact provider event identities.
  - Matched primary contracts and consumer-facing links.
  - Named outcome prices with one authoritative `quote_as_of` and stale warnings.
  - Lifecycle and explicit settlement, separate from provider exchange status.
  - Material rule differences.
  - Related-market context.
  - Current external evidence.
  - Agent probability estimate.
  - Uncertainty and limitations.
  - `YES`, `NO`, or `NO POSITION` conclusion.
- Calculate price differences, implied probabilities, and other numeric comparisons in Python.
- Allow a safe refusal to compare when no equivalent pair exists.
- Preserve no-tool and single-platform behavior.

#### Exit Criteria

- A representative comparison chains both market MCPs and Tavily before synthesis.
- Platform-specific queries do not force the other market MCP.
- Local-date requests use the requested IANA timezone rather than treating a UTC calendar date as
  the user's date.
- Search/detail follow-ups preserve sports metadata and one explicit normalized observation identity.
- Multi-turn follow-ups use the correct session context.
- The agent handles arbitrary reasonable in-scope queries rather than memorized examples.
- `NO POSITION` is produced when evidence or equivalence is insufficient.

#### Pivot Point

- Simplify output sections or graph branching if latency becomes excessive.
- Preserve tool selection, memory, and MCP correctness before optional forecast detail.

### Milestone 11: Optional SQLite Sports-Research Ledger

#### Work

- Build the ledger only after the complete research workflow is stable.
- Store a forecast only after an explicit user request.
- Save league, participants, provider event and contract references, local kickoff, quote timestamps,
  prices, probability, evidence URLs, equivalence decision, and explicit settlement when available.
- Support basic retrieval in a later turn.
- Document that local Cloud Run storage is not durable across instance replacement.

#### Exit Criteria

- The SQLite server is independently discovered and invoked through MCP.
- “Save that forecast” uses conversational context and creates one record.
- A different session cannot accidentally overwrite the record through ambiguous context.
- Ledger failure does not destroy the generated forecast response.

#### Pivot Point

- Cut or defer this milestone if core rubric work, deployment, or verification is incomplete.
- Do not introduce managed database infrastructure for Assignment 1.

### Milestone 12: Sports Failure Handling and Verification

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
- Test Jev timeout and fallback.
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

### Milestone 13: Cloud Run Deployment and Submission Artifacts

#### Work

- Finalize the multi-stage Dockerfile with a non-root runtime user.
- Confirm the application binds to `0.0.0.0:$PORT`.
- Configure secrets through environment variables or Secret Manager.
- Deploy with `--max-instances 1` to support instance-local memory.
- Run live tests against the deployed `/chat` endpoint.
- Verify same-session recall while the instance remains active.
- Verify Polymarket-only, Kalshi-only, sports comparison, local-date discovery, research, no-tool,
  memory, and failure responses.
- Complete README setup, run, deployment, cost, limitations, and MCP attribution sections.
- Add all three required architecture diagrams based on the final implementation.
- Prepare the source ZIP without `.env`, virtual environments, caches, local databases, or credentials.
- Leave `PROCESS_LOG.md` for Rylan Wade's accurate personal narrative and lessons learned.

#### Exit Criteria

- The live Cloud Run URL answers arbitrary reasonable queries.
- Deployment uses the required request and response contract.
- README diagrams match the code that was actually deployed.
- Required submission files are present and secrets are absent.
- The service remains available for grading.

### Milestone 14: Optional Polymarket US Migration Evaluation

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
  - Jev fallback.
  - Clarification choices and localized multiple-game selection.
  - Named outcomes, stale warnings, lifecycle, settlement, and comparison eligibility.
- Memory tests:
  - Same-session recall.
  - Cross-session isolation.
  - Context-dependent save request.
- Container tests:
  - Non-root execution.
  - MCP subprocess startup.
  - Environment validation.
  - Graceful shutdown.
- Live tests:
  - Small, opt-in provider and real-MCP smoke suite across supported leagues.
  - Deployed Cloud Run acceptance suite.

## 9. Scope-Priority Order

- Protect these first:
  1. Real MCP discovery and invocation.
  2. Correct sports identity, named outcomes, timing, freshness, lifecycle, and settlement.
  3. Correct selection between separate Polymarket and Kalshi MCP servers.
  4. Framework-native, session-scoped memory.
  5. Multi-step reasoning and bounded tool use.
  6. Graceful failure handling.
  7. Working Cloud Run deployment.
  8. Accurate documentation and required diagrams.
- Reduce these first if time is constrained:
  1. SQLite calibration summaries.
  2. SQLite sports-research ledger.
  3. Order-book depth.
  4. Jev-based article filtering.
  5. Additional leagues, spreads, totals, props, and futures.
  6. Detailed forecast presentation beyond the required evidence and uncertainty.
  7. Polymarket US migration.

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
- Jev is optional at runtime and fails safely.
- Memory works across at least two turns under the same `session_id` and remains isolated between sessions.
- The service handles required failure cases without crashing.
- Automated and manual verification results are documented.
- Cloud Run, README, diagrams, source ZIP, and Rylan Wade's personal process log are ready for submission.
