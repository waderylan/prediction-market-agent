"""Bounded JSON-lines interface to the shared watch service and SQLite repository."""

from __future__ import annotations

import argparse
import json
import os
import sys

from pydantic import ValidationError

from market_agent.watch.cli import WatchCli


def emit(cli: WatchCli, request: dict[str, object]) -> None:
    try:
        response = {"ok": True, **cli.execute(request)}
    except (KeyError, ValueError, ValidationError) as error:
        response = {"ok": False, "error": type(error).__name__, "detail": str(error)[:500]}
    print(json.dumps(response, default=str), flush=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default="artifacts/watches.db")
    parser.add_argument(
        "--operation", choices=("list", "inspect", "pause", "resume", "delete", "inbox")
    )
    parser.add_argument("--session-id")
    parser.add_argument("--watch-id")
    parser.add_argument("--limit", type=int, default=20)
    args = parser.parse_args()
    cli = WatchCli(
        args.db,
        telegram_configured=bool(os.getenv("TELEGRAM_BOT_TOKEN") and os.getenv("TELEGRAM_CHAT_ID")),
    )
    if args.operation:
        if not args.session_id:
            parser.error("--session-id is required with --operation")
        if args.operation in {"inspect", "pause", "resume", "delete"} and not args.watch_id:
            parser.error(f"--watch-id is required for {args.operation}")
        request: dict[str, object] = {
            "operation": args.operation,
            "session_id": args.session_id,
        }
        if args.watch_id:
            request["watch_id"] = args.watch_id
        if args.operation == "inbox":
            request["limit"] = args.limit
        emit(cli, request)
        return
    for line in sys.stdin:
        if not line.strip():
            continue
        try:
            request = json.loads(line)
            if not isinstance(request, dict):
                raise ValueError("input must be a JSON object")
            emit(cli, request)
        except (KeyError, ValueError, ValidationError) as error:
            print(
                json.dumps(
                    {
                        "ok": False,
                        "error": type(error).__name__,
                        "detail": str(error)[:500],
                    }
                ),
                flush=True,
            )


if __name__ == "__main__":
    main()
