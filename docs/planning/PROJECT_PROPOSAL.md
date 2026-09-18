# Project Proposal: Cross-Market Prediction Research Agent

## Project Summary

This project is a read-only research agent for binary event markets listed on both Polymarket and Kalshi. Given a topic, it identifies comparable contracts, checks whether their resolution rules are truly equivalent, gathers a small amount of current evidence, and returns a concise probability assessment that the user can optionally save.

The project is intentionally not a trading system, a general research assistant, or a custom forecasting model. Its single purpose is to answer: **“How do two equivalent prediction markets price the same event, and what does a bounded evidence review suggest?”**

## Fixed Scope

### Included

- Search for the target binary contracts and a small set of related contracts on Polymarket and Kalshi.
- Compare prices only after confirming that contract rules describe the same outcome.
- Use related contracts as supporting market context, never as if they were equivalent to the target contract.
- Run a bounded news search for evidence relevant to that outcome.
- Produce a short forecast with cited evidence and an explicit uncertainty statement.
- Save a forecast snapshot when the user asks.
- Remember conversational context within a session.
- Deploy one HTTP service to Google Cloud Run.

### Excluded

- Placing trades or connecting user brokerage accounts.
- Portfolio management, position sizing, or profit-and-loss tracking.
- Continuous market monitoring or background jobs.
- Automated market settlement or automatic forecast resolution.
- Custom price-prediction or sports-prediction models.
- A dashboard, mobile application, or user-account system.
- Unbounded web browsing.
- Durable chat memory across Cloud Run instance replacement.
- Support for markets that cannot be matched confidently across both platforms.

## User Experience

A representative request is:

> Compare the Polymarket and Kalshi contracts for whether the Federal Reserve will cut rates at its next meeting. Research the strongest current evidence and give me your probability.

The agent will:

1. Search both market platforms for candidate contracts.
2. Retrieve at most three related contracts per platform that may provide useful context, such as another deadline, threshold, or linked event.
3. Normalize dates, thresholds, and outcome direction in code.
4. Use Jev for the narrow semantic decision of whether the primary contracts' resolution rules are equivalent.
5. Reject mismatched primary contracts or route ambiguous matches to the main LLM for review.
6. Run no more than two Tavily searches, inspect no more than five results per search, and extract no more than three articles.
7. Return the matched primary contract names, current prices, material rule differences, relevant related-market signals, key evidence, an estimated probability, and a clear `YES`, `NO`, or `NO POSITION` conclusion.
8. Save the forecast to the SQLite ledger only if the user requests it.

If no safe cross-market match exists, the agent says so instead of forcing a comparison.
Related contracts are labeled as contextual evidence and are not treated as interchangeable with the primary contracts.

## Overall Architecture

- **HTTP layer:** FastAPI exposes `POST /chat` with `{ "query": string, "session_id": string }` and returns `{ "response": string }`.
- **Agent framework:** LangGraph manages the reasoning loop, tool selection, bounded workflow, and conversational state.
- **Primary LLM:** OpenAI GPT-5 interprets user requests, chooses MCP tools, reviews ambiguous matches, and synthesizes the final answer.
- **Conversational memory:** A LangGraph checkpointer stores messages and current market context under `session_id` for the lifetime of the Cloud Run instance.
- **MCP integration:** `langchain-mcp-adapters` discovers and invokes tools over the real MCP protocol.
- **Jev decision node:** Jev is called through Vercel AI Gateway for contract-equivalence classification. It is a specialized model step, not another MCP server and not the final forecaster.
- **Deployment:** A multi-stage Docker image runs as a non-root user on Google Cloud Run and reads `PORT` and API credentials from environment configuration.

### MCP Servers

1. **Polymarket MCP**
   - Student-authored wrapper around Polymarket’s public Gamma Markets API and CLOB market-data endpoints.
   - Searches for primary and related markets and retrieves contract metadata, prices, order-book data, and resolution rules.

2. **Kalshi MCP**
   - Student-authored wrapper around the public Kalshi Trade API v2 market-data endpoints.
   - Searches for primary and related markets and retrieves contract metadata, prices, order-book data, and settlement rules.

3. **Tavily MCP**
   - Uses Tavily Search and Extract for current, topic-scoped evidence.
   - Enforces fixed result and extraction limits so the agent cannot search indefinitely.

4. **SQLite Forecast Ledger MCP**
   - Stores user-requested forecast snapshots, market prices, cited sources, timestamps, and later manually entered outcomes.
   - Supports simple retrieval and calibration summaries without becoming a portfolio-management system.

### External APIs

- OpenAI API for GPT-5.
- Polymarket Gamma and CLOB APIs for public market data.
- Kalshi Trade API v2 for public market data.
- Tavily Search and Extract APIs for current evidence.
- Vercel AI Gateway with `typesafe-ai/jev` for structured contract-equivalence decisions.

## How the Proposal Meets the Assignment Requirements

