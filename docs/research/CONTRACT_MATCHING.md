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
  game state arrives, then runs the optional Jev node before cross-platform interpretation.
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
- Resolution authority and an explicit official-result requirement. Authority URLs are compared
  by normalized source domain: equal domain sets match, overlapping sets require review, and
  different labels alone do not prove incompatible settlement. Disjoint sources conflict only
  when both contracts explicitly make them exclusive.
- Postponement or rescheduling window.
- Cancellation payout.
- Overtime or extra-innings inclusion.
- Tie treatment.
- Shortened-game and abandoned-game treatment.
- Material exclusions and the complete supplied rule text.

Kalshi NO is never relabeled as the opponent. A matching Kalshi YES participant only establishes
the affirmative mapping; cancellation, tie, and other settlement checks must still pass before
equivalence.

Supported deterministic semantic extraction is intentionally narrow. Explicit values such as a
two-day rescheduling window, waiting until a postponed game is completed, included overtime, a
50/50 payout, or a fair-price cancellation payout can match or conflict in code. Missing,
truncated, or unsupported rule text stays `ambiguous`. Differently worded complete terms can enter
the bounded Jev review described below.

## Jev semantic-review boundary

Jev is an optional LangGraph decision node, not an MCP server and not a replacement for the
deterministic matcher. `JEV_ENABLED=false` is the default. Enabling it also requires an
`AI_GATEWAY_API_KEY`; the integration hard-codes the `typesafe-ai/jev` model.

A pair is eligible only when it is a supported sports pair, deterministic event identity is a
match, contract equivalence is ambiguous, both rule texts are complete and untruncated, neither
rule exceeds 12,000 characters, and no dimension has a deterministic conflict. At most three
pairs are reviewed per request. Calls run concurrently, use a three-second attempt timeout, retry
once for transport/retryable HTTP failures, reject responses over 1 MiB, and use a 128-entry
exact-request cache.

Jev receives only normalized event identity, named-outcome mapping, deterministically matched and
unresolved dimensions, complete rule text, and stated resolution authority. It does not receive
market prices, game scores, sporting results, or sports-state evidence. Its typed output records
the full `equivalent`/`different`/`ambiguous` distribution, provider confidence, selected
probability, input tokens, latency, reviewed dimensions, and cache status.

Automatic action is asymmetric and conservative: `equivalent` requires selected probability at
least 0.90, `different` requires 0.75, and both require provider confidence at least 0.60.
Ambiguous, low-confidence, malformed, timed-out, or unavailable results preserve deterministic
ambiguity for main-model explanation. The main model cannot upgrade that result or override a
deterministic veto.

### Market-to-game identity

Sports-state evidence is incorporated only when the user request already required a game-state
tool call. Each typed `MarketGameAssessment` includes:

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
- Current Polymarket/Kalshi NFL and MLB authority formats and postponement/cancellation templates,
  including shared-domain authorities expressed with different surrounding text.
- Matching and conflicting market/game identity, sports-state unavailability, and final games whose
  contracts remain non-equivalent or unsettled.
- Bounded candidate counts, duplicate IDs, generic dangerous near-matches, and independent
  code-generated notices.
- Jev eligibility, request redaction, thresholds, distributions, cache reuse, pair limits,
  retries, timeouts, malformed responses, deterministic vetoes, disabled operation, and graph
  integration after real MCP detail calls.

The committed 13-case label set contains deterministic positives/conflicts, missing and truncated
rules, semantic settlement conflicts, an equivalent paraphrase, and overlapping authorities.
Repeated live Jev evaluations increased automatic decisions from five under Milestone 7 alone to
eight or nine; every accepted decision matched its label and no semantic false equivalence was
accepted. The latest run accepted four semantic conflicts for nine total decisions. The equivalent
paraphrase remained ambiguous because it did not clear the 0.90/0.60 action thresholds. Variation
near the difference threshold is additional evidence for the conservative fallback and Milestone
8A retention review.

A separate 2026-09-20 real-slate replay used ten 2026-09-19 NCAA games plus ten NFL and ten MLB
games on 2026-09-20. All 30 games existed on both platforms, producing 60 cross-platform pairs.
Every pair had a deterministic settlement conflict, so all 60 were rejected before Jev and the
Jev call count was zero. This validates the veto boundary but provides no evidence that Jev adds
value on that real slate. Milestone 8A retains the required keep/simplify/remove decision.

Current support remains conservative: no live semantic equivalence has cleared the automatic
threshold, and Jev depends on an external gateway/model whose behavior, availability, and cost can
change. Spreads, totals, partial-game markets, props, futures, pushes, and season-long identity
still require type-specific models and tests.

See [Implementation plan](../planning/IMPLEMENTATION_PLAN.md) for milestone gates and
[Sports MCP design](SPORTS_MCP.md) for provider discovery, identity, and projection contracts.
