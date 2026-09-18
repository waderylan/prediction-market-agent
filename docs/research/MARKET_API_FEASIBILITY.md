# Market API Feasibility — 2026-09-18

## Decision

- Both public APIs expose enough read-only metadata, rules, status, timing, and pricing data for the
  canonical market model.
- Use Polymarket Gamma free-text search for Polymarket discovery.
- Use a bounded Kalshi event-catalog scan followed by deterministic local token ranking because the
  official events and markets endpoints do not expose a natural-language query parameter.
- Do not call either order-book endpoint in version one. Gamma provides `outcomePrices`, `bestBid`,
  `bestAsk`, and `lastTradePrice`; Kalshi provides dollar-denominated bid, ask, and last-price fields.
  Add order books only if later matching or liquidity tests demonstrate a need for depth.

## Official Sources Reviewed

- Polymarket: [Discover Markets](https://docs.polymarket.com/market-data/discover-markets),
  [Market Details](https://docs.polymarket.com/market-data/market-details), and
  [Prices and Order Books](https://docs.polymarket.com/market-data/prices-order-books).
- Kalshi: [Market Data Quick Start](https://docs.kalshi.com/getting_started/quick_start_market_data),
  [Get Events](https://docs.kalshi.com/api-reference/events/get-events),
  [Get Markets](https://docs.kalshi.com/api-reference/market/get-markets),
  [Get Market](https://docs.kalshi.com/api-reference/market/get-market), and
  [Rate Limits](https://docs.kalshi.com/getting_started/rate_limits).

## Endpoints Evaluated

| Provider | Public GET endpoint | Purpose | Live result |
|---|---|---|---|
| Polymarket | `/public-search?q=...` | Free-text event discovery | `200`; useful election, crypto, and sports candidates; some stale results |
| Polymarket | `/events/keyset?closed=false&limit=2` | Cursor-based event feed | `200`; distinct second page using `after_cursor` |
| Polymarket | `/events/{id}` | Event and nested-market details | `200`; candidate event retrieved |
| Polymarket | `/markets/{id}` | Market details, rules, prices, status | `200`; market `561229` retrieved |
| Polymarket | `/markets?closed=true&limit=1` | Closed-market status | `200`; closed state and settled outcome prices present |
| Kalshi | `/events?status=open&limit=200` | Catalog discovery | `200`; cursor present; multiple categories represented |
| Kalshi | `/events/{ticker}?with_nested_markets=true` | Event and nested markets | `200`; `KXPRESPERSON-28` retrieved |
| Kalshi | `/markets?status=open&limit=2` | Market feed pagination | `200`; distinct second page using `cursor` |
| Kalshi | `/markets/{ticker}` | Rules, prices, timing, status | `200`; `KXPRESPERSON-28-JVAN` retrieved |
| Kalshi | `/series/{ticker}` | Settlement sources and contract terms | `200`; official source and terms URL retrieved |

Base URLs are `https://gamma-api.polymarket.com` and
`https://external-api.kalshi.com/trade-api/v2`.

## Authentication, Pagination, and Limits

- Evaluated market-data requests required no credentials or authenticated headers. No write or
  account endpoints were called.
- Polymarket keyset feeds return `next_cursor`; the next request supplies `after_cursor`. Public
  search accepts a page number and reports `pagination.hasMore` and `totalResults`.
- Kalshi events default to 200 and cap at 200 per page. Kalshi markets default to 100 and cap at
  1,000. Both return an opaque `cursor`, with an empty cursor marking completion.
- Kalshi documents token-bucket limits for authenticated traffic, with most endpoints costing 10
  tokens and a Basic read budget of 200 tokens/second. The public responses tested did not expose
  rate-limit headers. The reviewed Polymarket market-data pages do not publish a numeric public
  limit. Clients should stay bounded and handle `429` plus `Retry-After` conservatively.

## Live Checks and Data Quality

- Categories checked on Polymarket: economics, crypto, elections, and sports. Categories observed
  in the bounded Kalshi catalog included economics, elections, sports, climate/weather, science,
  entertainment, health, and companies.
- Empty searches return `200` with empty collections on both providers.
- Invalid Polymarket market ID returned `422` with a validation error; an unknown Kalshi ticker
  returned `404` with a structured `not_found` error.
- Polymarket free-text results can be stale or extremely broad. The Federal Reserve query ranked
  2023/2024 events, and “Bitcoin price” reported over 100,000 results. Results require active-state
  filtering and bounded ranking.
- Kalshi has no documented text-search parameter. Six bounded 200-event pages located useful
  candidates, but catalog order is not relevance order and nested responses can be several MB.
  Discovery should fetch event summaries first, rank locally, and retrieve nested markets only for
  a small candidate set.
- Polymarket encodes `outcomes`, `outcomePrices`, and `clobTokenIds` as JSON strings. Index zero is
  YES and index one is NO for binary markets.
- Polymarket often embeds the complete resolution rule and authorities in `description` while
  `resolutionSource` is empty. Kalshi puts immediate conditions in `rules_primary` and
  `rules_secondary`; `/series/{series_ticker}` supplies settlement sources and contract terms.
- Live Kalshi objects used `active` and `finalized`, while list filters are documented as `open` and
  `settled`. Normalization must accept observed object states rather than copying filter names.
- Missing bid, ask, liquidity, or timing data is legitimate. It must remain `null`; zero values must
  not be treated as missing.

## Cross-Platform Pair Evidence

Plausible pair requiring later semantic review:

- Polymarket `561229`: “Will JD Vance win the 2028 US Presidential Election?”
- Kalshi `KXPRESPERSON-28-JVAN`: “Who will win the next presidential election?” / J.D. Vance.
- Both describe the same candidate and presidential term, and prices were available on both.
- They are not safe to label equivalent yet. Polymarket resolves when AP, Fox News, and NBC all call
  the race, falling back to inauguration; Kalshi resolves if J.D. Vance is inaugurated. That timing
  and authority difference belongs in the later contract-equivalence workflow.

Near-matches found in the same live catalogs include:

- Kalshi `KXPRESPARTY-2028` (winning party) versus Polymarket candidate-winner contracts.
- Kalshi `KXPRESELECTIONOCCUR-28` (whether the election occurs) versus winner contracts.
- Kalshi `KXPRESOUTCOME-28NOV07` (exact outcome) versus a single-candidate YES/NO contract.
- Kalshi `KXPRESMATCHUP-28NOV07` (matchup) versus election winner.
- Kalshi `KXFEDDECISION-28JAN` (meeting decision) versus `KXFED-28JAN` (post-meeting funds-rate
  level); these are related economic markets, not equivalent contracts.

## Canonical Model Mapping

| Canonical field | Polymarket | Kalshi |
|---|---|---|
| `market_id` | `id` | `ticker` |
| `event_id` | `events[0].id` | `event_ticker` |
| `title` | `question` | combine `title` with `yes_sub_title` when needed |
| `description` | `description` | `subtitle` or `null` |
| `outcomes` | parsed `outcomes` JSON string | explicit `Yes` / `No` labels |
| `yes_price`, `no_price` | parsed `outcomePrices` | last price plus binary complement when present |
| `yes_bid`, `yes_ask` | `bestBid`, `bestAsk` | `yes_bid_dollars`, `yes_ask_dollars` |
| times | `startDate`, `endDate`, `closedTime` | `open_time`, `close_time`, `expected_expiration_time` |
| `rules` | `description` | join non-empty `rules_primary` and `rules_secondary` |
| `resolution_source` | `resolutionSource`, else rule text only | series `settlement_sources` |
| `status` | derived from active/closed/archived/order flags | normalized object `status` |
| activity | `liquidity`, `volume` | `liquidity_dollars`, `volume_fp` |
| `source_url` | market slug URL | public Kalshi market URL derived from ticker |

## Remaining Risks and Next Steps

- Bound Kalshi discovery by page count and candidate count; rank title, subtitle, event ticker, and
  market outcome labels locally. This is the smallest supported fallback and needs no scraping or
  persistent index.
- Preserve provider status values in bounded raw metadata while exposing a small canonical status
  enum.
- Test string-encoded Polymarket arrays, Kalshi fixed-point strings, missing quotes, out-of-range
  probabilities, malformed JSON, timeouts, `429`, and `5xx` responses.
- Treat the identified pair as plausible test data, not a confirmed equivalent contract.
- No fixed architecture change or scope pivot is recommended.

## Fixture Policy

- `tests/fixtures/` contains hand-reduced snapshots based on the public responses above.
- Fixtures retain only fields needed by the domain mapping and tests, contain no credentials or
  user/account data, and are deterministic inputs for the normal suite.
- Live market values are time-sensitive examples, not assertions about current prices.
