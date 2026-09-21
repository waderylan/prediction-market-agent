"""Standalone read-only MCP access to the existing Jev contract reviewer."""

import asyncio
import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.exceptions import ToolError
from mcp.types import ToolAnnotations
from pydantic import BaseModel, ConfigDict, Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

from market_agent.domain import CanonicalMarket, Platform
from market_agent.domain.matching import ContractEvidence, PairAssessment, match_candidates
from market_agent.jev import JevReviewer, SemanticReviewer
from market_agent.mcp.common import MarketDetail


class _JevMcpSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    ai_gateway_api_key: SecretStr | None = None
    jev_enabled: bool = False
    jev_timeout_seconds: float = Field(default=3, ge=0.5, le=10)
    jev_equivalent_threshold: float = Field(default=0.9, ge=0, le=1)
    jev_different_threshold: float = Field(default=0.75, ge=0, le=1)
    jev_confidence_threshold: float = Field(default=0.6, ge=0, le=1)


class JevMcpResult(BaseModel):
    """Deterministic audit plus the final bounded semantic-review result."""

    model_config = ConfigDict(extra="forbid")

    deterministic_assessment: PairAssessment
    final_assessment: PairAssessment
    jev_called: bool
    usage_note: str = (
        "This classifies supplied contract meaning only. It does not predict a game, establish "
        "market settlement, compare prices, or recommend a trade."
    )


def _tool_error(code: str, message: str) -> ToolError:
    return ToolError(
        json.dumps(
            {"error": {"code": code, "message": message, "fields": {}}},
            separators=(",", ":"),
            sort_keys=True,
        )
    )


def _contract(detail: MarketDetail) -> ContractEvidence:
    return ContractEvidence.model_validate(detail.model_dump(mode="json"))


def create_server(reviewer: SemanticReviewer | None = None) -> FastMCP[Any]:
    """Create the diagnostic MCP server, optionally with an injected reviewer for tests."""

    active_reviewer = reviewer
    owns_reviewer = reviewer is None
    reviewer_lock = asyncio.Lock()

    async def get_reviewer() -> SemanticReviewer:
        nonlocal active_reviewer
        if active_reviewer is not None:
            return active_reviewer
        async with reviewer_lock:
            if active_reviewer is not None:
                return active_reviewer
            settings = _JevMcpSettings()
            if not settings.jev_enabled:
                raise _tool_error(
                    "jev_disabled",
                    "Set JEV_ENABLED=true to use the Jev contract-review tool.",
                )
            if settings.ai_gateway_api_key is None or not (
                api_key := settings.ai_gateway_api_key.get_secret_value().strip()
            ):
                raise _tool_error(
                    "jev_key_missing",
                    "Set AI_GATEWAY_API_KEY to use the Jev contract-review tool.",
                )
            active_reviewer = JevReviewer(
                api_key,
                timeout_seconds=settings.jev_timeout_seconds,
                equivalent_threshold=settings.jev_equivalent_threshold,
                different_threshold=settings.jev_different_threshold,
                confidence_threshold=settings.jev_confidence_threshold,
            )
            return active_reviewer

    @asynccontextmanager
    async def lifespan(server: FastMCP[Any]) -> AsyncIterator[None]:
        try:
            yield
        finally:
            if owns_reviewer and isinstance(active_reviewer, JevReviewer):
                await active_reviewer.aclose()

    server = FastMCP("Jev contract review", lifespan=lifespan, log_level="CRITICAL")
    annotations = ToolAnnotations(readOnlyHint=True, destructiveHint=False, openWorldHint=True)

    @server.tool(annotations=annotations)
    async def jev_review_contracts(
        polymarket_contract: MarketDetail,
        kalshi_contract: MarketDetail,
    ) -> JevMcpResult:
        """Review whether two supplied full-game-winner contracts have equivalent payout terms.
        First call polymarket_get_market and kalshi_get_market, then copy their complete structured
        results unchanged into the corresponding arguments. The server reruns deterministic event,
        outcome, and settlement checks before Jev. Deterministic conflicts always veto Jev;
        incomplete or truncated evidence stays ambiguous. Jev receives contract identity and rules
        only, never prices, scores, game state, or results. This tool does not predict a winner,
        establish settlement, compare prices, or recommend a position.
        """
        if polymarket_contract.platform != Platform.POLYMARKET:
            raise _tool_error(
                "platform_mismatch",
                "polymarket_contract must be the unchanged Polymarket detail result.",
            )
        if kalshi_contract.platform != Platform.KALSHI:
            raise _tool_error(
                "platform_mismatch",
                "kalshi_contract must be the unchanged Kalshi detail result.",
            )

        contracts = [_contract(polymarket_contract), _contract(kalshi_contract)]
        polymarket_inputs: list[CanonicalMarket | ContractEvidence] = [contracts[0]]
        kalshi_inputs: list[CanonicalMarket | ContractEvidence] = [contracts[1]]
        deterministic = match_candidates(polymarket_inputs, kalshi_inputs)
        if len(deterministic.pairs) != 1:
            raise _tool_error(
                "unsupported_pair",
                "The supplied details did not produce exactly one cross-platform pair.",
            )
        if deterministic.pairs[0].verdict != "ambiguous":
            return JevMcpResult(
                deterministic_assessment=deterministic.pairs[0],
                final_assessment=deterministic.pairs[0],
                jev_called=False,
            )
        semantic_reviewer = await get_reviewer()
        try:
            async with asyncio.timeout(8):
                reviewed = await semantic_reviewer.review(deterministic, contracts)
        except TimeoutError:
            raise _tool_error(
                "jev_tool_timeout", "Jev contract review exceeded its eight-second budget."
            ) from None
        except ToolError:
            raise
        except Exception:
            raise _tool_error(
                "jev_review_failed", "Jev contract review failed without changing the verdict."
            ) from None

        final = reviewed.pairs[0]
        return JevMcpResult(
            deterministic_assessment=deterministic.pairs[0],
            final_assessment=final,
            jev_called=final.semantic_review is not None,
        )

    registered = server._tool_manager.get_tool("jev_review_contracts")  # noqa: SLF001
    assert registered is not None
    registered.parameters["additionalProperties"] = False
    return server


def main() -> None:
    create_server().run(transport="stdio")


if __name__ == "__main__":
    main()
