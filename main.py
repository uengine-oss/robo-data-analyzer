"""FastAPI 진입점 — uvicorn 으로 실행.

Usage::

    uvicorn main:app --host 0.0.0.0 --port 8000

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

from fastapi import FastAPI

from api.analyze_router import router as analyze_router


app = FastAPI(title="ROBO Analyzer", version="0.1.0")
app.include_router(analyze_router)


@app.get("/")
async def health() -> dict:
    """health check."""
    return {"status": "ok", "service": "robo-analyzer"}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
