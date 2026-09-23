# Sports game-state MCP

## Product boundary

The student-authored `sports_state` stdio process answers two exact-game questions: what is
happening now, and how are the teams and players performing? It supports MLB, NFL, and NCAA
Division I football. It does not expose odds, win probability, projections, news, standings,
season statistics, prediction-market contracts, settlement, trading, accounts, polling, or
notifications.

Game state is independent evidence. Even `lifecycle="final"` establishes only the attributed
sporting observation. It never proves that a Kalshi or Polymarket contract is equivalent,
resolved, or settled to a particular outcome.

## Free provider boundary

| Source | Fixed routes | Role |
|---|---|---|
| ESPN public JSON | `site.api.espn.com/apis/site/v2/sports/{sport}/{league}/scoreboard` and `/summary` | Primary discovery and detail for all supported leagues |
| MLB StatsAPI | `statsapi.mlb.com/api/v1/schedule` and `/api/v1.1/game/{gamePk}/feed/live` | MLB-only detail fallback after ESPN failure and exact identity matching |

Both sources are unauthenticated and free for the bounded reads used here. ESPN's surface is
undocumented and has no SLA. MLB StatsAPI supplies no fallback for NFL or NCAA football. The
client uses `httpx` without a spoofed browser identity; a 403 is controlled provider
unavailability, not a reason to rotate identities or evade access controls.

Provider URLs are code-owned allowlists. No tool accepts a URL, host, path, provider name, HTTP
header, credential, or arbitrary fetch target. No new environment variable is required.

## MCP tools

### `sports_state_find_games`

Required inputs:

- `query`: exact supported team or matchup text, or reserved exact value `all` for a bounded
  same-day slate; 1-200 characters.
- `league`: `mlb`, `nfl`, or `ncaa_football`.
- `timezone`: IANA timezone used for the requested local calendar day.

Optional inputs:

- `local_date`: one exact local date. If omitted, the server derives today in `timezone` and
  returns that date with `coverage.derived_local_date=true`.
- `limit`: integer 1-10, default 5. It counts games.
- `compact`: boolean, default false. When true, each game contains only teams, UTC/local start,
  lifecycle, and the opaque reference needed for detail; league/timezone/date remain at result root.

The tool reuses the reviewed market-team catalog and exact alias resolver. Ambiguous NCAA names,
unknown opponents, conflicting leagues, and unsupported query residue return clarification,
machine-ready `suggested_queries`, and an exact example retry call while performing no provider
request. Use the reserved exact query `all` for a bounded same-day league slate. It returns at most
the requested limit and warns when truncated; it does not offer date ranges, season scans,
next/recent selectors, pagination, or exhaustive mode.

Clarifications use `discovery_mode="clarification"`, distinct from successful `team` and `schedule`
discovery. Coverage explicitly marks `utc_boundary_check=true` when multiple ESPN UTC date pages
were required to cover the one requested local calendar day.

Each returned summary contains canonical and raw home/away names, ESPN event ID, opaque
`game_ref`, UTC/local start, score, lifecycle, period/clock, bounded last play, source URL,
observation identity/time, cache fields, and bounded warnings. Results never include a raw provider
payload. ESPN's observed `-1` football-down transition sentinel is treated as unavailable and
returned as null with a warning; other values outside 1-4 reject detail.

Discovery is explicitly labeled as a lightweight scoreboard snapshot for choosing a game. Detail
is authoritative for normalized state fields. For `scheduled` and `pregame` games, provider
placeholders such as `0-0`, period `1`, clock `0:00`, top of inning, count, bases, and last play are
normalized to null; baseball `half` remains the explicit non-null enum value `unknown`.
Live discovery and detail are independent observations rather than a transactional snapshot, so
scores and last-play text may legitimately advance or disappear between calls.

### `sports_state_get_game_state`

Input:

- `game_ref`: one opaque value copied unchanged from discovery.

The tool returns the common summary fields plus exactly one situation object:

- MLB: `sport="baseball"`, semantic `phase`, inning/half, balls, strikes, outs, three
  base-occupancy flags, batter, and pitcher. `phase` is `not_started`, `active`, `transition`,
  `complete`, or `unavailable`; it explains why situation fields may be null.
- NFL/NCAA football: `sport="football"`, possession team, down, distance, field position,
  red-zone flag, home/away timeouts, and provider down-distance label.

Every situation field except the sport discriminator and baseball phase is nullable. Transitions,
halftime, replay, delay, and provider update races legitimately omit fields. The parser does not
derive possession from last-play team, infer bases from prose, or calculate unavailable
timeout/red-zone state.

