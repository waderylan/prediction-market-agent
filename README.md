# Prediction Market Research Agent

This repository contains CSCI 599 Assignment 1: a tool-using agent with MCP integration, conversational memory, and a Google Cloud Run deployment. Implementation currently covers the repository foundation; market API and MCP work follows the gated implementation plan.

## Proposed Project

The project is a read-only research agent for binary events listed on both Polymarket and Kalshi. It will find comparable contracts, verify that their resolution rules describe the same outcome, inspect a bounded set of related contracts and current news, and return a concise probability assessment that the user may save for later evaluation.

The proposed workflow is:

1. Search Polymarket and Kalshi for a primary contract pair and up to three related contracts per platform.
2. Normalize dates, thresholds, and outcome direction in code.
3. Use Jev for the narrow decision of whether the primary contracts are equivalent.
4. Gather current evidence with a fixed Tavily search budget.
5. Use GPT-5 to synthesize the evidence and return `YES`, `NO`, or `NO POSITION`.
6. Save a forecast through the SQLite ledger only when requested.

## Proposed Architecture

- LangGraph agent with a FastAPI `POST /chat` endpoint.
- LangGraph checkpointer keyed by `session_id` for conversational memory.
- Polymarket, Kalshi, Tavily, and SQLite MCP servers.
- OpenAI GPT-5 for tool selection and evidence synthesis.
- Jev through Vercel AI Gateway for contract-equivalence classification.
- Docker deployment to Google Cloud Run.

The scope excludes trading, brokerage connections, continuous monitoring, automated settlement, dashboards, and custom price-prediction models.

## Development Approach

This course is designed to teach practical development with AI tools, so LLM-assisted coding, debugging, and refactoring are expected parts of the project workflow. The student remains responsible for understanding the system, verifying its behavior, and explaining the implementation and design decisions.

## Development Setup

Python 3.12 or newer and [uv](https://docs.astral.sh/uv/) are required for local development. From the repository root:

```powershell
uv sync --all-extras
Copy-Item .env.example .env
```

Replace only the placeholder values in the ignored `.env` file. Configuration validation reports missing required variables without printing their values.

Run the local quality suite:

```powershell
uv run pytest -m "not live_smoke"
uv run ruff check .
uv run mypy src
```

Test markers separate isolated unit tests, local integration tests, and opt-in live checks:

```powershell
uv run pytest -m unit
uv run pytest -m integration
uv run pytest -m live_smoke
```

Live-smoke tests are never part of the default deterministic suite.

## Documentation

- [Focused project proposal](PROJECT_PROPOSAL.md)
- [Implementation plan](IMPLEMENTATION_PLAN.md)
- [Assignment requirements](Assignment_1_Description.md)
