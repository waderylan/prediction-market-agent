# MCP startup and Cloud Run performance verification

Measured October 5, 2026 (Pacific time). This report records observations from the deployed
service and local checks. It contains no service URL, project ID, credentials, or request logs.
The live URL stays in the ignored `.cloud-run-url` file.

## Change under test

The previous production path started and discovered four stdio MCP servers on every chat
request. The new FastAPI lifespan starts them concurrently once per container instance. Requests
reuse those sessions. A failed transport hides only its server, then reconnects after active
turns release the old session. No background heartbeat or polling runs between requests.

Both deploy scripts specify 1 vCPU, 1 GiB memory, concurrency 4, zero minimum instances, and
one maximum instance. Cloud Run retains request-based billing. The four-request setting matches
the agent's own semaphore. The deployed revision received 100% of traffic, had no traffic tag,
and had no minimum-instance or always-allocated-CPU setting.

## Method

- Timings are client wall-clock measurements unless marked otherwise. They include network and
  model time, so individual answers are not directly comparable when Gemini produces different
  output lengths or reasoning paths.
- The local stub-model check isolates MCP setup from paid model latency. It ran the old request
  connector and the new instance pool on the same machine.
- Local Docker checks used the rebuilt production image. Cloud checks used the newly deployed
  revision. The optional browser UI was not running or sending Cloud Run traffic.
- The new revision received 12 live HTTP chat probes, below the 30-probe ceiling. A tool-using
  probe can contain multiple Gemini rounds; HTTP probe count is not model-call count.

## Latency measurements

| Environment and operation | Before | After | Notes |
| --- | ---: | ---: | --- |
| Local, stub model, no-tool request | 6.010s, 5.986s | 0.016s, 0.014s, 0.017s | After timing excludes one-time pool startup. |
| Local, MCP connect and discovery | 5.5–5.75s per request | 1.751s per container startup | The new startup opens servers concurrently. |
| Cloud Run, warm no-tool chat | 13.54s | 2.09s | One independently observed sample per revision; model and network time vary. |
| Cloud Run, score chat with sports-state tools | 18.35s | 7.78s | New request successfully used find-games and get-game-state. |
| Cloud Run, first chat after confirmed scale-to-zero | — | 24.52s | Monitoring showed zero active and idle instances first. |
| Cloud Run, immediate repeat after that cold chat | — | 6.73s | Same simple question; Gemini produced a longer answer than in the 2.09s warm probe. |
| Cloud Run, same-session recall | — | 1.71s | Correctly recalled the earlier turn. |
| Cloud Run, four-source chained brief | — | 22.87s | Eight successful MCP calls across sports-state, Kalshi, Polymarket, and Tavily. |
| Cloud Run, four simultaneous no-tool chats | — | 4.16–4.47s each | All returned HTTP 200; group wall time was 4.50s. |
| Cloud Run, two simultaneous score chats | — | 8.58s and 11.44s | Both returned HTTP 200 with two successful sports-state calls each. |

Local Docker checks with the real model returned a no-tool answer in 2.06s, same-session recall
in 1.76s, and a two-tool score answer in 8.32s. The warm container used about 339 MiB according
to Docker stats. After three local requests, its logs showed four total MCP discovery events,
one per server, confirming that request traffic did not rediscover the tools.

The 24.52s cold request is the remaining latency tradeoff of scale-to-zero. Cloud Run had
reported one idle instance for several minutes, then zero active and zero idle instances at
03:30 UTC before the cold probe. The next revision logs showed four startup discovery events.
The following warm request spent most of its 6.73s in the model call, based on log timestamps.

## Memory, failures, and correctness

The previous 512 MiB revision logged a memory-limit kill at 525 MiB and at least one HTTP 429.
On the new 1 GiB revision, Cloud Monitoring sampled 364.4, 371.6, 373.1, and 374.1 MiB after
startup and during the probe window. These are minute-sampled values, not measured peaks.
Across the 12 new-revision chat probes, logs showed zero memory-limit errors and zero HTTP 429s.
Four discovery events appeared for each of the two observed container lifetimes, rather than on
every request. This supports the OOM and repeated-startup fix for the tested workload; larger
unmeasured traffic could still reach the one-instance capacity limit.

Local verification passed 401 offline tests. The new real-stdio integration tests also passed:
concurrent calls through one session, partial availability when one server cannot start, and a
child server exiting during a tool call while the other three remain usable and the failed server
reconnects. Ruff, formatting, mypy, and diff checks passed. The existing tool-error and malformed
response tests remained in the passing suite. The system prompt and tool schemas were not trimmed.

## Cost exposure

Zero minimum instances and request-based billing mean no continuously billed Cloud Run CPU or
memory while the service is scaled to zero. The pool's child processes exist only while a
container instance exists; they do not send keepalive requests. One maximum instance limits
Cloud Run instance count. The higher 1 GiB memory limit increases the rate during active request
time, while avoiding the measured 512 MiB OOM. Cloud Build, image storage, Gemini, and optional
Tavily usage are separate cost sources. This verification did not measure an invoice or exact
model-token charges, so it does not claim a monthly dollar total.

The chosen setting favors low idle cost and working MCP behavior over cold-start speed. Enabling
minimum instances or always-allocated CPU would remove part of that cold delay but create
continuous billing, so neither setting was enabled.
