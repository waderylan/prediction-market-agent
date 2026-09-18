# Implementation Plan: Cross-Market Prediction Research Agent

## 1. Purpose

- Build the assignment as a sequence of independently testable milestones.
- Address high-risk assumptions before adding optional features.
- Maintain a working vertical slice throughout development.
- Preserve room to change APIs, schemas, prompts, thresholds, and internal module boundaries when testing provides better evidence.
- Keep the implementation aligned with `../assignment/Assignment_1_Description.md` and the focused scope in `PROJECT_PROPOSAL.md`.

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
- Use Jev only for contract-equivalence classification, not price prediction or arithmetic.
- Keep the service read-only with respect to prediction-market platforms.
- Never place trades or require exchange-account credentials.

## 3. Decisions That May Change

- Exact API endpoints used for discovery, metadata, rules, prices, and order books.
- Whether current prices require Polymarket CLOB calls or can come from Gamma responses.
- Search-query construction and candidate-ranking logic.
- Canonical market-model fields beyond the minimum required fields.
- Exact MCP tool arguments and result schemas after API experiments.
- Jev prompt structure, confidence thresholds, retry policy, and fallback threshold.
- Tavily result and extraction limits within the bounded-research requirement.
- Internal Python module layout.
- Whether the SQLite forecast ledger is completed for Assignment 1 or retained as a stretch milestone.
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
  `-- SQLite MCP ----------> Optional forecast ledger
```

- The two market servers remain separate processes and separate MCP configurations.
- Market servers share Python domain types where useful, but they do not call each other.
- Each server has unique tool names to avoid collisions after tool aggregation.
- Tavily remains a separate MCP integration.
- Jev receives normalized contract data after deterministic validation.
- The primary LLM remains responsible for tool selection, ambiguous-case review, and final synthesis.

## 5. Proposed Tool Surface

### Polymarket MCP

- `polymarket_search_markets`
  - Input: topic, status, result limit.
  - Output: bounded candidate summaries.
- `polymarket_get_market`
  - Input: stable Polymarket market identifier.
  - Output: normalized market details, rules, prices, and source metadata.
- `polymarket_get_order_book`
  - Add only if order-book data materially improves the project.

### Kalshi MCP

- `kalshi_search_markets`
  - Input: topic, status, result limit.
  - Output: bounded candidate summaries.
- `kalshi_get_market`
  - Input: Kalshi market ticker.
  - Output: normalized market details, rules, prices, and source metadata.
- `kalshi_get_order_book`
  - Add only if order-book data materially improves the project.

### Tavily MCP

- Use the supported Tavily search and extraction tools.
- Enforce budgets in application state rather than relying only on the prompt.

### Optional SQLite MCP

- `save_forecast`
- `get_forecast`
- `list_forecasts`
- Add scoring tools only after save and retrieval work reliably.

## 6. Canonical Market Model

- Define one normalized model used after provider-specific API parsing.
- Initial fields:
  - `platform`
  - `market_id`
  - `event_id`
  - `title`
  - `description`
  - `outcomes`
  - `yes_price`
  - `no_price`
  - `yes_bid`
  - `yes_ask`
  - `open_time`
  - `close_time`
  - `resolution_deadline`
  - `resolution_source`
  - `rules`
  - `status`
  - `liquidity`
  - `volume`
  - `source_url`
  - `retrieved_at`
- Allow unavailable fields to be null rather than inventing values.
- Retain a bounded raw-provider payload for debugging when appropriate.
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

#### Execution Record

- **Status:** Complete on 2026-09-18.
- **Implemented:** Added a Python 3.12 `src/` package, `uv` dependency workflow and lockfile,
  environment template, validated settings, safe JSON logging, pytest marker layout, initial
  smoke/configuration/logging tests, comprehensive generated-file ignores, and local commands.
- **Verification:** Recreated `.venv` with `uv`, ran `uv sync --all-extras`, then observed 4 tests
  pass, Ruff pass, strict mypy pass, `git diff --check` pass, and ignore rules match secrets,
  environments, caches, databases, coverage, and generated artifacts.
