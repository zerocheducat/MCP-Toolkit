from __future__ import annotations

import hmac
import os
import sqlite3

from fastapi import Depends, FastAPI, Header, HTTPException
from pydantic import BaseModel, Field

from .service import service

app = FastAPI(title="Aicy Semantic Search", version="0.1.0")


def authenticate(x_semantic_search_token: str | None = Header(default=None)) -> None:
    secret = os.environ.get("SEMANTIC_SEARCH_API_TOKEN", "")
    if not secret or not x_semantic_search_token or not hmac.compare_digest(secret, x_semantic_search_token):
        raise HTTPException(status_code=401, detail="invalid semantic search token")


class IndexFilesRequest(BaseModel):
    paths: list[str] = Field(min_length=1)
    prune: bool = False


class MemoryHubRequest(BaseModel):
    db_path: str = Field(min_length=1)


class SearchRequest(BaseModel):
    query: str = Field(min_length=1, max_length=4000)
    kind: str = "text"
    limit: int = Field(default=10, ge=1, le=100)
    source_kind: str | None = None


def _call(fn, *args):
    try:
        return fn(*args)
    except (ValueError, OSError, sqlite3.Error) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except (RuntimeError, ImportError) as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@app.get("/v1/status", dependencies=[Depends(authenticate)])
def status() -> dict:
    return _call(service().status)


@app.post("/v1/index/files", dependencies=[Depends(authenticate)])
def index_files(body: IndexFilesRequest) -> dict:
    return _call(service().index_files, body.paths, body.prune)


@app.post("/v1/index/memoryhub", dependencies=[Depends(authenticate)])
def sync_memoryhub(body: MemoryHubRequest) -> dict:
    return _call(service().sync_memoryhub, body.db_path)


@app.post("/v1/search", dependencies=[Depends(authenticate)])
def search(body: SearchRequest) -> dict:
    return _call(service().search, body.query, body.kind, body.limit, body.source_kind)
