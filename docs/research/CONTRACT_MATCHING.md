# Contract comparison

## Implemented pipeline

The application uses one bounded deterministic matching pipeline; it does not expose a comparison
MCP tool.

- `assess_pair` compares one Polymarket detail result with one Kalshi detail result.
- `match_candidates` deduplicates IDs, considers at most three candidates per platform, and
  evaluates at most nine pairs without network or model calls.
- Market detail is retained as typed `ContractEvidence`, including sports identity, named outcome
  quotes, complete-rule status, settlement fields, and separate clocks.
- The graph rebuilds one `MatchingReport` whenever validated market detail or already-requested
  game state arrives before cross-platform interpretation.
- A code-generated notice exposes contract eligibility and market/game identity independently of
  the model's explanation.

The pair verdicts remain `equivalent`, `different`, and `ambiguous`. `equivalent` means the complete
supplied terms passed the supported deterministic checks; it does not certify unseen external
documents. Related contracts remain contextual evidence. The model cannot upgrade a deterministic
rejection.

## Three separate checks

### Market-to-market event identity

For supported full-game winners, the matcher compares:

- League and season. Season is provider-supplied when available; otherwise it is derived from the
  scheduled start using the MLB or football season calendar.
- Both canonical participants, independent of home/away ordering.
- Each contract's internal provider event ID against its typed sports event ID. Kalshi and
  Polymarket IDs remain separate namespaces and are not expected to be equal.
- Scheduled starts after UTC normalization. Drift through 30 minutes is accepted as provider
  schedule variance; larger drift is a deterministic conflict.
- The `game_winner` market type.
- Game number when either provider supplies it. Different numbers conflict; one missing number is
  insufficient evidence; neither supplied leaves participant/start identity decisive.

Missing required identity is `insufficient_evidence`; an explicit conflict is `different`.
Doubleheaders therefore remain distinct by start and game number rather than title similarity.

### Contract-to-contract equivalence

After event identity, the matcher checks:

- Exact named affirmative-outcome mapping. The Kalshi YES participant must be one of the complete
  Polymarket named outcomes.
- Line or threshold. The initial supported type requires no line.
- Postponement or rescheduling window.
- Cancellation payout.
- Exact complete supplied rule text.

Kalshi NO is never relabeled as the opponent. A matching Kalshi YES participant only establishes
the affirmative mapping; settlement checks must still pass before
equivalence.

Supported deterministic semantic extraction is intentionally narrow. Explicit values such as a
two-day rescheduling window, waiting until a postponed game is completed, a 50/50 payout, or a
fair-price cancellation payout can match or conflict in code. Missing, truncated, or differently
worded complete terms stay `ambiguous` unless one of those core checks already proves a conflict.

## Milestone 8A retained boundary

The [Milestone 8A evaluation](MATCHING_VALUE_EVALUATION.md) found no safely comparable real pair.
The deterministic matcher and a smaller baseline both classified all 15 complete held-out pairs
correctly, while forced Jev review downgraded three correct decisions to ambiguity and added no
useful pair. Jev and the unproven specialized semantic parsers were removed.

The retained matcher never sends ambiguity to a model for adjudication. The primary model may
explain the typed checks but cannot change their verdict. Exact full-rule agreement is intentionally
strict: it accepts false rejection risk rather than creating an unmeasured false-equivalence path.

### Market-to-game identity

Sports-state evidence is incorporated only when the user request already required exact current
state or box-score detail. Each typed `MarketGameAssessment` includes:

- Market platform and ID.
- Opaque game reference, source, observation time, and sporting lifecycle.
- Dimension checks for league, both participants, scheduled-start drift, and provider-backed
  identifiers.
- An overall `match`, `different`, or `insufficient_evidence` verdict and `use_together` flag.

This assessment covers sporting-event identity only. It cannot alter the contract pair verdict,
establish payout semantics, or mark a market settled. A final score cannot upgrade an
ambiguous/different pair, and a completed sporting lifecycle cannot mark a market settled; only
explicit market settlement fields establish market settlement. Sports-state unavailability does
not prevent market-to-market matching.

## Clock policy

The report keeps these clocks independent:

- Scheduled sporting-event start.
- Trading close.
- Expected resolution time.
- Final resolution deadline.

Only scheduled start participates in event identity. Different trading or resolution clocks are
reported with provenance but do not by themselves make the sporting event different or block an
otherwise complete sports equivalence decision.

## Generic compatibility

The earlier narrow generic matcher remains available for non-sports contracts. It recognizes
explicit conditional subjects, YES/NO outcomes, polarity, numeric thresholds, units,
inclusive/exclusive comparisons, timezone-aware event timestamps, timing operators, selected
settlement triggers, authority, cancellation clauses, exclusions, and full-rule text.

Its numeric conditional parser is not used as a substitute for sports settlement parsing. An
equal title, rule prefix, shared game, or related market is never sufficient by itself.

## Verification and limitations

Deterministic and real-MCP scripted-agent tests cover:

- A complete equivalent sports pair without sports-state data.
- Preserved typed identity and named outcomes through both MCP detail calls and agent synthesis.
- Wrong opponents, different game numbers, and start drift inside/outside the 30-minute bound.
- Cancellation payout conflicts, incomplete rules, and truncated/unsupported semantics.
- Current Polymarket/Kalshi MLB, NFL, and NCAA postponement/cancellation templates.
- Matching and conflicting market/game identity, sports-state unavailability, and final games whose
  contracts remain non-equivalent or unsettled.
- Bounded candidate counts, duplicate IDs, generic dangerous near-matches, and independent
  code-generated notices.
- Exact full-rule differences remain ambiguous when the retained core parser finds no explicit
  conflict.

A separate 2026-09-20 real-slate replay used ten 2026-09-19 NCAA games plus ten NFL and ten MLB
games on 2026-09-20. All 30 games existed on both platforms, producing 60 cross-platform pairs.
Every pair had a deterministic settlement conflict, so all 60 were rejected safely.

The later Milestone 8A sample covered 15 complete held-out pairs over three dates and all supported
leagues. It found no equivalent pair and justified the simplified boundary. Spreads, totals,
partial-game markets, props, futures, pushes, and season-long identity still require type-specific
models and tests.

See [Implementation plan](../planning/IMPLEMENTATION_PLAN.md) for milestone gates and
[Matching value evaluation](MATCHING_VALUE_EVALUATION.md) for the retention decision and
[Sports MCP design](SPORTS_MCP.md) for provider discovery, identity, and projection contracts.
