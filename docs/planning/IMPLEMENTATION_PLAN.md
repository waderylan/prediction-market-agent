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
| 6. Sports-aware deterministic matching | In progress |
| 7. Jev sports-contract equivalence | Planned |
| 8. Bounded sports evidence research | Planned |
| 9. Complete sports agent and forecast output | Planned |
| 10. Optional sports-research ledger | Optional |
| 11. Failure handling and verification | In progress across implemented layers |
| 12. Cloud Run and submission | Required |
| 13. Polymarket US migration evaluation | Optional after deployment |

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
  |-- Tavily MCP ----------> Search and extraction
  `-- SQLite MCP ----------> Optional sports-research ledger
```

- The two market servers remain separate processes and separate MCP configurations.
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
  Milestone 9.
- The independent Kalshi server and shared projections preserve partial availability, unique tool
  names, provider/identifier validation, and model-driven platform selection.
- Both servers support MLB, NFL, and NCAA Division I full-game winner discovery using reviewed
  identity metadata and provider-backed scope resolution.
- Deterministic, real MCP, agent-consumption, and bounded live tests cover common names, ambiguous
  aliases, wrong opponents, doubleheaders, pagination, grouped games, named prices, timing,
  lifecycle, freshness, settlement, links, and identifier safety.
- `../research/SPORTS_MCP.md` is the source of truth for algorithms, schemas, budgets, catalog
  maintenance, and known provider boundaries.

### Milestone 6: Sports-Aware Deterministic Contract Matching

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

### Milestone 7: Jev Sports-Contract Equivalence Evaluation

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

### Milestone 8: Bounded Sports Evidence Research

#### Work

- Connect the supported Tavily MCP as a third server.
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

### Milestone 9: Complete Sports Agent Workflow and Forecast Output

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

### Milestone 10: Optional SQLite Sports-Research Ledger

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

### Milestone 11: Sports Failure Handling and Verification

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

### Milestone 12: Cloud Run Deployment and Submission Artifacts

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

### Milestone 13: Optional Polymarket US Migration Evaluation

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