| Assignment requirement | How this project satisfies it |
|---|---|
| Build one LLM-powered, tool-using agent | One LangGraph agent accepts a research question, selects tools, evaluates their results, and produces one final response. |
| Use a supported framework | The project uses LangGraph with FastAPI and `langchain-mcp-adapters`, the assignment’s recommended stack. |
| Integrate at least two MCP servers | The agent integrates four named MCP servers: Polymarket, Kalshi, Tavily, and SQLite. |
| Additional MCP server criterion | Four functioning servers exceed the three-server threshold for full credit, while each remains directly related to the single workflow. |
| Use real MCP protocol invocation | The MCP adapter performs tool discovery and invocation through `tools/list` and `tools/call`; market and search responses are never hard-coded. |
| Use valid tool schemas | Each student-authored market server exposes narrow tools with JSON Schema inputs, such as market search and market-detail retrieval. |
| Demonstrate agent reasoning and tool selection | The LLM selects market tools for discovery, Tavily for current evidence, and SQLite only when a forecast must be saved or retrieved. It can answer follow-up questions without forcing an unrelated tool call. |
| Demonstrate multi-step tool use | A normal request requires primary and related-market discovery on both platforms, contract comparison, bounded evidence retrieval, and final synthesis. |
| Maintain conversational memory | LangGraph’s built-in checkpointer stores state by the request’s `session_id`, allowing follow-ups such as “What would change your mind?” or “Save that forecast.” |
| Avoid a custom dictionary for memory | Memory uses the framework’s checkpointer rather than a hand-built in-memory map. |
| Expose the required web-service contract | FastAPI implements `POST /chat` with the required request and response shapes. |
| Run correctly on Cloud Run | The service binds to `0.0.0.0`, reads the injected `PORT`, and is packaged in a multi-stage Docker image with a non-root runtime user. |
| Use a cloud-accessible LLM | The deployed service calls GPT-5 through the OpenAI API; it does not deploy Ollama. |
| Handle MCP failures | The application catches transport failures, tool execution errors, and malformed responses, then returns a useful partial answer or explains which dependency failed. |
| Protect credentials | Local secrets live in an ignored `.env`; committed `.env.example` values are placeholders; deployed credentials are provided through Cloud Run environment configuration or Secret Manager. |
| Provide three architecture diagrams | The README will contain a project-specific system architecture diagram, tool-invocation loop, and Cloud Run deployment topology. |
| Provide complete documentation | The README will cover setup, local execution, deployment, API usage, costs, MCP-server attribution, and known limitations. |
| Provide a substantive process log | `PROCESS_LOG.md` will contain Rylan Wade’s own development and verification narrative, including material prompts, failures, fixes, and lessons learned. |
| Verify more than the happy path | Tests will cover tool routing, session isolation, multi-turn recall, schema validation, research limits, unavailable MCP servers, tool errors, malformed responses, and arbitrary reasonable `/chat` queries. |
| Supply a live deployment | The final deliverable will include a live Cloud Run URL left available through grading. |

## Why It Stands Out as a Portfolio Project

### It solves a real data-integration problem

Prediction markets often appear to offer direct comparisons while using different deadlines, wording, settlement sources, or edge-case rules. Identifying whether two contracts are genuinely equivalent is a practical problem that requires both deterministic validation and semantic judgment.

Related-contract discovery adds useful market context without weakening that distinction. For example, contracts with different deadlines or thresholds may reveal how probability changes across time or conditions, while remaining clearly labeled as non-equivalent evidence.

### It uses each model for a constrained role

The project does not treat one LLM as a universal solution. GPT-5 handles tool selection and evidence synthesis; Jev handles one fast, typed classification; ordinary code handles dates, numeric comparisons, limits, and price calculations. This demonstrates deliberate system design rather than a single oversized prompt.

### It demonstrates real MCP engineering

Two student-authored market MCP servers convert unrelated external APIs into consistent agent tools. Tavily and SQLite complete the workflow through the same protocol, showing tool discovery, schema-driven invocation, failure handling, and multi-server orchestration.

### It is bounded and auditable

The research budget is enforced in code, contract matches expose their reasoning status, saved forecasts preserve the prices and evidence available at decision time, and `NO POSITION` is a valid outcome. These constraints make the system easier to test and more credible than an agent that can browse indefinitely until it finds supporting evidence.

### It produces measurable artifacts

The forecast ledger creates a record that can later be scored with Brier score and summarized for calibration. The assignment implementation only needs manual outcome entry and simple summaries, but the stored records make the project’s quality inspectable instead of relying only on polished demonstrations.

### It has a clear production story

The final artifact is a containerized API deployed on Cloud Run with session-scoped memory, secret handling, graceful dependency failures, and a documented cost profile. A reviewer can understand and test the system through one endpoint without installing a custom interface.

## Definition of Done

The project is complete when:

- One deployed `/chat` endpoint handles arbitrary requests within the stated prediction-market scope.
- All four MCP servers are discovered and successfully invoked through MCP.
- One representative query chains both market servers and Tavily before synthesis.
- Related-contract discovery is capped at three results per platform and labels those contracts as contextual rather than equivalent.
- A follow-up request demonstrates memory using the same `session_id`.
- A user can explicitly save and retrieve a forecast through the SQLite MCP.
- Jev classifies contract equivalence, while deterministic code handles dates and numbers.
- Search and tool-call limits are enforced even when the agent wants more information.
- All three required MCP failure classes return controlled responses rather than crashes.
- Automated tests and manual Cloud Run verification are documented.
- The README contains the three required diagrams and deployment instructions.
- The live URL, source ZIP, README, and personally written `PROCESS_LOG.md` are ready for submission.

Anything beyond this list is a future enhancement, not part of Assignment 1.
