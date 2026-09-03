from __future__ import annotations

import argparse
import os

import uvicorn
from fastapi import FastAPI, HTTPException

from alpha_spy.config import load_config
from alpha_spy.db import Journal

from .processor import DeltaProcessor


def create_app(processor: DeltaProcessor) -> FastAPI:
    app = FastAPI(
        title="SPY Market Platform — Delta",
        version="0.1.0",
        description="Read-only Alpha/Beta/Gamma convergence and analytical streams.",
    )

    @app.get("/api/delta/state")
    def state():
        return processor.build().as_dict()

    @app.get("/api/delta/streams")
    def streams():
        return processor.streams()

    @app.get("/api/delta/streams/{stream_name}")
    def stream(stream_name: str):
        available = processor.streams()
        if stream_name not in available:
            raise HTTPException(status_code=404, detail=f"unknown stream: {stream_name}")
        return available[stream_name]

    @app.get("/api/delta/health")
    def health():
        delta = processor.build()
        return {
            "status": "ONLINE" if delta.data_quality["composite"] >= 0.70 else "DEGRADED",
            "timestamp": delta.timestamp,
            "data_quality": delta.data_quality,
            "anomalies": delta.anomalies,
            "authority": delta.authority,
        }

    return app


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the read-only Delta market-state API")
    parser.add_argument(
        "--config",
        default=os.getenv("ALPHA_SPY_CONFIG", "/etc/alpha-spy/config.yaml"),
    )
    parser.add_argument(
        "--beta-state-url",
        default=os.getenv("BETA_SPY_STATE_URL", "http://127.0.0.1:8790/api/state"),
    )
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8791)
    args = parser.parse_args()

    config = load_config(args.config)
    processor = DeltaProcessor(
        Journal(config.paths.database),
        beta_state_url=args.beta_state_url,
    )
    uvicorn.run(create_app(processor), host=args.host, port=args.port)


if __name__ == "__main__":
    main()
