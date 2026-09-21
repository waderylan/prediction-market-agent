"""HTTP contract and application-owned model/checkpointer lifecycle."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Literal

from fastapi import FastAPI
from langchain_openai import ChatOpenAI
from openai import DefaultAsyncHttpxClient, DefaultHttpxClient
from pydantic import BaseModel, ConfigDict, Field

from market_agent.agent import ChatAgent, ToolActivity
from market_agent.config import load_settings
from market_agent.jev import JevReviewer
from market_agent.logging import configure_logging


class ChatRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True, strict=True)
    query: str = Field(min_length=1, max_length=4000)
    session_id: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_-]+$")
    model: Literal["sol", "terra", "luna"] | None = None
    reasoning_effort: Literal["low", "medium", "high", "xhigh"] | None = None


class ChatResponse(BaseModel):
    response: str


class ChatInspectionResponse(ChatResponse):
    activity: list[ToolActivity]


def create_app(agent: ChatAgent | None = None) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        if agent is None:
            settings = load_settings()
            configure_logging(settings.log_level)
            # Own these clients per lifespan; SDK defaults cache pools across event loops.
            http_client = DefaultHttpxClient()
            http_async_client = DefaultAsyncHttpxClient()
            model = ChatOpenAI(
                model=settings.openai_model,
                api_key=settings.openai_api_key,
                base_url=str(settings.openai_base_url),
                reasoning_effort="low",
                timeout=settings.llm_timeout_seconds,
                max_retries=0,
                max_completion_tokens=2000,
                use_responses_api=False,
                http_client=http_client,
                http_async_client=http_async_client,
            )
            reviewer = (
                JevReviewer(
                    settings.ai_gateway_api_key.get_secret_value(),
                    timeout_seconds=settings.jev_timeout_seconds,
                    equivalent_threshold=settings.jev_equivalent_threshold,
                    different_threshold=settings.jev_different_threshold,
                    confidence_threshold=settings.jev_confidence_threshold,
                )
                if settings.jev_enabled and settings.ai_gateway_api_key is not None
                else None
            )
            app.state.agent = ChatAgent(
                model,
                model_timeout=settings.llm_timeout_seconds,
                semantic_reviewer=reviewer,
            )
        else:
            app.state.agent = agent
        try:
            yield
        finally:
            if agent is None:
                if reviewer is not None:
                    await reviewer.aclose()
                await http_async_client.aclose()
                http_client.close()

    app = FastAPI(title="Prediction Market Contract Reader", lifespan=lifespan)

    @app.post("/chat", response_model=ChatResponse)
    async def chat(body: ChatRequest) -> ChatResponse:
        model_name = f"gpt-5.6-{body.model}" if body.model else None
        response = await app.state.agent.chat(
            body.query,
            body.session_id,
            model_name=model_name,
            reasoning_effort=body.reasoning_effort,
        )
        return ChatResponse(response=response)

    @app.post("/chat/inspect", response_model=ChatInspectionResponse)
    async def chat_inspect(body: ChatRequest) -> ChatInspectionResponse:
        model_name = f"gpt-5.6-{body.model}" if body.model else None
        turn = await app.state.agent.chat_detailed(
            body.query,
            body.session_id,
            model_name=model_name,
            reasoning_effort=body.reasoning_effort,
        )
        return ChatInspectionResponse(response=turn.response, activity=turn.activity)

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    return app


app = create_app()
