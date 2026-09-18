# Prediction Market Research Agent

This repository contains CSCI 599 Assignment 1: a tool-using agent with MCP integration, conversational memory, and a Google Cloud Run deployment. The current repository contains the assignment reference and the focused proposal; implementation is the next phase.

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

## Documentation

- [Focused project proposal](PROJECT_PROPOSAL.md)
- [Assignment requirements](Assignment_1_Description.md)
