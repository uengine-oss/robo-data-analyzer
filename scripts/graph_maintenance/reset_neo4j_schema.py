"""Neo4j 스키마 1회 리셋 — 기존 제약/인덱스 전부 제거 후 현 스펙으로 재생성.

DB 가 옛 버전·타 프로젝트 스키마로 오염됐을 때 **1회** 실행하는 운영 도구.
평상시 run 의 ``ensure_schema`` 는 ``IF NOT EXISTS`` 로 드롭을 안 하므로 (스키마는
안정적 — 매 run drop-recreate X), 오염 정리는 이 스크립트로 분리한다.

동작:
1. 기존 제약 전부 DROP (backing 인덱스 함께 사라짐).
2. 남은 인덱스 DROP — 단 내장 토큰 ``LOOKUP`` 인덱스는 보존.
3. ``ensure_schema`` 로 현 스펙(``schema_constraints.CONSTRAINTS + INDEXES``) 재생성.

사용::

    python scripts/graph_maintenance/reset_neo4j_schema.py
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from graph.neo4j_client import Neo4jClient
from graph.schema.schema_constraints import ensure_schema


async def main() -> None:
    neo = Neo4jClient()
    try:
        # 1. 제약 전부 제거 (제약 먼저 — 제약 backing 인덱스가 함께 사라짐).
        constraints = await neo.run_read_query("SHOW CONSTRAINTS YIELD name")
        for row in constraints:
            await neo.run_query(f"DROP CONSTRAINT `{row['name']}` IF EXISTS")
        print(f"dropped {len(constraints)} constraints")

        # 2. 잔여 인덱스 제거 (LOOKUP = Neo4j 내장 토큰 인덱스라 보존).
        indexes = await neo.run_read_query("SHOW INDEXES YIELD name, type")
        dropped = 0
        for row in indexes:
            if row.get("type") == "LOOKUP":
                continue
            await neo.run_query(f"DROP INDEX `{row['name']}` IF EXISTS")
            dropped += 1
        print(f"dropped {dropped} indexes ({len(indexes) - dropped} LOOKUP kept)")

        # 3. 현 스펙대로 재생성.
        await ensure_schema(neo)
        cons = await neo.run_read_query("SHOW CONSTRAINTS YIELD name")
        idxs = await neo.run_read_query("SHOW INDEXES YIELD name")
        print(f"recreated per current spec: {len(cons)} constraints, {len(idxs)} indexes")
    finally:
        await neo.close()


if __name__ == "__main__":
    asyncio.run(main())
