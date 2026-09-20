# Sports MCP design

## Product boundary

The Kalshi and Polymarket MCP servers provide read-only sports contract research. The initial
supported contract is a **full-game winner** in MLB, NFL, or NCAA Division I football.
NCAA participants carry the catalog's FBS/FCS classification; mixed-division matchups are
possible. Other divisions, unsupported participants, and unknown competitions are not silently
promoted into this scope.

The servers discover and describe contracts. They do not decide which platform offers an
equivalent bet, estimate a sporting outcome, recommend a position, or place an order.
Those distinctions matter: the two providers can describe the same teams and game while
paying differently after a cancellation.

## Module ownership

| Module | Responsibility |
|---|---|
| `providers/sports_teams.json` | Reviewed team identity and exact aliases; no prices, schedules, market IDs, or forecasts |
| `providers/sports.py` | Team lookup, query disambiguation, typed sports event and coverage models |
| `providers/sports_search.py` | Bounded provider-specific sports discovery and event enrichment |
| `providers/kalshi.py`, `providers/polymarket.py` | HTTP-backed clients, canonical contract parsing, detail enrichment |
| `mcp/common.py` | Typed public projections, price labels, links, deadline and error translation |
| `mcp/kalshi.py`, `mcp/polymarket.py` | Independently discoverable tool definitions and provider-client lifetimes |

The ordinary clients retain their generic `search_markets` interface. MCP search resolves
sports requests before delegating to the sports discovery functions. Compatibility behavior
for non-sports provider lookup and the five public tool names remains available.

The shared domain `CanonicalMarket` remains compatible with the agent. Provider
clients carry additive sports evidence in `provider_data`; MCP projection exposes only
explicitly typed fields. Raw provider payloads are not sent to the model.

## Query resolution

Both search tools accept a team or matchup, optional league, and optional scheduled UTC date.

```json
{"query": "Yankees vs Padres", "limit": 5}
{"query": "Giants", "league": "nfl"}
{"query": "Ohio State vs Michigan", "league": "ncaa_football", "event_date": "2026-11-28"}
```

The date above illustrates input syntax, not an assertion that a game or market exists.

Resolution follows these rules:

1. Normalize case, whitespace, and punctuation into token boundaries.
2. Recognize an explicit league or a league phrase such as `NCAA football`.
3. Match exact aliases in the reviewed catalog. A longer overlapping alias wins:
   `Michigan State` does not become `Michigan`; `New York Y` does not become `New York`.
4. Infer a league only when the alias evidence is consistent. `Miami Padres` resolves MLB
   because Padres identifies that league; `Giants` alone remains ambiguous.
5. Require one team or two distinct requested teams. Shared aliases within a league produce
   choices: `MLB New York` cannot choose between Yankees and Mets.
6. Treat common college ambiguities explicitly. `OSU`, bare `USC`, and unqualified college
   `Miami` require clarification. Full provider-backed names resolve them.
7. Reject unrecognized remaining opponent, date, or contract-type terms. The server never drops
   `vs Mystery` and returns the known team's unrelated games. Use `event_date` for dates.
8. Preserve unsupported non-sports queries for the generic discovery path.

There is no edit-distance search or nickname-only cross-provider join. Short abbreviations are
accepted as whole queries or with explicit league context, preventing incidental short words
in generic questions from triggering sports mode.

A clarification response has no market candidates, a reason in `clarification`, and optional
`choices`. It performs zero upstream requests. A client should ask the user to resolve it.

## Why the JSON catalog exists

The catalog lets one sports query resolve names without downloading a team directory for every
call or asking an LLM to invent abbreviations. It is a small, inspectable identity dependency,
not a copied exchange database or a store of live market data.

