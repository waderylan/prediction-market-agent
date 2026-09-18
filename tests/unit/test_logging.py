import json
import logging

import pytest

from market_agent.logging import JsonFormatter, log_event


@pytest.mark.unit
def test_structured_logging_redacts_secrets_and_omits_prompt(
    caplog: pytest.LogCaptureFixture,
) -> None:
    logger = logging.getLogger("test.safe_logging")
    caplog.set_level(logging.INFO, logger=logger.name)

    log_event(
        logger,
        "request_received",
        query="Tell me something private",
        api_key="not-a-real-secret",
        session_id="session-1",
    )

    record = caplog.records[-1]
    rendered = json.loads(JsonFormatter().format(record))
    assert rendered["event"] == "request_received"
    assert rendered["fields"]["query"] == "[OMITTED length=25]"
    assert rendered["fields"]["api_key"] == "[REDACTED]"
    assert rendered["fields"]["session_id"] == "session-1"
