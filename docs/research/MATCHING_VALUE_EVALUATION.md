# Milestone 8A Matching Value Evaluation

## Decision

- **Milestone 7:** simplify and retain the smallest proven deterministic safety boundary.
- **Milestone 8:** remove Jev completely.
- **User-visible policy:** compare prices only when typed event identity, named outcome,
  game-winner type, postponement/cancellation terms, and exact complete rules agree. Otherwise
  explain the conflict or ambiguity and refuse the comparison.

This decision was implemented in the same milestone. The Jev graph node, reviewer, standalone MCP
server, feature flags, gateway settings, prompts, fixtures, and dedicated tests were removed. The
specialized authority, official-result, overtime, tie, shortened-game, abandoned-game, and
exclusion parsers were also removed. Different full rule text now remains ambiguous unless the
retained core checks establish a conflict.

## Predeclared Retention Thresholds

These thresholds were fixed before scoring the held-out real pairs:

- Zero false equivalences is mandatory.
- A retained matcher must authorize at least 10% of manually equivalent real pairs or prevent a
  correctness failure missed by the smaller identity-plus-core-settlement baseline.
- Jev must add at least 10 percentage points of correct automatic coverage with no false
  equivalence.
- Jev median provider latency must remain below 3 seconds and input use must be reported even when
  the correctness threshold fails.
- A component that misses its value threshold is removed or reduced in this milestone rather than
  retained as disabled code.

## Evaluation Set

On 2026-09-20, bounded public discovery selected 25 real games across MLB, NFL, and NCAA football
on September 18, 19, and 20. Seventeen games existed on both Polymarket and Kalshi. Two shared
games lacked complete Kalshi detail rules, leaving 15 complete pairs for scoring. The sampled
contracts were resolved; their terminal 0.99/1.00 or 0.01/0.00 differences were not actionable
prices.

Each label was manually reviewed from the complete supplied terms. All 15 pairs concerned the same
game and named outcome but were `different`: Polymarket waited for a postponed game to complete and
resolved cancellation 50/50, while Kalshi imposed a two-day or 48-hour start window and used fair-
price settlement after that window. Prices and final scores were excluded from labeling.

| Case | League/date | Polymarket | Kalshi | Manual label | Forced Jev |
|---|---|---|---|---|---|
| KC–PIT | MLB 09-18 | `4495388` | `KXMLBGAME-26SEP181840KCPIT-PIT` | different | different |
| MIL–BAL | MLB 09-18 | `4495390` | `KXMLBGAME-26SEP181905MILBAL-BAL` | different | different |
| BOS–TB | MLB 09-18 | `4495394` | `KXMLBGAME-26SEP181910BOSTB-TB` | different | different |
| ATL–HOU | MLB 09-20 | `4551913` | `KXMLBGAME-26SEP201410ATLHOU-HOU` | different | different |
| DET–CWS | MLB 09-20 | `4551907` | `KXMLBGAME-26SEP201410DETCWS-CWS` | different | different |
| WSH–STL | MLB 09-20 | `4551920` | `KXMLBGAME-26SEP201415WSHSTL-STL` | different | different |
| TOR–TEX | MLB 09-20 | `4551931` | `KXMLBGAME-26SEP201435TORTEX-TEX` | different | different |
| SEA–COL | MLB 09-20 | `4551941` | `KXMLBGAME-26SEP201510SEACOL-COL` | different | different |
| CIN–HOU | NFL 09-20 | `3482626` | `KXNFLGAME-26SEP20CINHOU-HOU` | different | ambiguous |
| CLE–TB | NFL 09-20 | `3482681` | `KXNFLGAME-26SEP20CLETB-TB` | different | ambiguous |
| GB–NYJ | NFL 09-20 | `3482662` | `KXNFLGAME-26SEP20GBNYJ-NYJ` | different | ambiguous |
| CCU–DEL | NCAA 09-19 | `4350027` | `KXNCAAFGAME-26SEP19CCARDEL-DEL` | different | different |
| TUL–KST | NCAA 09-19 | `4350040` | `KXNCAAFGAME-26SEP19TULNKSU-KSU` | different | different |
| BGSU–ISU | NCAA 09-19 | `4350038` | `KXNCAAFGAME-26SEP19BGSUISU-ISU` | different | different |
| ASU–KU | NCAA 09-19 | `4350030` | `KXNCAAFGAME-26SEP19ASUKU-KU` | different | different |

This is a held-out real-market sample, not a balanced benchmark. It contains no manually
equivalent real pair, so equivalent recall is unmeasured. That absence is itself material: neither
the earlier 60-pair replay nor this set produced one safe cross-platform price comparison.

## Results

| Configuration | Correct automatic | Ambiguous | False equivalent | Useful-pair yield |
|---|---:|---:|---:|---:|
| Milestone 7 deterministic | 15/15 | 0 | 0 | 0/15 |
| Normal Jev routing | 15/15 | 0 | 0 | 0/15 |
| Forced Jev routing | 12/15 | 3 | 0 | 0/15 |
| Simple identity + postponement + cancellation baseline | 15/15 | 0 | 0 | 0/15 |

- Normal Jev made zero calls because deterministic core conflicts had already decided every pair.
- Forced Jev made 15 calls, consumed 29,254 input tokens, and had 308 ms median provider latency.
- Forced Jev added zero decisions and downgraded three correct NFL rejections to ambiguous.
- Exact billed dollar cost was unavailable from the gateway response, so token use is reported
  instead of inventing a price.
- The smaller baseline matched Milestone 7 on every real case. The removed semantic dimensions
  therefore showed no measured incremental safety or useful coverage.

## Tradeoffs

- The retained deterministic subset is fast, stable, inspectable, and sufficient to explain every
  material difference observed in the real sample.
- Exact full-rule agreement is deliberately strict. It may reject semantically equivalent
  paraphrases, but the evaluation found no real equivalent pair whose price could safely be used.
- Removing Jev eliminates an external dependency, model drift, gateway failures, token cost,
  confidence tuning, a graph node, and a fourth MCP process.
- Removing specialized edge-case parsers reduces explanation detail for unsupported wording. The
  safe fallback remains `ambiguous`, never title similarity or unconstrained model judgment.
- Sports-state data remains optional corroboration and cannot alter contract equivalence or market
  settlement.

## Revisit Condition

Reintroduce semantic matching only after collecting a balanced real-market set containing actual
equivalent pairs. A replacement must beat the retained deterministic baseline on held-out data,
meet the zero-false-equivalence requirement, and create measurable safe comparison yield after
latency and cost.
