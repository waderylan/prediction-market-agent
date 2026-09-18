# Milestones 3?6 verification and file inventory

Completed 2026-09-18. Scope was extended by the user from Milestones 3?4 to 3?6. No push.

## Milestone 3: 4f18b52 ? Build Polymarket MCP server with bounded structured tools

- `README.md`
- `docs/planning/IMPLEMENTATION_PLAN.md`
- `docs/research/MCP_VERTICAL_SLICE.md`
- `pyproject.toml`
- `src/market_agent/mcp/__init__.py`
- `src/market_agent/mcp/polymarket.py`
- `tests/integration/test_polymarket_mcp.py`
- `tests/live/test_polymarket_mcp_live.py`
- `uv.lock`

## Milestone 4: 723c3f8 ? Build conversational Polymarket contract reader vertical slice

- `.dockerignore`
- `.env.example`
- `Dockerfile`
- `README.md`
- `docs/planning/IMPLEMENTATION_PLAN.md`
- `docs/research/MCP_VERTICAL_SLICE.md`
- `main.py`
- `pyproject.toml`
- `scripts/codex_gateway.py`
- `src/market_agent/agent.py`
- `src/market_agent/app.py`
- `src/market_agent/config.py`
- `tests/integration/test_chat.py`
- `tests/integration/test_stdio.py`
- `tests/live/test_chat_live.py`
- `tests/support/polymarket_server.py`
- `tests/unit/test_config.py`
- `uv.lock`

## Milestone 5: 9e9daf6 ? Add independent Kalshi MCP and semantic platform selection

- `README.md`
- `docs/planning/IMPLEMENTATION_PLAN.md`
- `docs/research/MCP_VERTICAL_SLICE.md`
- `src/market_agent/agent.py`
- `src/market_agent/app.py`
- `src/market_agent/mcp/common.py`
- `src/market_agent/mcp/kalshi.py`
- `src/market_agent/mcp/polymarket.py`
- `src/market_agent/mcp/servers.json`
- `tests/integration/test_kalshi_mcp.py`
- `tests/integration/test_stdio.py`
- `tests/live/test_chat_live.py`
- `tests/live/test_kalshi_mcp_live.py`

## Milestone 6: Add deterministic contract checks before cross-market interpretation

- `README.md`
- `docs/planning/IMPLEMENTATION_PLAN.md`
- `docs/research/CONTRACT_MATCHING.md`
- `docs/research/MILESTONES_3_6_VERIFICATION.md`
- `src/market_agent/agent.py`
- `src/market_agent/domain/matching.py`
- `tests/integration/test_kalshi_mcp.py`
- `tests/live/test_chat_live.py`
- `tests/unit/test_matching.py`

## Verification results

| Check | Actual result |
|---|---|
| M3 offline suite | 45 passed |
| M4 offline suite | 62 passed |
| M5 offline suite | 82 passed |
| Final offline suite | 112 passed; 11 live cases deselected |
| Matcher cases | 26 passed within offline suite |
| Ruff lint and format | Passed |
| Strict mypy | Passed, 17 source files |
| Diff whitespace | Passed |
| Public provider checks | 2 passed separately |
| Polymarket MCP public stdio | 1 passed separately |
| Kalshi MCP public stdio | 1 passed separately |
| Real model integration | M4: 4 passed; M5: 7 passed; M6: 7 passed plus targeted refined cross-platform check |
| Real model identity | Headless gpt-5.6-sol, medium effort; no Astra or API-key billing |
| Local HTTP | Real /chat 200 and no-tool response; tests cover request validation and failures |
| Docker | Multi-stage builds passed; UID 10001; 0.0.0.0; PORT 8080 default and 9090 override; health 200; invalid body 422 |
| Docker live behavior | Both MCP servers called through the adapter; explicit settlement mismatch with source links and quotes |
| Docker memory | Same-session contract follow-up recalled sources; different session reported no prior context |
| Production GPT-5 API | Not run: no API credential configured; production backend retained |
| Cloud Run | Not deployed; later milestone |

The ordinary suite uses fixture-backed HTTP and scripted models; real MCP sessions and subprocesses
are exercised without external network dependencies. Live checks are explicitly enabled. Model
selection quality was verified with Sol; those checks are not evidence of GPT-5 quality.

## Decisions, deviations, and limits

- Official MCP v1 FastMCP is pinned because the installed adapter requires MCP <2.
- Separate Python stdio processes preserve the fixed architecture and require no Node runtime.
- Bounded canonical projections omit raw payloads; tool data is validated before model use.
- Framework-native InMemorySaver owns conversational state. One worker/instance is required;
  sessions are unauthenticated and memory is not durable or evicted.
- Request-owned sessions simplify cleanup and recovery, with process-start overhead per turn.
- User-authorized local Codex gateway is outside the container; CLI authentication is never copied.
- Code-generated pair verdicts make settlement differences visible. Unsupported semantics remain
  unresolved; deterministic matching is intentionally conservative and scoped to supplied terms.
- No order books, Jev implementation, Tavily, ledger, forecast output, or deployment was added.
- No force-compaction tool was exposed; an ignored handoff checkpoint was saved before M5.
- PROCESS_LOG.md was untouched. Transcript and handoff remain ignored local evidence.
- A third-party Starlette/AnyIO deprecation warning remains; tests pass.

Execution records and official-source research links are in IMPLEMENTATION_PLAN.md,
MCP_VERTICAL_SLICE.md, and CONTRACT_MATCHING.md.
