# Product scope: sports prediction-market research

## Purpose

Help a user identify the correct sports game contract, understand what each listed outcome
pays on, and inspect the provider's price and settlement terms. The initial sports are MLB,
NFL, and NCAA Division I football, including FBS/FCS participants.

The product is a read-only course project. It is not a trading platform, sportsbook,
automated arbitrage system, or proprietary sports forecasting model.

## Implemented product

The Kalshi and Polymarket MCP servers accept ordinary team names and matchups, infer a league
when exact aliases permit it, and return full-game winner candidates. The user does not need
to know a series ticker. Ambiguous names produce clarification choices.

Each result groups outcome contracts by game and preserves provider identity and raw labels
alongside canonical participants, localized kickoff, game lifecycle, quote freshness, and bounded
discovery coverage. Exact local dates, ranges, next/recent selection, and continuation cursors keep
common searches direct. A detail call supplies settlement rules and explicit resolution. The
FastAPI/LangGraph application invokes all four servers through real MCP and retains session
context. It routes narrow questions to the minimum useful source set and can build one sourced game
brief from exact-game state, both market platforms, and bounded web evidence. Independent calls in
one reasoning step run concurrently after required identifiers are established.

As an optional add-on to the core research agent, the same conversation creates event-aware
watches. LangGraph resolves an exact game and
full-game-winner contracts through those MCP tools, compiles one versioned rule, presents a precise
preview, and activates it only after confirmation. The host coordinator shares observations across
compatible rules and deterministically evaluates probability-point movement, scoring-event and
no-tracked-scoring-event windows, lifecycle changes, cross-platform divergence, cooldown, and
re-arm state. SQLite is the local store and Firestore is the Cloud Run store behind one repository
interface. The in-product inbox is authoritative; explicitly opted-in watches also project stored
triggers to one deployment-owned Telegram chat through a transactional outbox.

The sports-state MCP separately finds a supported game by team, league, one local calendar day,
and timezone, then reads current state, a box score, a compact player directory, or one player's
game statistics through an opaque discovery reference. It also reads bounded chronological play
windows with stable IDs for backward paging and later-unseen retrieval.
ESPN is the primary free source. MLB StatsAPI is an MLB-only fallback after exact team/date/start
matching; NFL and NCAA football fail honestly when ESPN is unavailable. The result includes score,
lifecycle, observation provenance, and either one nullable league-specific situation object or a
sports-specific game-only box score. The default box-score view contains scoring and compact
leaders; callers can request the full layout or one sport-applicable section, optionally narrowed
to one team side. MLB box scores contain inning/team/batting/pitching lines; NFL and NCAA box
scores contain period/team/player statistics. Inning participation distinguishes a played half
from a skipped or not-yet-reached half. Provider-unavailable required and optional statistics are
identified separately through field-level completeness metadata. The service does not provide
odds, forecasts, contract identity, settlement, or season-stat substitutions.
The player directory exposes stable provider IDs only for players with game-stat lines. Player
detail returns one selected player's lines from the same cached observation without returning
unrelated players.
Play-by-play retains a maximum of 50 requested plays per response, supports scoring, period, and
home/away filters, and exposes stable paging anchors. Baseball actions distinguish pre-event and
post-event outs and label structured substitutions separately from pitches and plate appearances.
ESPN supplies pitch/action MLB detail and football plays; exact-identity MLB fallback supplies
at-bat detail.

The Tavily MCP adds bounded current evidence only after one exact game is identified. It searches
for injuries, lineups, weather, venue/schedule changes, other game news, or postgame recaps;
supports a league-official-only source policy; retains at most five HTTPS sources naming both teams
and the exact date; preserves source provenance; and exposes the text as untrusted evidence.
Explicit empty status and corroboration cautions keep weak return/activation snippets from being
presented as confirmed facts. The graph permits at most two such searches per turn. Tavily cannot
establish game state, contract equivalence, market settlement, or a forecast.

Representative requests:

- Find Kalshi Yankees game-winner contracts.
- Find Polymarket Chiefs vs Bills contracts.
- Find Ohio State vs Michigan with league `ncaa_football`.
- Read the rules for this returned market ID.
- Which game does that price refer to, when was the quote observed, and is it stale?
- What is the Yankees score and inning in America/Los_Angeles?
- Who has possession in the Falcons game, and what are the down and distance?
- Get the Yankees box score. Who has a hit and how many strikeouts does the starter have?
- Show period scoring and player statistics for this returned NFL or NCAA football game.
- List the players with statistics in this game, then show only Aaron Judge's game line.
- Show the latest five plays, then retrieve only plays after the last ID I have seen.

Sports market results declare that contracts are under `games[].contracts`; generic topic results
use `markets[]`. Quote freshness distinguishes an unavailable authoritative timestamp from an
actually stale timestamp, and unusual provider close timing is a warning rather than kickoff.

Results describe discovered contracts, not a claim that a particular game is currently listed.
Multiple event IDs require selection before discussing a singular game.

## Contract scope

Full-game winner/moneyline is the initial supported sports contract type. Spreads, totals,
partial-game winners, player props, season series, and futures are not currently supported
by sports discovery. Additional sports and market types are planned extension points, not
permanent exclusions from the product.

NCAA scope uses provider-backed Division I participants and preserves FBS/FCS classifications.
School abbreviations, shared nicknames, and ambiguous competitions require clarification.
The server never silently maps a school into another competition.

## Product workflow

The unified all-in-one information workflow routes each request to the minimum useful combination
of market, game-state, statistics, play-history, deterministic comparison, and bounded research
paths. It synthesizes a source-attributed direct answer or game brief while preserving exact game
identity, observation times, provider boundaries, and useful partial results when one source is
unavailable.

The FastAPI and LangGraph application is the primary product path. A repository Codex skill lets a
developer test the same four configured MCPs directly in interactive or headless Codex. That path
tests MCP behavior and routing instructions and uses the bounded watch CLI with the shared schemas,
validator, evaluator, and SQLite repository. It does not replace LangGraph memory, per-turn
budgets, or the required HTTP endpoint, and it does not remain active after its process exits.

The optional saved-research snapshot ledger is deferred until after deployment. It is not a
current capability.

The product reports provider prices and relevant evidence but does not create an independent win
probability or betting recommendation. It must refuse unsupported comparisons when contract
equivalence or evidence is insufficient.

## Non-goals

- Orders, brokerage access, positions, profit-and-loss tracking, or account credentials.
- Unbounded catalog scans, web browsing, or model-driven polling.
- Automatic settlement, automatic forecast resolution, or custom predictive training.
- Guaranteed coverage of every provider listing or every NCAA competition.
- Durable conversational memory across Cloud Run instance replacement.
- Private multi-user alert history or user-selected Telegram recipients. Session IDs are routing
  keys, not authentication.

## Assignment commitments

The project retains two independent market MCP servers, one independent sports-state server, and
one independent bounded Tavily server
with actual `tools/list` and `tools/call`, model-driven tool choice, framework-native session
memory, and the required `POST /chat` contract. Cloud Run deployment, a live grading URL, source
ZIP, README diagrams, and Rylan Wade's personal `PROCESS_LOG.md` remain submission obligations.

The sports-state process is the third integrated MCP server. Bounded Tavily research is the fourth integrated MCP server; its key is optional because the official keyless access mode supports local verification.
Personal reflections must come from Rylan Wade. Technical development evidence in the
automatic transcript does not replace that authorship.

Adding another sport or contract type requires typed schema changes, verified provider
taxonomy and fields, settlement semantics, outcome mapping, matcher rules, deterministic
fixtures, MCP tests, bounded live checks, and real-agent validation. The team identity catalog
contains reviewed provider-derived names and aliases; it contains no market identifiers and
cannot enable a league, spread, total, prop, or future by itself.

See [Sports market MCP design](../research/SPORTS_MCP.md),
[game-state MCP design](../research/GAME_STATE_MCP.md),
[watch monitoring and alerts](../research/WATCH_MONITORING_AND_ALERTS.md), and
[implementation milestones](IMPLEMENTATION_PLAN.md) for acceptance criteria.
