# Prediction Market Research Agent

This repository contains CSCI 599 Assignment 1: a tool-using agent with MCP integration, conversational memory, and a planned Google Cloud Run deployment. Milestone 3 adds a working Polymarket MCP server over the shared market-data layer.

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

## Market Data Layer

Milestone 2 provides typed, asynchronous, read-only clients without MCP wrappers:

```python
from market_agent.providers import KalshiClient, PolymarketClient

async with PolymarketClient() as polymarket:
    candidates = await polymarket.search_markets("2028 presidential election", limit=5)

async with KalshiClient() as kalshi:
    market = await kalshi.get_market("KXPRESPERSON-28-JVAN")
```

Both clients return the shared `CanonicalMarket` type and raise typed errors for transport, HTTP,
validation, and missing-data failures. Normal tests use saved fixtures. Run the bounded public API
checks only when intended:

```powershell
$env:RUN_LIVE_SMOKE = "1"
uv run pytest -m live_smoke
Remove-Item Env:RUN_LIVE_SMOKE
```

The provider findings, endpoint choices, limitations, and fixture policy are documented in
[Market API feasibility](docs/research/MARKET_API_FEASIBILITY.md).

## Documentation

## Polymarket MCP

Start the student-authored server with `uv run python -m market_agent.mcp.polymarket`.
It speaks MCP over stdio; it is intended to be launched by an MCP client, not called as an HTTP API.
It needs no exchange or LLM credentials. Only public read-only Gamma endpoints are used.

- `polymarket_search_markets(query, status="open", limit=5)`: at most 10 candidate summaries.
- `polymarket_get_market(market_id)`: current prices and bounded resolution rules for a numeric ID.
- Prices are decimal strings, missing fields are null, and truncated rules are explicitly labeled.
- Search covers a bounded first page; empty results are not proof of market absence.

Protocol tests: `uv run pytest tests/integration/test_polymarket_mcp.py`.
Public subprocess check: set `RUN_LIVE_SMOKE=1`, then run
`uv run pytest tests/live/test_polymarket_mcp_live.py`.

The server uses the official [MCP Python SDK](https://github.com/modelcontextprotocol/python-sdk)
FastMCP implementation. See [implementation evidence](docs/research/MCP_VERTICAL_SLICE.md)
for dependency compatibility, transport, lifecycle, bounds, and verification results.

## Project Documents

- [Focused project proposal](docs/planning/PROJECT_PROPOSAL.md)
- [Implementation plan](docs/planning/IMPLEMENTATION_PLAN.md)
- [Assignment requirements](docs/assignment/Assignment_1_Description.md)
- [Original assignment PDF](docs/assignment/Assignment_1_Description.pdf)
