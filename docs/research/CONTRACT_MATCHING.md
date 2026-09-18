# Deterministic contract matching — Milestone 6

## Decision

Use conservative rejection and explicit uncertainty. A semantic model may explain unresolved
terms, but it does not override the deterministic comparison gate in this milestone. No Jev,
news search, order-book tools, or forecasting subsystem is introduced.

## Interface and lifecycle

- `assess_pair(Polymarket CanonicalMarket, Kalshi CanonicalMarket, truncation flags)` returns
  a typed verdict, relationship label, per-dimension evidence, and comparison eligibility.
- `match_candidates` deduplicates IDs, takes at most three candidates per platform, and evaluates
  at most nine pairs. No network or model calls occur in this layer.
- The graph collects validated detail snapshots within the current turn. After both platforms
  are represented, matching runs in the tool node before the next model call. It supplies the
  report to the reasoning loop and adds an independent, code-generated notice to `/chat` output.
- Per-turn candidate state resets; previous snapshots and reports remain in native message memory.
  Cross-platform requests are instructed to fetch both contracts. Search summaries alone cannot
  establish equivalence because they omit rules.

## Supported deterministic evidence

| Dimension | Treatment |
|---|---|
| Identity | Explicit conditional subjects; otherwise normalized titles, with differing titles unresolved |
| Binary outcomes | Require exactly YES and NO; array order is immaterial |
| Polarity | Explicit predicate negation and YES/NO payout; simple title negation |
| Threshold / unit / inclusivity | Decimal parsing and explicit comparison operators; USD/dollars and percent/% aliases |
| Event time | Explicit ISO timestamps with offsets normalized to UTC; naive timestamps unresolved |
| Timing | `on`, `by`, and `at` remain distinct |
| Settlement trigger | Recognized news-consensus condition versus inauguration; retain fallback distinction |
| Authority | Identical supplied authority agrees; missing/different labels require review rather than guessed aliases |
| Cancellation | Explicit cancellation payout conflicts reject; other clause differences require review |
| Exclusions / edge cases | Preserve excerpts and flag unresolved differences |
| Full rules | Compare the entire normalized text, never just a bounded preview |
| Close / resolution schedule | Report differences as unresolved; these fields are not event cutoffs |

The explicit threshold parser recognizes a leading conditional such as:
`If Bitcoin is above 100000 USD on 2028-01-01T00:00:00Z, the market resolves Yes.`
It is not a general natural-language contract parser. Unsupported phrasings require review.
Lexical normalization changes Unicode compatibility, case, and whitespace only; it retains
negation and numeric boundaries. Positive equivalence requires matching checks, identical full
supplied rules, known agreeing authority, and agreeing schedule metadata. The claim is scoped
to supplied terms; terms in external documents remain unverified.

## Observed iteration

- Reduced election fixtures established a dangerous near-match: news agreement with fallback
  versus direct inauguration. Deterministic rejection occurs before synthesis.
- The first Docker comparison safely returned ambiguous because live wording used “Associated
  Press” and “the next person inaugurated,” unlike the reduced fixture. Added these narrow observed
  forms and a regression case. This improves a specific parser boundary without guessing arbitrary
  paraphrases or calling all election contracts equivalent.
- A code-generated notice keeps comparison eligibility visible even if the model's explanation
  omits it. Model prose remains probabilistic and is not a formal semantic verifier.

## Verification and limits

- Fixture tests include threshold, unit, boundary, day, timezone, polarity, cancellation, authority,
  exclusions, late rule-text differences, truncated rules, nonbinary markets, deduplication, and
  candidate bounds. Positive tests cover identical supplied terms.
- Integration tests verify both rejected and ambiguous reports reach the model before synthesis,
  and that single-platform/no-tool paths retain their behavior.
- Scope is intentionally conservative: many valid equivalents will require review. No general
  sports, monetary-unit conversion, date-language parser, or logical theorem prover is claimed.
- Structured state lives in LangGraph's checkpointer; no custom conversation-memory map exists.
- Recommended next gate: evaluate semantic equivalence on a labeled fixture set as planned in
  Milestone 7, retaining deterministic vetoes and visible unknowns.

## Execution results — 2026-09-18

- 112 offline tests passed, including 26 matcher cases; Ruff lint/format, strict mypy, and diff
  whitespace checks passed.
- Public checks run separately: provider detail 2 passed, Polymarket MCP 1 passed, Kalshi MCP 1 passed.
- Real headless Sol-medium suite: 7 passed. After refining observed settlement wording, the targeted
  cross-platform case passed again and requires the explicit settlement-trigger rejection notice.
- Docker runtime includes both independently discoverable MCP processes, runs as UID 10001, and
  answered health checks on default 8080 and overridden 9090. Invalid chat input returned 422.
  The host Codex gateway provided real model decisions while the container executed public MCP calls.
- A request sent before container startup completed initially failed at the HTTP transport. Waiting
  for `/health` before the acceptance request resolved the startup race; no application failure was
  hidden. No deployment or real-key GPT-5 verification is claimed.
