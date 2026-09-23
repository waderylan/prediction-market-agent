# Sports game-state MCP

## Product boundary

The student-authored `sports_state` stdio process answers exact-game questions about current state,
team performance, individual player performance, and chronological plays. It supports MLB, NFL, and NCAA
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

Inputs:

- `game_ref`: one opaque value copied unchanged from discovery. Team names, dates, ESPN IDs, MLB
  IDs, and provider selectors are not accepted.
- `view`: `summary` by default; `full`, `line_score`, `batting`, `pitching`, `team_stats`,
  or `player_stats` when applicable to the sport.
- `team_side`: `both` by default; `away` or `home` narrows team/player sections.

The result shares exact game identity, source URL, lifecycle, teams, scores, observation time and
ID, cache metadata, warnings, and an explicit `completeness` object with the state tool. The
default summary contains the line score and compact provider-backed leaders plus a follow-up tip
for full and section-specific layouts. The full normalized observation remains in the cache for
player lookup and later section requests. Results are sport-discriminated:

- MLB returns `line_score.innings`, team runs/hits/errors/left-on-base when supplied, `batting`
  by team, and `pitching` by team. Batter lines carry stable provider player IDs and game-only
  lineup, position, and counting statistics. Pitcher lines carry stable provider IDs, appearance
  order, `outs_recorded`, provider `innings_pitched_display`, and game-only counting statistics.
- NFL and NCAA football return `line_score.periods`, named team statistics, and player statistics
  grouped by provider categories such as passing, rushing, receiving, defense, returns, kicking,
  and punting. The same football model applies to both leagues.

Optional statistics absent from a provider response are omitted rather than serialized as
permanent null fields. `completeness` reports each section as `complete`, `partial`, or
`unavailable` and names exact missing required and optional fields. Optional omissions do not
make a section partial. Pregame player arrays remain empty and unavailable. The parser never fills
game fields with season statistics.

Every baseball inning half carries `played`, `not_played`, `not_reached`, or `unknown`
participation. A final unnecessary home half is `not_played` and renders as X without making the
line score partial; zero means a played scoreless half.
Pitching calculations use `outs_recorded`: display `1.1` means four outs, not 1.1 mathematical
innings.

The endpoint includes no play-by-play or pitch history. Current score, inning/count/runners and
active players belong to `sports_state_get_game_state`; chronological events belong to
`sports_state_get_play_by_play`. Tavily is never a source for structured game statistics.

### `sports_state_list_players`

Input:

- `game_ref`: one opaque value copied unchanged from discovery.

The result is a compact lookup directory derived from the same normalized box-score observation.
Each entry contains a stable provider `player_id`, name, canonical team, home/away side, and the
available game-stat groups. Baseball groups are batting and pitching; football groups retain the
provider categories such as passing, rushing, receiving, defense, returns, kicking, and punting.

The directory contains only players with provider-backed game-stat lines. It is not an active,
pregame, depth-chart, or season roster. Duplicate appearances across football categories or a
baseball batting/pitching role are collapsed under one player ID. Conflicting name or team identity
for one ID rejects the response instead of merging it.

### `sports_state_get_player_stats`

Inputs:

- `game_ref`: one opaque value copied unchanged from discovery.
- `player_id`: one stable identifier copied unchanged from `sports_state_list_players` for that
  same game reference.

The result contains the exact game identity and provenance plus only the selected player's game
statistics. MLB returns batting and/or pitching lines. NFL and NCAA football return categorized
stat groups for that player. Unrelated player rows, team aggregates, line scoring, season fields,
and play-by-play are absent. Invalid player-ID syntax is rejected before provider I/O;
`player_not_found` identifies a valid ID that has no line in the exact game.

Player listing and detail reuse the box-score request, lifecycle-aware cache, exact identity
validation, completeness state, warnings, fallback policy, retrieval time, and observation ID. A
list-followed-by-detail flow normally performs one provider summary request inside the cache window.

### `sports_state_get_play_by_play`

Inputs:

- `game_ref`: one opaque value copied unchanged from discovery.
- `limit`: integer 1-50, default 20.
- `before_play_id`: optional stable ID used to page toward earlier plays.
- `after_play_id`: optional stable ID used to request only later, unseen plays.
- `play_filter`: `all` or `scoring`, default `all`.
- `period`: optional period or inning number, 1-30.
- `team`: optional `away` or `home` side filter.

`before_play_id` and `after_play_id` are mutually exclusive. With neither anchor, the tool returns
the latest matching window. Every returned window remains chronological. `first_play_id` and
`next_before_play_id` support backward inspection; `last_play_id` and `resume_after_play_id`
provide a checkpoint for a later call. `has_earlier` and `has_later` describe matching plays outside
the window. An anchor must identify a play in the exact current game observation, including when
other filters exclude that anchor from the returned subset.

