"""DBMS catalog 서비스 HTTP 공용 — enrich_metadata / enrich_lineage 공유.

분석기 책임: URL 호출 + 응답 처리. catalog 인증/dataset 식별 등은 catalog
영역 — 분석기는 인자로 받지 X.

catalog 가 활성(``ROBO_CATALOG_ENABLED``)일 때 HTTP·파싱 실패는 **심각** → 전파(raise).
404 만 정상(해당 자원이 catalog 에 없을 뿐) — None 반환.
"""
from __future__ import annotations

from typing import Any, Optional

import httpx

from shared.config.env_catalog import ENV
from shared.log.logger import log_info


def catalog_base_url_or_skip(step: str) -> Optional[str]:
    """catalog 활성·base_url 가드 — enrich_metadata / enrich_lineage 공용.

    비활성(``ROBO_CATALOG_ENABLED=false``)/base_url 미설정이면 ``log_info(skip)`` 후 None
    (호출 step 은 None 이면 ``return {}``). 활성이면 rstrip 된 base_url 반환.
    """
    if not ENV.catalog.enabled:
        log_info(step, "skip", reason="catalog 비활성화 (ROBO_CATALOG_ENABLED=false)")
        return None
    if not ENV.catalog.base_url:
        log_info(step, "skip", reason="ROBO_CATALOG_BASE_URL 미설정")
        return None
    return ENV.catalog.base_url.rstrip("/")


async def get_catalog_json(
    http: httpx.AsyncClient, url: str, *, label: str,
) -> Optional[Any]:
    """catalog GET — 404 → None, non-200 → RuntimeError, 200 → JSON 파싱 결과.

    JSON 파싱 실패 시 ValueError 전파 (조용히 묻지 않음). ``label`` 은 오류 메시지용
    (``"metadata"`` / ``"lineage"`` 등). 분석기는 HTTP 응답 처리만 — 인증은 catalog
    서비스 측 (IP whitelist / 내부망 등).
    """
    resp = await http.get(url)
    if resp.status_code == 404:
        return None
    if resp.status_code != 200:
        raise RuntimeError(
            f"catalog {label} non-200 응답 — url={url} status={resp.status_code}"
        )
    return resp.json()