- **Learned:** `uv` resolves and installs the complete development environment directly from
  `pyproject.toml`; Pydantic settings provide clear missing-variable validation without reading or
  logging secret values.
- **Deviations:** Adopted `uv` and `uv.lock` for local development instead of pip requirements files
  at Rylan Wade's request. Container dependency installation remains a later deployment decision.
- **Recommendation:** Keep the `src/` layout and `uv` workflow; no project pivot is supported by the
  Milestone 0 evidence.

### Milestone 1: Market API Feasibility Spike

#### Work

- Test Polymarket discovery, market detail, rule text, price, pagination, and error behavior.
- Test Kalshi discovery, market detail, rule text, price, pagination, and error behavior.
- Use several event categories rather than one hard-coded example.
- Include at least one likely cross-platform pair and several near-matches.
- Record response fields and API limitations in development notes.
- Save sanitized response fixtures for automated tests.
- Determine whether order-book calls are necessary for the initial version.

#### Questions to Resolve

- Can natural-language topics produce useful candidates on each platform?
- Where are complete resolution rules exposed?
- Which price fields are stable and comparable?
- How are binary outcomes and polarity represented?
- How are active, closed, resolved, and invalid markets represented?
- What pagination, timeout, and rate-limit behavior must the clients handle?

#### Exit Criteria

- Both APIs return enough information to build the canonical market model.
- At least one plausible contract pair can be retrieved from both platforms.
- Major missing fields and workarounds are documented.
- Sanitized fixtures cover success, empty results, and malformed or incomplete data.

#### Pivot Point

- If reliable topic search is unavailable, use a bounded local candidate-ranking layer over paginated event or market results.
- If full rules cannot be retrieved reliably, narrow the supported market categories or reconsider the matching workflow before building MCP servers.

#### Execution Record

- **Status:** Complete on 2026-09-18.
- **Implemented:** Reviewed current official API documentation, ran bounded public GET checks across
  several categories and failure cases, documented endpoint behavior and canonical mappings in
  `../research/MARKET_API_FEASIBILITY.md`, and added reduced fixtures for success, empty, incomplete, and
  malformed responses.
- **Verification:** Observed successful discovery/detail/rules/prices/status calls for both
  providers; distinct cursor pages; empty `200` results; Polymarket `422` and Kalshi `404` errors;
  one plausible 2028 presidential pair; several near-matches; and valid JSON for all 11 fixtures.
  The deterministic 4-test suite, Ruff, and strict mypy remained green.
- **Learned:** Polymarket supports public free-text search but can rank stale results. Kalshi has no
  documented natural-language search and requires a bounded catalog scan plus local ranking.
  Summary responses expose sufficient prices for version one, so order-book depth is unnecessary.
- **Deviations:** Selected Polymarket public search plus keyset feeds and Kalshi event pagination as
  the concrete discovery boundaries. Kalshi series lookup will enrich settlement-source metadata.
- **Recommendation:** Apply the plan's existing smallest fallback: scan a fixed number of Kalshi
  event pages, rank locally, and fetch nested markets only for top candidates. No fixed-decision
  change or major pivot is warranted.

### Milestone 2: Shared Domain Layer and API Clients

#### Work

- Implement provider-specific asynchronous HTTP clients.
- Add explicit request timeouts and bounded retries.
- Parse provider responses into the canonical market model.
- Add deterministic normalization for timestamps, probabilities, outcome direction, units, and status.
- Define typed provider exceptions for transport, HTTP, validation, and missing-data failures.
- Test parsing primarily against saved fixtures.
- Keep a small set of opt-in live smoke tests.

#### Exit Criteria

- Each client can search for candidates and fetch one market in normalized form.
- Unit tests do not require network access.
- Live smoke tests can be run independently when APIs are available.
- Invalid responses fail with typed, inspectable errors.

#### Pivot Point

- Change internal models and API boundaries now, before MCP schemas make them externally visible.

#### Execution Record

