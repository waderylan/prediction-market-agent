"""HTTP contract and application-owned model/checkpointer lifecycle."""

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Literal
from uuid import uuid4

from fastapi import FastAPI, Header, HTTPException
from langchain_openai import ChatOpenAI
from openai import DefaultAsyncHttpxClient, DefaultHttpxClient
from pydantic import BaseModel, ConfigDict, Field

from market_agent.agent import ChatAgent, ToolActivity
from market_agent.config import load_settings
from market_agent.logging import configure_logging
from market_agent.watch.models import PollResult
from market_agent.watch.runtime import WatchRuntime, build_watch_runtime


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


def create_app(
    agent: ChatAgent | None = None,
    watch_runtime: WatchRuntime | None = None,
) -> FastAPI:
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
            runtime = watch_runtime or build_watch_runtime(settings)
            app.state.watch_runtime = runtime
            app.state.agent = ChatAgent(
                model,
                model_timeout=settings.llm_timeout_seconds,
                watch_service=runtime.service,
            )
        else:
            app.state.agent = agent
            app.state.watch_runtime = watch_runtime
        try:
            yield
        finally:
            if agent is None:
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

    @app.post("/internal/watches/poll", response_model=PollResult)
    async def poll_watches(
        authorization: str | None = Header(default=None),
    ) -> PollResult:
        runtime: WatchRuntime | None = app.state.watch_runtime
        if runtime is None or runtime.oidc is None:
            raise HTTPException(503, "Scheduler authentication is not configured")
        if not await asyncio.to_thread(runtime.oidc.verify, authorization):
            raise HTTPException(401, "Invalid scheduler identity")
        invocation = uuid4().hex
        result = await runtime.coordinator.poll(owner=f"cloud-{invocation}")
        attempts = await runtime.delivery.run_once(f"cloud-delivery-{invocation}")
        return result.model_copy(update={"delivery_attempts": attempts})

    return app


app = create_app()
