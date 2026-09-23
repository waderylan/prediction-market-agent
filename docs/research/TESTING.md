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
| `unit/test_sports.py` | Exact aliases, two-team provider queries, local dates/ranges, selectors, grouping, continuation, lifecycle, settlement, coverage, cancellation, malformed data, quote semantics |
| `integration/test_sports_mcp.py` | Actual tools/list schemas and tools/call results, invalid limit rejection, clarification without HTTP, safe errors, agent schema consumption, typed cross-market sports report |
| `unit/test_game_state.py` | Game identity/state parsing, lifecycle, situation nulls/bounds, refs, box/player projections, cache, retry/size limits, cancellation, MLB fallback |
| `integration/test_game_state_mcp.py` | Third server tools/list and tools/call, strict state/box/player schemas, errors, agent routing, market/game identity, settlement separation |
| `live/test_game_state_mcp_live.py` | Current ESPN/MLB compatibility through the independent stdio process across all three leagues |
| `live/test_sports_mcp_live.py` | Independent stdio processes, current public API compatibility, searches across MLB/NFL/NCAA, detail retrieval when a candidate exists |
| `unit/test_research.py` | Fixed Tavily request shape, local-date query construction, authority ordering, key/keyless authentication, game/date/focus filtering, URL safety, provenance, sanitization, malformed data |
| `integration/test_tavily_mcp.py` | Fourth-server tools/list and tools/call, strict schema, typed evidence, safe provider errors, invalid-input rejection |
| `live/test_tavily_mcp_live.py` | Current keyless Tavily compatibility through the independent stdio MCP process |
| Provider unit tests | Parsing, status, identity, arrays, retries, transport and HTTP errors |
| MCP integration tests | Discovery/detail, input/output schema validation, oversized data, lifecycle and partial server availability |
| `unit/test_matching.py` | Generic and sports-aware deterministic matching, typed market/game identity, dangerous near-matches, conservative ambiguity |
| `integration/test_chat.py`, `integration/test_stdio.py` | Graph control flow, memory, real adapter/subprocess invocation, dependency failure |

The sports suite specifically verifies:

- Yankees/Padres discovery without series knowledge; NFL matchup aliases; college abbreviations.
- New York Y/M and Michigan/Michigan State remain distinct.
- OSU, USC, shared cities, unknown opponents, and unsupported requested contract types require
  clarification instead of silent substitution.
- A wrong opponent, unrelated sibling, spread, total, or future cannot fill a sports result limit.
- Doubleheaders keep separate event IDs and expose event alternatives.
- Local dates cross UTC boundaries correctly; invalid IANA zones and conflicting date inputs fail.
- A sports limit counts games, each game groups its returned outcome contracts, and next/recent
  selectors choose by scheduled start.
- Opaque continuation cursors resume Kalshi cursor and Polymarket page traversal directly.
- Cursor provider/query/filter mismatches return distinct stable codes before provider I/O.
- Invalid timezones, conflicting dates, reversed ranges, and conflicting selectors return their
  offending fields and values before provider I/O.
- Kalshi NCAA scopes share one page budget; pagination cycles stop.
- Polymarket can recover a game through league metadata/catalog fallback after empty text search.
- Polymarket sends both resolved teams using MLB full names, NFL nicknames, or college school names;
  a prior same-matchup game outside the requested local date cannot displace the requested game.
- Gamma search totals retain their actual meaning even when fallback finds a contract.
- Named-team snapshot prices differ from last trades; YES quotes are not assigned to named outcomes.
- Scheduled start remains separate from later trading close and resolution timing.
- Lifecycle moves through pregame, live, awaiting resolution, and settled without treating an
  exchange's open flag as proof that a completed game is live.
- Search/detail reuse one explicit `observation_id` inside the 30-second cache window and expose
  cache hits/age. A missing provider quote clock leaves `quote_as_of` null and stale with a reason.
- Malformed individual provider records are discarded with bounded warnings while valid siblings
  remain available; malformed page roots still fail safely.
- Local timezone/date/start metadata survives search-to-detail projection.
- Conflicting identities, timestamps, duplicate event ownership, and nonfinite prices fail.
- External cancellation propagates; provider failures remain controlled tool errors.
- Injected timeout, 429, 503, and malformed-response transports exercise outage behavior without a
  production failure switch; a platform-specific failure never substitutes the other provider.
- The agent receives the sports schema through real MCP and validates it.
- Market detail preserves typed event identity and named outcomes through the host matching path.
- Equivalent full-game-winner fixtures pass without sports-state data; missing settlement evidence
  stays ambiguous and explicit identity, outcome, or settlement conflicts are rejected.