- **Status:** Complete on 2026-09-18.
- **Implemented:** Added the immutable canonical market model and status/platform enums; separate
  asynchronous Polymarket and Kalshi clients; isolated provider parsers; explicit 10-second
  timeouts; at most two retries by default; typed transport, HTTP, validation, and missing-data
  failures; UTC/probability normalization; bounded Kalshi local discovery; and opt-in live tests.
- **Verification:** Observed 22 deterministic tests pass with 2 live tests deselected, then enabled
  the bounded live suite and observed both provider detail tests pass. Ruff and strict mypy passed.
  Tests cover success, empty search, incomplete fields, malformed data, missing required identity,
  `4xx`, retried `5xx`, and timeout behavior.
- **Learned:** Provider status vocabulary requires explicit mappings (`active`/`finalized` versus
  canonical `open`/`resolved`). Kalshi series metadata reliably enriches resolution sources, while
  Polymarket's rules often carry authority details even when its source field is empty.
- **Deviations:** Kalshi detail performs an optional series lookup for settlement sources, and
  search uses summary-page ranking before nested-event retrieval. No order-book call was added.
- **Recommendation:** Keep these client and model boundaries for the later separate MCP servers;
  no fixed architecture change or project pivot is supported by current evidence.

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

#### Execution Record

- **Status:** Complete on 2026-09-18; all four exit criteria verified.
- **Implemented:** Student-authored stdio FastMCP server wrapping the existing PolymarketClient;
  search/detail tools, typed input/output schemas, canonical projections, bounded results,
  explicit rule truncation, sanitized tool errors, lifespan-owned HTTP client, and usage docs.
- **Verification:** 45 deterministic tests passed; real MCP discovery and both tool calls validated
  against declared schemas. Invalid arguments, empty results, transport/HTTP/malformed/missing
  data, and output bounds passed. Public stdio discovery/search/detail smoke passed separately
  (1 test). Ruff lint/format, strict mypy, and git diff whitespace checks passed.
- **Learned:** Current adapter dependencies require MCP v1 despite SDK v2 availability; supported
  v1 FastMCP provides structured output and real protocol testing without another server framework.
- **Deviations:** Selected stdio, 10-result search cap, and 12,000-character flagged rule cap.
  These refine flexible boundaries; no fixed decision changed and no order-book tool was needed.
- **Recommendation:** Preserve the two-tool interface and defer SDK v2 until adapter support.
  Detailed evidence and official references: `../research/MCP_VERTICAL_SLICE.md`.

### Milestone 4: Early Vertical Slice

#### Work

- Create the FastAPI application and required `/chat` contract.
- Create a minimal LangGraph reasoning and tool loop.
- Connect the Polymarket MCP through `langchain-mcp-adapters`.
- Add a framework-native in-memory checkpointer keyed by `session_id`.
- Support a Polymarket query, a no-tool query, and a memory follow-up.
- Run the slice locally and inside the initial Docker container.

#### Exit Criteria

- `/chat` returns the required response shape.
- The agent makes a real Polymarket MCP call for an appropriate request.
- The agent can answer an appropriate no-tool request without forcing a tool call.
- Two turns with the same session recall prior context.
- Two different session IDs remain isolated.
- The Dockerized slice starts as a non-root user and reads `PORT`.

#### Pivot Point

- Resolve MCP lifecycle, LangGraph state, and container-process issues before adding more servers.

#### Execution Record

- **Status:** Complete on 2026-09-18; all six exit criteria verified with real local-model
  integration. Production GPT-5 API verification remains explicitly unavailable without a key.
- **Implemented:** FastAPI chat contract, minimal LangGraph semantic tool loop, adapter-backed
  stdio MCP session, native InMemorySaver, session synchronization, four-call budget, controlled
  dependency errors, safe tool observability, and a non-root multi-stage Docker runtime.
  Contract readouts emphasize settlement conditions and caveats alongside sourced prices.
- **Verification:** 62 offline tests passed, including fixture protocol and real subprocess tests.
  Two public provider checks and one public MCP check passed separately. Four real-model checks
  passed using headless Sol at medium effort: no-tool recall/isolation, contract readout, and two
  search phrasings. Docker built and ran as UID 10001, bound to 0.0.0.0:9090 via PORT, returned
  health 200 / invalid-body 422, and executed a real MCP detail call for a sourced readout.
