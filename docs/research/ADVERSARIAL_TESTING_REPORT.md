# Milestone 13 Adversarial Testing Report

## Scope

Milestone 13 verifies that the HTTP service, LangGraph agent, MCP processes, provider clients, and
normalization boundaries fail in controlled and observable ways. The deterministic test suite uses
recorded provider fixtures and scripted model decisions while still running real MCP sessions and
stdio subprocesses.

All four configured MCP servers are covered: Polymarket, Kalshi, sports-state, and Tavily. The
assignment's three required failure classes are tested separately:

- connection or transport failure;
- tool execution failure; and
- malformed or schema-invalid tool response.

## Failure matrix

| Boundary | Injected condition | Expected behavior | Primary evidence |
|---|---|---|---|
| Every MCP process | One named server cannot start | Healthy servers remain available; total dependency loss returns a controlled response | `integration/test_stdio.py` |
| Polymarket MCP | Timeout, HTTP 404/429/503, malformed JSON, missing fields, oversized body, nonfinite price | Typed tool error; MCP session remains usable; no invented market data | `integration/test_polymarket_mcp.py`, `integration/test_sports_mcp.py`, `unit/test_polymarket_client.py` |
| Kalshi MCP | Timeout, HTTP 404/429/503, malformed or missing data, pagination cycles | Typed tool error or bounded partial result; no platform substitution | `integration/test_kalshi_mcp.py`, `integration/test_sports_mcp.py`, `unit/test_kalshi_client.py` |
| Sports-state MCP | Provider failure, malformed response, identity conflict, invalid or forged reference, oversized body | Stable error code; no raw exception or cross-game fallback | `integration/test_game_state_mcp.py`, `unit/test_game_state.py` |
| Tavily MCP | Provider error, malformed response, unsafe or irrelevant URL, partial source set | Controlled research failure; verified market evidence remains available | `integration/test_tavily_mcp.py`, `integration/test_sports_mcp.py`, `unit/test_research.py` |
| LangGraph tool loop | Tool error, invalid MCP payload, model error, excessive sequential or parallel calls | Bounded response and activity; next turn remains usable | `integration/test_chat.py` |
| FastAPI request boundary | Missing, blank, oversized, mistyped, extra, or disallowed fields | HTTP 422 before model or MCP activity | `integration/test_chat.py` |

## Adversarial identity and data cases

The retained suite checks cases that can silently produce convincing but incorrect sports answers:

- ambiguous city and school aliases, unknown opponents, and unsupported spreads, totals, props, or
  mixed-league requests require clarification;
- wrong opponents, sibling markets, doubleheaders, colliding IDs, schedule drift, and conflicting
  provider metadata cannot be merged into one event;
- invalid timezones, reversed ranges, conflicting selectors, and provider- or query-mismatched
  cursors fail before provider I/O;
- malformed sibling records are discarded without hiding valid siblings, while malformed page roots
  fail explicitly;
- missing timestamps remain unavailable rather than becoming inferred quote times, and only an old
  authoritative timestamp creates a stale quote;
- sporting results remain separate from prediction-market settlement and contract equivalence;
- cancellation propagates, retry counts remain bounded, and response byte limits apply before JSON
  parsing.

## Agent and API behavior

The orchestration tests cover no-tool, single-tool, multi-tool, and all-four-source turns. They also
verify same-session recall, cross-session isolation, source-specific partial availability, research
identity gates, the two-search research budget, and the eight-call market/state budget.

One adversarial batch exposed a response-contract defect: a model could request more than ten tools
in one message, causing `activity` to exceed its declared maximum even though execution budgets were
enforced. The agent now returns a `ToolMessage` for every requested call, records no more than ten
activity entries, and produces a controlled tool-limit response if final synthesis fails.

## Unit-test value audit

No test bloat means each retained module protects a distinct boundary rather than increasing the
count through duplicate happy paths.

| Unit module | Cases | Behavior retained |
|---|---:|---|
| `test_config.py` | 2 | Required settings and clear startup failure |
| `test_documentation.py` | 3 | Current product, submission, and MCP manifest contracts |
| `test_domain_models.py` | 3 | Probability and UTC timestamp invariants |
| `test_game_state.py` | 61 | Identity, lifecycle, state, statistics, cache, fallback, transport, and size limits |
| `test_kalshi_client.py` | 8 | Client parsing, missing data, retries, and typed failures |
| `test_kalshi_discovery.py` | 17 | Ranking, pagination, deduplication, and malformed discovery data |
| `test_logging.py` | 1 | Secret redaction and prompt omission |
| `test_matching.py` | 39 | Contract equivalence and dangerous near-match rejection |
| `test_polymarket_client.py` | 8 | Client parsing, missing data, retries, and response limits |
| `test_polymarket_discovery.py` | 23 | Pagination, quote meaning, resolution, and invalid values |
| `test_research.py` | 15 | Query bounds, source safety, identity, corroboration, and malformed results |
| `test_sports.py` | 78 | Team resolution, event identity, filters, cursors, lifecycle, settlement, and partial records |
| **Total** | **258** | **Distinct unit-level behavior cases** |

The audit removes `tests/test_smoke.py`; importing the package and comparing its version constant did
not test product behavior. It also removes assertions that searched documentation for retired names
or historical evaluation numbers. Documentation tests now protect only current setup, architecture,
milestone, safety, and manifest claims.

## Verification results

Run from the repository root on Windows with Python 3.12 through `uv`:

| Command | Observed result |
|---|---|
| `uv run pytest tests/unit -q` | 258 passed |
| `uv run pytest -m "not live_smoke" -q` | 389 passed, 16 deselected |
| `uv run pytest tests/live/test_chat_live.py::test_live_no_tool_recall_isolation -q` through the local Codex gateway | 1 passed |
| `uv run ruff check .` | Passed |
| `uv run ruff format --check .` | Passed |
| `uv run mypy src` | Passed for 23 source files |
| `git diff --check` | Passed |

The deterministic run emits one dependency deprecation warning from Starlette's `TestClient`
typing alias. It does not affect request execution or test results.

The headless Codex check exercises the real FastAPI and LangGraph application path, same-session
recall, cross-session isolation, and MCP discovery. The public-provider smoke suite remains opt-in
because provider availability and current event inventory are not deterministic. Passing fixture
tests establishes local failure behavior and data contracts; it does not claim that every external
provider is currently available.
