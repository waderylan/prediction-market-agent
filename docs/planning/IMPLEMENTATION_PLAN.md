# Implementation plan

## Current implementation boundary

The working system contains the FastAPI/LangGraph application, framework-native session
memory, two public read-only MCP servers, canonical contract parsing, and a conservative
deterministic matcher. Sports discovery and server result models support MLB, NFL,
and NCAA Division I game winners.

The provider/MCP layers own sports discovery. The repository has no sports-aware equivalence
matcher, forecast subsystem, research server, saved-research database, or Cloud Run deployment.

| Area | Status | Source of truth |
|---|---|---|
| Kalshi/Polymarket MCP | Implemented | [Sports MCP design](../research/SPORTS_MCP.md) |
| Exact team identity and sports coverage | Implemented | Provider catalog and server tests |
| Agent consumption of sports schemas | Tested with scripted model and real MCP | Sports integration tests |
| Sports-specific matching and agent instructions | Planned | Acceptance criteria below |
| News, semantic helper, forecast, ledger | Planned | Optional stages below |
| Cloud Run and submission | Required, not deployed | Assignment document |

## 1. Add sports semantics to the matcher

Use `match_candidates`, its bounded candidate pairing, and the current `matching_report`
before synthesis. Do not add a competing MCP comparison tool.

Acceptance criteria:

- Compare league, both participants, provider event identity evidence, scheduled start,
  game number when known, full-game market type, line/threshold, and outcome mapping.
- Keep doubleheaders and wrong opponents distinct even when titles are similar.
- Map a Kalshi YES outcome to a named team only with supplied evidence. Do not assume its
  NO outcome equals the opponent's win under every settlement condition.
- Compare postponement windows, cancellation payouts, overtime, shortened/abandoned games,
  ties, official-result requirements, and settlement authority.
- Classify equivalent, related, incompatible, and insufficient-evidence relationships
  conservatively within the result pipeline.
- Keep scheduled start, trading close, and resolution deadline independent. A later
  trading close alone is not an identity or rules conflict.
- Preserve deterministic vetoes and an independent user-visible report.
- Require regressions for equivalent supplied terms, dangerous near-matches, missing terms,
  rule truncation, and unsupported competitions.

## 2. Adapt the agent's sports workflow

Keep model-driven tool selection and the current MCP client interface.

Acceptance criteria:

- Prefer direct team/matchup search for sports; use series discovery only when it adds precision.
- Consume clarification choices and event alternatives without inventing identifiers.
- Ask the user to select the game when a singular request matches multiple event IDs.
- Display named outcome prices and explain snapshot versus last-trade semantics.
- Use the matching report before a cross-platform price comparison.
- Verify real-model tool selection, multi-step calls, memory follow-ups, and platform-specific
  requests. Scripted model tests alone are insufficient for semantic-selection claims.

The agent accepts the sports result schemas, but its system prompt remains series-oriented for
Kalshi, its activity summary omits sports filters, and its comparison logic is not sports-aware.
Real-model tests must establish any claimed sports-first selection behavior.

## 3. Optional sports and contract expansion

Breadth beyond the initial leagues and full-game winners is not required for the course
deliverable. Prioritize the deployment and submission work in section 7 over optional
breadth. Each additional type must pass its own schema and evidence gates:

| Type | Required typed identity and settlement evidence |
|---|---|
| Additional league | Competition, season, participants, schedule, governing authority |
| Spread | Side, signed line, push behavior, overtime, postponement and cancellation |
| Total | Over/under, threshold, push behavior, included periods, overtime |
| Player prop | Athlete, team/game, statistic, threshold, official stat source, corrections |
| Future | Competition, season, field/outcome, elimination, withdrawal and void rules |