- **Learned:** SDK HTTP pool caching crossed test application event loops; explicit lifespan-owned
  clients resolved observed failures. Request-owned MCP sessions provide straightforward cleanup
  and next-request recovery without a process supervisor.
- **Deviations:** User requested headless Codex for local tests; added a development-only gateway
  excluded from the deployment image. Retained the cloud-accessible GPT-5 configuration.
  Tavily credentials became optional until the Tavily milestone. No fixed architecture changed.
- **Recommendation:** Preserve the contract-literacy focus and two-tool pattern for Kalshi;
  verify production GPT-5 separately before deployment. See `../research/MCP_VERTICAL_SLICE.md`.

### Milestone 5: Kalshi MCP and Platform Routing

#### Work

- Build the Kalshi MCP using the same interface discipline as the Polymarket MCP.
- Register both servers with the multi-server MCP client.
- Ensure all aggregated tool names remain unique.
- Describe tools clearly enough for semantic selection.
- Add routing tests that inspect actual tool calls.

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

#### Pivot Point

- Improve tool descriptions, system instructions, or graph routing if the LLM repeatedly selects the wrong platform.
- Do not replace semantic selection with hard-coded keyword routing solely to make tests pass.

### Milestone 6: Deterministic Contract Matching

#### Work

- Generate bounded candidate pairs from the two market results.
- Reject clear mismatches in code before any model call.
- Compare:
  - Event identity.
  - Outcome polarity.
  - Dates and time zones.
  - Thresholds and units.
  - Inclusive versus exclusive boundaries.
  - Resolution authority.
  - Cancellation conditions.
  - Material exclusions and edge cases.
- Separate primary equivalent candidates from related contextual contracts.
- Return explicit reasons for rejection or ambiguity.

#### Exit Criteria

- Obvious mismatches are rejected deterministically.
- Related markets are labeled as context, not equivalent contracts.
- Ambiguous semantic cases are routed for review.
- Tests include dangerous near-matches, not only easy positive pairs.

#### Pivot Point

- Narrow the supported event types if rule normalization becomes too broad for the assignment timeline.

### Milestone 7: Jev Equivalence Evaluation

#### Work

- Define a small, typed Jev interface independent of the graph implementation.
- Send only normalized contract fields and remaining semantic questions.
- Produce `equivalent`, `ambiguous`, or `different` with probability and confidence information.
- Build a hand-labeled development fixture set containing matches and near-misses.
- Compare deterministic-only, deterministic-plus-Jev, and main-LLM-review behavior.
- Add a short timeout, at most one retry, and a primary-LLM fallback.
- Place Jev behind a feature flag so it can be disabled without changing the workflow.

#### Exit Criteria

- Jev improves or usefully accelerates semantic classification on the fixture set.
- Low-confidence and unavailable-model cases fall back cleanly.
- Jev never performs arithmetic or produces the final forecast.
- The entire request still succeeds when Jev is disabled.

#### Pivot Point

- If Jev does not improve the evaluation set, retain the interface but disable automatic routing and use the primary LLM for ambiguous pairs.

### Milestone 8: Bounded Tavily Research

#### Work

- Connect the supported Tavily MCP as a third server.
- Run news research only after a viable primary pair or clearly scoped single-platform request exists.
- Track search and extraction counts in per-request graph state.
- Start with these provisional limits:
  - At most two searches.
  - At most five results inspected per search.
  - At most three article extracts.
- Preserve source title, URL, publication date, retrieval time, and evidence relationship.
- Keep supporting and conflicting evidence distinct.
- Treat retrieved text as untrusted data and prevent it from overriding system instructions.

#### Exit Criteria

- Tavily tools are discovered and invoked through MCP.
- Search budgets cannot be exceeded by repeated model requests.
- Responses cite the evidence actually retrieved.
- Search failure produces a useful partial answer rather than a server error.

#### Pivot Point

