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
from analyzer.pipeline_control import pipeline_controller

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

    # 유니크 제약조건 쿼리
    _CONSTRAINT_QUERIES = [
        "CREATE CONSTRAINT table_unique IF NOT EXISTS FOR (__cy_t__:Table) "
        "REQUIRE (__cy_t__.db, __cy_t__.schema, __cy_t__.name) IS UNIQUE",
        "CREATE CONSTRAINT column_unique IF NOT EXISTS FOR (__cy_c__:Column) "
        "REQUIRE (__cy_c__.fqn) IS UNIQUE",
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

    async def execute_queries(self, queries: list[str], params: Optional[dict] = None) -> list[Any]:
        """Cypher 쿼리 실행 및 결과 반환 (조회/CRUD용)
        
        모든 쿼리를 순차 실행하고 각 쿼리의 결과를 반환합니다.
        
        Args:
            queries: 실행할 Cypher 쿼리 리스트
            params: 쿼리 파라미터 (선택사항, 모든 쿼리에 동일하게 적용)
            
        Returns:
            각 쿼리의 결과 데이터 리스트 [[쿼리1결과], [쿼리2결과], ...]
            
        Raises:
            RuntimeError: 쿼리 실행 실패 시
        """
        if not queries:
            return []
        
        try:
            async with self._driver.session(database=self._database) as session:
                results = []
                for query in queries:
                    query_result = await session.run(query, params or {})
                    results.append(await query_result.data())
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
                        # 자동 커밋 모드: 각 쿼리가 즉시 커밋됨
                        # 쿼리가 딕셔너리인 경우 (쿼리+파라미터 바인딩)
                        if isinstance(query, dict):
                            query_str = query['query']
                            params = query.get('params', {})
                            graph = await (await session.run(query_str, params)).graph()
                        else:
                            # 기존 방식 (문자열만)
                            graph = await (await session.run(query)).graph()

                        for node in graph.nodes:
                            self._collect_node(node, nodes)

                        for rel in graph.relationships:
                            relationships[rel.element_id] = {
                                "Relationship ID": rel.element_id,
                                "Type": rel.type,
                                "Properties": dict(rel),
                                "Start Node ID": rel.start_node.element_id,
                                "End Node ID": rel.end_node.element_id,
                            }
                            self._collect_node(rel.start_node, nodes)
                            self._collect_node(rel.end_node, nodes)
                    
                    yield {
                        "Nodes": list(nodes.values()),
                        "Relationships": list(relationships.values()),
                        "batch": batch_idx + 1,
                        "total_batches": total_batches,
                    }
                    
        except Exception as e:
            raise RuntimeError(f"그래프 쿼리 실행 중 오류 발생 (query_count={len(queries)}): {e}") from e

    @staticmethod
    def _collect_node(node, nodes_dict: dict) -> None:
        """노드를 결과 딕셔너리에 추가 (중복/빈 노드 제외)"""
        if node.element_id in nodes_dict:
            return
        labels, props = list(node.labels), dict(node)
        if not labels and not props:
            return
        nodes_dict[node.element_id] = {
            "Node ID": node.element_id,
            "Labels": labels,
            "Properties": props,
        }

    # =========================================================================
    # 유틸리티
    # =========================================================================

    async def ensure_constraints(self) -> None:
        """MERGE 시 중복/충돌 방지를 위한 유니크 제약조건 생성"""
        try:
            async with self._driver.session(database=self._database) as session:
                for query in self._CONSTRAINT_QUERIES:
                    try:
                        await session.run(query)
                    except Exception:
                        logging.debug(f"제약조건 생성 스킵 (이미 존재): {query[:50]}...")
        except Exception as e:
            logging.warning(f"제약조건 보장 중 경고: {e}")

    async def execute_with_params(
        self,
        query: str,
        params: dict[str, Any],
    ) -> list[Any]:
        """UNWIND 배치용 - 파라미터와 함께 단일 쿼리 실행
        
        DDL 처리, 벡터라이징 등 대량 데이터 처리에 사용.
        
        예시:
            query = "UNWIND $items AS item MERGE (__cy_t__:Table {name: item.name})"
            params = {"items": [{"name": "users"}, {"name": "orders"}]}
            → 1번 Neo4j 호출로 2개 테이블 생성
        
        Args:
            query: UNWIND $items AS item 형태의 Cypher 쿼리
            params: {"items": [...]} 형태의 파라미터
            
        Returns:
            쿼리 결과 데이터 리스트
        """
        try:
            async with self._driver.session(database=self._database) as session:
                result = await session.run(query, params)
                return await result.data()
        except Exception as e:
            raise RuntimeError(f"파라미터 쿼리 실행 중 오류 발생: {e}") from e

    async def run_batch_unwind(
        self,
        query: str,
        items: list[dict],
        batch_size: int = 500,
    ) -> dict[str, list]:
        """UNWIND 배치 + 그래프 결과 반환 (DDL 처리용)
        
        DDL 처리 시 스키마/테이블/컬럼/FK를 대량 생성하고
        생성된 노드/관계 정보를 반환합니다.
        
        예시:
            - 500개 테이블 → 1번 Neo4j 호출 (기존 500번)
        
        Args:
            query: UNWIND $items AS item 형태의 Cypher 쿼리
            items: 처리할 데이터 리스트
            batch_size: 한 번에 처리할 항목 수 (기본 500)
            
        Returns:
            {"Nodes": [...], "Relationships": [...]} - 생성된 그래프 데이터
        """
        if not items:
            return {"Nodes": [], "Relationships": []}
        
        try:
            nodes: dict[str, dict] = {}
            relationships: dict[str, dict] = {}
            
            async with self._driver.session(database=self._database) as session:
                for i in range(0, len(items), batch_size):
                    batch = items[i:i + batch_size]
                    graph = await (await session.run(query, {"items": batch})).graph()
                    
                    for node in graph.nodes:
                        self._collect_node(node, nodes)
                    
                    for rel in graph.relationships:
                        relationships[rel.element_id] = {
                            "Relationship ID": rel.element_id,
                            "Type": rel.type,
                            "Properties": dict(rel),
                            "Start Node ID": rel.start_node.element_id,
                            "End Node ID": rel.end_node.element_id,
                        }
                        self._collect_node(rel.start_node, nodes)
                        self._collect_node(rel.end_node, nodes)
            
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
        """지정된 파일에 해당하는 노드 존재 여부 확인
        
        Args:
            file_names: [(directory, file_name), ...] 튜플 리스트
            
        Returns:
            하나라도 존재하면 True
        """
        if not file_names:
            return False
        
        query = """
            UNWIND $pairs as target
            MATCH (__cy_n__)
            WHERE __cy_n__.directory = target.directory
              AND __cy_n__.file_name = target.file_name
            RETURN COUNT(__cy_n__) > 0 AS exists
        """
        
        try:
            pairs = [{"directory": d, "file_name": f} for d, f in file_names]
            params = {"pairs": pairs}

            async with self._driver.session(database=self._database) as session:
                result = await session.run(query, params)
                record = await result.single()
                return record["exists"] if record else False
        except Exception as e:
            raise RuntimeError(f"노드 존재 여부 확인 중 오류: {e}") from e

