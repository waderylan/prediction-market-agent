"""HTTP contract and application-owned model/checkpointer lifecycle."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Literal

from fastapi import FastAPI
from langchain_core.language_models import BaseChatModel
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_openai import ChatOpenAI
from openai import DefaultAsyncHttpxClient, DefaultHttpxClient
from pydantic import BaseModel, ConfigDict, Field

from market_agent.agent import ChatAgent, ToolActivity
from market_agent.config import load_settings
from market_agent.logging import configure_logging
from market_agent.mcp.pool import MCPToolPool


class ChatRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True, strict=True)
    query: str = Field(min_length=1, max_length=4000)
    session_id: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_-]+$")
    model: str | None = Field(
        default=None, min_length=1, max_length=100, pattern=r"^[A-Za-z0-9][A-Za-z0-9._:/-]*$"
    )
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
            http_client = None
            http_async_client = None
            pool = MCPToolPool()
            model: BaseChatModel
            if settings.use_gemini:
                model = ChatGoogleGenerativeAI(
                    model=settings.model_name,
                    api_key=settings.model_api_key,
                    vertexai=False,
                    reasoning_effort="low",
                    timeout=settings.llm_timeout_seconds,
                    max_retries=0,
                    max_output_tokens=2000,
                )
            else:
                # Own these clients per lifespan; SDK defaults cache pools across event loops.
                headers = (
                    {"Host": settings.openai_host_header} if settings.openai_host_header else {}
                )
                http_client = DefaultHttpxClient(headers=headers)
                http_async_client = DefaultAsyncHttpxClient(headers=headers)
                model = ChatOpenAI(
                    model=settings.model_name,
                    api_key=settings.model_api_key,
                    base_url=str(settings.openai_base_url),
                    reasoning_effort="low",
                    timeout=settings.llm_timeout_seconds,
                    max_retries=0,
                    max_completion_tokens=2000,
                    use_responses_api=False,
                    http_client=http_client,
                    http_async_client=http_async_client,
                )
            await pool.start()
            app.state.agent = ChatAgent(
                model,
                pool.tools,
                model_timeout=settings.llm_timeout_seconds,
                parallel_tool_calls_option=not settings.use_gemini,
                transport_failure=pool.transport_failed,
            )
        else:
            app.state.agent = agent
        try:
            yield
        finally:
            if agent is None:
                await pool.close()
                if http_async_client is not None:
                    await http_async_client.aclose()
                if http_client is not None:
                    http_client.close()

    app = FastAPI(title="SportsWatch MCP", lifespan=lifespan)

    @app.post("/chat", response_model=ChatResponse)
    async def chat(body: ChatRequest) -> ChatResponse:
        model_name = (
            f"gpt-5.6-{body.model}" if body.model in {"sol", "terra", "luna"} else body.model
        )
        response = await app.state.agent.chat(
            body.query,
            body.session_id,
            model_name=model_name,
            reasoning_effort=body.reasoning_effort,
        )
        return ChatResponse(response=response)

    @app.post("/chat/inspect", response_model=ChatInspectionResponse)
    async def chat_inspect(body: ChatRequest) -> ChatInspectionResponse:
        model_name = (
            f"gpt-5.6-{body.model}" if body.model in {"sol", "terra", "luna"} else body.model
        )
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
