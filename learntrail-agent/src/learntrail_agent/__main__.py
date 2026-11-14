"""Run the unauthenticated development service on loopback only."""

import uvicorn


if __name__ == "__main__":
    uvicorn.run("learntrail_agent.main:app", host="127.0.0.1", port=8100)
