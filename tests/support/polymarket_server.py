"""Offline subprocess fixture: real production server with injected HTTP transport."""

import json
from pathlib import Path

import httpx

from market_agent.mcp.polymarket import create_server
from market_agent.providers import PolymarketClient


def handle(request: httpx.Request) -> httpx.Response:
    name = "search_success" if request.url.path == "/public-search" else "market_success"
    fixture = Path(__file__).parents[1] / "fixtures" / "polymarket" / f"{name}.json"
    return httpx.Response(200, json=json.loads(fixture.read_text()))


if __name__ == "__main__":
    client = httpx.AsyncClient(
        base_url="https://gamma-api.polymarket.com", transport=httpx.MockTransport(handle)
    )
    create_server(PolymarketClient(http_client=client)).run(transport="stdio")
