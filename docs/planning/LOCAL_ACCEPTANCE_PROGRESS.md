# Local acceptance progress

Scope: local agent, all four MCP services, browser inspection UI, and Docker parity. Cloud Run
deployment and the watch branch remain separate work.

## Completed

### Kessel browser workbench — 2026-10-01

- Added `scripts/run_kessel_ui.py`: starts separate localhost agents through Kessel's Claude Code
  and Codex routes. The browser selects provider, model, and reasoning effort and shows MCP calls.
  Kessel injects credentials into agent processes; no key is present in browser requests or files.
- The first Kessel version rejected parallel tool calls. After Kessel gained parallel support,
  restored the agent's parallel tool-call setting for all model endpoints.
- Verified `POST /api/chat/inspect` through each provider with a no-tool moneyline question:
  both returned a substantive answer and an empty activity list.
- Verified real sports routing while both games were live (19:45 PDT): Claude Code used
  `sports_state_find_games` and `sports_state_get_game_state` for Steelers at Browns (10–21,
  start of Q4); Codex used the same two tools for Phillies at Braves (2–6, bottom of 8th).
  All four MCP calls succeeded. Live scores can change after these observations.
- Local quality gate: 394 non-live tests passed; Ruff check and format check passed; mypy passed.
  The config test now runs outside the repository so a real ignored `.env` cannot alter its result.

### Live sports and market MCP pass — 2026-10-01

- Added `scripts/check_live_sports.py` to repeat exact-game checks through this repo's stdio MCP
  server. At about 19:47 PDT, NFL Steelers at Browns, MLB Phillies at Braves, and NCAA football
  Western Kentucky at New Mexico State were all live. For each, discovery, state, every supported
  box-score view, player directory/detail, and play-by-play passed.
- Local Kalshi stdio searches for Steelers, Browns, Phillies, and Braves returned structured game
  results without malformed-response errors. Kalshi, Polymarket, and cross-league live smoke tests
  passed (5 tests). The broader sports-state live test initially found a stale test limit: NCAA
  FCS coverage now permits up to six bounded scoreboard requests. The assertion now matches that
  contract, and the test passed on rerun.
- The configured MCP tools in this Codex session still use an older NFL parser; the repo's fresh
  stdio process passed the affected live game. Restart clients that host the old MCP process to
  load the checked-in fix.

### Kessel parallel tool calls — 2026-10-01

- Claude Code returned two independent sports-game discovery calls followed by two game-state
  calls in one `/chat/inspect` turn; all four succeeded and the answer used both results.
- `scripts/check_kessel_parallel.py` exercised streaming through both Claude Code and Codex:
  two `delta.tool_calls` were assembled by index, each `tool_call_id` received a matching tool
  response, and the final response used both. Both providers passed.
- A deterministic agent integration test checks that two concurrent MCP calls retain distinct
  tool-call IDs and both success results reach the model.

### Docker parity — 2026-10-01

- Built `market-agent:local` from the locked multi-stage Dockerfile. The runtime process reported
  UID 10001. A second container bound and answered `/health` with `PORT=8092`.
- Kessel's loopback-only service accepted Docker Desktop traffic through `host.docker.internal`
  when the HTTP Host header was explicitly set to `127.0.0.1:8000`. Added optional
  `OPENAI_HOST_HEADER` to support this local route without changing normal model endpoints.
- In the Kessel-backed container, `/chat/inspect` answered a no-tool question; a same-session
  follow-up recalled a code word and a different session did not; a Steelers–Browns question
  succeeded with sports discovery and state calls; malformed `/chat` input returned HTTP 422.

## Remaining

- Verify same-session memory, cross-session isolation, malformed HTTP requests, and additional
  request-specific tool routing with the real model.
- Probe partial failures across MCP servers and inspect controlled outputs and traces.
