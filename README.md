# Sports Prediction-Market Research

A read-only CSCI 599 course project for sports prediction-market research on Kalshi and
Polymarket. Initial support covers MLB, NFL, and NCAA Division I football game winners.
The two MCP servers turn team names into
provider-backed candidates with explicit event identity, labeled prices, schedules, rules,
and discovery coverage. They never place orders or access accounts.

Start with `kalshi_search_markets(query="Yankees")` or
`polymarket_search_markets(query="Chiefs vs Bills")`. No exchange taxonomy or constructed
ticker is required. For college football, use a school name and `league="ncaa_football"`.
Shared cities and ambiguous abbreviations produce clarification choices.

## What is implemented

| Capability | Current behavior |
|---|---|
| Two independent MCP servers | Kalshi and Polymarket, public GET requests, real stdio discovery and calls |
| Sports discovery | MLB, NFL, NCAA Division I FBS/FCS; full-game winners only |
| Team identity | Exact aliases from a reviewed provider catalog; same-city teams stay distinct |
| Candidate selection | Both requested opponents must match; separate event IDs preserve doubleheaders |
| Prices | Outcome labels and decimal prices; snapshots, last trades, and derived complements stay distinct |
| Coverage | Actual pages/events/contracts scanned, truncation, continuation evidence, and scoped totals |
| Contract detail | Rules, separate timing fields, API provenance, available human-facing links |
| Application | FastAPI, LangGraph tool loop, session memory, local inspection UI |
| Comparison boundary | Deterministic contract checks run before synthesis; sports equivalence is not implemented |

Sports-specific matching, agent prompt refinement, external evidence research, forecasting,
a saved-forecast ledger, and Cloud Run deployment are **future work**. The current agent accepts
the sports MCP result schemas, but its comparison engine does not establish equivalence
between named-team sports contracts.

The product can expand to additional sports and contract types, including spreads, totals,
props, and futures. These require verified provider mappings, typed models, settlement
checks, and tests; they are not enabled by adding team aliases. See the
[implementation plan](docs/planning/IMPLEMENTATION_PLAN.md) for acceptance criteria.

Read [Sports MCP design](docs/research/SPORTS_MCP.md) for the complete server contract,
catalog maintenance procedure, algorithms, and request budgets.

## Setup and run

