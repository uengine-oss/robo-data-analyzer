"""Neo4j 비동기 클라이언트

Cypher 쿼리 실행 및 그래프 데이터 관리를 담당합니다.

주요 기능:
- 비동기 쿼리 실행
- 그래프 결과 반환 (노드/관계)
- 제약조건 관리
- 배치 쿼리 실행 (동시성 안전)
"""

import asyncio
import logging
import warnings
from typing import Any, Optional, AsyncGenerator

from neo4j import AsyncGraphDatabase

from config.settings import settings
# Exceptions: 모든 커스텀 예외는 RuntimeError로 대체됨
from analyzer.common.pipeline.pipeline_control import pipeline_controller

# Neo4j notification warnings 무시
warnings.filterwarnings("ignore", category=DeprecationWarning, module="neo4j")
warnings.filterwarnings("ignore", message=".*Received notification from DBMS server.*")
logging.getLogger("neo4j.notifications").setLevel(logging.ERROR)


class Neo4jClient:
    """Neo4j 비동기 연결 관리 및 쿼리 실행
    
    사용법:
        client = Neo4jClient()
        try:
            result = await client.execute_queries([query1, query2])
        finally:
            await client.close()
    
    또는 async with 구문 사용:
        async with Neo4jClient() as client:
            result = await client.execute_queries([query])
    """

    __slots__ = ("_driver", "_lock", "_config", "_database")

    # 제약조건 + 인덱스 (MERGE 성능의 핵심)
    _CONSTRAINT_QUERIES = [
        # 유니크 제약조건 (자동으로 인덱스도 생성됨)
        "CREATE CONSTRAINT table_unique IF NOT EXISTS FOR (n:Table) REQUIRE (n.db, n.schema, n.name) IS UNIQUE",
        "CREATE CONSTRAINT column_unique IF NOT EXISTS FOR (n:Column) REQUIRE (n.fqn) IS UNIQUE",
        # Example Mapping: Rule 공유 노드, Example/Question 개별 노드
        "CREATE CONSTRAINT rule_hash_unique IF NOT EXISTS FOR (n:Rule) REQUIRE (n.rule_hash) IS UNIQUE",
        "CREATE CONSTRAINT example_id_unique IF NOT EXISTS FOR (n:Example) REQUIRE (n.example_id) IS UNIQUE",
        "CREATE CONSTRAINT question_id_unique IF NOT EXISTS FOR (n:Question) REQUIRE (n.question_id) IS UNIQUE",
        # 인덱스 — MATCH/MERGE 시 전체 스캔 방지
        "CREATE INDEX func_id IF NOT EXISTS FOR (n:FUNCTION) ON (n.function_id)",
        "CREATE INDEX var_id IF NOT EXISTS FOR (n:VARIABLE) ON (n.variable_id)",
        "CREATE INDEX const_id IF NOT EXISTS FOR (n:CONSTANT) ON (n.variable_id)",
        "CREATE INDEX module_fqn IF NOT EXISTS FOR (n:MODULE) ON (n.fqn)",
        "CREATE INDEX package_fqn IF NOT EXISTS FOR (n:PACKAGE) ON (n.fqn)",
        "CREATE INDEX table_name IF NOT EXISTS FOR (n:Table) ON (n.name)",
    ]

    def __init__(self, database: Optional[str] = None):
        """환경변수에서 연결 정보를 읽어 드라이버 초기화
        
        Args:
            database: 사용할 데이터베이스 이름. None이면 settings.neo4j.database 사용
        """
        self._config = settings.neo4j
        self._database = database if database is not None else self._config.database
        self._driver = AsyncGraphDatabase.driver(
            self._config.uri,
            auth=(self._config.user, self._config.password),
        )
        self._lock = asyncio.Lock()

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.close()

    async def close(self):
        """데이터베이스 연결 종료"""
        if self._driver:
            await self._driver.close()


    # =========================================================================
    # 쿼리 실행 메서드
    # =========================================================================
    # 
    # 1. execute_queries: 조회/CRUD용 (결과 데이터 반환)
    #    - API 조회, 용어집 CRUD 등
    #    - 순차 개별 실행, 각 쿼리 결과 반환
    #
    # 2. run_graph_query: 분석 결과 저장용 (노드/관계 그래프 반환)
    #    - Phase 1 AST, Phase 2 LLM 분석 결과 저장
    #    - APOC으로 순차 실행 (노드→관계 의존성 보장, 1번 호출)
    #
    # 3. execute_with_params: UNWIND 배치용 (파라미터 전달)
    #    - DDL 처리, 벡터라이징 등 대량 데이터 처리
    #
    # 4. run_batch_unwind: UNWIND + 그래프 결과 반환
    #    - DDL 처리 시 생성된 노드/관계 반환
    # =========================================================================

    async def execute_queries(
        self,
        queries: list[str | dict[str, Any]],
        params: Optional[dict] = None,
    ) -> list[Any]:
        """Cypher 쿼리 실행 및 결과 반환 (조회/CRUD용)

        각 쿼리를 managed transaction 으로 실행 → TransientError(Deadlock 등) 자동 재시도.

        Args:
            queries: 실행할 Cypher 쿼리 리스트
                - 문자열: 쿼리 문자열만
                - 딕셔너리: {"query": "...", "params": {...}}  ('params' 단일 키)
            params: 공통 쿼리 파라미터 (선택사항, 모든 쿼리에 기본 적용)

        Returns:
            각 쿼리의 결과 데이터 리스트 [[쿼리1결과], [쿼리2결과], ...]

        Raises:
            RuntimeError: 쿼리 실행 실패 시 (재시도 후에도 실패)
        """
        if not queries:
            return []

        async def _normalize(query: str | dict[str, Any]) -> tuple[str, dict[str, Any]]:
            if isinstance(query, dict):
                query_str = str(query.get("query") or "").strip()
                if not query_str:
                    raise RuntimeError("execute_queries dict item requires non-empty 'query'")
                merged = {**(params or {}), **(query.get("params") or {})}
                return query_str, merged
            return query, params or {}

        try:
            async with self._driver.session(database=self._database) as session:
                results: list[list[dict[str, Any]]] = []
                for query in queries:
                    query_str, merged_params = await _normalize(query)

                    async def _tx_work(tx, q=query_str, p=merged_params):
                        result = await tx.run(q, p)
                        return await result.data()

                    data = await session.execute_write(_tx_work)
                    results.append(data)

                return results

        except Exception as e:
            raise RuntimeError(f"Cypher 쿼리 실행 중 오류 발생 (query_count={len(queries)}): {e}") from e

    async def run_graph_query(
        self, 
        queries: list[str | dict[str, Any]],
        batch_size: Optional[int] = None,
    ) -> AsyncGenerator[dict[str, Any], None]:
        """분석 결과 저장용 - 배치 단위로 실행하고 각 배치 결과를 yield
        
        Phase 1 AST, Phase 2 LLM 분석 결과 저장에 사용.
        각 쿼리가 자동 커밋되어 즉시 DB에 반영됨.
        배치 단위로 결과를 yield하여 실시간 스트리밍 가능.
        
        Args:
            queries: 실행할 Cypher 쿼리 리스트
                - 문자열: 기존 방식 (쿼리 문자열)
                - 딕셔너리: 파라미터 바인딩 방식 {"query": "...", "params": {...}}
            batch_size: 배치 크기 (None이면 settings에서 가져옴)
            
        Yields:
            {"Nodes": [...], "Relationships": [...], "batch": N, "total_batches": M}
        """
        if not queries:
            yield {"Nodes": [], "Relationships": [], "batch": 0, "total_batches": 0}
            return
        
        if batch_size is None:
            batch_size = settings.batch.neo4j_query_batch_size
        
        total_batches = (len(queries) + batch_size - 1) // batch_size
        
        try:
            async with self._driver.session(database=self._database) as session:
                for batch_idx in range(total_batches):
                    # 배치 시작 전 일시정지/중단 체크
                    if not await pipeline_controller.check_continue():
                        return

                    start = batch_idx * batch_size
                    end = min(start + batch_size, len(queries))
                    batch_queries = queries[start:end]

                    nodes: dict[str, dict] = {}
                    relationships: dict[str, dict] = {}

                    for query in batch_queries:
                        # managed transaction — TransientError(Deadlock 등) 자동 재시도.
                        # tx.run().graph() 결과는 트랜잭션 종료 후 사용 불가 → 클로저 안에서
                        # 필요한 데이터(노드/관계 식별자/라벨/속성)를 미리 추출해 반환.
                        async def _tx_work(tx, q=query):
                            if isinstance(q, dict):
                                result = await tx.run(q['query'], q.get('params') or {})
                            else:
                                result = await tx.run(q)
                            graph = await result.graph()
                            return {
                                "nodes": [
                                    (n.element_id, list(n.labels), dict(n))
                                    for n in graph.nodes
                                ],
                                "rels": [
                                    (
                                        r.element_id, r.type, dict(r),
                                        r.start_node.element_id,
                                        list(r.start_node.labels), dict(r.start_node),
                                        r.end_node.element_id,
                                        list(r.end_node.labels), dict(r.end_node),
                                    )
                                    for r in graph.relationships
                                ],
                            }

                        snapshot = await session.execute_write(_tx_work)

                        for node_id, labels, props in snapshot["nodes"]:
                            self._collect_node_data(node_id, labels, props, nodes)

                        for r_id, r_type, r_props, s_id, s_labels, s_props, e_id, e_labels, e_props in snapshot["rels"]:
                            relationships[r_id] = {
                                "Relationship ID": r_id,
                                "Type": r_type,
                                "Properties": r_props,
                                "Start Node ID": s_id,
                                "End Node ID": e_id,
                            }
                            self._collect_node_data(s_id, s_labels, s_props, nodes)
                            self._collect_node_data(e_id, e_labels, e_props, nodes)

                    yield {
                        "Nodes": list(nodes.values()),
                        "Relationships": list(relationships.values()),
                        "batch": batch_idx + 1,
                        "total_batches": total_batches,
                    }

        except Exception as e:
            raise RuntimeError(f"그래프 쿼리 실행 중 오류 발생 (query_count={len(queries)}): {e}") from e

    @staticmethod
    def _collect_node_data(node_id: str, labels: list[str], props: dict, nodes_dict: dict) -> None:
        """노드 스냅샷을 결과 딕셔너리에 추가 (중복/빈 노드 제외).

        managed transaction 클로저에서 미리 추출한 (id, labels, props) 튜플을 받음.
        트랜잭션 종료 후에도 안전하게 사용 가능 (driver 객체 참조 X).
        """
        if node_id in nodes_dict:
            return
        if not labels and not props:
            return
        nodes_dict[node_id] = {
            "Node ID": node_id,
            "Labels": labels,
            "Properties": props,
        }

    # =========================================================================
    # 유틸리티
    # =========================================================================

    async def ensure_constraints(self) -> None:
        """MERGE 시 중복/충돌 방지를 위한 유니크 제약조건 생성.

        모든 _CONSTRAINT_QUERIES 는 `CREATE CONSTRAINT ... IF NOT EXISTS` 형식이라
        이미 존재해도 정상 통과. 그 외 예외(권한 부족·문법·연결 문제 등)는 fail-fast.

        managed transaction (`session.execute_write`) → TransientError 자동 재시도.
        """
        async def _tx_work(tx, q=None):
            await tx.run(q)

        async with self._driver.session(database=self._database) as session:
            for query in self._CONSTRAINT_QUERIES:
                try:
                    await session.execute_write(_tx_work, q=query)
                except Exception as e:
                    logging.error(
                        "[NEO4J:CONSTRAINT] 제약조건 생성 실패 — query=%s | %s: %s",
                        query[:80], type(e).__name__, e, exc_info=True,
                    )
                    raise RuntimeError(
                        f"Neo4j 제약조건 생성 실패: {type(e).__name__}: {e} (query={query[:80]})"
                    ) from e

    async def run_batch_unwind(
        self,
        query: str,
        items: list[dict],
        batch_size: int = 500,
    ) -> dict[str, list]:
        """UNWIND 배치 + 그래프 결과 반환 (DDL 처리용)

        DDL 처리 시 스키마/테이블/컬럼/FK를 대량 생성하고 생성된 노드/관계 정보를 반환.
        managed transaction → TransientError(Deadlock 등) 자동 재시도.

        Args:
            query: ``UNWIND $items AS item`` 형태의 Cypher 쿼리
            items: 처리할 데이터 리스트
            batch_size: 한 번에 처리할 항목 수 (기본 500)

        Returns:
            {"Nodes": [...], "Relationships": [...]} — 생성된 그래프 데이터
        """
        if not items:
            return {"Nodes": [], "Relationships": []}

        async def _tx_work(tx, batch):
            result = await tx.run(query, {"items": batch})
            graph = await result.graph()
            return {
                "nodes": [
                    (n.element_id, list(n.labels), dict(n))
                    for n in graph.nodes
                ],
                "rels": [
                    (
                        r.element_id, r.type, dict(r),
                        r.start_node.element_id,
                        list(r.start_node.labels), dict(r.start_node),
                        r.end_node.element_id,
                        list(r.end_node.labels), dict(r.end_node),
                    )
                    for r in graph.relationships
                ],
            }

        try:
            nodes: dict[str, dict] = {}
            relationships: dict[str, dict] = {}

            async with self._driver.session(database=self._database) as session:
                for i in range(0, len(items), batch_size):
                    batch = items[i:i + batch_size]
                    snapshot = await session.execute_write(_tx_work, batch)

                    for node_id, labels, props in snapshot["nodes"]:
                        self._collect_node_data(node_id, labels, props, nodes)

                    for r_id, r_type, r_props, s_id, s_labels, s_props, e_id, e_labels, e_props in snapshot["rels"]:
                        relationships[r_id] = {
                            "Relationship ID": r_id,
                            "Type": r_type,
                            "Properties": r_props,
                            "Start Node ID": s_id,
                            "End Node ID": e_id,
                        }
                        self._collect_node_data(s_id, s_labels, s_props, nodes)
                        self._collect_node_data(e_id, e_labels, e_props, nodes)

            return {
                "Nodes": list(nodes.values()),
                "Relationships": list(relationships.values()),
            }
        except Exception as e:
            raise RuntimeError(f"배치 쿼리 실행 중 오류 발생 (항목 수: {len(items)}): {e}") from e

    async def check_nodes_exist(
        self,
        file_names: list[tuple[str, str]],
    ) -> bool:
        """지정된 파일에 해당하는 노드 존재 여부 확인.

        managed transaction (`session.execute_read`) → TransientError 자동 재시도.

        Args:
            file_names: [(directory, file_name), ...] 튜플 리스트

        Returns:
            하나라도 존재하면 True
        """
        if not file_names:
            return False

        query = (
            "UNWIND $pairs as target\n"
            "MATCH (n)\n"
            "WHERE n.directory = target.directory AND n.file_name = target.file_name\n"
            "RETURN COUNT(n) > 0 AS exists"
        )
        pairs = [{"directory": d, "file_name": f} for d, f in file_names]

        async def _tx_work(tx):
            result = await tx.run(query, {"pairs": pairs})
            record = await result.single()
            return bool(record["exists"]) if record else False

        try:
            async with self._driver.session(database=self._database) as session:
                return await session.execute_read(_tx_work)
        except Exception as e:
            raise RuntimeError(f"노드 존재 여부 확인 중 오류: {e}") from e

