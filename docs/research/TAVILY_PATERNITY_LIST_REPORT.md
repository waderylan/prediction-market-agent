# Tavily Paternity-List Miss: Reproduction and Fix Report

Date: 2026-10-06 (game: Brewers @ Padres, NLDS Game 3, Petco Park)

## Summary

- Reproduced locally through the registered stdio MCP servers (`sports_state`, `tavily`).
- `tavily_search_game_evidence(focus="injuries")` returned one source, the ESPN game page, with a generic IL table and no paternity-list entries.
- A plain query ("Padres paternity list NLDS Game 3") through the same Tavily endpoint returned 5/5 relevant articles (Heavy, SD Union-Tribune, Reuters, Reddit, MLB.com).
- Cause is in our query construction and result filters, not Tavily coverage.
- Second cause is agent behavior: a retained but irrelevant source was treated as proof of absence.

## Reproduction

- `claude mcp list`: `polymarket`, `kalshi`, `sports_state`, `tavily` already registered at user scope via `uv run python -m market_agent.mcp.<name>` and connected. No registration needed.
- `sports_state_find_games(mlb, America/Los_Angeles, "Padres")` returned game 401908004, local date 2026-10-06, start 2026-10-07T01:30Z.
- `tavily_search_game_evidence(focus=injuries, source_policy=all)`: `evidence_found`, 1 source (ESPN game page), `rejected_result_count=4`, no cautions. Snippet is the ESPN injury table (Vaughn, Ortiz, Woodruff, Estrada, Giolito, Mize...). No mention of paternity.
- Same call with `source_policy=official_only`: `no_qualifying_sources`, `rejected_result_count=0`. Tavily returned zero results for the quoted query restricted to `mlb.com`.
- `focus=other_game_news`: ESPN page plus a betting preview that mentions Mason Miller only as a blown save. Still no paternity news.

## Root causes

1. **Query shape pulls game-page aggregators** (`research.py:420`).
   - Query: `"San Diego Padres" "Milwaukee Brewers" October 06 2026 injuries player availability mlb`.
   - Quoted full names plus date terms match game pages (ESPN, NYT Athletic, Bleacher Report, USA Today, Yahoo scoreboard), not roster-move articles.
   - Roster-move articles say "Padres", "Game 3", "paternity list". They rarely contain both full team names.
2. **Date filter rejects most real articles** (`_matches_game_date`, `research.py:286`).
   - Requires a literal date string (`2026-10-06`, `10/6/2026`, `October 6, 2026`) in title, URL, or snippet.
   - Per-filter check on the production results: team match True for all 5; date match True only for ESPN (1/5).
   - Articles use "Game 3" or "Tuesday". URL dates like `2026/10/06` also fail because only dash-separated ISO is accepted.
3. **Focus vocabulary lacks roster-availability terms** (`FOCUS_TERMS["injuries"]`).
   - No `paternity`, `bereavement`, `restricted list`, `placed on`, `added to`, `roster`.
   - The Yahoo "Padres remove player from NLDS roster" article, which does mention paternity, fails the focus filter (`focus=False`).
4. **A non-answering source reads as a negative.**
   - ESPN's injury table has no paternity category, so absence of a paternity entry is meaningless.
   - `evidence_found` plus a clean table nudged the agent to say "no reported players on the paternity list".
   - The system prompt (`agent.py:162`) covers `no_qualifying_sources` only. It does not cover `evidence_found` where no source addresses the asked topic.
5. **`include_domains=["mlb.com"]` with the quoted query returns nothing**, so `official_only` is effectively unusable for roster moves. The MLB.com articles do exist (found with a plain query).

## Fixes (in priority order)

1. **Agent prompt (highest value, smallest change)**
   - Add: a categorical negative ("no players on X") is allowed only when a retrieved source explicitly lists that category or says none exist. Otherwise say the status could not be verified from the search.
   - Add: `evidence_found` does not mean the question was answered. Check that a source mentions the asked topic.
   - Add a host-side notice in `_research_notice` (`agent.py:706`) when a retained source set contains none of the user's topic keywords.
2. **Add a `roster_moves` focus** (or extend `injuries`)
   - Query: `Padres Brewers paternity list roster move Game 3` using short team names plus the league.
   - Terms: `paternity`, `bereavement`, `restricted list`, `placed on`, `added to`, `activated`, `roster move`.
3. **Relax the date filter for articles**
   - Accept URL dates in `YYYY/MM/DD` and `YYYY-MM-DD` forms.
   - Accept `published_date` within about 3 days before the game when the text names both teams or the series (e.g. "Game 3", "NLDS", "Division Series").
   - Keep the strict check for game-page sources.
4. **Query construction**
   - Drop quotes around team names, use nicknames (`Padres Brewers`), and add the round or game number when known.
   - For `official_only`, use unquoted queries with `include_domains`. The quoted form returned zero results.
5. **Optional second query**
   - The host already allows two searches. Use the second for a topic-specific plain query when the first yields only game pages.

## Tests to add

- Unit test in `tests/unit` for `_matches_game_date` with a `2026/10/06` URL and a "Game 3" article.
- Unit test that a paternity-list snippet passes the injuries/roster focus filter.
- Integration test in `tests/integration/test_chat.py` with a stub Tavily result containing only a generic IL table, asserting the answer says status cannot be verified rather than "none".

## Notes

