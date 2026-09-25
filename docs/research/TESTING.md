# Testing and verification

This document defines the verification strategy and the evidence each test level provides.

The [Milestone 13 adversarial testing report](ADVERSARIAL_TESTING_REPORT.md) records the failure
matrix, unit-test value audit, and observed local quality-gate results.

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
| `unit/test_game_state.py` | Game identity/state parsing, lifecycle, situation bounds, box views, inning participation, field coverage, pre/post outs, substitutions, cache, fallback |
| `integration/test_game_state_mcp.py` | Third server tools/list and tools/call, strict state/box-view/player/play schemas, errors, agent routing, identity, settlement separation |
| `live/test_game_state_mcp_live.py` | Current ESPN/MLB compatibility through the independent stdio process across all three leagues |
| `live/test_sports_mcp_live.py` | Independent stdio processes, current public API compatibility, searches across MLB/NFL/NCAA, detail retrieval when a candidate exists |
| `unit/test_research.py` | Fixed Tavily request shape, local-date query construction, official-only policy, recap focus, explicit empty results, corroboration cautions, URL safety, provenance, malformed data |
| `integration/test_tavily_mcp.py` | Fourth-server tools/list and tools/call, strict schema, typed evidence, safe provider errors, invalid-input rejection |
| `live/test_tavily_mcp_live.py` | Current keyless Tavily compatibility through the independent stdio MCP process |
| Provider unit tests | Parsing, status, identity, arrays, retries, transport and HTTP errors |
| MCP integration tests | Discovery/detail, input/output schema validation, oversized data, lifecycle and partial server availability |
| `unit/test_matching.py` | Generic and sports-aware deterministic matching, typed market/game identity, dangerous near-matches, conservative ambiguity |
| `integration/test_chat.py`, `integration/test_stdio.py` | Graph control flow, memory, real adapter/subprocess invocation, dependency failure |
| Headless Codex with `.agents/skills/sports-information` | Repository skill discovery and direct routing to the user's configured MCP servers |
| `unit/test_watch_engine.py` | Versioned schemas, confirmation, point thresholds, scoring correlation, stale/out-of-order evidence, leases, restart recovery, SQLite/Firestore parity, outbox retry, Telegram failures, OIDC, Secret Manager |
| `integration/test_watch_cli.py` | Local Codex preview, confirm, inspect, and lifecycle workflow through the shared SQLite service |
| `integration/test_watch_endpoint.py` | Dedicated Scheduler OIDC boundary and the public `/chat` contract |
| `live/test_telegram_live.py` | Opt-in real Telegram send (passes with configured credentials); clean skip without them |

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
  cache hits/age. A missing provider quote clock leaves `quote_as_of` null with
  `quote_freshness="timestamp_unavailable"`; stale requires an old authoritative quote timestamp.
- Sports and generic searches identify `games[].contracts` and `markets[]` respectively through
  result-kind metadata. A close more than 24 hours after scheduled start produces a timing warning.
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
- MLB inning participation, skipped final home halves, team totals, game-only batting/pitching
  fields, stable player IDs, integer outs normalization, required/optional field coverage, summary,
  full, section, and team-side projections, and exact MLB StatsAPI fallback.
- Football period scoring, named team statistics, categorized stable-player statistics, completed
  games, live cache bounds, content-derived observation IDs, and omission of unavailable fields.
- Stable-ID play windows for MLB, NFL, and NCAA football; latest, before, and later-unseen
  navigation; chronological ordering; baseball pre/post-event outs and structured substitutions;
  scoring, period, and team filters; bounded malformed-play warnings; lifecycle-aware caching; and
  exact MLB at-bat fallback.

The Tavily suite additionally verifies:

- One game-scoped tool; no arbitrary query, extraction, crawl, map, or research surface.
- Exact typed league/team/date/start context before provider I/O and a hard two-search graph budget.
- Five-result request bounds, HTTPS-only sources, participant/date relationship checks, duplicate
  and private-address rejection, focus-relevance checks, official-only domain restriction,
  postgame-recap focus, bounded snippets, publication/retrieval provenance, and no raw payload.
- A local game date is never paired with the UTC clock from a different calendar day; retained
  league/major-media/other sources follow the declared authority ordering.
