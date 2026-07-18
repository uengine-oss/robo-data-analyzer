"""FastAPI 진입점 — uvicorn 으로 실행.

Usage::

    uvicorn main:app --host 0.0.0.0 --port 15502

또는 직접::

    python -m main
"""
from __future__ import annotations

import sys
from pathlib import Path

# robo-analyzer-refact 패키지 root 를 sys.path 에 추가 (pyproject.toml 없이 동작)
_ROOT = Path(__file__).resolve().parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from contextlib import asynccontextmanager

from fastapi import FastAPI

from api.analyze_router import router as analyze_router
from api.mcp_app import mcp
from api.search_router import router as search_router
from api.source_router import router as source_router


@asynccontextmanager
async def _lifespan(app: FastAPI):
    # FastMCP streamable-http 는 세션 매니저가 돌아야 응답한다(mount 만으론 sub-app lifespan 미실행).
    async with mcp.session_manager.run():
        yield


app = FastAPI(title="ROBO Analyzer", version="0.1.0", lifespan=_lifespan)
app.include_router(analyze_router)
app.include_router(search_router)
app.include_router(source_router)
app.mount("/robo/mcp", mcp.streamable_http_app())


@app.get("/")
async def health() -> dict:
    """health check."""
    return {"status": "ok", "service": "robo-analyzer"}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=15502)
