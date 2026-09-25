# Watches: next steps toward production

This doc lists what the watch add-on needs before it is reliable enough for production use. For how
watches work, see [WATCHES_EXPLAINED.md](WATCHES_EXPLAINED.md).

## Where the flakiness comes from

The watch system itself is sound: rules, confirmation, fingerprints, the outbox, and Telegram
delivery all work, and a live runner delivers real alerts. The unreliability comes from the
**evidence it polls**:

- Prices are checked once a minute, so fast moves can be missed or blurred.
- Kalshi and Polymarket return no quote timestamps, so comparisons use fetch time instead of when
  the price was actually set.
- Thin order books produce jumps with no game cause. In one live Padres–Dodgers check,
  Polymarket moved 5 points while the only on-field action was a batter working the count.
- ESPN's play feed runs roughly 10–60 seconds behind the broadcast, so an alert can fire before the
  play that explains it appears.
- Each check starts fresh MCP server processes, which costs seconds per poll.
- The local runner is a terminal process. If it stops, monitoring stops silently.

The items below are ordered by priority.

## 0. Assignment first

Watches are an add-on and earn no rubric credit. The graded deliverables come first.

- **Deploy the core agent to Cloud Run.** The live URL is required; a localhost-only submission
  scores zero.
- **Keep watches out of the graded deployment** unless they are solid. Add a
  `WATCHES_ENABLED=false` setting so the graded service runs only the core agent and four MCP
  servers.
- **Write `PROCESS_LOG.md`** in Rylan Wade's own voice. It is 10% of the assignment score.
- **Merge the feature branch** into `main` after review.

## 1. Fix the evidence

These address the flakiness directly.

- **Stream prices instead of polling them.** Kalshi and Polymarket both offer real-time WebSocket
  price feeds. Streaming catches fast moves, provides real quote timestamps (removing the
  "did not report quote times" note), and makes price windows accurate.
- **Filter thin-book noise.**
  - Require a move to persist across two consecutive checks, or for N seconds, before alerting.
  - Evaluate the bid/ask midpoint instead of the last trade.
  - Skip or down-rank alerts when the spread is wide.
- **Add cross-platform confirmation.** Offer a "both platforms move the same direction" rule, or
  require agreement by default. A move on one platform alone is usually noise.
- **Allow for ESPN lag.** When a price move is detected, wait one extra check before sending so the
  play that likely caused it is in the feed and appears in the alert.
- **Add a faster baseball source.** MLB StatsAPI's live feed is quicker and more detailed than ESPN
  for MLB and is already integrated as a fallback. NFL has no comparable free alternative.

## 2. Make the runner production-grade

- **Keep MCP sessions alive.** Hold long-lived MCP sessions inside the runner, or call the provider
  clients directly on the polling path. MCP remains the boundary for the conversational agent.
- **Run as a managed service.** Replace the foreground terminal runner with a Cloud Run job or an
  always-on worker with health checks and automatic restarts.
- **Use a long-running worker for sub-minute alerts.** Cloud Scheduler cannot fire more often than
  once per minute. A persistent worker pairs naturally with streaming prices.
- **Add a heartbeat.** If no check completes within about three minutes, send a Telegram message
  such as "watcher is down." Silent failure is the worst failure mode for an alert system.
- **Add observability.** Record per-check latency, per-source error rate, and alert counts, and put
  them somewhere visible.

## 3. Make alerts worth reading

- **Raise the signal bar.** Use larger default thresholds and richer rules, such as "moved X points
  *and* a scoring play happened," or "market disagrees with the current score."
- **Show context for the move.** Include a small win-chance chart or a "before / after" framing
  around the key play. ESPN and MLB publish free win-probability data for comparison.
- **Bundle bursts.** Merge alerts that fire within the same minute (both platforms, several rules)
  into one Telegram message.
- **Add Telegram buttons.** Inline buttons for "Mute this watch," "Pause 10 min," and "Why did this
  fire?" map directly onto the existing watch commands.

## 4. Product scope

- **Real accounts before multiple users.** Session IDs are routing keys, not authentication, and
  delivery goes to one configured Telegram chat. Multi-user support needs sign-in and per-user
  Telegram linking.
- **More market types only with demand.** Spreads and totals need their own settlement logic and
  schemas; full-game winners stay the only supported type until then.

## Recommended order

1. Section 0: deploy the core agent, write the process log, and keep watches disabled in the graded
   service.
2. Stream prices and require moves to persist across two checks. Together these remove most
   observed false alerts and the missing-timestamp problem.
3. Heartbeat, managed runner, and persistent MCP sessions.
4. Alert quality and product scope.