For every expansion, verify provider taxonomy, identifiers, discovery endpoints, pagination,
price meaning, schedule fields, and rules against provider documentation and live responses.
Add typed schemas and conservative matching only after those mappings exist. Require fixtures,
parser/schema tests, dangerous near-match tests, MCP protocol tests, bounded live checks,
real-agent checks, and deployment checks before enabling user-facing search. Editing
`sports_teams.json` alone never enables a sport or contract type.

## 4. Add bounded external evidence research

A third MCP server can research injuries, lineup news, weather, and other relevant evidence
after the intended event is identified. Start with a fixed search/extraction budget and
validate whether the evidence improves the answer.

Preserve publication time, retrieval time, source URL, and the event relationship. Treat
retrieved content as untrusted data. Provider or research failure should produce a controlled
partial result. Do not force research for a simple contract lookup.

## 5. Evaluate optional semantic assistance and forecasting

Evaluate a specialized equivalence helper against labeled cases before enabling it.
It must not override deterministic conflicts or perform the final arithmetic.
Keep timeouts, fallback behavior, and a disable path.

A forecast requires a verified event/outcome, cited evidence, explicit uncertainty, and an
abstention path. Provider prices are inputs, not the model's independent forecast.
No forecast endpoint or recommendation subsystem is implemented at present.

## 6. Optional saved research ledger

Build a separate ledger only after the core research workflow is stable.
Save a snapshot only on explicit user request. Store event and contract references, outcome
labels, source links, quote times, evidence, and comparison status. Treat local container
storage as non-durable. Do not add managed database infrastructure merely for this assignment.

## 7. Deploy and prepare submission

- Confirm locked dependencies, packaged team metadata, non-root container execution,
  and `0.0.0.0:$PORT`.
- Configure a cloud-accessible LLM backend and deploy the application to Cloud Run with one worker and
  `--max-instances 1` for instance-local memory.
- Verify arbitrary reasonable queries, actual calls to both MCP servers, no-tool responses,
  multi-turn recall, and all three required MCP failure classes.
- Publish the live URL and retain it for grading.
- Keep the three README diagrams accurate.
- Build the source ZIP without secrets, virtual environments, caches, or local credentials.
- Obtain Rylan Wade's personal process narrative and learning reflection; do not generate it
  as if it were his experience.

## 8. If time remains: evaluate Polymarket US migration

After the required deployment and submission work is complete, evaluate replacing the
international Gamma adapter with a read-only Polymarket US adapter for the project's US
audience. This is not a base-URL substitution: bounded live probes confirmed that the current
`/public-search`, `/sports`, `/events`, and `/markets/{id}` paths return 404 from the US gateway.

Migration acceptance criteria:

- Use the documented US routes, including `/v1/search`, `/v1/markets`, market detail, and
  league-event discovery, while preserving the current MCP tool interface.
- Treat US market and event IDs, slugs, prices, statuses, and settlement terms as a separate
  provider namespace. Never relabel Gamma results or links as US contracts.
- Parse US `marketSides`, `bestBidQuote`, `bestAskQuote`, `sportsMarketTypeV2`, and wrapped
  detail responses according to their actual semantics.
- Verify MLB, NFL, and NCAA football taxonomy independently. Initial probes found MLB and NFL
  league routes but did not establish the correct NCAA football league-discovery route.
- Replace human-facing links only after the returned US slug is verified against the US API.
- Add deterministic fixtures, identity and quote-semantics regressions, real MCP schema/call
  tests, bounded live checks, agent-consumption tests, and updated provider documentation.
- Keep the integration read-only; do not use authenticated order, account, or portfolio APIs.

## Verification gates

Use deterministic tests for provider contracts, identity, matching, budgets, and schema
consumption. Use real MCP sessions/subprocesses for protocol evidence. Use opt-in bounded
live checks for current provider compatibility. Verify real-model behavior and deployed
behavior separately; neither is established by fixture tests.

Protect the assignment contract, MCP correctness, memory, failure handling, and deployment
before optional forecasts, semantic helpers, or a ledger. See
[Testing](../research/TESTING.md) for commands.
