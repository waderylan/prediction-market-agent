"""Cloud Run entry point; one worker preserves instance-local memory."""

import uvicorn

from market_agent.config import load_settings

if __name__ == "__main__":
    uvicorn.run("market_agent.app:app", host="0.0.0.0", port=load_settings().port, workers=1)