- Tavily results vary run to run. ESPN stayed the only retained source across four runs.
- I confirmed the Yahoo Miller article, the Heavy/SD Union-Tribune/Reuters Buehler articles, and `mlb.com/news/walker-buehler-returning-to-padres-for-nlds-game-3`. The `mlb.com/padres/news/...` URLs in the original report did not appear in my results, so I could not verify those exact paths.
- `claude mcp list` shows `jobwatcher` failing to connect (unrelated). Its configured command line embeds an auth token in plain text, so consider moving it to an env var.
- Status: this report was written before the fix. The fixes below were implemented afterward in commit `038bfb7` (source labeling, rejection reasons, `roster_moves` focus, `topic_hint`, key forwarding to the MCP subprocess) plus 429 handling. See `WEB_RESEARCH_MCP.md` for the current behavior.

---

## Addendum: all-focus local test (general Tavily failure)

Method: real keyless Tavily calls from a local script that imports the repo's own filter functions and reports which check rejects each result. Production query shape, all six focuses, 30 results total. No Cloud Run.

| Rejection reason | Count |
| --- | ---: |
| date (no literal date string in title/URL/snippet) | 24 |
| kept | 4 |
| team (non-sports page) | 1 |
| focus terms | 1 |

- Date check causes 80% of rejections. `lineups`, `venue_or_schedule` and `postgame_recap` retained 0/5 each, matching the live "0 retained / 5 rejected" traces.
- Rejected-by-date results include on-topic pages: `mlb.com/news/...game-3-starting-lineups...`, `foxsports.com/...nlds-game-3-...-oct-06-2026`, `bleacherreport.com/game/...-2026-10-6-...`, `fox6now.com/...nlds-schedule`.
  - Bleacher Report URL uses `2026-10-6`, which fails the zero-padded ISO marker.
  - Fox Sports URL uses `oct-06-2026`, which fails every marker.
- The date requirement plus game-page-dominated ranking leaves ESPN's game page as the only retained source for injuries. That source never covers roster moves.
- `postgame_recap` returned only game pages and 0 retained, so selecting it for a live game is a pure waste of a search. Nothing in the prompt forbids it. Gate it on `lifecycle == final` from game state.
- Two answer-quality issues beyond filtering:
  - `evidence_found` with off-topic sources is treated as an answer (see root cause 4).
  - The trace has only `rejected_result_count`, so the cause can't be diagnosed from logs.

### Query comparison (same 5-result budget)

| Query | Result |
| --- | --- |
| Production: quoted full names + date + "injuries player availability" | Game pages only. No paternity article. |
| `Padres Brewers NLDS Game 3 injuries roster moves` | 4/5 mention paternity and Buehler or Miller (SI, Heavy, Yahoo). |
| `Padres Brewers paternity list Mason Miller Walker Buehler` | 5/5 paternity (WSLS, Reviewing the Brew, SI, Mod Bee, Padres Mission). |
| Same short query with `topic=news` | 3/5 paternity (Yahoo, including the Miller placement article). |
| `Padres Brewers NLDS Game 3 news` / `series news` | Relevant series/Game 3 articles; MLB.com, Yahoo, SI. |
| `include_domains=["mlb.com"]` combined with a long query | Sometimes 0 results from Tavily itself. A short topic query returned MLB.com pages; one with Miller/Buehler/paternity in text. |

Results vary between runs. One `postgame_recap`-style query and the Buehler domain query returned empty, so a single empty result is weak evidence.

### Prototype relaxed filter

- Rule: keep a result if it names at least one team, and (strict date match, or URL date in `2026-10-6`/`2026/10/06`/`oct-06-2026` form, or published within 3 days of the game and mentions NLDS/division series/Game 3/playoffs).
- On the short-query results it kept all 5 paternity articles, 5/5 series-news articles, and 4/5 lineup articles (strict kept 1 or 0). Remaining rejects were a YouTube result with no date signal.
- This is a prototype only. It was tested on one game; precision against two same-day games (e.g. doubleheaders) is untested.

### Regression material

- `mlb.com/padres/news/mason-miller-on-paternity-list-during-nlds` and `.../walker-buehler-returning-to-padres-for-nlds-game-3` both return HTTP 200 now. Tavily did not surface the `/padres/news/` paths in my queries, only `mlb.com/news/walker-buehler-returning-to-padres-for-nlds-game-3`. As noted, they may not have been indexed at the live request time.
- The Yahoo article "Star closer Mason Miller goes on paternity list..." and the SI/Heavy roster-move articles are retrievable and make a better stable fixture set than live search. Save their Tavily payloads as fixtures under `tests/fixtures/tavily/`.

### Updated fix list

1. Add per-result rejection reasons (`url`, `team`, `date`, `focus`) to the result and logs. Highest diagnostic value, small change.
2. Replace the strict date requirement with the relaxed rule above; keep strict matching only when the source is a game page.
3. Rebuild the query: short nicknames, no quotes, add series/round and focus-specific words (e.g. `roster moves paternity list` for roster questions). Let the agent pass a free-text `topic` hint, or use the second search for it.
4. Extend focus vocabulary (`paternity`, `bereavement`, `restricted list`, `placed on`, `added to`), and add a `roster_moves` or `series_news` focus.
5. Prompt: no categorical negatives from sources that don't address the topic; do not choose `postgame_recap` unless game state is final; report retained vs rejected counts honestly.
6. Add the regression fixtures and tests listed above.