Each play includes its stable provider-backed ID, feed sequence, period, clock, bounded text,
event kind, scoring flag, supplied score, attributed team/side, wall-clock time when available,
and one sport-specific context object. MLB context can include pitch count, `outs_before`,
`outs_after`, and at-bat ID. The out fields make post-action state explicit rather than
presenting it as batter-start state. Provider-labeled substitutions use
`event_kind="substitution"` and a separate structured participant payload when supplied; they are
not plate appearances. Football context can include down, distance, field position, yards,
turnover, and penalty. Optional provider fields stay null; participant names come only from
structured provider fields.

ESPN supplies pitch/action granularity for MLB and play granularity for NFL and NCAA football. An
exact-identity MLB StatsAPI fallback supplies at-bat granularity after ESPN transport, HTTP, or
schema failure. `granularity` exposes that distinction. The service normalizes at most 1,000
provider plays and returns at most 50. Malformed individual plays are discarded with bounded
warnings; malformed identity, participants, lifecycle, or the enclosing response rejects the
feed. Raw provider payloads, win probability, odds, and season statistics are absent.

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
| Provider plays normalized | 1,000 |
| Plays returned | 50 |
| Cache | Separate 256-entry normalized state, box-score, and play-feed caches |

External cancellation propagates. No background request, polling task, WebSocket, or provider
disconnect operation exists.

## Cache semantics

The detail caches use the provider's `Cache-Control: max-age` when present. State caps are:

- 5 seconds for live, halftime, delayed, suspended, or unknown state.
- 30 seconds for scheduled or pregame state.
- 5 minutes for final, postponed, or cancelled state.

Box-score caps are 10 seconds for live, halftime, delayed, suspended, or unknown games; 30 seconds
for scheduled or pregame games; and 5 minutes for final, postponed, or cancelled games. Play-feed
caps are 5 seconds for live, halftime, delayed, suspended, or unknown games; 30 seconds for
scheduled or pregame games; and 5 minutes for final, postponed, or cancelled games.

Each normalized state receives an observation ID. Box-score and play-feed observation IDs are
deterministic hashes of their normalized provider snapshots, so unchanged data retains its ID
after cache expiry and an underlying statistics or play change produces a new ID. Different play
windows and filters over one cached feed retain the feed observation ID. A cache hit retains the
original `retrieved_at` and observation ID and changes only `cache_hit` and `cache_age_ms`. Cache
expiry causes a new provider observation; data is never restamped merely because it was read again.

## Stable errors

MCP tool errors contain compact JSON with `error.code`, safe `message`, and caller-correctable
`fields`. Current codes include:

- `invalid_timezone`, `invalid_limit`, and `invalid_game_ref`.
- `provider_unavailable` and `tool_timeout`.
- `malformed_response`, `response_too_large`, `impossible_state`, and
  `unknown_provider_team`.
- `response_identity_mismatch`.
- `mlb_fallback_ambiguous`, `game_state_unavailable`, and `box_score_unavailable`.
- `invalid_player_id` and `player_not_found`.
- `invalid_play_limit`, `conflicting_play_anchors`, `invalid_play_id`, `invalid_play_filter`,
  `invalid_period`, `invalid_team_filter`, `play_not_found`, and `play_by_play_unavailable`.

Transport/HTTP failure, tool execution error, and malformed/schema-invalid response are tested
independently. Error text excludes provider bodies, prompts, credentials, and opaque references.
Operational logs go to stderr and contain only operation, league, status, latency, cache status,
result count where available, and sanitized error class. Stdout remains MCP protocol traffic.

## Agent consumption

The model selects game-state tools semantically. `sports_state_get_game_state` answers score,
lifecycle, and current-situation requests. `sports_state_get_box_score` uses its summary view for
ordinary requests, then offers the full layout or an available section. Explicit full and focused
requests select the corresponding view and team side. For one player's game statistics, the model
lists players, resolves the
requested name to a returned ID, then requests only that player's detail. It must discover before
detail calls and ask the user to select when multiple games or same-name player choices remain
plausible. For chronological events, it requests a bounded play window, retains the returned play
IDs as checkpoints, pages backward with `before_play_id`, and resumes live inspection with
`after_play_id`. Scoring, period, and team filters narrow the provider-backed feed without prose
search or a full-box-score transfer.

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
season-stat exclusion, summary/full/section box-score projections, skipped home halves,
required/optional field coverage, the shared NFL/NCAA football box-score model, compact player
selection, multi-category player aggregation, missing-player errors, stable play IDs,
pre/post-event outs, structured substitutions, backward and forward play windows, focused play
filters, malformed-play isolation, MLB at-bat fallback, and cache/observation reuse.

Real MCP tests exercise `tools/list`, strict schemas, `tools/call`, discovery-before-detail, stable
errors, host validation, semantic routing, no-tool behavior, market/game identity checks, and an
independent stdio process. Opt-in public checks query all three leagues and retrieve exact state,
box scores, player directories, individual player lines, and bounded play windows when a current
game is available; an empty slate is valid. Provider checks cover completed MLB, NFL, and NCAA
football games plus live MLB. ESPN can omit individual fields during transitions, and its
undocumented schema may change without notice.
