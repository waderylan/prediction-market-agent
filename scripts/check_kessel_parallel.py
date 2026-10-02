"""Smoke Kessel's streamed parallel OpenAI tool-call protocol without exposing its key."""

from __future__ import annotations

import json
import os
from collections import defaultdict
from typing import Any

from openai import OpenAI


def main() -> None:
    client = OpenAI(base_url=os.environ["OPENAI_BASE_URL"], api_key=os.environ["OPENAI_API_KEY"])
    tools = [
        {
            "type": "function",
            "function": {
                "name": name,
                "description": f"Look up the {league} score for this fixed test game.",
                "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
            },
        }
        for name, league in (("lookup_nfl", "NFL"), ("lookup_mlb", "MLB"))
    ]
    messages: list[dict[str, Any]] = [
        {
            "role": "user",
            "content": "Call lookup_nfl and lookup_mlb once. Report both returned scores.",
        }
    ]
    chunks = client.chat.completions.create(
        model="default",
        messages=messages,  # type: ignore[arg-type]
        tools=tools,  # type: ignore[arg-type]
        tool_choice="required",
        parallel_tool_calls=True,
        stream=True,
    )
    calls: dict[int, dict[str, str]] = defaultdict(lambda: {"id": "", "name": "", "arguments": ""})
    for chunk in chunks:
        for delta in chunk.choices[0].delta.tool_calls or []:
            call = calls[delta.index]
            call["id"] += delta.id or ""
            if delta.function:
                call["name"] += delta.function.name or ""
                call["arguments"] += delta.function.arguments or ""
    ordered = [calls[index] for index in sorted(calls)]
    if len(ordered) != 2 or {call["name"] for call in ordered} != {"lookup_nfl", "lookup_mlb"}:
        raise RuntimeError("Kessel did not stream both expected tool calls")
    if not all(call["id"] and isinstance(json.loads(call["arguments"]), dict) for call in ordered):
        raise RuntimeError("Kessel streamed incomplete tool-call identifiers or arguments")
    messages.append(
        {
            "role": "assistant",
            "tool_calls": [
                {
                    "id": call["id"],
                    "type": "function",
                    "function": {"name": call["name"], "arguments": call["arguments"]},
                }
                for call in ordered
            ],
        }
    )
    scores = {"lookup_nfl": "Browns 21, Steelers 10", "lookup_mlb": "Braves 6, Phillies 2"}
    messages.extend(
        {"role": "tool", "tool_call_id": call["id"], "content": scores[call["name"]]}
        for call in ordered
    )
    final = client.chat.completions.create(model="default", messages=messages)  # type: ignore[arg-type]
    answer = final.choices[0].message.content or ""
    if not all(name in answer for name in ("Browns", "Braves")):
        raise RuntimeError("Kessel did not incorporate both tool results")
    print("Kessel streamed two indexed tool calls and accepted both matching tool results: PASS")


if __name__ == "__main__":
    main()
