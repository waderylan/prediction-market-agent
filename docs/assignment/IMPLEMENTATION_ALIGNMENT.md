# Assignment implementation and submission mapping

## Purpose

This document maps Market Lens to CSCI 599 Assignment 1. The canonical requirements remain in
[Assignment_1_Description.md](Assignment_1_Description.md). The product-facing overview, setup,
API usage, deployment status, costs, and architecture diagrams remain in the repository
[README](../../README.md).

## Required implementation contract

| Assignment requirement | Repository implementation |
|---|---|
| LLM-powered agent | The FastAPI application invokes a configured chat model through a LangGraph reasoning loop. |
| Model-driven tool choice | MCP tools are bound to the model; scripted tests verify routing and live-agent tests remain opt-in. |
| At least two MCP servers | Four separate stdio servers provide Kalshi, Polymarket, sports-state, and bounded Tavily tools. |
| Actual MCP integration | `langchain-mcp-adapters` performs MCP initialization, `tools/list`, and `tools/call`; application code does not call tool functions directly. |
| Conversational memory | LangGraph `InMemorySaver` keys state by the caller's `session_id`. |
| Required endpoint | `POST /chat` accepts `query` and `session_id` and returns one `response` string. |
| Cloud deployment | The Docker image is designed for Google Cloud Run. A live URL remains required for submission. |
| Testing | Unit, integration, stdio, bounded live-provider, and optional real-model suites cover the application and MCP boundaries. |
| Add-on: event-aware watches (beyond requirements) | LangGraph compiles exact confirmed rules; a no-model coordinator evaluates shared MCP observations into a persistent inbox. |
| Add-on: external alerts (beyond requirements) | A transactional outbox projects explicitly opted-in triggers to one configured Telegram chat; live delivery is verified from the local runner. |

## Repository deliverables

- `main.py`: application entry point.
- `src/market_agent/app.py`: FastAPI request and response contract.
- `src/market_agent/agent.py`: LangGraph workflow, memory, tool budgets, validation, matching,
  concurrent independent calls, and synthesis controls.
- `src/market_agent/mcp/`: four student-authored MCP servers and their packaged manifest.
- `tests/`: deterministic unit and integration coverage plus opt-in live checks.
- `src/market_agent/watch/`: versioned rule models, SQLite/Firestore repositories, deterministic
  evaluator/coordinator, OIDC and Secret Manager boundaries, inbox, and Telegram outbox.
- `scripts/watch_cli.py`, `scripts/run_watches.py`: bounded local Codex interface, foreground
  monitoring, and credential-free deterministic replay.
- `Dockerfile`: non-root production image containing the application and all MCP servers.
- `README.md`: product overview, setup, verification, deployment, costs, and three architecture
  diagrams.
- `PROCESS_LOG.md`: Rylan Wade's personal development reflection.
- `AI_TRANSCRIPT.md`: append-only development evidence maintained under `AGENTS.md`.

## Submission checks

- Run `uv run pytest -m "not live_smoke"`.
- Run `uv run ruff check .` and `uv run ruff format --check .`.
- Run `uv run mypy src`.
- Build and run the container with the intended runtime environment variables.
- Deploy the image to Cloud Run with one worker and `--max-instances 1`.
- Configure Firestore, Cloud Scheduler OIDC, Secret Manager, and the optional Telegram recipient;
  local substitutes do not establish live Google Cloud acceptance.
- Verify the public `POST /chat` endpoint with a new session and a same-session follow-up.
- Confirm the submitted source archive excludes `.env`, credentials, caches, virtual
  environments, and local build artifacts.
- Confirm the final submission includes the live service URL, source archive, README, diagrams,
  and Rylan Wade's completed `PROCESS_LOG.md`.

## Academic integrity boundary

The automatic transcript records qualifying development work. It does not supply personal
reflection. Rylan Wade authors the process log and verifies that its prompts, decisions, problems,
and lessons are accurate before submission.
