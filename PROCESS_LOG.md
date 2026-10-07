# SportsWatch MCP Process Log

Rylan Wade, CSCI 599 Assignment 1

## AI tools used

- OpenAI Codex with GPT-5.6 Sol on my education subscription and GPT-6 Sol on my personal subscription for planning, implementation, debugging, and documentation.
- TypeSafe Jev through the Vercel AI Gateway for an experimental contract-matching evaluation; the model version is not recorded. I removed it from the final agent.
- Gemini 3.8 Flash as the deployed agent model on Cloud Run.
- Claude Code with Sonnet 5.5 to reproduce and fix the Tavily research bug described below.

## Development narrative

I began with an agent that used MCP to research prediction markets. In the implementation prompt I specified, "Polymarket and Kalshi must remain separate future MCP servers," so the model could choose a source based on the query. Codex built typed public API clients, real stdio MCP servers, and a FastAPI/LangGraph tool loop with session-scoped memory. Live checks found a Kalshi Padres contract that direct ticker lookup could retrieve but bounded catalog search missed. Series-filtered discovery repaired that path; Polymarket also needed corrections to pagination, resolution status, and quote semantics.

I expanded the agent to answer MLB, NFL, and NCAA football questions. The agent gained game-scoped market discovery, a separate sports-state MCP, exact-game player and play tools, and bounded Tavily searches. A checksummed `game_ref` let follow-up tools find the same game without reconstructing team names or dates. I had Codex produce an eight-case MCP exam covering ambiguous teams, unchanged references, scheduled-game nulls, phase semantics, and timezone-scoped identity. I then used a 25-scenario market MCP exam with exact tool calls, field-level pass criteria, and strict scoring; fixes raised the score from 13/25 to 21/25. Jev did not improve the real-market comparisons and changed three correct rejections to ambiguous, so I removed it.

I built a watch feature that turned plain-language game conditions into rules, polled games, and sent triggered alerts through Telegram. After days of work on that branch, I realized the feature had no use at all and cut it from the submission. I deployed the Gemini-backed agent to Cloud Run. The revision started four MCP subprocesses per request and exceeded its 512 MiB memory limit. A per-instance session pool and 1 GiB limit addressed the observed failure.

The hardest bug came from a live answer. I asked the deployed agent about the paternity list for the Padres game and it said, "There are no reported players on the paternity list." Tavily had not failed and the request returned 200, but it only returned an ESPN game page, and MLB had reported Mason Miller going on the list before Game 3. I had Claude Code run the Tavily tool locally against real MLB, NFL, and college football results. 24 of 30 results died on a check that needed the exact date written in the text, and the tool only returned a count, so nothing showed why. I changed the filters to label each source instead of dropping it, listed a reason for every rejection, added a topic hint so the agent knows when no source mentions what I asked about, and told it never to say "none" unless a source does. The Tavily key also never reached the MCP subprocess on Cloud Run, so I forward it explicitly.

## Verification narrative

I got the transcript idea from [HackerRank Orchestrate](https://www.hackerrank.com/blog/getting-better-at-orchestrate/), where the AI chat record was part of how builders were evaluated. I asked Codex to put logging instructions in `AGENTS.md` so coding sessions would record their work in `AI_TRANSCRIPT.md`. Entries contained the user prompt, agent response, work performed, and tool and branch context. I later narrowed the log to verified major features and bugs. I used those entries alongside Git commits and test reports to reconstruct which prompts produced working changes, where the agent got stuck, and what I decided to keep or remove.

I used fixtures and scripted model replies for repeatable tests. The MCP integration tests opened real stdio sessions and exercised `tools/list` and `tools/call`. Live provider and model tests checked current API responses and tool choice. I inspected tool traces because a plausible answer alone did not show that the agent used the intended MCP server. In local Codex testing it sometimes chose web search, so I had to give an explicit MCP-only instruction when evaluating those tools.

I gave MCP-capable agents scored exams that restricted them to the registered tools and returned data. For each task, the agent had to show its tool calls, the response fields it relied on, and its conclusion. The eight-task sports-state exam checked whether it could discover a game, resolve an ambiguous team, copy a `game_ref` unchanged into detail calls, and distinguish scheduled, live, and final state across timezones. The 25-scenario market exam added invalid arguments, pagination cursors, quote provenance, contract eligibility, and provider failures. I scored exact fields and controlled behavior, including no upstream request for invalid input and no guessed identifiers or cross-provider substitution. The market MCP score rose from 13/25 to 21/25 after fixes; a blocked live cursor case still received zero.

The adversarial suite tested unavailable servers, tool errors, malformed responses, excess tool calls, and session isolation; its recorded run passed 389 offline tests. For the deployment fix, 401 offline tests passed and 12 Cloud Run probes covered cold and warm requests, memory, recall, all four sources, and concurrent requests. The performance report recorded no OOM or HTTP 429 in those probes. For the Tavily fix, 135 offline tests covered each rejection reason, the 429 retry, and key forwarding, and I reran the paternity question and a full Padres game report on the deployed service; the answer cited NBC Sports for Miller's placement.

## What I learned

I learned to check whether an extra feature has a use before expanding the project: I spent days and tokens on watches before realizing they had no use at all. Codex sometimes used web search instead of my MCPs, so I had to specify an MCP-only test, use a shared `game_ref` to keep tools on the same event, and grade agent runs on how accurately and efficiently they got data through MCP. Local tests also missed the repeated MCP startup and memory limit problem, which I found by measuring the deployed service. The Tavily bug looked like a search outage until I made the tool say why it rejected each result.

## External code attribution

- The Tavily provider's optional keyless-mode handling was adapted from <https://github.com/tavily-ai/tavily-mcp/blob/main/src/index.ts> so the project could use the same documented public access path. The source comment in `src/market_agent/providers/research.py` also records this attribution.
