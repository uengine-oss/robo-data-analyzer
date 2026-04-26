"""robo-data-catalog HTTP 클라이언트.

analyzer Phase 2 Linking 완료 후, 식별된 테이블명 batch 를 1회 POST 하여
catalog 가 매칭·샘플 조회한 결과 map 을 받는다.

호출 경로:
  analysis_context.build_table_sample_map
    → CatalogClient.sample_context
    → POST {settings.catalog.service_url}/robo/tables/sample-context
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional, Sequence

import aiohttp

from config.settings import settings

logger = logging.getLogger(__name__)

_DEFAULT_TIMEOUT = 30.0


class CatalogClient:
    """catalog 서비스의 /robo/tables/sample-context 1회 호출 전용 클라이언트."""

    def __init__(
        self,
        base_url: Optional[str] = None,
        timeout: float = _DEFAULT_TIMEOUT,
    ):
        self._base_url = (base_url or settings.catalog.service_url).rstrip("/")
        self._timeout = timeout

    async def sample_context(
        self,
        datasource: str,
        table_names: Sequence[str],
        sample_limit: int = 5,
        similarity_threshold: float = 85.0,
    ) -> Dict[str, Optional[Dict[str, Any]]]:
        """catalog 에 테이블명 batch 를 전달해 샘플 컨텍스트 map 을 받는다.

        응답 key = 요청 table_names 원본. 값이 None 이면 매칭 실패.
        """
        if not datasource:
            raise ValueError("datasource is required")
        if not table_names:
            return {}

        url = f"{self._base_url}/robo/tables/sample-context"
        payload = {
            "datasource": datasource,
            "table_names": list(table_names),
            "sample_limit": sample_limit,
            "similarity_threshold": similarity_threshold,
        }
        timeout = aiohttp.ClientTimeout(total=self._timeout)

        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(url, json=payload) as resp:
                resp.raise_for_status()
                return await resp.json()