- Current NFL/MLB rule templates distinguish wait-until-complete from bounded postponement windows
  and 50/50 cancellation payouts from fair-price payouts.
- Wrong opponents, different game numbers, start drift beyond 30 minutes, and cancellation payout
  conflicts remain distinct even with similar titles.

The game-state suite additionally verifies:

- One exact local day, derived-date disclosure, and at most three UTC-boundary scoreboard reads.
- Reviewed team resolution, NCAA clarification without I/O, doubleheader-safe event IDs, and
  checksummed references that reject edits before I/O.
- Scheduled, pregame, live, halftime, delayed, suspended, postponed, cancelled, final, and unknown
  lifecycle normalization without using score as phase evidence.
- Nullable football/baseball situations, impossible-value rejection, bounded last play, and no raw
  provider payload.
- Two-attempt retry policy, 10-second HTTP attempts, 30-second tool budget, 5 MiB response limit,
  200-event pages, ten candidates, external cancellation, and 256-entry lifecycle-aware cache.
- ESPN transport/HTTP/schema failures, no NFL/NCAA substitution, exact MLB schedule/live-feed
  fallback, ambiguous doubleheader refusal, response identity conflicts, and provider conflicts.
- Typed host validation of league/participants/start/provider references before market/state
  combination, plus matching/conflicting state, unavailable state, and an enforced separation
  between final score, contract equivalence, and prediction-market settlement.
- Exact-reference box scores for MLB, NFL, and NCAA football through unit, MCP, stdio, and agent
  paths; the tool accepts no selector beyond `game_ref`.
- Compact available-player lookup and single-player game-stat detail for every supported league;
  stable player IDs bind selection, unrelated player lines stay out of detail, and list/detail
  reuse one box-score observation inside the cache window.
- MLB inning null semantics, team totals, game-only batting/pitching fields, stable player IDs,
  integer outs normalization, ESPN partial-field reporting, and exact MLB StatsAPI fallback.
- Football period scoring, named team statistics, categorized stable-player statistics, completed
  games, live cache bounds, content-derived observation IDs, and omission of unavailable fields.

The Tavily suite additionally verifies:

- One game-scoped tool; no arbitrary query, extraction, crawl, map, or research surface.
- Exact typed league/team/date/start context before provider I/O and a hard two-search graph budget.
- Five-result request bounds, HTTPS-only sources, participant/date relationship checks, duplicate
  and private-address rejection, focus-relevance checks, bounded snippets, publication/retrieval
  provenance, and no raw provider payload.
- A local game date is never paired with the UTC clock from a different calendar day; retained
  league/major-media/other sources follow the declared authority ordering.
- Keyless and optional bearer-key paths without key/error leakage.
- Malformed, rate-limited, unavailable, and empty-result behavior through real MCP calls.
- Deterministic source listing and useful synthesis from existing market evidence when Tavily fails.

## Public checks

```powershell
$env:RUN_LIVE_SMOKE = "1"
uv run pytest tests/live/test_game_state_mcp_live.py tests/live/test_sports_mcp_live.py tests/live/test_tavily_mcp_live.py tests/live/test_market_clients_live.py tests/live/test_kalshi_mcp_live.py tests/live/test_polymarket_mcp_live.py -s
Remove-Item Env:RUN_LIVE_SMOKE
```

These are bounded public reads. Sports tests check all supported leagues, schema discovery,
`limit=20` rejection, coverage bounds, and current-state, box-score, player-list, and single-player
detail for returned candidates.
An empty current result is valid; exact historical/open-game identity belongs in fixtures.

A real-market replay over ten prior-day NCAA games and ten current-day games from each of NFL and
MLB produced 60 cross-platform pairs. Every pair was rejected by deterministic settlement checks,
so no cross-platform comparison was allowed. The later
[Milestone 8A evaluation](MATCHING_VALUE_EVALUATION.md) measured deterministic, semantic, forced,
and simple-baseline configurations on 15 complete held-out real pairs. That decision removed Jev
and reduced matching to the proven deterministic subset.

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
session recall, all four MCP servers, and controlled failure cases. Local tests do not establish
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

The MCP schemas declare strict integer limits from 1 through 10. A sports limit counts games;
generic search limits contracts. A rejected `limit=20` request demonstrates enforcement, not
absence of constraints.

The code uses bounded pagination, duplicate guards, explicit Polymarket settlement
metadata, finite-price checks, correct null YES quotes for named/reversed outcomes, and
detail-response identity validation. Regression tests verify these properties against current
code. Test counts alone do not establish coverage of a particular behavior.

## Performance evidence

Any latency or bandwidth claim requires a repeatable workload, environment, provider state,
sample size, baseline, and measured results. Logical request counts establish a budget, not
an observed performance improvement.
