# Sports game-state MCP

## Product boundary

The student-authored `sports_state` stdio process answers one narrow question: what is the
provider-reported state of a supported game now? It supports MLB, NFL, and NCAA Division I
football. It does not expose odds, win probability, projections, news, standings, rosters, full
box scores, prediction-market contracts, settlement, trading, accounts, polling, or notifications.

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

- `query`: exact supported team or matchup text, 1-200 characters.
- `league`: `mlb`, `nfl`, or `ncaa_football`.
- `timezone`: IANA timezone used for the requested local calendar day.

Optional inputs:

- `local_date`: one exact local date. If omitted, the server derives today in `timezone` and
  returns that date with `coverage.derived_local_date=true`.
- `limit`: integer 1-10, default 5. It counts games.

The tool reuses the reviewed market-team catalog and exact alias resolver. Ambiguous NCAA names,
unknown opponents, conflicting leagues, and unsupported query residue return clarification and
perform no provider request. It does not offer date ranges, season scans, next/recent selectors,
pagination, or exhaustive mode.

Each returned summary contains canonical and raw home/away names, ESPN event ID, opaque
`game_ref`, UTC/local start, score, lifecycle, period/clock, bounded last play, source URL,
observation identity/time, cache fields, and bounded warnings. Results never include a raw provider
payload. ESPN's observed `-1` football-down transition sentinel is treated as unavailable and
returned as null with a warning; other values outside 1-4 reject detail.

### `sports_state_get_game_state`

Input:

- `game_ref`: one opaque value copied unchanged from discovery.

The tool returns the common summary fields plus exactly one situation object:

- MLB: `sport="baseball"`, inning/half, balls, strikes, outs, three base-occupancy flags,
  batter, and pitcher.
- NFL/NCAA football: `sport="football"`, possession team, down, distance, field position,
  red-zone flag, home/away timeouts, and provider down-distance label.

Every situation field except the sport discriminator is nullable. Transitions, halftime, replay,
delay, and provider update races legitimately omit fields. The parser does not derive possession
from last-play team, infer bases from prose, or calculate unavailable timeout/red-zone state.

## Identity and opaque references

Discovery references carry only version, source namespace, league, ESPN event ID, scheduled start,
requested timezone, and canonical home/away teams. Canonical JSON is wrapped with a
domain-separated SHA-256 checksum and URL-safe base64 encoding.

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
update clock. `provider_updated_at` remains null unless a future verified response supplies an
authoritative state-update timestamp. Scores are nonnegative integers or null. Impossible down,
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
| Cache | 256 normalized detail snapshots |

External cancellation propagates. No background request, polling task, WebSocket, or provider
disconnect operation exists.

## Cache semantics

The detail cache uses the provider's `Cache-Control: max-age` when present, capped at:

- 5 seconds for live, halftime, delayed, suspended, or unknown state.
- 30 seconds for scheduled or pregame state.
- 5 minutes for final, postponed, or cancelled state.

Each normalized detail receives an `observation_id`. A cache hit retains the original
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
- `mlb_fallback_ambiguous` and `game_state_unavailable`.

Transport/HTTP failure, tool execution error, and malformed/schema-invalid response are tested
independently. Error text excludes provider bodies, prompts, credentials, and opaque references.
Operational logs go to stderr and contain only operation, league, status, latency, cache status,
result count where available, and sanitized error class. Stdout remains MCP protocol traffic.

## Agent consumption

The model selects game-state tools semantically for score, lifecycle, or situation requests. It
must discover before detail and ask the user to select when multiple games remain plausible.

When market and game detail coexist, the host independently checks league, both participants, and
scheduled start within 30 minutes. The model receives `sports_identity_report` with `match`,
`different`, or `insufficient_evidence`; only `match` permits the observations to be discussed as
the same scheduled game. This report does not certify equivalent contracts.

Every response that used exact game detail receives a code-generated notice naming source and
observation time and stating that sporting result does not establish market settlement or contract
equivalence.

## Verification and observed limitations

Deterministic tests cover all lifecycle values, exact aliases, NCAA ambiguity, doubleheaders,
timezone rollover, null situation fields, impossible bounds, reference edits, response identity,
cache identity/TTL, response limits, retries, cancellation, malformed siblings/roots, exact MLB
fallback, fallback ambiguity, and provider conflict warnings.

Real MCP tests exercise `tools/list`, strict schemas, `tools/call`, discovery-before-detail, stable
errors, host validation, semantic routing, no-tool behavior, market/game identity checks, and an
independent stdio process. Opt-in public checks query all three leagues and retrieve exact state
when a current game is available; an empty slate is valid.

On 2026-09-20, the project `httpx` stack observed HTTP 200 from all three ESPN scoreboards and MLB
StatsAPI. Live MLB exposed inning/count/out/base/player fields. Live NFL exposed possession,
down/distance, field position, score, period, clock, and last play. ESPN can omit individual
situation fields during transitions, and its undocumented schema may change without notice.
