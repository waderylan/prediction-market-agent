# Final steps

This is the last of the work for the core project. Once these steps are done, the
Assignment 1 submission is complete.

The watch feature (event-aware watches, Telegram alerts, Firestore/Scheduler cloud
automation) stays on its own branch, `feature/watch-monitoring-alerts`. It is not part
of this submission and is not merged into `main`. `main` is at Milestone 13 with no
watch code.

The browser inspection UI on `main` was built for fun as optional local tooling. It is not part
of the Assignment 1 submission and is not deployed to Cloud Run. The graded service is the
FastAPI/LangGraph `POST /chat` agent.

Ground truth for every requirement is `docs/assignment/Assignment_1_Description.md`.

## Already met on `main`

- Four MCP servers (Kalshi, Polymarket, sports-state, Tavily) reached through
  `langchain-mcp-adapters` with real `tools/list` and `tools/call`.
- Connection, tool-execution, and malformed-response failure handling (Milestone 13).
- Model-driven tool selection, no-tool answers, and chained calls.
- LangGraph `InMemorySaver` memory keyed by `session_id`.
- `POST /chat` contract, `PORT` binding, non-root multi-stage Dockerfile.
- Three architecture diagrams in `README.md`.

## Remaining work

Gemini integration, the local quality gate, GCP CLI setup, and Cloud Run deployment are complete.
The education-billed project is deployed in `us-west1`; its URL is in the ignored
`.cloud-run-url` file for the optional browser UI and Brightspace submission. The bullets below
are the final review and submission checks. Do not put the live URL in tracked files.

1. **Request-appropriate responses (verify locally, then again on the live URL)**
   - The agent pulls only the sources a request needs. A narrow question gets a narrow
     answer; only a broad request expands into a multi-source brief.
   - Check the tools actually invoked (from the activity trace or logs), not just the
     final prose:
     - "What's the score of the Yankees game?" → sports-state only.
     - "What's the Kalshi price for this game?" → Kalshi only.
     - "What's the Polymarket price?" → Polymarket only.
     - "Compare Kalshi and Polymarket for this game" → both market servers.
     - "Any injury news for this game?" → Tavily after the game is identified.
     - "Give me a sourced game brief" → state, both markets, and Tavily.
     - General question with no tool need (e.g. "what is a moneyline?") → no tools.
   - Check response scope: no unrequested market, stats, or news sections in a narrow
     answer; a follow-up reuses session context instead of re-fetching everything.
   - Fix by adjusting tool descriptions, system instructions, or graph routing. Do not
     add hard-coded keyword routing (the rubric gives no credit for it).

2. **Live acceptance on the Cloud Run URL**
   - No-tool query.
   - One query per MCP server (Kalshi, Polymarket, sports-state, Tavily).
   - Chained multi-server query.
   - Same-session recall ("what did I ask you last?") and cross-session isolation.
   - Malformed request returns a controlled error, no stack trace.
   - One partial-failure probe.
   - Inspect Cloud Run logs and revision health.

3. **README updates**
   - Confirm diagram 3 shows local dev (`.env`, Docker), Docker image → Artifact Registry →
     Cloud Run service → live URL, and the `.env` → shell → `--set-env-vars` flow.
   - Deploy section: describe what was actually run; keep the actual URL only in the ignored
     `.cloud-run-url` file and Brightspace text field.
   - Cost disclosure: real numbers from the billing page (Cloud Run, model, Tavily).
   - MCP attribution: cite external providers and projects used.
   - Mention that watches exist on a separate branch, or omit them from the README.

4. **`PROCESS_LOG.md`** (10% of the grade; written by Rylan Wade in his own voice)
   - AI tools used, with versions.
   - Development narrative, 200-300 words, with specific prompts.
   - Verification narrative: failure injection, memory isolation, tool-routing tests.
   - What I learned, 2-3 sentences.

5. **Code cleanup pass**
   - No unused imports, dead code, or leftover debug prints.
   - Attribution comments (`# Adapted from <URL> — <rationale>`) for any copied code.

6. **Package and submit**
   - Build the source ZIP with `README.md`, `main.py`, `deploy.sh`, source, and MCP
     configuration. Exclude `.env`, `.venv`, `node_modules`, caches, and anything with keys.
     Leave out the optional browser files in `web/` and `scripts/run_kessel_ui.py` /
     `scripts/run_chat_ui.py`; they are not required for the `/chat` service.
   - Submit to Brightspace: live URL, ZIP, `PROCESS_LOG.md`, `README.md`.
   - Leave the service running until grades are posted.

## After submission (optional)

Decide whether to deploy the watch feature from `feature/watch-monitoring-alerts` as a
separate revision. It is not required for the assignment.