Requires Python 3.12+ and [uv](https://docs.astral.sh/uv/).

```powershell
uv sync --all-extras
Copy-Item .env.example .env
```

Keep credentials in the ignored `.env`. The MCP servers need no exchange or LLM credentials:

```powershell
uv run python -m market_agent.mcp.kalshi
uv run python -m market_agent.mcp.polymarket
```

These commands speak MCP on stdin/stdout; use an MCP client rather than an HTTP browser.
The packaged [server manifest](src/market_agent/mcp/servers.json) configures both processes
for the agent.

For the conversational application, configure `OPENAI_API_KEY` and run:

```powershell
uv run python main.py
```

The application binds to `0.0.0.0:$PORT` (default 8080). Its assignment endpoint is:

```text
POST /chat
{"query": "Find Kalshi Yankees game-winner markets", "session_id": "sports-1"}

Response:
{"response": "..."}
```

Reuse a session ID for follow-ups; use a new, unguessable ID for a new conversation.
IDs are not authentication. LangGraph memory lasts for the process lifetime.
Interactive HTTP documentation is at `/docs`.

## Tool reference

| Tool | Inputs |
|---|---|
| `kalshi_search_markets` | `query`, `status="open"`, `limit=5`, optional `league`, `event_date`, `series_ticker` |
| `kalshi_search_series` | `query`, optional exact `category` and `tags`, `limit=5` |
| `kalshi_get_market` | Exact returned uppercase `market_id` |
| `polymarket_search_markets` | `query`, `status="open"`, `limit=5`, optional `league`, `event_date` |
| `polymarket_get_market` | Exact returned numeric Gamma `market_id` |

- Limits are strict integers from 1 through 10 in the client-visible MCP schema.
- League values are `mlb`, `nfl`, and `ncaa_football`.
- `event_date` is an ISO scheduled **UTC** date, independent of trading close.
- Sports discovery accepts full-game winners; it does not substitute a spread, total,
  partial-game winner, player prop, season series, or future.
- Clarification results include `clarification` and `choices`; they do not trigger upstream requests.
- Multiple matching event IDs appear in `discovery.matching_events`. Select the intended
  game before interpreting one price; two games on the same day remain separate.
- Explicit series discovery is an optional precision control, not a prerequisite for an
  ordinary sports query.
- Fetch details before interpreting settlement rules. A displayed price is not a forecast.
- Empty bounded discovery is not proof of absence. Polymarket automatically checks its league
  catalog when exhausted text search yields no qualifying game and page budget remains.

## Local inspection UI

```powershell
uv run python scripts/run_chat_ui.py
```

This starts Market Lens at `http://127.0.0.1:3000`, the FastAPI backend, and the local
Codex test gateway. The gateway supplies model decisions; the application still owns
LangGraph memory and performs real MCP calls. The UI shows actual tool activity and
supports session follow-ups. Ctrl+C stops the local processes.

The default local test model is `gpt-5.6-sol` with medium effort. UI controls also support
the configured Terra/Luna options and reasoning levels. This development gateway uses local
CLI authentication and stays outside the deployment image. It is not a production API backend.
Use `--no-browser` or `--port 3001` on the runner as needed.

For separate gateway/backend terminals:

```powershell
uv run python scripts/codex_gateway.py
# In another terminal:
$env:OPENAI_API_KEY = "local-codex-placeholder"
$env:OPENAI_BASE_URL = "http://127.0.0.1:8091/v1"
$env:LLM_TIMEOUT_SECONDS = "120"
uv run python main.py
```

Clear these overrides before using the configured cloud model.

## Verification

```powershell
uv run pytest -m "not live_smoke"
uv run ruff check .
uv run ruff format --check .
uv run mypy src
```

The deterministic suite uses fixture HTTP and scripted model replies while exercising real
MCP sessions and the agent. It does not require a currently open game.

Bounded public checks:

```powershell
$env:RUN_LIVE_SMOKE = "1"
uv run pytest tests/live/test_sports_mcp_live.py tests/live/test_market_clients_live.py tests/live/test_kalshi_mcp_live.py tests/live/test_polymarket_mcp_live.py
Remove-Item Env:RUN_LIVE_SMOKE
```

The sports live checks discover schemas, reject `limit=20`, search all three supported
leagues, and retrieve a returned contract when available. They do not fabricate an expected
open game. See [Testing](docs/research/TESTING.md) for test boundaries
and [Provider contracts](docs/research/MARKET_API_FEASIBILITY.md) for primary sources.

Real-model checks are separate: configure a backend, set `RUN_LIVE_AGENT=1`, and run
`tests/live/test_chat_live.py`. Scripted-model tests establish schema consumption and control
flow, not model selection quality.

## Container and deployment

```powershell
docker build -t market-agent:local .
docker run --rm -p 8080:8080 --env-file .env market-agent:local
```

The multi-stage image installs locked runtime dependencies and runs as UID 10001.
It includes both Python MCP servers and requires no Node runtime. For Docker Desktop with
the host test gateway, override `OPENAI_BASE_URL=http://host.docker.internal:8091/v1`
and use a local placeholder API key.

Cloud Run is not deployed. Deployment remains an assignment deliverable:
build the image, publish it to Artifact Registry, deploy with runtime environment variables,
one worker and `--max-instances 1`, then verify the live `POST /chat` URL and memory.
The [assignment](docs/assignment/Assignment_1_Description.md) supplies the deployment commands
and required submission contract. Never deploy the local CLI gateway or copy local credentials
into an image.

Public provider calls use no account credentials in this implementation. LLM usage, Cloud Run,
builds, image storage, and future external research can incur provider charges. There is no
continuous polling or background research. No latency or bandwidth improvement is claimed
without measurement.

## Architecture

```mermaid
flowchart LR
    U[User] -->|query and session_id| F[FastAPI POST /chat]
    F -->|response| U
    F -->|invoke| G[LangGraph reasoning loop]
    G -->|final message| F
    G <--> L[Configured cloud LLM or local test gateway]
    G <--> M[InMemorySaver by session_id]
    G --> C[Deterministic contract check]
    G --> A[MCP client adapter]
    A <-->|stdio tools/list, tools/call, results| K[Kalshi MCP]
    A <-->|separate stdio session and results| P[Polymarket MCP]
    K --> S[Exact sports identity and projections]
    P --> S
    K <-->|bounded GET and response| KA[Public Kalshi API]
    P <-->|bounded GET and response| PA[Public Gamma API]
```

```mermaid
flowchart LR
    Q[Query and session context] --> R[LLM reasoning]
    R --> T[Tool selection]
    T --> C[MCP tools/call]
    C --> S[Server validation and bounded provider requests]
    S --> V[Structured result or controlled error]
    V --> H[Host schema validation and matching report]
    H --> R
    R -->|Answer ready or tool budget exhausted| O[LLM synthesis and response]
```

```mermaid
flowchart LR
    ENV[Ignored local .env / shell variables] --> LOCAL[Local Docker or Python]
    IMG[Multi-stage Docker image] --> LOCAL
    LOCAL --> DEV[Optional host test gateway]
    IMG -. Planned .-> AR[Artifact Registry]
    AR -. Planned .-> CR[Cloud Run: one instance]
    DOTENV[Ignored local .env] -. source into shell .-> SEC[Shell variables]
    SEC -. Planned --set-env-vars .-> CR
    CR -. Planned .-> URL[Live URL: deployment required]
```

The servers are student-authored using the official
[MCP Python SDK](https://github.com/modelcontextprotocol/python-sdk).
The application uses [LangGraph](https://docs.langchain.com/oss/python/langgraph/overview),
[FastAPI](https://fastapi.tiangolo.com/), and
[langchain-mcp-adapters](https://github.com/langchain-ai/langchain-mcp-adapters).

## Documentation map

- [Sports MCP design and catalog maintenance](docs/research/SPORTS_MCP.md)
- [Runtime architecture](docs/research/MCP_VERTICAL_SLICE.md)
- [Provider contracts and field mapping](docs/research/MARKET_API_FEASIBILITY.md)
- [Current matching and future sports comparison](docs/research/CONTRACT_MATCHING.md)
- [Testing](docs/research/TESTING.md)
- [Product scope](docs/planning/PROJECT_PROPOSAL.md)
- [Remaining implementation plan](docs/planning/IMPLEMENTATION_PLAN.md)

`PROCESS_LOG.md` is Rylan Wade's personal reflection and remains a required submission
artifact. `AI_TRANSCRIPT.md` is separate automatic development evidence, governed by
`AGENTS.md`; it does not replace personal authorship.
