# Contract comparison

## Implemented pipeline

The application uses one deterministic matching pipeline:

- `assess_pair` compares supplied canonical contract terms.
- `match_candidates` deduplicates IDs, considers at most three candidates per platform,
  and evaluates at most nine pairs without network or model calls.
- The graph collects validated detail snapshots within the current turn and supplies
  `matching_report` to the model before cross-platform interpretation.
- A code-generated eligibility notice accompanies the final response independently of
  the model's explanation.

The current verdicts are equivalent supplied terms, different, and ambiguous. Related
contracts remain contextual evidence. The model cannot upgrade a deterministic rejection.

## Current evidence model

The parser recognizes explicit conditional subjects, YES/NO outcomes, polarity, numeric
thresholds, units, inclusive/exclusive comparisons, explicit timezone-aware event timestamps,
timing operators, selected settlement triggers, authority, cancellation clauses, exclusions,
and full-rule text.

Its numeric conditional parser is not a sports settlement parser. Unsupported wording, absent authority,
truncated rules, and unknown timing semantics remain unresolved. Equality of a short title
or a rule prefix is insufficient. Positive equivalence is scoped to the supplied terms;
unseen external documents are not certified.

The generic matcher tracks trading close separately from an event cutoff, but it can still
report differing close/resolution metadata as unresolved. That generic behavior is not a
sports-specific interpretation of normal settlement timing.

## Sports boundary

Sports MCP results provide league, canonical and raw participants, provider event identity,
scheduled start, full-game market type, explicit outcome prices, and settlement evidence. They
declare comparison eligibility `insufficient_evidence` with the specific reason: discovery
verifies event/type identity but does not establish equivalent settlement rules.

The current matching engine does not yet use all those fields. It requires YES/NO outcomes,
so a named-team Polymarket contract cannot be promoted to equivalence with a Kalshi contract.
An apparent price difference is not an established arbitrage or comparable probability gap.

## Planned sports extension

Extend the matcher and report rather than implementing a separate MCP comparison tool.

| Dimension | Required treatment |
|---|---|
| Event identity | Both participants, league, scheduled date/time, game number when known, provider evidence |
| Contract type | Match the same explicitly supported type and period; begin with full-game winners and add type-specific checks for later spreads, totals, props, and futures |
| Outcome mapping | Exact named-team mapping; do not equate Kalshi NO with the opponent without rules evidence |
| Postponement | Compare allowed rescheduling windows and original-versus-current game identity |
| Cancellation | Compare void, fair-value, 50/50, or other payout terms explicitly |
| Overtime/ties | Compare inclusion and tie settlement under the relevant league |
| Shortened games | Compare official-result and minimum-completion conditions |
| Authority | Compare governing-body result requirements and fallback sources |
| Clocks | Separate scheduled start, trading close, expected resolution, and final deadline |
| Final relationship | Equivalent, related, incompatible, or insufficient evidence |

A later trading close is not itself a conflict with an earlier game start. Genuine identity
or rule conflicts should remain decisive. A semantic helper may explain unresolved terms,
but deterministic conflicts and missing evidence must remain visible.

Additional contract types require their own outcome, line, subject, and settlement checks.
A shared game does not make a winner, spread, total, or player prop equivalent. Futures
require competition and season identity rather than assuming a single-game schedule.

See [Remaining work](../planning/IMPLEMENTATION_PLAN.md) for acceptance criteria and
[Sports MCP design](SPORTS_MCP.md) for the data already available to that extension.