- Keyless and optional bearer-key paths without key/error leakage.
- Malformed, rate-limited, unavailable, explicit no-qualifying-source behavior, and return/activation
  corroboration cautions through real MCP calls.
- Deterministic source listing and useful synthesis from existing market evidence when Tavily fails.

The watch suite additionally verifies:

- Only confirmed version-1 rules persist; another session cannot confirm, inspect, or mutate them.
- Exact eight-point boundaries fire while stale, missing, malformed, and out-of-order evidence does
  not satisfy source-dependent rules.
- `no_tracked_scoring_event` remains a bounded normalized-play statement and blocks when the sports
  source is unavailable.
- Compatible watches share one observation set, a separate coordinator instance resumes durable
  state, and concurrent leases prevent duplicate claims.
- Trigger fingerprints and unique outbox records suppress duplicate logical alerts across retries.
- The foreground replay produces one inbox alert and one fake Telegram projection with zero model
  calls and zero Tavily calls.
- Alert text states each platform's move in points, lists recent plays in game-local time (MLB
  at-bats with an in-progress pitch sequence and count; NFL/NCAA plays with quarter, clock, and
  starting down and distance), and folds duplicate source notes.
- Pause and resume move only between active and paused; a terminal watch cannot be revived.
- Telegram timeout, disconnect, `429`, `5xx`, malformed body, authorization failure, and blocked
  recipient behavior produces bounded retry or sanitized terminal state without losing the inbox.
- Firestore transactions, Google OIDC claims, and Secret Manager access run through controlled
  substitutes. These checks do not establish live Google Cloud behavior.

## Public checks

```powershell
$env:RUN_LIVE_SMOKE = "1"
uv run pytest tests/live/test_game_state_mcp_live.py tests/live/test_sports_mcp_live.py tests/live/test_tavily_mcp_live.py tests/live/test_market_clients_live.py tests/live/test_kalshi_mcp_live.py tests/live/test_polymarket_mcp_live.py -s
Remove-Item Env:RUN_LIVE_SMOKE
```

The Telegram smoke test is separately gated by `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID`:

```powershell
uv run pytest tests/live/test_telegram_live.py -q -s
```

These are bounded public reads. Sports tests check all supported leagues, schema discovery,
`limit=20` discovery rejection, coverage bounds, and current-state, box-score, player-list,
single-player detail, and bounded play-by-play for returned candidates.
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

Direct Codex checks run from the repository with a fresh session:

```powershell
codex exec '$sports-information Find today''s MLB games in UTC. Use sports state only.'
```

This check establishes repository skill discovery and direct MCP behavior. It does not execute the
LangGraph graph, application checkpointer, host-side deterministic matcher, per-turn budgets, or
FastAPI endpoint. Those remain covered by application tests and deployment acceptance.

The deterministic local watch replay requires no provider, model, or Telegram credentials:

```powershell
uv run python scripts/run_watches.py --replay tests/fixtures/watch/yankees_scoring_replay.json
```

Its summary reports the synchronized observation count, boundary trigger decision, inbox record,
fake Telegram projection, model calls, and Tavily calls. The command prints the complete bounded
evidence message so wording and correlation limits remain reviewable.

The replay also performs a duplicate polling cycle, reports unique stored observations, and prints
local polling elapsed time plus the fixture quote-to-trigger interval. Five consecutive local runs
measure 104-118 ms with a 108 ms median for three polls. This is a local deterministic benchmark,
not a provider or deployment latency claim.

The complete non-live suite passes on the local Windows development environment, with live tests
skipped unless their credentials or opt-in flags are set. Ruff lint and formatting, strict mypy
over `src`, JavaScript syntax checking, and `git diff --check` pass.

A fresh headless Codex gateway session covers the conversational path with the configured MCPs. It
clarifies a terminal/ambiguous Yankees request, resolves the next exact Yankees-Rays game, pins one
Kalshi and one Polymarket full-game-winner contract, previews two event-aware thresholds, requires
the draft ID, confirms the rule, and inspects the active stored watch. This is a local LangGraph and
MCP verification; it is not a Cloud Run acceptance result.

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