- Adjust research limits using observed latency, cost, and answer quality.
- Remove extraction if search snippets provide enough evidence for the assignment demonstration.

### Milestone 9: Complete Agent Workflow and Forecast Output

#### Work

- Assemble the full LangGraph workflow:
  - Interpret request.
  - Select zero, one, or both market MCPs.
  - Retrieve primary and related candidates.
  - Normalize and match contracts.
  - Run Jev or fallback review for ambiguity.
  - Retrieve bounded current evidence when appropriate.
  - Synthesize a final response.
- Define a consistent response structure containing:
  - Matched primary contracts.
  - Current platform prices.
  - Material rule differences.
  - Related-market context.
  - Current external evidence.
  - Agent probability estimate.
  - Uncertainty and limitations.
  - `YES`, `NO`, or `NO POSITION` conclusion.
- Calculate price differences and other numeric comparisons in Python.
- Allow a safe refusal to compare when no equivalent pair exists.
- Preserve no-tool and single-platform behavior.

#### Exit Criteria

- A representative comparison chains both market MCPs and Tavily before synthesis.
- Platform-specific queries do not force the other market MCP.
- Multi-turn follow-ups use the correct session context.
- The agent handles arbitrary reasonable in-scope queries rather than memorized examples.
- `NO POSITION` is produced when evidence or equivalence is insufficient.

#### Pivot Point

- Simplify output sections or graph branching if latency becomes excessive.
- Preserve tool selection, memory, and MCP correctness before optional forecast detail.

### Milestone 10: Optional SQLite Forecast Ledger

#### Work

- Build the ledger only after the complete research workflow is stable.
- Store a forecast only after an explicit user request.
- Save normalized market references, prices, probability, evidence URLs, timestamp, and equivalence decision.
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

### Milestone 11: Failure Handling and Verification

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
- Run formatting, linting, type checking, unit tests, and integration tests.

#### Exit Criteria

- All three rubric-required MCP failure classes return controlled responses.
- The HTTP service does not expose unhandled stack traces.
- Tests demonstrate no-tool, single-tool, and multi-tool reasoning.
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
- Verify Polymarket-only, Kalshi-only, comparison, research, no-tool, and failure responses.
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

## 8. Verification Strategy

- Unit tests:
  - Provider response parsing.
  - Canonical normalization.
  - Deterministic equivalence checks.
  - Research-budget counters.
  - Response formatting.
- MCP contract tests:
  - Tool discovery.
  - Input-schema validation.
  - Tool invocation.
  - Error conversion.
- Agent integration tests:
  - Correct platform selection.
  - Both-platform chaining.
  - No-tool behavior.
  - Tavily use after matching.
  - Jev fallback.
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
  - Small, opt-in API smoke suite.
  - Deployed Cloud Run acceptance suite.

## 9. Scope-Priority Order

- Protect these first:
  1. Real MCP discovery and invocation.
  2. Correct selection between separate Polymarket and Kalshi MCP servers.
  3. Framework-native, session-scoped memory.
  4. Multi-step reasoning and bounded tool use.
  5. Graceful failure handling.
  6. Working Cloud Run deployment.
  7. Accurate documentation and required diagrams.
- Reduce these first if time is constrained:
  1. SQLite calibration summaries.
  2. SQLite forecast ledger.
  3. Order-book depth.
  4. Jev-based article filtering.
  5. Broad support for many market categories.
  6. Detailed forecast presentation beyond the required evidence and uncertainty.

## 10. Completion Definition

- The deployed agent handles Polymarket-only, Kalshi-only, cross-market, research, memory, and no-tool requests.
- The agent chooses between the two separate market MCP servers based on request intent.
- All required servers are reached through real MCP operations.
- Equivalent contracts are distinguished from related or mismatched contracts.
- Research and tool use remain bounded.
- Jev is optional at runtime and fails safely.
- Memory works across at least two turns under the same `session_id` and remains isolated between sessions.
- The service handles required failure cases without crashing.
- Automated and manual verification results are documented.
- Cloud Run, README, diagrams, source ZIP, and Rylan Wade's personal process log are ready for submission.