Its provenance is Kalshi's
[structured targets endpoint](https://docs.kalshi.com/api-reference/structured-targets/get-structured-targets).
Each row stores:

| Field | Meaning |
|---|---|
| `league` | Supported product league |
| `name` | Readable canonical team name |
| `kalshi_name` | Exact provider display label, including Y/M, C/WS, and other distinctions |
| `kalshi_id` | Opaque structured-target identity; never a market ticker |
| `aliases` | Reviewed exact names, provider abbreviations, and full names |
| `division` | FBS or FCS for college football; null for professional leagues |

The file contains the 30 MLB teams, 32 NFL teams, and provider-listed FBS/FCS participants.
Provider metadata is not accepted blindly: all-star league entries, a minor-league team
categorized under MLB, and a non-team retirement record are excluded.

Canonical professional names combine the provider's market/city and team name without
duplicating abbreviated city prefixes. College full names come from provider metadata.
The standalone Athletics name is retained without turning the display label `A's` into
a location. Identical nicknames such as Cowboys are never used to join different schools.
A provider team ID cannot override a conflicting recognized display label.

The module loads the packaged JSON once per process. It caches ID and exact-alias indexes
in memory. These caches contain identity metadata only; prices are fetched on each request.
The catalog has an explicit verification date. It is not an automatic guarantee that a school
retains its division forever.

### Catalog maintenance

1. Fetch complete structured-target pages with `page_size=2000` for:
   - `type=baseball_team&competition=MLB`
   - `type=football_team&competition=NFL`
   - `type=football_team&competition=NCAAFB`
2. Follow a returned cursor during a deliberate maintenance task. Do not treat an incomplete
   directory response as a complete replacement.
3. Retain only the supported professional teams and college rows with FBS/FCS metadata.
   Review promotions, relocations, renamed teams, and anomalous league assignments.
4. Preserve provider IDs and raw labels. Derive full names from the provider fields;
   review any conflict rather than matching on a shared nickname.
5. Add only evidence-backed aliases. Ambiguous aliases must stay ambiguous or trigger
   explicit clarification; never assign OSU to the first search hit.
6. Update the verification date and run catalog, alias, wrong-opponent, and MCP tests.
   Inspect the diff for unexpected removals and duplicate identities.

This is controlled reference-data maintenance. Runtime discovery does not rewrite the catalog.
An unknown team requires a catalog review or a precise generic provider lookup, not a guess.

## Kalshi discovery

Verified full-game series are:

| League/scope | Series ticker | Expected provider title |
|---|---|---|
| MLB | `KXMLBGAME` | Professional Baseball Game |
| NFL | `KXNFLGAME` | Professional Football Game |
| NCAA primary/FBS schedule | `KXNCAAFGAME` | College Football Game |
| NCAA FCS schedule | `KXNCAAFCSGAME` | College Football FCS Game |

These are observed recurring-series identifiers, not templates for constructing event or
market IDs. The server fetches each selected series and checks its identity and title before
using it. Unexpected metadata fails explicitly.

For a known FBS-only request, the primary college series is sufficient. An FCS participant
search checks both college scopes because that participant can appear against an FBS team.
The two scopes share one three-page budget. An explicit `series_ticker` narrows the scope
but cannot change a sports winner request into a spread or another league.

Each events request supplies `with_nested_markets=true` and `with_milestones=true`.
This returns contract data and schedule evidence together. The server does not make one
event-detail request per discovered game.

An open search narrows the event feed to open events, then checks every contract's own
status. Historical searches leave event status unrestricted: a settled contract can coexist
with a sibling that has a different state. Nested historical markets can still be omitted by
the provider's historical cutoff.

Participants resolve from structured-target IDs or exact labels in winner contracts.
The event must identify exactly two supported participants. Both requested opponents must
be present. Contract title, binary type, and absent line/threshold fields keep unrelated
siblings out of a winner result.

The server scans within its budget, deduplicates event and market IDs, detects repeated
cursors, and ranks qualifying contracts chronologically. Within a game, the requested team's
YES contract precedes its opponent's contract. The returned limit applies after filtering
and ranking.

## Polymarket discovery

Gamma public search accepts a free-text query and a league tag slug:
`mlb`, `nfl`, or `cfb`. The server uses these verified slugs, not display labels such as
`CFB (All)`; labels are not interchangeable with search tag values.

Search uses one resolved team, then requires all requested participants locally. Professional
queries use a unique exact nickname alias when the catalog supplies one; league tags prevent
that nickname from crossing into another competition. College game
titles use school names, so the server searches the school name rather than a mascot-heavy
full name that primarily finds futures. It disables profile/tag search and reduced optimized
responses, preserving numeric Gamma IDs and order-acceptance fields.

The server admits only contracts with `sportsMarketType="moneyline"`, a supported league
tag, and two distinct recognized team outcomes. A future with YES/NO outcomes, a total, a
spread, or an unrelated sibling does not consume the result limit.

If text search is exhausted without a qualifying game and data-page budget remains:

1. Fetch `/sports` and read the selected league's current `series` ID.
2. Query `/events` with that provider-supplied `series_id`.
3. Apply the same participant, contract-type, date, and contract-status checks.

The fallback repairs search-index/name mismatches inside the server. It shares the three-page
budget with text search; it does not start an unbounded second scan. Series IDs are never
assembled from a season or copied from an assumed permanent mapping.

Public-search pages are ranked locally before truncation. Early completion stops after a
page supplies enough qualifying contracts. This is not a globally sorted traversal of every
provider result. Duplicate pages stop the scan instead of consuming the entire budget.

## Event identity and selection

A sports result includes a `sports` object:

| Field | Meaning |
|---|---|
| `league` | MLB, NFL, or NCAA football |
| `provider_event_id` | Exact provider event identity |
| `raw_title` | Original provider event title |
| `participants` | Two canonical participant names |
| `raw_participants` | Corresponding provider labels |
| `divisions` | Known NCAA divisions represented in the game |
| `market_type` | `game_winner` |
| `line` | Null: no spread or total is supported |
| `scheduled_start` | Timezone-aware scheduled game time, when available |
| `schedule_source` | Field supplying the schedule |
| `comparison_eligibility` | Always `unverified`; discovery cannot certify equivalence |

`market_id`, `event_id`, `title`, and `raw_title` also remain available at contract level.
Different provider event IDs are never merged merely because the participants match.
Two doubleheader games remain two events even when both fall on the same UTC date.

`discovery.matching_events` lists up to ten distinct qualifying events independently of the
contract result limit. `candidate_event_count` reports the full count within the scan.
`selection_required` signals that a singular-game interpretation needs event/time selection;
a user asking for a list can simply inspect the alternatives.

## Prices, clocks, and links

An `outcome_quotes` entry carries a raw label, optional canonical participant, optional
binary side, price, price kind, bid, and ask. Decimal values serialize as strings.

| Provider field | Exposed meaning |
|---|---|
| Kalshi `last_price_dollars` | YES-side last-trade price |
| Kalshi `1 - last_price_dollars` | NO-side derived complement, not an independently observed trade |
| Kalshi `yes/no_bid/ask_dollars` | Corresponding side's quote |
| Gamma `outcomePrices[i]` | Provider snapshot for `outcomes[i]` |
| Gamma `lastTradePrice` | Separate provider last-trade value; named-outcome mapping is left unknown |
| Gamma `bestBid/bestAsk` | YES quote only for standard ordered Yes/No outcomes |

A Kalshi NO label is `Not <participant>`, not the opponent's name: cancellation or special
settlement can prevent that logical substitution. Named Polymarket outcomes do not acquire
fabricated YES/NO prices or bid/ask assignments. No additional order-book requests are made.

`retrieved_at` is local retrieval time. `provider_updated_at` is object-update metadata,
not a quote or trade observation clock. `price_observed_at` and `last_trade_at` remain null
when those specific clocks are unavailable.

| Time field | Source and interpretation |
|---|---|
| Scheduled start | Kalshi matching milestone `start_date`; Gamma `gameStartTime`, otherwise event `startTime` |
| `close_time` | Trading close/end field; never substituted for scheduled start |
| `expected_resolution_time` | Kalshi expected expiration estimate |
| `resolution_deadline` | Kalshi latest expiration, otherwise supplied expiration; Gamma null |

A game beginning before trading closes is normal. Conflicting explicit game-start fields or
conflicting identities fail validation; normal settlement timing does not create a warning.
Detail retrieval uses the exact event ID to recover Kalshi milestone metadata.

`source_url` retains its compatible API provenance meaning; `api_url` makes that meaning
explicit. Polymarket `market_url` uses the documented human-facing `/market/{slug}` route
with the returned slug. Kalshi human links stay null when a reliable page URL is unavailable;
API URLs are never presented as exchange webpages. Link discovery must not invent slugs.

## Coverage and operating budgets

| Field | Interpretation |
|---|---|
| `pages_scanned` | Actual data pages requested; metadata lookups are separate |
| `events_scanned` | Event entries examined, including duplicate entries in responses |
| `markets_scanned` | Contracts examined within unique events |
| `candidates_matched` | Qualified unique contracts before returned-limit truncation |
| `truncated` | More/possibly more provider data or local candidates exist |
| `has_more` | Provider continuation evidence; null when an offset feed cannot say |
| `continuation` | Provider cursor/page/offset evidence, not an automatic exhaustive request |
| `provider_total` | Gamma public-search total before local contract filters; null if unavailable |
| `total_meaning` | Explains that total's scope |
| `stop_reason` | Exhaustion, result limit, page budget, or repeated/empty-page guard |

A Gamma search total is not the number of matching moneylines. It can be zero while the
league-catalog fallback finds a game. An offset page containing 100 entries only suggests
possible continuation; it does not provide an exact total. Exhausting a scoped feed does
not prove universal market absence.

| Operation | Maximum logical HTTP requests with default server settings |
|---|---|
| Kalshi sports search | 3 data pages + 1 series check; 2 series checks for dual NCAA scope |
| Polymarket sports search | 3 data pages total + at most 1 metadata request for fallback |
| Kalshi sports detail | 1 market + 1 exact-event/milestone request |
| Polymarket detail | 1 market request with embedded event metadata |

Each logical HTTP request allows at most two retries. HTTP attempts have a ten-second
timeout and bounded backoff. The MCP tool has a thirty-second wall-clock budget, including
retries and metadata. Cancellation propagates; it is not converted into an empty result.
Malformed data and provider failures return sanitized MCP errors.

The generic Kalshi search retains its own bounded three-page/ten-event expansion path.
Generic Polymarket search retains its three-page early-completion path. The richer
`discovery` counters describe sports discovery; generic searches expose their existing
textual coverage statement.

The MCP limit remains 1–10 contracts. There is no exhaustive mode, polling service, account
connection, order endpoint, or additional MCP comparison tool.

## Expansion contract

The catalog is a reviewed provider-derived identity and alias resource. Its `kalshi_id` values
identify structured team targets, not events or markets. Adding rows cannot enable another
sport or contract type because the current public types admit only `mlb`, `nfl`, and
`ncaa_football`, and sports discovery admits only `game_winner` with a null line.

Every expansion must add a typed league and contract schema, verified provider discovery and
detail mappings, relevant settlement fields, exact outcome/line semantics, conservative
matching rules, and tests at unit, MCP, live-provider, real-agent, and deployment levels.
Spreads require side and line normalization; totals require over/under and threshold semantics;
props require subject, statistic, threshold, and stat authority; futures require competition,
season, field, and elimination/void rules. A type remains disabled until its provider and
settlement gates pass. This expansion is optional and follows the required assignment
deployment and submission work.
