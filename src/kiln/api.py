from __future__ import annotations

from fastapi import FastAPI
from pydantic import BaseModel

from .models import TargetContract
from .orchestrator import Kiln
from .registry import ProgramRegistry
from .synthesis import HeuristicSynthesizer

app = FastAPI(title="Kiln")


class IngestionRequest(BaseModel):
    source_family: str
    rows: list[dict[str, object]]
    contract: TargetContract


@app.get("/health")
def health():
    return {"status": "ok", "service": "kiln"}


@app.post("/ingestions")
def ingest(request: IngestionRequest):
    with ProgramRegistry() as registry:
        runner = Kiln(HeuristicSynthesizer(), registry)
        return runner.run(request.rows, request.contract, request.source_family)
