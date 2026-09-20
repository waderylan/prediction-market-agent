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
FastAPI/LangGraph application invokes
both servers through real MCP and retains session context.

Representative requests:

- Find Kalshi Yankees game-winner contracts.
- Find Polymarket Chiefs vs Bills contracts.
- Find Ohio State vs Michigan with league `ncaa_football`.
- Read the rules for this returned market ID.
- Which game does that price refer to, when was the quote observed, and is it stale?

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

## Future product work

The next sports capability is a defensible comparison of two identified contracts. It must
extend the deterministic matching pipeline, not create a parallel MCP matching system.
Only after event, outcome, and settlement equivalence is established should the application
present a comparable price difference.

Later optional work includes bounded external evidence research through a third MCP server,
an evaluated semantic-equivalence helper, an evidence-based forecast, and an explicitly
requested saved research snapshot. These are plans, not current capabilities.

The forecast design should use named outcomes for sports. A generic YES/NO recommendation
must not erase which team, game, or cancellation rule it refers to. The application must
abstain when equivalence or evidence is insufficient.

## Non-goals

- Orders, brokerage access, positions, profit-and-loss tracking, or account credentials.
- Unbounded catalog scans, web browsing, or background monitoring.
- Automatic settlement, automatic forecast resolution, or custom predictive training.
- Guaranteed coverage of every provider listing or every NCAA competition.
- Durable conversational memory across Cloud Run instance replacement.

## Assignment commitments

The project retains two independent MCP servers with actual `tools/list` and `tools/call`,
model-driven tool choice, framework-native session memory, and the required `POST /chat`
contract. Cloud Run deployment, a live grading URL, source ZIP, README diagrams, and
Rylan Wade's personal `PROCESS_LOG.md` remain submission obligations.

A third integrated MCP server is a rubric opportunity; it is not already implemented.
Personal reflections must come from Rylan Wade. Technical development evidence in the
automatic transcript does not replace that authorship.

Adding another sport or contract type requires typed schema changes, verified provider
taxonomy and fields, settlement semantics, outcome mapping, matcher rules, deterministic
fixtures, MCP tests, bounded live checks, and real-agent validation. The team identity catalog
contains reviewed provider-derived names and aliases; it contains no market identifiers and
cannot enable a league, spread, total, prop, or future by itself.

See [Sports MCP design](../research/SPORTS_MCP.md) for implemented details and
[Remaining work](IMPLEMENTATION_PLAN.md) for future acceptance criteria.
