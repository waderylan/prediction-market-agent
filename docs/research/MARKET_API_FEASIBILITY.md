# Provider contracts and data mapping

## Public read-only surface

Both providers are accessed through bounded asynchronous GET requests without account
credentials. No account, order, trade-submission, or authenticated portfolio endpoint is used.

| Provider | Endpoint | Use |
|---|---|---|
| Kalshi | `/series` | Optional generic series discovery |
| Kalshi | `/series/{ticker}` | Verify selected sports series; generic settlement metadata |
| Kalshi | `/events` | Bounded event scan with nested markets and sports milestones |
| Kalshi | `/events?tickers=...` | Exact sports event and schedule enrichment |
| Kalshi | `/events/{ticker}` | Generic event expansion |
| Kalshi | `/markets/{ticker}` | Contract detail |
| Kalshi | `/structured_targets` | Reviewed team-catalog maintenance, not runtime quote lookup |
| Polymarket Gamma | `/public-search` | League-scoped sports or generic topic search |
| Polymarket Gamma | `/sports` | Runtime league-series metadata for discovery fallback |
| Polymarket Gamma | `/events?series_id=...` | Bounded league-catalog fallback |
| Polymarket Gamma | `/markets/{id}` | Contract detail; event metadata when Gamma includes it |

Base URLs are `https://external-api.kalshi.com/trade-api/v2` and
`https://gamma-api.polymarket.com`.

Current sporting state is a separate provider boundary, never a market fallback:

| Provider | Fixed route | Use |
|---|---|---|
| ESPN public JSON | League `/scoreboard` and event `/summary` under `site.api.espn.com` | MLB/NFL/NCAA game discovery and normalized state |
| MLB StatsAPI | Date `/api/v1/schedule` and exact `/api/v1.1/game/{gamePk}/feed/live` | MLB-only state fallback after ESPN failure and identity matching |

These free unauthenticated routes supply no market prices, contract rules, outcome mapping, or
settlement. ESPN is undocumented and has no SLA. The exact state contract and measured limitations
are in [Game-state MCP](GAME_STATE_MCP.md).

## Contract-sensitive details

- Kalshi event pages cap at 200 and return an opaque cursor. Nested historical markets can
  be omitted beyond the provider's historical cutoff.
- `with_milestones=true` belongs on the events collection request. Sports detail uses the
  collection's exact `tickers` filter to obtain schedule metadata alongside the event.
- Kalshi objects use states such as `active` and `finalized`; these are normalized separately
  from list-filter values such as `open` and `settled`.
- A historical sports search does not filter the entire event to settled merely because
  one requested contract is settled. Contract status is checked individually.
- Gamma public search supports page numbers, `events_tag`, event status, `hasMore`, and
  `totalResults`. Sports uses verified tag **slugs**, not display labels.
- Gamma text-search totals describe upstream search hits before local contract/type/team
  filters. They are not moneyline counts or totals for the fallback catalog.
- Gamma `outcomes` and `outcomePrices` are JSON-encoded arrays. Their indexes correspond,
  but index zero is not necessarily YES. Named and reversed outcomes are valid.
- Gamma resolved status requires explicit resolution metadata; a price of zero or one
  is not settlement evidence.
- Reduced `optimized` search output is disabled because the server needs numeric Gamma
  IDs and order-acceptance fields.
- An open sports search uses the provider's active/open event scope and checks contract
  status again. Results describe that upstream scope, not a universal listing audit.
- HTTP response identity is validated; a market ID cannot silently become another market,
  and sports discovery rejects conflicting event ownership.
- Gamma can omit `events` from sports market detail. The client retains verified search context
  for 15 minutes in a bounded 256-entry cache; it never fabricates a missing event identity.

## Field semantics

| Public field | Kalshi | Polymarket |
|---|---|---|
| `market_id` | Exact ticker | Exact numeric Gamma ID |
| `event_id` | Event ticker | Event ID |
| `raw_title` | Unmodified market title | Unmodified question |
| `outcome_quotes[].price` | Last-trade YES / derived NO complement | Corresponding `outcomePrices` snapshot |
| `provider_last_trade_price` | `last_price_dollars` | Separate `lastTradePrice` |
| `yes_bid/yes_ask` | Dollar quote fields | Only standard ordered Yes/No contracts |
| `sports.scheduled_start` | Matching milestone `start_date` | `gameStartTime`, otherwise event `startTime` |
| `close_time` | `close_time` | `endDate` |
| `expected_resolution_time` | `expected_expiration_time` | Null |
| `resolution_deadline` | `latest_expiration_time`, otherwise `expiration_time` | Null |
| `provider_updated_at` | `updated_time` | `updatedAt` |
| `quote_as_of` | Null unless an authoritative price clock is supplied | Null unless an authoritative price clock is supplied |
| settlement | `settlement_value_dollars`, `result`, `settlement_ts` | Explicit resolution plus unambiguous terminal outcome vector and `closedTime` |
| `price_observed_at/last_trade_at` | Null without those specific clocks | Null without those specific clocks |
| `rules` | Primary and secondary rule text | Description |
| `api_url/source_url` | Market API endpoint | Market API endpoint |
| `market_url` | Indexed series/event route plus returned identities | Documented market route plus returned slug |

Object-update time is not last-trade or quote time. `quote_as_of` therefore remains null for the
currently mapped endpoints instead of copying `retrieved_at`; stale flags explain the missing
authoritative clock. `observation_id` identifies normalized response reuse independently from time.
Trading close is not scheduled game start.
A NO complement is not necessarily the opponent's win. Missing data remains null.
The server does not infer settlement from a 99-cent or 1-cent trade. Settlement fields require
provider resolution evidence. The server does not fetch order books because this product does
not require depth or execution-price calculations.

## Identity metadata and maintenance

The team catalog uses provider structured IDs and exact labels, with reviewed canonical
names and aliases. It excludes non-team and out-of-scope metadata records. See
[Catalog maintenance](SPORTS_MCP.md#catalog-maintenance) for the complete update procedure.
No market or event ID is constructed from a team abbreviation, calendar date, or game time.

## Primary references

- Kalshi [Get Events](https://docs.kalshi.com/api-reference/events/get-events),
  [Get Market](https://docs.kalshi.com/api-reference/market/get-market),
  [Get Series List](https://docs.kalshi.com/api-reference/market/get-series-list), and
  [Structured Targets](https://docs.kalshi.com/api-reference/structured-targets/get-structured-targets).
- Polymarket [Discover Markets](https://docs.polymarket.com/market-data/discover-markets),
  [Search](https://docs.polymarket.com/api-reference/search/search-markets-events-and-profiles),
  [List Events](https://docs.polymarket.com/api-reference/events/list-events), and
  [List Teams](https://docs.polymarket.com/api-reference/sports/list-teams).

Fixtures preserve only relevant public fields and use synthetic prices where appropriate.
They do not contain account data or credentials. Live prices are not deterministic assertions.
