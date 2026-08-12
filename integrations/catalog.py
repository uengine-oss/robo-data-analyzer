"""DBMS catalog 서비스 HTTP 공용 — enrich_metadata / enrich_lineage 공유.

분석기 책임: URL 호출 + 응답 처리. catalog 인증/dataset 식별 등은 catalog
영역 — 분석기는 인자로 받지 X.

catalog 가 활성(``ROBO_CATALOG_ENABLED``)일 때 HTTP·파싱 실패는 **심각** → 전파(raise).
404 만 정상(해당 자원이 catalog 에 없을 뿐) — None 반환.
"""
from __future__ import annotations

from typing import Any, Optional

import httpx

from shared.config.settings import SETTINGS
from shared.events.runtime_logger import log_info


def catalog_base_url_or_skip(step: str) -> Optional[str]:
    """catalog 활성·base_url 가드 — enrich_metadata / enrich_lineage 공용.

    비활성(``ROBO_CATALOG_ENABLED=false``)/base_url 미설정이면 ``log_info(skip)`` 후 None
    (호출 step 은 None 이면 ``return {}``). 활성이면 rstrip 된 base_url 반환.
    """
    if not SETTINGS.catalog_api.enabled:
        log_info(step, "skip", reason="catalog 비활성화 (ROBO_CATALOG_ENABLED=false)")
        return None
    if not SETTINGS.catalog_api.base_url:
        log_info(step, "skip", reason="ROBO_CATALOG_BASE_URL 미설정")
        return None
    return SETTINGS.catalog_api.base_url.rstrip("/")


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


async def post_sample_context(
    http: httpx.AsyncClient,
    base_url: str,
    datasource: str,
    table_names: list[str],
    *,
    sample_limit: int = 5,
    similarity_threshold: float = 85.0,
) -> dict[str, Any]:
    """catalog ``POST /tables/sample-context`` — 테이블명 batch → 매칭·샘플 map.

    응답: ``{요청 테이블명: {resolved, score, columns, sample_rows} | None}`` (매칭 실패=None).
    빈 ``table_names`` → ``{}`` (호출 안 함). non-200 → RuntimeError 전파(조용히 묻지 X).
    ``base_url`` 은 catalog api_prefix(``/robo``) 포함 — ``get_catalog_json`` 과 동일 규약.
    """
    if not table_names:
        return {}
    url = f"{base_url}/tables/sample-context"
    body = {
        "datasource": datasource,
        "table_names": table_names,
        "sample_limit": sample_limit,
        "similarity_threshold": similarity_threshold,
    }
    resp = await http.post(url, json=body)
    if resp.status_code != 200:
        raise RuntimeError(
            f"catalog sample-context non-200 응답 — url={url} status={resp.status_code}"
        )
    return resp.json()


async def get_table_discovery(
    http: httpx.AsyncClient,
    base_url: str,
    datasource: str,
    *,
    sample_limit: int = 5,
    max_tables: int = 1000,
) -> Optional[dict[str, Any]]:
    """catalog ``GET /tables/discovery`` — datasource 실제 테이블·컬럼·샘플 열거.

    단어장 이원화(DDL ∪ 실DB) 계약: specs/107-datasource-table-discovery/contracts/.
    404 = datasource 미등록 → None (호출 step 이 경고 후 DDL 단어장으로 계속).
    그 외 non-200 → RuntimeError (catalog 활성 상태의 실패 = 심각, 조용히 묻지 X).
    """
    url = (
        f"{base_url}/tables/discovery"
        f"?datasource={datasource}&sample_limit={sample_limit}&max_tables={max_tables}"
    )
    resp = await http.get(url)
    if resp.status_code == 404:
        return None
    if resp.status_code != 200:
        raise RuntimeError(
            f"catalog discovery non-200 응답 — url={url} status={resp.status_code}"
        )
    return resp.json()


async def post_object_resolution_context(
    http: httpx.AsyncClient,
    base_url: str,
    datasource: str,
    references: list[dict[str, Any]],
    *,
    sample_limit: int = 5,
) -> Optional[dict[str, Any]]:
    """Catalog exact object resolver batch. 404 datasource is the only nullable outcome."""
    if not references:
        return {"datasource": datasource, "results": [], "metrics": {}}
    url = f"{base_url}/tables/resolve-context"
    resp = await http.post(url, json={
        "datasource": datasource,
        "references": references,
        "sample_limit": sample_limit,
    })
    if resp.status_code == 404:
        return None
    if resp.status_code != 200:
        raise RuntimeError(
            f"catalog object resolution non-200 응답 — url={url} status={resp.status_code}"
        )
    payload = resp.json()
    if not isinstance(payload, dict) or not isinstance(payload.get("results"), list):
        raise ValueError("catalog object resolution response is malformed")
    return payload