Baseball invariants prevent contradictory state: `not_started` has no inning/count/base/player
values and `half="unknown"`; `active` requires a positive inning and top/bottom half, with balls
0-3, strikes 0-2, and outs 0-2. Provider terminal-count sentinels or incomplete live inning data
are labeled `transition` rather than misrepresented as an active plate appearance.

ESPN base occupancy remains null when the current situation supplies no base keys. When at least
one base key is present, present bases are occupied and omitted sibling bases are empty. Current
batter and pitcher `playerId` values are resolved only through explicit names in the same bounded
summary box score or roster; unresolved or conflicting IDs remain null.

### `sports_state_get_box_score`

Input:

- `game_ref`: one opaque value copied unchanged from discovery. Team names, dates, ESPN IDs, MLB
  IDs, provider selectors, modes, and projection controls are not accepted.

The result shares exact game identity, source URL, lifecycle, teams, scores, observation time and
ID, cache metadata, warnings, and an explicit `completeness` object with the state tool. It is
sport-discriminated:

- MLB returns `line_score.innings`, team runs/hits/errors/left-on-base when supplied, `batting`
  by team, and `pitching` by team. Batter lines carry stable provider player IDs and game-only
  lineup, position, and counting statistics. Pitcher lines carry stable provider IDs, appearance
  order, `outs_recorded`, provider `innings_pitched_display`, and game-only counting statistics.
- NFL and NCAA football return `line_score.periods`, named team statistics, and player statistics
  grouped by provider categories such as passing, rushing, receiving, defense, returns, kicking,
  and punting. The same football model applies to both leagues.

Optional statistics absent from a provider response are omitted rather than serialized as
permanent null fields. `completeness` reports each section as `complete`, `partial`, or
`unavailable`; `is_partial` and warnings make omissions visible. Pregame player arrays remain
empty and unavailable. The parser never fills game fields with season statistics.

Inning scoring is the intentional exception to omission semantics. A null home-inning value in
the top half means the home team has not batted; zero means it completed the inning without a run.
Pitching calculations use `outs_recorded`: display `1.1` means four outs, not 1.1 mathematical
innings.

The endpoint includes no play-by-play or pitch history. Current score, inning/count/runners and
active players belong to `sports_state_get_game_state`; chronological events belong to a separate
play surface when the product needs one. Tavily is never a source for structured game statistics.

## Identity and opaque references

Discovery references carry only version, source namespace, league, ESPN event ID, scheduled start,
requested timezone, and canonical home/away teams. Canonical JSON is wrapped with a
domain-separated SHA-256 checksum and URL-safe base64 encoding.

Because requested timezone is required identity context, rediscovering the same ESPN event in a
different timezone intentionally produces a different `game_ref`. Compare the explicit source,
league, provider event ID, participants, and UTC start when recognizing the same real-world event;
never compare opaque token text or cross-use references between discovery contexts.

The checksum detects truncation and common model edits; it is not authentication. References need
no deployment secret and remain valid across subprocess or Cloud Run restarts. Before provider
I/O, detail rejects invalid encoding, envelope/schema keys, version, source, checksum, event ID,
timezone, catalog team, or duplicate participant.

ESPN detail must match event ID, league route, both home/away participants, requested local date,
and scheduled start within 30 minutes. Identity conflicts never invoke fallback. An ESPN market ID,
raw event ID, MLB `gamePk`, or constructed token is not a valid detail input.

For MLB fallback, one date-scoped schedule response is filtered by canonical home/away teams. One
same-team candidate is accepted. Multiple candidates require exactly one start within 30 minutes;
otherwise the server returns `mlb_fallback_ambiguous`. The exact returned `gamePk` alone is then
used for one live-feed request, whose teams/date/start are revalidated.

## Lifecycle and field provenance

Explicit provider status code/name/state determines lifecycle before display text:

- `scheduled`, `pregame`, `live`, `halftime`, `delayed`, `suspended`, `postponed`, `cancelled`,
  `final`, or `unknown`.

Display text only refines compatible states such as halftime. Score never determines lifecycle.
Nonzero does not imply live, and 0-0 does not imply scheduled.

`retrieved_at` is when this service observed the response. It is never relabeled as a provider
update clock. `provider_updated_at` is omitted from box scores unless the provider supplies an
authoritative state-update timestamp. Scores are nonnegative integers or unavailable. Impossible down,
outs, count, score, participant, or identity values reject exact detail. In a safe scoreboard
envelope, malformed sibling events are skipped with bounded discard warnings.

