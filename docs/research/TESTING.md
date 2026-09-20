# Testing and verification

This document defines the verification strategy and the evidence each test level provides.

## Local quality gate

```powershell
uv run pytest -m "not live_smoke"
uv run ruff check .
uv run ruff format --check .
uv run mypy src
git diff --check
```

The offline suite uses deterministic HTTP fixtures and scripted model replies. Real MCP
sessions and subprocesses still execute: only upstream HTTP and model decisions are controlled.
No test requires a particular game to be open.

## Test map

| Tests | Contract being verified |
|---|---|
| `unit/test_sports.py` | Exact aliases, common names, same-city teams, college ambiguity, wrong opponents, sibling exclusion, doubleheaders, coverage, budgets, cancellation, malformed data, quote semantics |
| `integration/test_sports_mcp.py` | Actual tools/list schemas and tools/call results, invalid limit rejection, clarification without HTTP, safe errors, agent schema consumption |
| `live/test_sports_mcp_live.py` | Independent stdio processes, current public API compatibility, searches across MLB/NFL/NCAA, detail retrieval when a candidate exists |
| Provider unit tests | Parsing, status, identity, arrays, retries, transport and HTTP errors |
| MCP integration tests | Discovery/detail, input/output schema validation, oversized data, lifecycle and partial server availability |
| `unit/test_matching.py` | Deterministic matching and conservative ambiguity |
| `integration/test_chat.py`, `integration/test_stdio.py` | Graph control flow, memory, real adapter/subprocess invocation, dependency failure |

The sports suite specifically verifies:

- Yankees/Padres discovery without series knowledge; NFL matchup aliases; college abbreviations.
- New York Y/M and Michigan/Michigan State remain distinct.
- OSU, USC, shared cities, unknown opponents, and unsupported requested contract types require
  clarification instead of silent substitution.
- A wrong opponent, unrelated sibling, spread, total, or future cannot fill a sports result limit.
- Doubleheaders keep separate event IDs and expose event alternatives.
- Kalshi NCAA scopes share one page budget; pagination cycles stop.
- Polymarket can recover a game through league metadata/catalog fallback after empty text search.
- Gamma search totals retain their actual meaning even when fallback finds a contract.
- Named-team snapshot prices differ from last trades; YES quotes are not assigned to named outcomes.
- Scheduled start remains separate from later trading close and resolution timing.
- Conflicting identities, timestamps, duplicate event ownership, and nonfinite prices fail.
- External cancellation propagates; provider failures remain controlled tool errors.
- The agent receives the sports schema through real MCP and validates it.

## Public checks

```powershell
$env:RUN_LIVE_SMOKE = "1"
uv run pytest tests/live/test_sports_mcp_live.py tests/live/test_market_clients_live.py tests/live/test_kalshi_mcp_live.py tests/live/test_polymarket_mcp_live.py -s
Remove-Item Env:RUN_LIVE_SMOKE
```

These are bounded public reads. Sports tests check all supported leagues, schema discovery,
`limit=20` rejection, coverage bounds, and details for returned candidates.
An empty current result is valid; exact historical/open-game identity belongs in fixtures.

## What each verification level establishes

| Level | Establishes | Does not establish |
|---|---|---|
| Unit | Deterministic parsing and invariants | Current provider availability |
| Fixture MCP | Real protocol schemas, calls, errors | Public API compatibility |
| Scripted agent + MCP | Host schema consumption and graph behavior | Real model tool-selection quality |
| Live provider/MCP | Current public responses and subprocess compatibility | Exhaustive coverage or future uptime |
| Real model | Observed semantic selection and synthesis | Formal contract equivalence |
| Container/deployment | Behavior in the tested runtime | Behavior in a runtime not tested |

Real-model checks use `RUN_LIVE_AGENT=1` and a configured backend with
`tests/live/test_chat_live.py`. They can incur model costs and are separate from provider checks.

Cloud Run acceptance remains required: verify the deployed URL, arbitrary reasonable queries,
session recall, both MCP servers, and controlled failure cases. Local tests do not establish
a completed deployment.

## Expansion gate

An additional sport or contract type remains disabled until verification covers:

- Typed input/output schemas and rejection of unsupported combinations.
- Provider taxonomy, identity, pagination, schedule, price, and rule mappings in fixtures and
  bounded live reads.
- Contract-specific settlement behavior, including pushes, voids, postponements, overtime,
  stat corrections, withdrawal, or elimination where relevant.
- Exact event, outcome, subject, and line matching plus dangerous near-match rejections.
- Real MCP discovery/calls, real-model tool selection, session follow-ups, and deployed behavior.

Catalog-only changes do not satisfy this gate. `sports_teams.json` supplies reviewed identity
and alias data, not market identifiers or runtime support for another schema value.

## Protocol and data invariants

The MCP schemas declare strict integer limits from 1 through 10. A rejected
`limit=20` request demonstrates enforcement, not absence of constraints.

The code uses bounded pagination, duplicate guards, explicit Polymarket settlement
metadata, finite-price checks, correct null YES quotes for named/reversed outcomes, and
detail-response identity validation. Regression tests verify these properties against current
code. Test counts alone do not establish coverage of a particular behavior.

## Performance evidence

Any latency or bandwidth claim requires a repeatable workload, environment, provider state,
sample size, baseline, and measured results. Logical request counts establish a budget, not
an observed performance improvement.
