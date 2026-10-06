# SportsWatch MCP — Process Log

Rylan Wade · CSCI 599 Assignment 1

## AI tools used

- OpenAI Codex with GPT-5.6 Sol on my education subscription and GPT-6 Sol on my personal subscription for planning, implementation, debugging, and documentation.
- TypeSafe Jev through the Vercel AI Gateway for an experimental contract-matching evaluation; the model version is not recorded. I removed it from the final agent.
- Gemini 3.8 Flash as the deployed agent model on Cloud Run.

## Development narrative

I began with an agent that used MCP to research prediction markets. In the implementation prompt I specified, “Polymarket and Kalshi must remain separate future MCP servers,” so the model could choose a source based on the query. Codex built typed public API clients, real stdio MCP servers, and a FastAPI/LangGraph tool loop with session-scoped memory. Live checks found a Kalshi Padres contract that direct ticker lookup could retrieve but bounded catalog search missed. Series-filtered discovery repaired that path; Polymarket also needed corrections to pagination, resolution status, and quote semantics.

I then expanded the use case to sports information alongside prediction-market research for MLB, NFL, and NCAA football. The agent gained game-scoped market discovery, a separate sports-state MCP, exact-game player and play tools, and bounded Tavily evidence retrieval. A checksummed, opaque `game_ref` let follow-up tools address the same provider event without reconstructing team names or dates. To evaluate that boundary, I had Codex produce an eight-case black-box MCP exam covering ambiguous teams, unchanged references, scheduled-game nulls, phase semantics, and timezone-scoped identity. I then used a 25-scenario market MCP exam with exact tool calls, field-level pass criteria, and strict scoring; hardening raised the score from 13/25 to 21/25. Jev was tested for contract equivalence, then removed after the real-market evaluation showed no useful safe comparison and forced review downgraded three correct rejections.

I built a watch feature that turned plain-language game conditions into rules, polled games, and sent triggered alerts through Telegram. After days of work on that branch, I realized the feature had no use at all and cut it from the submission. I deployed the Gemini-backed agent to Cloud Run. The revision started four MCP subprocesses per request and exceeded its 512 MiB memory limit. A per-instance session pool and 1 GiB limit addressed the observed failure.

## Verification narrative

I got the transcript idea from [HackerRank Orchestrate](https://www.hackerrank.com/blog/getting-better-at-orchestrate/), where the AI chat record was part of how builders were evaluated. I asked Codex to put logging instructions in `AGENTS.md` so coding sessions would record their work in `AI_TRANSCRIPT.md`. Entries contained the user prompt, agent response, work performed, and tool and branch context. I later narrowed the log to verified major features and bugs. I used those entries alongside Git commits and test reports to reconstruct which prompts produced working changes, where the agent got stuck, and what I decided to keep or remove. The transcript recorded the process; the tests and live traces checked the behavior.

I used fixtures and scripted model replies for repeatable tests, while the MCP integration tests still opened real stdio sessions and exercised `tools/list` and `tools/call`. Live provider checks and real-model tests covered a different question: whether the current APIs and model routing worked outside the fixtures. I inspected tool traces because a plausible answer alone did not show that the agent used the intended MCP server. In local Codex testing it sometimes chose web search, so I had to give an explicit MCP-only instruction when evaluating those tools.

I also gave MCP-capable agents scored exams: sets of tasks they had to complete using only the registered MCP tools and returned data, without web search or sports facts from memory. For each task, the agent had to show its tool calls, the response fields it relied on, and its conclusion. The eight-task sports-state exam checked whether it could discover a game, resolve an ambiguous team, copy an opaque `game_ref` unchanged into detail calls, and distinguish scheduled, live, and final state across timezones. The 25-scenario market exam added invalid arguments, pagination cursors, quote provenance, contract eligibility, and provider failures. I scored exact fields and controlled behavior, including no upstream request for invalid input and no guessed identifiers or cross-provider substitution. Under that strict rubric, the market MCP score rose from 13/25 to 21/25 after fixes; a blocked live cursor case still received zero. This tested whether an agent could obtain the right evidence through MCP accurately and within the tools' request bounds, rather than merely produce a plausible answer.

The adversarial suite tested unavailable servers, tool errors, malformed responses, excess tool calls, and session isolation; its recorded run passed 389 offline tests. For the deployment fix, 401 offline tests passed and 12 bounded Cloud Run probes covered cold and warm requests, memory, recall, all four sources, and concurrent requests. The performance report recorded no OOM or HTTP 429 in those probes. These are recorded development results, not a new test run for this log.

## What I learned

I learned to check whether an extra feature has a use before expanding the project: I spent days and tokens on watches before realizing they had no use at all. Codex sometimes used web search instead of my MCPs, so I had to specify an MCP-only test, use a shared `game_ref` to keep tools on the same event, and grade agent runs on how accurately and efficiently they got data through MCP. Local tests also missed the repeated MCP startup and memory limit problem, which I found by measuring the deployed service.

## External code attribution

- The Tavily provider's optional keyless-mode handling was adapted from <https://github.com/tavily-ai/tavily-mcp/blob/main/src/index.ts> so the project could use the same documented public access path. The source comment in `src/market_agent/providers/research.py` also records this attribution.
