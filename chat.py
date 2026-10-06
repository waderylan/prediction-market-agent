"""Local testing client for the deployed chat service. Not part of the graded service.

Usage: python chat.py "your prompt" 7   (the number is the session id).
Reads the service URL from the gitignored .cloud-run-url file and prints the
response, tool calls, and timings from POST /chat/inspect.
"""

import json
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

if len(sys.argv) != 3 or not sys.argv[2].isdigit():
    sys.exit('Usage: python chat.py "your prompt" <session number>')


def log(message: str) -> None:
    print(message, file=sys.stderr)


prompt, session = sys.argv[1], sys.argv[2]
url = Path(__file__).with_name(".cloud-run-url").read_text().strip().rstrip("/")
body = json.dumps({"query": prompt, "session_id": session}).encode()
request = urllib.request.Request(
    f"{url}/chat/inspect", body, {"Content-Type": "application/json"}, method="POST"
)

log(f"[sent] session={session} chars={len(prompt)}")
started = time.perf_counter()
try:
    with urllib.request.urlopen(request, timeout=120) as reply:
        result = json.load(reply)
except urllib.error.HTTPError as error:
    sys.exit(f"[error] HTTP {error.code}: {error.read().decode()[:500]}")
elapsed = time.perf_counter() - started

activity = result["activity"]
log(f"[received] {elapsed:.1f}s total, {len(activity)} tool call(s)")
for call in activity:
    log(f"  - {call['server']}.{call['tool']} {call['status']} {call['duration_ms'] / 1000:.1f}s")
if activity:
    tool_time = sum(call["duration_ms"] for call in activity) / 1000
    log(
        f"[time] tools {tool_time:.1f}s (summed), "
        f"model + overhead {max(elapsed - tool_time, 0):.1f}s"
    )
log("")
sys.stdout.reconfigure(encoding="utf-8")
print(result["response"])
