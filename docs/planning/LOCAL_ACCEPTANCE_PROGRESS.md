# Local acceptance progress

Scope: local agent, all four MCP services, browser inspection UI, and Docker parity. Cloud Run
deployment and the watch branch remain separate work.

## Completed

### Kessel browser workbench — 2026-10-01

- Added `scripts/run_kessel_ui.py`: starts separate localhost agents through Kessel's Claude Code
  and Codex routes. The browser selects provider, model, and reasoning effort and shows MCP calls.
  Kessel injects credentials into agent processes; no key is present in browser requests or files.
- Adapted the agent to disable parallel tool calls for Kessel, which rejects that option. Retained
  the existing behavior for other model endpoints.
- Verified `POST /api/chat/inspect` through each provider with a no-tool moneyline question:
  both returned a substantive answer and an empty activity list.
- Verified real sports routing while both games were live (19:45 PDT): Claude Code used
  `sports_state_find_games` and `sports_state_get_game_state` for Steelers at Browns (10–21,
  start of Q4); Codex used the same two tools for Phillies at Braves (2–6, bottom of 8th).
  All four MCP calls succeeded. Live scores can change after these observations.
- Local quality gate: 394 non-live tests passed; Ruff check and format check passed; mypy passed.
  The config test now runs outside the repository so a real ignored `.env` cannot alter its result.

## Remaining

- Verify same-session memory, cross-session isolation, malformed HTTP requests, and additional
  request-specific tool routing with the real model.
- Verify local stdio MCP box-score views for the live NFL and MLB games and run bounded live
  provider checks for NFL, NCAA football, and MLB. The configured MCP process still reports the
  older NFL metadata error; the checked-in parser accepts the current ESPN payload.
- Build and run the Docker image, inspect UID and `PORT`, and run `/chat` plus follow-up through
  a reachable model backend.
- Probe partial failures across MCP servers and inspect controlled outputs and traces.
