"""
Neo4j 스키마 초기화 (DBMS 표준)

DBMS 표준 스키마:
- TABLE PK: {db, schema, name} (기본값: default, PUBLIC, 테이블명)
- COLUMN PK: {fqn} (db.schema.table.column 형식)

사용 예시:
    schema = Neo4jSchemaManager(driver)
    await schema.ensure_all_constraints()
    
    # 또는 별칭 사용
    schema = SchemaManager(driver, ddl_root="data/ddl")
    await schema.check_table_exists("ORDERS")
"""

import logging
from typing import Any, List, Dict, Optional
from dataclasses import dataclass

from ..helpers.logger import get_logger


@dataclass
class ConstraintResult:
    """제약조건 생성 결과"""
    name: str
    success: bool
    message: str


class Neo4jSchemaManager:
    """
    Neo4j 스키마 관리 (DBMS 표준)
    
    DBMS 표준 제약조건:
    - TABLE: (db, schema, name) UNIQUE
    - COLUMN: fqn UNIQUE (fqn = db.schema.table.column)
    """
    
    # 필수 제약조건 목록 (DBMS 표준 스키마)
    CONSTRAINTS = [
        # TABLE 고유키 (DBMS 표준: db + schema + name)
        {
            "name": "table_pk_unique",
            "cypher": """
            CREATE CONSTRAINT table_pk_unique IF NOT EXISTS
            FOR (t:Table) REQUIRE (t.db, t.schema, t.name) IS UNIQUE
            """,
        },
        # COLUMN 고유키 (DBMS 표준: fqn = db.schema.table.column)
        {
            "name": "column_fqn_unique",
            "cypher": """
            CREATE CONSTRAINT column_fqn_unique IF NOT EXISTS
            FOR (c:Column) REQUIRE c.fqn IS UNIQUE
            """,
        },
    ]
    
    # 필수 인덱스 목록 (플랜 기반)
    INDEXES = [
        # 후보 선정용 인덱스
        {
            "name": "idx_resolution_candidate",
            "cypher": """
            CREATE INDEX idx_resolution_candidate IF NOT EXISTS
            FOR (n:Statement) ON (n.resolution_candidate)
            """,
        },
        # deepagents_status 기반 조회
        {
            "name": "idx_deepagents_status",
            "cypher": """
            CREATE INDEX idx_deepagents_status IF NOT EXISTS
            FOR (n:Statement) ON (n.deepagents_status)
            """,
        },
        # FROM 관계 인덱스
        {
            "name": "idx_from_rel",
            "cypher": """
            CREATE INDEX idx_from_rel IF NOT EXISTS
            FOR ()-[r:FROM]-() ON (r.source)
            """,
        },
        # WRITES 관계 인덱스
        {
            "name": "idx_writes_rel",
            "cypher": """
            CREATE INDEX idx_writes_rel IF NOT EXISTS
            FOR ()-[r:WRITES]-() ON (r.source)
            """,
        },
    ]
    
    def __init__(self, driver: Any, ddl_root: Optional[str] = None):
        """
        Args:
            driver: Neo4j 드라이버
            ddl_root: DDL 파일 경로 (향후 파일 기반 체크용, 현재 미사용)
        """
        self.driver = driver
        self.ddl_root = ddl_root
    
    async def _execute_query(self, cypher: str) -> Dict[str, Any]:
        """쿼리 실행"""
        async with self.driver.session() as session:
            result = await session.run(cypher)
            summary = await result.consume()
            return {
                "counters": {
                    "nodes_created": summary.counters.nodes_created,
                    "nodes_deleted": summary.counters.nodes_deleted,
                    "relationships_created": summary.counters.relationships_created,
                    "relationships_deleted": summary.counters.relationships_deleted,
                    "properties_set": summary.counters.properties_set,
                } if summary.counters else {},
            }
    
    async def ensure_constraint(self, name: str, cypher: str) -> ConstraintResult:
        """
        단일 제약조건 생성
        """
        try:
            await self._execute_query(cypher)
            return ConstraintResult(
                name=name,
                success=True,
                message="Constraint created or already exists",
            )
        except Exception as e:
            return ConstraintResult(
                name=name,
                success=False,
                message=str(e),
            )
    
    async def ensure_all_constraints(self) -> List[ConstraintResult]:
        """
        모든 필수 제약조건 생성
        
        S2: artifact_key UNIQUE
        """
        results = []
        
        for constraint in self.CONSTRAINTS:
            result = await self.ensure_constraint(
                constraint["name"],
                constraint["cypher"],
            )
            results.append(result)
        
        return results
    
    async def ensure_all_indexes(self) -> List[ConstraintResult]:
        """
        모든 필수 인덱스 생성
        """
        results = []
        
        for index in self.INDEXES:
            result = await self.ensure_constraint(
                index["name"],
                index["cypher"],
            )
            results.append(result)
        
        return results
    
    async def add_dbtable_label(self) -> Dict[str, int]:
        """
        R7: Table/PendingTable에 공통 라벨 :DbTable 추가
        
        이렇게 하면 단일 쿼리로 모든 테이블 관련 노드를 조회 가능:
        MATCH (t:DbTable) RETURN t
        """
        results = {}
        
        # Table에 DbTable 라벨 추가
        query1 = """
        MATCH (t:Table)
        WHERE NOT t:DbTable
        SET t:DbTable
        RETURN count(t) as updated
        """
        result1 = await self._execute_query(query1)
        results["table_updated"] = result1.get("counters", {}).get("labels_added", 0)
        
        # PendingTable에 DbTable 라벨 추가
        query2 = """
        MATCH (t:PendingTable)
        WHERE NOT t:DbTable
        SET t:DbTable
        RETURN count(t) as updated
        """
        result2 = await self._execute_query(query2)
        results["pending_table_updated"] = result2.get("counters", {}).get("labels_added", 0)
        
        return results
    
    async def ensure_schema(self) -> Dict[str, Any]:
        """
        전체 스키마 초기화 (제약조건 + 인덱스 + 라벨)
        """
        constraint_results = await self.ensure_all_constraints()
        index_results = await self.ensure_all_indexes()
        label_results = await self.add_dbtable_label()
        
        return {
            "constraints": [
                {"name": r.name, "success": r.success}
                for r in constraint_results
            ],
            "indexes": [
                {"name": r.name, "success": r.success}
                for r in index_results
            ],
            "labels": label_results,
        }
    
    async def check_ddl_exists(self, table_name: str) -> bool:
        """
        DDL Missing Policy: 테이블이 DDL에 존재하는지 확인
        
        Args:
            table_name: 테이블명 (대소문자 구분)
        
        Returns:
            True if table exists in DDL, False otherwise
        """
        query = """
        MATCH (t:Table {name: $name})
        WHERE t.from_ddl = true
        RETURN count(t) > 0 as exists
        """
        
        async with self.driver.session() as session:
            result = await session.run(query, {"name": table_name})
            record = await result.single()
            exists = record["exists"] if record else False
            
            # 로깅
            log = get_logger()
            log.ddl_check(table_name, exists)
            
            return exists
    
    async def check_table_exists(self, table_name: str) -> bool:
        """
        check_ddl_exists의 별칭 메서드
        
        main_agent.py와의 호환성을 위해 제공됩니다.
        
        Args:
            table_name: 테이블명
        
        Returns:
            True if table exists in DDL, False otherwise
        """
        return await self.check_ddl_exists(table_name)
    
    async def create_pending_table(
        self, 
        table_name: str,
        reason: str,
        patch_id: str,
        run_id: str
    ) -> Dict[str, Any]:
        """
        DDL에 없는 테이블을 PendingTable로 생성
        
        R7: DbTable 라벨도 같이 추가
        """
        query = """
        MERGE (t:PendingTable:DbTable {name: $name})
        ON CREATE SET
            t.reason = $reason,
            t.created_at = datetime(),
            t.patch_id = $patch_id,
            t.run_id = $run_id,
            t.status = 'pending_review'
        ON MATCH SET
            t.updated_at = datetime()
        RETURN t, elementId(t) as element_id
        """
        
        async with self.driver.session() as session:
            result = await session.run(query, {
                "name": table_name,
                "reason": reason,
                "patch_id": patch_id,
                "run_id": run_id,
            })
            record = await result.single()
            
            if record:
                return {
                    "name": table_name,
                    "element_id": record["element_id"],
                    "created": True,
                }
            return {"name": table_name, "created": False}


# =============================================================================
# 클래스 별칭 (main_agent.py 호환성)
# =============================================================================

# main_agent.py에서 SchemaManager로 import하므로 별칭 제공
SchemaManager = Neo4jSchemaManager