## Request and size budgets

| Operation | Bound |
|---|---|
| Discovery | One requested-date ESPN scoreboard; adjacent UTC boundary pages only when no match, maximum 3 total |
| ESPN detail | One summary request |
| MLB fallback | After ESPN transport/HTTP/schema failure only: one date schedule + one exact live feed |
| Attempts | Maximum 2 per logical request |
| Retry classes | Transport, HTTP 429, and HTTP 5xx only |
| No retry | Other 4xx, malformed JSON/schema, oversized data, identity conflict |
| HTTP timeout | 10 seconds per attempt |
| MCP wall clock | 30 seconds per tool |
| Response size | 5 MiB |
| Scoreboard events | 200 per page |
| Returned games | 10 |
| Warnings | 20 |
| State text | 1,000 characters per bounded field |
| Cache | Separate 256-entry normalized state and box-score caches |

External cancellation propagates. No background request, polling task, WebSocket, or provider
disconnect operation exists.

## Cache semantics

The detail caches use the provider's `Cache-Control: max-age` when present. State caps are:

- 5 seconds for live, halftime, delayed, suspended, or unknown state.
- 30 seconds for scheduled or pregame state.
- 5 minutes for final, postponed, or cancelled state.

Box-score caps are 10 seconds for live, halftime, delayed, suspended, or unknown games; 30 seconds
for scheduled or pregame games; and 5 minutes for final, postponed, or cancelled games.

Each normalized state receives an observation ID. A box-score observation ID is a deterministic
hash of the normalized provider snapshot, so unchanged data retains its ID after cache expiry and
an underlying scoring or statistics change produces a new ID. A cache hit retains the original
`retrieved_at` and observation ID and changes only `cache_hit` and `cache_age_ms`. Cache expiry
causes a new provider observation; data is never restamped merely because it was read again.

## Stable errors

MCP tool errors contain compact JSON with `error.code`, safe `message`, and caller-correctable
`fields`. Current codes include:

- `invalid_timezone`, `invalid_limit`, and `invalid_game_ref`.
- `provider_unavailable` and `tool_timeout`.
- `malformed_response`, `response_too_large`, `impossible_state`, and
  `unknown_provider_team`.
- `response_identity_mismatch`.
- `mlb_fallback_ambiguous`, `game_state_unavailable`, and `box_score_unavailable`.

Transport/HTTP failure, tool execution error, and malformed/schema-invalid response are tested
independently. Error text excludes provider bodies, prompts, credentials, and opaque references.
Operational logs go to stderr and contain only operation, league, status, latency, cache status,
result count where available, and sanitized error class. Stdout remains MCP protocol traffic.

## Agent consumption

The model selects game-state tools semantically. `sports_state_get_game_state` answers score,
lifecycle, and current-situation requests. `sports_state_get_box_score` answers inning or period
scoring, team totals, and player game-stat requests. It must discover before either detail call and
ask the user to select when multiple games remain plausible.

When market and game detail coexist, the host independently checks league, both participants,
scheduled start within 30 minutes, and provider-backed references. The model receives typed
`matching_report.market_to_game` assessments whose dimensions and overall verdict are `match`,
`different`, or `insufficient_evidence`; only an overall `match` permits the observations to be
discussed as the same scheduled game. Each assessment includes the sports source and observation
time. This report cannot certify equivalent contracts or market settlement.

Every response that used exact game detail receives a code-generated notice naming source and
observation time and stating that sporting result does not establish market settlement or contract
equivalence.

## Verification and observed limitations

Deterministic tests cover all lifecycle values, exact aliases, NCAA ambiguity, doubleheaders,
timezone rollover, null situation fields, impossible bounds, reference edits, response identity,
cache identity/TTL, response limits, retries, cancellation, malformed siblings/roots, exact MLB
fallback, fallback ambiguity, provider conflict warnings, MLB batting/pitching normalization,
season-stat exclusion, and the shared NFL/NCAA football box-score model.

Real MCP tests exercise `tools/list`, strict schemas, `tools/call`, discovery-before-detail, stable
errors, host validation, semantic routing, no-tool behavior, market/game identity checks, and an
independent stdio process. Opt-in public checks query all three leagues and retrieve exact state
and box scores when a current game is available; an empty slate is valid. Current provider checks
cover completed MLB, NFL, and NCAA football games plus live MLB. ESPN can omit individual fields
during transitions, and its undocumented schema may change without notice.
