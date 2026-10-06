# Testing and verification

## Local quality gate

```powershell
uv run --extra dev pytest -m "not live_smoke"
uv run --extra dev ruff check .
uv run --extra dev ruff format --check .
uv run --extra dev mypy src
git diff --check
```

The offline suite uses fixtures for provider HTTP responses and scripted model replies. MCP integration tests still execute real local `tools/list` and `tools/call` operations; the stdio test starts a server process. The suite does not establish current public API compatibility or real-model tool choice.

## Retained coverage

| Layer | Main checks |
|---|---|
| Provider and domain unit tests | Representative parsing, identity, settlement, pagination bounds, malformed data, fallback, and response-size failures. |
| MCP integration tests | Real discovery and calls across Kalshi, Polymarket, sports, sports state, and Tavily; controlled provider and transport failures; one unavailable server leaving others usable. |
| Agent and HTTP integration tests | Tool selection, multi-server synthesis, session memory and isolation, failure recovery, typed market/game evidence, and research gating. |
| Live smoke tests | Opt-in public MCP probes for each server and two real-model chat flows. |

The tests are a small regression set for distinct risks. The historical [adversarial testing report](ADVERSARIAL_TESTING_REPORT.md) describes a broader suite that existed when the report was written; its test counts and per-file inventory are historical, not the current quality gate.

## Public MCP checks

```powershell
$env:RUN_LIVE_SMOKE = "1"
uv run --extra dev pytest tests/live -m live_smoke --ignore=tests/live/test_chat_live.py -s
Remove-Item Env:RUN_LIVE_SMOKE
```

These checks call public providers and can vary with provider availability or current games. A current empty result can be valid; exact identity and failure behavior belongs in offline fixtures.

## Real-model checks

```powershell
$env:RUN_LIVE_AGENT = "1"
uv run --extra dev pytest tests/live/test_chat_live.py -s
Remove-Item Env:RUN_LIVE_AGENT
```

These tests need a configured model backend and may incur model costs. They check observed memory, tool choice, and synthesis but do not prove that all reasonable prompts will route correctly.

## Deployment acceptance

After deployment, use the live Cloud Run URL to check `POST /chat` with arbitrary reasonable queries, two turns using the same session ID, and a separate session ID. Inspect logs for real MCP activity. Local tests do not establish Cloud Run behavior.
