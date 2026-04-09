"""DDL Phase 공통 로직 — Framework/DBMS 양쪽에서 사용.

DDL 파일 처리의 핵심 루프를 한 곳에서 관리.
전략별 차이(상태 저장, 메시지 형식)는 콜백으로 처리.
"""

from __future__ import annotations

import logging
import os
from typing import Any, AsyncGenerator, Optional

from analyzer.common.ddl_utils import list_ddl_files
from analyzer.common.ddl_processor import process_ddl
from util.ndjson_stream import emit_message, emit_phase_event

logger = logging.getLogger(__name__)


async def run_ddl_processing(
    *,
    orchestrator: Any,
    client: Any,
    cypher_lock: Any,
    ddl_schemas: set,
    ddl_table_metadata: dict,
    datasource_name: str = "",
    skip_message: str = "📋 DDL 파일 없음 — DDL 처리 생략",
    on_file_start: Optional[Any] = None,
) -> AsyncGenerator[bytes, None]:
    """DDL 파일 처리 공통 루프.

    Args:
        orchestrator: 오케스트레이터 (dirs, datasource_name 등)
        client: Neo4j 클라이언트
        cypher_lock: asyncio.Lock
        ddl_schemas: DDL에서 발견된 스키마 이름 set (in-place 수정)
        ddl_table_metadata: DDL 테이블 메타데이터 dict (in-place 수정)
        datasource_name: 데이터소스 이름
        skip_message: DDL 없을 때 메시지
        on_file_start: 파일 시작 시 콜백 (idx, total, fname) -> bytes

    Yields:
        NDJSON bytes
    """
    ddl_files = list_ddl_files(orchestrator)
    if not ddl_files:
        yield emit_message(skip_message)
        return

    ddl_dir = orchestrator.dirs.get("ddl", "")
    total_files = len(ddl_files)
    ds_name = datasource_name or getattr(orchestrator, "datasource_name", "") or ""

    yield emit_message(f"📋 DDL 파일 {total_files}개 처리 시작")

    for idx, fname in enumerate(ddl_files):
        ddl_path = os.path.join(ddl_dir, fname)
        file_base = int((idx / total_files) * 100)
        file_end = int(((idx + 1) / total_files) * 100)

        if on_file_start:
            yield on_file_start(idx + 1, total_files, fname)
        else:
            yield emit_message(f"  📄 [{idx + 1}/{total_files}] {fname}")

        try:
            async for chunk in process_ddl(
                ddl_path=ddl_path,
                client=client,
                file_name=fname,
                orchestrator=orchestrator,
                cypher_lock=cypher_lock,
                ddl_schemas=ddl_schemas,
                ddl_table_metadata=ddl_table_metadata,
                emit_progress=True,
                file_base_progress=file_base,
                file_end_progress=file_end,
                datasource_name=ds_name,
            ):
                if isinstance(chunk, bytes):
                    yield chunk
        except Exception as e:
            logger.error("[PHASE:0:DDL] DDL 처리 실패 | file=%s error=%s", fname, e, exc_info=True)
            yield emit_message(f"  ⚠️ DDL 처리 실패: {fname} ({str(e)[:60]})")

        yield emit_phase_event(0, "DDL 처리", "running", file_end)

    yield emit_message(f"✅ DDL 처리 완료 | schemas={len(ddl_schemas)} tables={len(ddl_table_metadata)}")
