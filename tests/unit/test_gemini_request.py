"""Guard the Gemini generation fields sent by the locked LangChain adapter."""

import pytest
from langchain_core.messages import HumanMessage
from pydantic import SecretStr

from market_agent.app import _gemini_model
from market_agent.config import Settings


@pytest.mark.unit
def test_gemini_request_omits_deprecated_generation_fields() -> None:
    settings = Settings(
        _env_file=None,
        gemini_api_key=SecretStr("test-only-placeholder"),
        gemini_model="gemini-3.8-flash",
    )
    model = _gemini_model(settings)

    for effort, expected_level in ((None, "LOW"), ("high", "HIGH")):
        options = {"generation_config": {"candidate_count": None}}
        if effort is not None:
            options["reasoning_effort"] = effort
        request = model._prepare_request([HumanMessage("Hello")], **options)
        config = request["config"].model_dump(exclude_none=True, mode="json")

        assert config["thinking_config"] == {"thinking_level": expected_level}
        assert not {"temperature", "top_p", "top_k", "candidate_count"} & config.keys()
        assert "thinking_budget" not in config["thinking_config"]
