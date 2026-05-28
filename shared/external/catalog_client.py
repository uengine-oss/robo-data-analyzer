"""DBMS catalog 서비스 HTTP 공용 — enrich_metadata / enrich_lineage 공유.

catalog 가 활성(`ROBO_CATALOG_ENABLED`)일 때 HTTP·파싱 실패는 **심각** → 전파(raise).
404 만 정상(해당 자원이 catalog 에 없을 뿐) — None 반환.
"""
from __future__ import annotations

from typing import Any, Optional

import httpx


def catalog_headers(api_key: Optional[str]) -> dict:
    """catalog API key → Authorization 헤더 (없으면 빈 dict)."""
    return {"Authorization": f"Bearer {api_key}"} if api_key else {}


async def get_catalog_json(
    http: httpx.AsyncClient, url: str, *, headers: dict, label: str,
) -> Optional[Any]:
    """catalog GET — 404 → None, non-200 → RuntimeError, 200 → JSON 파싱 결과.

    JSON 파싱 실패 시 ValueError 전파 (조용히 묻지 않음). ``label`` 은 오류 메시지용
    (``"metadata"`` / ``"lineage"`` 등).
    """
    resp = await http.get(url, headers=headers)
    if resp.status_code == 404:
        return None
    if resp.status_code != 200:
        raise RuntimeError(
            f"catalog {label} non-200 응답 — url={url} status={resp.status_code}"
        )
    return resp.json()
