# Final steps

This is the last of the work for the core project. Once these steps are done, the
Assignment 1 submission is complete.

The watch feature (event-aware watches, Telegram alerts, Firestore/Scheduler cloud
automation) stays on its own branch, `feature/watch-monitoring-alerts`. It is not part
of this submission and is not merged into `main`. `main` is at Milestone 13 with no
watch code.

Ground truth for every requirement is `docs/assignment/Assignment_1_Description.md`.

## Already met on `main`

- Four MCP servers (Kalshi, Polymarket, sports-state, Tavily) reached through
  `langchain-mcp-adapters` with real `tools/list` and `tools/call`.
- Connection, tool-execution, and malformed-response failure handling (Milestone 13).
- Model-driven tool selection, no-tool answers, and chained calls.
- LangGraph `InMemorySaver` memory keyed by `session_id`.
- `POST /chat` contract, `PORT` binding, non-root multi-stage Dockerfile.
- Three architecture diagrams in `README.md` (diagram 3 needs fixes below).

## Remaining work

1. **Cloud model backend**
   - Get a cloud-accessible key (OpenAI GPT-5, or NVIDIA NIM) and set `OPENAI_API_KEY`
     (plus `OPENAI_BASE_URL` for NIM) in the ignored `.env`.
   - The local Codex gateway is dev-only and cannot be deployed.

2. **Local quality gate on `main`**
   - `uv run pytest -m "not live_smoke"`, `uv run ruff check .`,
     `uv run ruff format --check .`, `uv run mypy src`.
   - `docker build` and `docker run`; verify `/chat`, a same-session follow-up, and
     that the container runs as non-root and reads `PORT`.

3. **GCP setup**
   - `gcloud` authenticated with the USC account; project billed to the education credit.
   - Enable `run.googleapis.com` and `cloudbuild.googleapis.com`; use `us-west1`.

4. **`deploy.sh`**
   - Build with Cloud Build, deploy with `--allow-unauthenticated`, `--memory 512Mi`,
     `--min-instances 0`, `--max-instances 1`.
   - Pass credentials via `--set-env-vars` from shell variables sourced from `.env`.
     No literal keys in the script or repository.

5. **Live acceptance on the Cloud Run URL**
   - No-tool query.
   - One query per MCP server (Kalshi, Polymarket, sports-state, Tavily).
   - Chained multi-server query.
   - Same-session recall ("what did I ask you last?") and cross-session isolation.
   - Malformed request returns a controlled error, no stack trace.
   - One partial-failure probe.
   - Inspect Cloud Run logs and revision health.

6. **README updates**
   - Diagram 3: add local dev (`.env`, Docker), Docker image → Artifact Registry →
     Cloud Run service → live URL, and the `.env` → shell → `--set-env-vars` flow.
   - Deploy section: describe what was actually run, with the live URL.
   - Cost disclosure: real numbers from the billing page (Cloud Run, model, Tavily).
   - MCP attribution: cite external providers and projects used.
   - Mention that watches exist on a separate branch, or omit them from the README.

7. **`PROCESS_LOG.md`** (10% of the grade; written by Rylan Wade in his own voice)
   - AI tools used, with versions.
   - Development narrative, 200-300 words, with specific prompts.
   - Verification narrative: failure injection, memory isolation, tool-routing tests.
   - What I learned, 2-3 sentences.

8. **Code cleanup pass**
   - No unused imports, dead code, or leftover debug prints.
   - Attribution comments (`# Adapted from <URL> — <rationale>`) for any copied code.

9. **Package and submit**
   - Build the source ZIP with `README.md`, `main.py`, `deploy.sh`, source, and MCP
     configuration. Exclude `.env`, `.venv`, `node_modules`, caches, and anything with keys.
   - Submit to Brightspace: live URL, ZIP, `PROCESS_LOG.md`, `README.md`.
   - Leave the service running until grades are posted.

## After submission (optional)

Decide whether to deploy the watch feature from `feature/watch-monitoring-alerts` as a
separate revision. It is not required for the assignment.
