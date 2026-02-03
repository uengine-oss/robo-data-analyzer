"""
Neo4j Tools (DBMS 표준 스키마)

그래프 조회 및 트랜잭션 배치 도구.

DBMS DDL 표준 스키마:
- TABLE PK: {db, schema, name}
- COLUMN PK: {fqn} (db.schema.table.column)
- 관계: FROM, WRITES, HAS_COLUMN, FK_TO_TABLE, FK_TO

속성 규칙:
- TABLE: db, schema, name, description, description_source, table_type
- COLUMN: fqn, name, dtype, description, description_source, nullable, pk_constraint
- 임시 속성(created_by, run_id 등)은 TABLE/COLUMN에 넣지 않음 (evidence로)

금지 사항:
- REFERENCES 관계 금지 → FK_TO_TABLE 사용
- Artifact 노드 생성 금지
- RESOLVES_TO 관계 금지

이름 정규화 정책:
- TABLE_NAME_POLICY env 변수로 제어 (ORIGINAL | UPPER | LOWER)
- 기본값: ORIGINAL (원본 그대로 유지)

dtype 정책:
- SQL 타입 변환 없음, 자바 타입 그대로 저장
"""

from typing import Any, Dict, List, Optional
from dataclasses import dataclass
import os


# =============================================================================
# 이름 정규화 정책
# =============================================================================

def get_name_policy() -> str:
    """env에서 이름 정규화 정책 가져오기"""
    return os.getenv("TABLE_NAME_POLICY", "ORIGINAL")


def normalize_name(name: str, policy: str = None) -> str:
    """
    이름 정규화 (정책에 따라)
    
    Args:
        name: 원본 이름
        policy: 정책 (ORIGINAL | UPPER | LOWER). None이면 env에서 가져옴
    
    Returns:
        정규화된 이름
    """
    if policy is None:
        policy = get_name_policy()
    
    if policy == "UPPER":
        return name.upper()
    elif policy == "LOWER":
        return name.lower()
    else:  # ORIGINAL
        return name


@dataclass
class QueryResult:
    """쿼리 결과"""
    records: List[Dict[str, Any]]
    summary: Optional[Dict[str, Any]] = None


class Neo4jTools:
    """
    Neo4j 도구 (Phase 0 기반)
    
    S2: artifact_key UNIQUE constraint
    S3: elementId 사용
    S4: MERGE only, 직접 CREATE 금지
    """
    
    def __init__(self, driver: Any):
        """
        Args:
            driver: Neo4j Python Driver
        """
        self.driver = driver
    
    async def query(
        self, 
        cypher: str, 
        params: Optional[Dict[str, Any]] = None
    ) -> QueryResult:
        """
        Cypher 쿼리 실행 (읽기)
        
        Args:
            cypher: Cypher 쿼리문
            params: 파라미터
        
        Returns:
            QueryResult
        """
        params = params or {}
        
        async with self.driver.session() as session:
            result = await session.run(cypher, params)
            records = [dict(record) async for record in result]
            summary = await result.consume()
            
            # SummaryCounters를 dict로 변환
            counters_dict = {}
            if summary.counters:
                counters_dict = {
                    "nodes_created": summary.counters.nodes_created,
                    "nodes_deleted": summary.counters.nodes_deleted,
                    "relationships_created": summary.counters.relationships_created,
                    "relationships_deleted": summary.counters.relationships_deleted,
                    "properties_set": summary.counters.properties_set,
                    "labels_added": summary.counters.labels_added,
                    "labels_removed": summary.counters.labels_removed,
                    "indexes_added": summary.counters.indexes_added,
                    "indexes_removed": summary.counters.indexes_removed,
                    "constraints_added": summary.counters.constraints_added,
                    "constraints_removed": summary.counters.constraints_removed,
                }
            
            return QueryResult(
                records=records,
                summary={
                    "counters": counters_dict,
                    "query_type": summary.query_type,
                }
            )
    
    async def find_by_element_id(self, element_id: str) -> Optional[Dict[str, Any]]:
        """
        S3: elementId로 노드 조회
        """
        query = """
        MATCH (n) WHERE elementId(n) = $element_id
        RETURN n, labels(n) as labels, elementId(n) as element_id
        """
        
        result = await self.query(query, {"element_id": element_id})
        
        if result.records:
            return result.records[0]
        return None

    # =========================================================================
    # 그래프 검색 도구 (Graph Search Tools)
    # =========================================================================

    async def search_nodes_by_keyword(
        self, 
        keyword: str, 
        labels: Optional[List[str]] = None,
        limit: int = 50
    ) -> QueryResult:
        """
        키워드로 노드 검색 (대소문자 무시)
        
        node_code, name 필드에서 검색
        
        Args:
            keyword: 검색 키워드
            labels: 검색할 라벨 목록 (없으면 전체)
            limit: 최대 결과 수
        
        Returns:
            QueryResult
        """
        label_filter = ""
        if labels:
            label_filter = ":" + ":".join(labels)
        
        query = f"""
        MATCH (n{label_filter})
        WHERE toLower(n.node_code) CONTAINS toLower($keyword)
           OR toLower(n.name) CONTAINS toLower($keyword)
        RETURN n, labels(n) as labels, elementId(n) as element_id,
               n.directory as directory, n.startLine as startLine, n.endLine as endLine
        LIMIT $limit
        """
        
        return await self.query(query, {"keyword": keyword, "limit": limit})

    async def get_graph_schema(self) -> Dict[str, Any]:
        """
        그래프 스키마 조회 - 라벨별 속성 목록
        
        Returns:
            라벨별 속성 정보
        """
        # 라벨 목록 조회
        labels_query = """
        CALL db.labels() YIELD label
        RETURN collect(label) as labels
        """
        labels_result = await self.query(labels_query)
        labels = labels_result.records[0].get("labels", []) if labels_result.records else []
        
        schema = {}
        
        # 각 라벨별 속성 샘플링
        for label in labels:
            props_query = f"""
            MATCH (n:`{label}`)
            WITH n LIMIT 10
            UNWIND keys(n) as key
            RETURN DISTINCT key
            ORDER BY key
            """
            props_result = await self.query(props_query)
            schema[label] = [r.get("key") for r in props_result.records]
        
        # 관계 타입 조회
        rels_query = """
        CALL db.relationshipTypes() YIELD relationshipType
        RETURN collect(relationshipType) as rel_types
        """
        rels_result = await self.query(rels_query)
        rel_types = rels_result.records[0].get("rel_types", []) if rels_result.records else []
        
        return {
            "node_labels": schema,
            "relationship_types": rel_types,
        }

    async def find_imports_in_file(self, directory: str) -> QueryResult:
        """
        특정 파일의 IMPORT 노드 조회
        
        Args:
            directory: 파일 경로 (예: "SAMPLE/OrderService.java")
        
        Returns:
            QueryResult - IMPORT 노드 목록
        """
        query = """
        MATCH (n)
        WHERE n.directory = $directory
          AND (n.name STARTS WITH 'IMPORT' OR labels(n)[0] = 'IMPORT')
        RETURN n, elementId(n) as element_id, 
               n.node_code as node_code, n.startLine as startLine
        ORDER BY n.startLine
        """
        
        return await self.query(query, {"directory": directory})

    async def find_nodes_by_directory(
        self, 
        path_pattern: str,
        labels: Optional[List[str]] = None,
        limit: int = 100
    ) -> QueryResult:
        """
        경로 패턴으로 노드 검색
        
        Args:
            path_pattern: 경로 패턴 (예: "SAMPLE/", "*.java")
            labels: 검색할 라벨 목록
            limit: 최대 결과 수
        
        Returns:
            QueryResult
        """
        label_filter = ""
        if labels:
            label_filter = ":" + ":".join(labels)
        
        # CONTAINS 또는 STARTS WITH 사용
        query = f"""
        MATCH (n{label_filter})
        WHERE n.directory CONTAINS $pattern 
           OR n.directory STARTS WITH $pattern
           OR n.file_name CONTAINS $pattern
        RETURN n, labels(n) as labels, elementId(n) as element_id,
               n.directory as directory, n.file_name as file_name,
               n.startLine as startLine, n.endLine as endLine
        ORDER BY n.directory, n.startLine
        LIMIT $limit
        """
        
        return await self.query(query, {"pattern": path_pattern, "limit": limit})

    async def get_node_children(
        self, 
        parent_element_id: str
    ) -> QueryResult:
        """
        부모 노드의 자식 노드 조회 (startLine/endLine 기반)
        
        부모의 line 범위 안에 포함된 노드들을 반환
        
        Args:
            parent_element_id: 부모 노드의 elementId
        
        Returns:
            QueryResult - 자식 노드 목록
        """
        query = """
        MATCH (parent) WHERE elementId(parent) = $parent_id
        WITH parent, parent.directory as dir, parent.startLine as pStart, parent.endLine as pEnd
        MATCH (child)
        WHERE child.directory = dir
          AND child.startLine >= pStart
          AND child.endLine <= pEnd
          AND elementId(child) <> $parent_id
        RETURN child, labels(child) as labels, elementId(child) as element_id,
               child.name as name, child.startLine as startLine, child.endLine as endLine,
               child.node_code as node_code
        ORDER BY child.startLine
        """
        
        return await self.query(query, {"parent_id": parent_element_id})

    # =========================================================================
    # DDL / 스키마 관리 도구 (Schema Management Tools)
    # =========================================================================

    async def check_table_exists(
        self, 
        table_name: str,
        db: str = "default",
        schema: str = "PUBLIC"
    ) -> bool:
        """
        Table 노드 존재 여부 확인 (DBMS 표준)
        
        Args:
            table_name: 테이블명
            db: 데이터베이스명
            schema: 스키마명
        
        Returns:
            존재 여부
        """
        # 먼저 DBMS 표준 PK로 검색
        query = """
        MATCH (t:Table {db: $db, schema: $schema, name: $table_name})
        RETURN count(t) > 0 as exists
        """
        
        result = await self.query(query, {
            "db": db,
            "schema": schema,
            "table_name": table_name.upper()
        })
        
        if result.records and result.records[0].get("exists", False):
            return True
        
        # fallback: 이름만으로 검색 (기존 데이터 호환)
        fallback_query = """
        MATCH (t:Table)
        WHERE toLower(t.name) = toLower($table_name)
        RETURN count(t) > 0 as exists
        """
        
        result = await self.query(fallback_query, {"table_name": table_name})
        return result.records[0].get("exists", False) if result.records else False

    async def enrich_table_schema(
        self,
        table_name: str,
        columns: Optional[List[Dict[str, Any]]] = None,
        fk_relationships: Optional[List[Dict[str, Any]]] = None,
        source_info: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """
        기존 테이블 정보 보강 (DBMS 표준)
        
        Args:
            table_name: 테이블명
            columns: 컬럼 정보 [{name, dtype, nullable, pk_constraint, description}, ...]
            fk_relationships: FK 관계 [{to_table, fk_column, pk_column}, ...]
            source_info: 출처 정보 {db, schema}
        
        Returns:
            처리 결과
        """
        results = {"columns_added": 0, "columns_updated": 0, "fk_added": 0}
        source_info = source_info or {}
        db = source_info.get("db", "default")
        schema = source_info.get("schema", "PUBLIC")
        
        # 이름 정규화 정책 적용 (create_table_schema와 동일하게!)
        normalized_table = normalize_name(table_name)
        
        # 컬럼 추가/업데이트 (DBMS 표준)
        if columns:
            for col in columns:
                col_name = col.get("name", "")
                if not col_name:
                    continue
                
                # 이름 정규화 적용 (create_table_schema와 동일하게!)
                normalized_col = normalize_name(col_name)
                fqn = f"{db}.{schema}.{normalized_table}.{normalized_col}"
                
                col_query = """
                MATCH (t:Table {db: $db, schema: $schema, name: $table_name})
                MERGE (c:Column {fqn: $fqn})
                ON CREATE SET 
                    c.name = $col_name,
                    c.dtype = $dtype,
                    c.description = $description,
                    c.description_source = 'runtime_resolution',
                    c.nullable = $nullable,
                    c.pk_constraint = $pk_constraint
                ON MATCH SET
                    c.dtype = CASE WHEN c.dtype IS NULL OR c.dtype = '' THEN $dtype ELSE c.dtype END,
                    c.nullable = $nullable
                MERGE (t)-[:HAS_COLUMN]->(c)
                RETURN c.description_source = 'runtime_resolution' as is_new
                """
                
                result = await self.query(col_query, {
                    "db": db,
                    "schema": schema,
                    "table_name": normalized_table,
                    "fqn": fqn,
                    "col_name": normalized_col,
                    "dtype": col.get("dtype", col.get("datatype", "UNKNOWN")),
                    "description": col.get("description", ""),
                    "nullable": col.get("nullable", True),
                    "pk_constraint": col.get("pk_constraint", None),
                })
                
                if result.records:
                    is_new = result.records[0].get("is_new", False)
                    if is_new:
                        results["columns_added"] += 1
                    else:
                        results["columns_updated"] += 1
        
        # FK 관계 추가
        if fk_relationships:
            for fk in fk_relationships:
                fk_result = await self.add_fk_relationship(
                    from_table=normalized_table,  # 정규화된 이름 사용
                    to_table=fk.get("to_table", ""),
                    fk_column=fk.get("fk_column", ""),
                    pk_column=fk.get("pk_column", ""),
                    source_info=source_info
                )
                if fk_result.get("created"):
                    results["fk_added"] += 1
        
        return results

    async def create_table_schema(
        self,
        table_name: str,
        columns: List[Dict[str, Any]],
        fk_relationships: Optional[List[Dict[str, Any]]] = None,
        source_info: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """
        DBMS 표준 스키마로 테이블 생성 (DDL 없어도 생성)
        
        Table 노드, Column 노드, FK 관계 모두 생성
        
        DBMS 표준 속성:
        - TABLE: {db, schema, name} + description, description_source, table_type
        - COLUMN: {fqn} + name, dtype, description, description_source, nullable, pk_constraint
        
        이름 정규화:
        - TABLE_NAME_POLICY env 변수 따름 (ORIGINAL | UPPER | LOWER)
        
        dtype:
        - 자바 타입 그대로 저장 (SQL 변환 없음)
        - "type" 또는 "dtype" 필드에서 가져옴
        
        Args:
            table_name: 테이블명
            columns: 컬럼 정보 [{name, dtype/type, nullable, pk_constraint, description}, ...]
            fk_relationships: FK 관계 [{to_table, fk_column, pk_column}, ...]
            source_info: 출처 정보 {db, schema, source_path, description}
        
        Returns:
            처리 결과
        """
        source_info = source_info or {}
        db = source_info.get("db", "default")
        schema = source_info.get("schema", "PUBLIC")
        description = source_info.get("description", "")
        description_source = source_info.get("source_path", source_info.get("source", "runtime_resolution"))
        
        # 이름 정규화 정책 적용
        normalized_table = normalize_name(table_name)
        
        # Table 노드 생성 (DBMS 표준 PK: db, schema, name)
        # description은 ON CREATE 시에만 설정, ON MATCH 시에는 유지 (별도 merge_description 도구로 LLM 통합)
        table_query = """
        MERGE (t:Table {db: $db, schema: $schema, name: $name})
        ON CREATE SET 
            t.description = $description,
            t.description_source = $desc_source,
            t.table_type = 'TABLE'
        ON MATCH SET 
            t.description_source = CASE
                WHEN t.description_source IS NULL THEN $desc_source
                WHEN $desc_source = '' OR t.description_source CONTAINS $desc_source THEN t.description_source
                ELSE t.description_source + '; ' + $desc_source END
        RETURN t, elementId(t) as element_id
        """
        
        await self.query(table_query, {
            "db": db,
            "schema": schema,
            "name": normalized_table,
            "description": description,
            "desc_source": description_source,
        })
        
        # Column 노드 생성 및 HAS_COLUMN 관계 연결 (DBMS 표준 PK: fqn)
        columns_created = 0
        for col in columns:
            col_name = col.get("name", "")
            if not col_name:
                continue
            
            # 이름 정규화 적용
            normalized_col = normalize_name(col_name)
            
            # fqn = db.schema.table.column
            fqn = f"{db}.{schema}.{normalized_table}.{normalized_col}"
            
            # dtype: 자바 타입 그대로 (type 또는 dtype 필드에서 가져옴, SQL 변환 없음)
            dtype = col.get("type") or col.get("dtype") or col.get("datatype") or ""
            
            col_description = col.get("description", "")
            
            # Column description도 ON CREATE 시에만 설정 (별도 merge_description 도구로 LLM 통합)
            col_query = """
            MATCH (t:Table {db: $db, schema: $schema, name: $table_name})
            MERGE (c:Column {fqn: $fqn})
            ON CREATE SET 
                c.name = $col_name,
                c.dtype = $dtype,
                c.description = $description,
                c.description_source = $desc_source,
                c.nullable = $nullable,
                c.pk_constraint = $pk_constraint
            ON MATCH SET 
                c.dtype = CASE WHEN c.dtype IS NULL OR c.dtype = '' THEN $dtype ELSE c.dtype END,
                c.description_source = CASE
                    WHEN c.description_source IS NULL THEN $desc_source
                    WHEN $desc_source = '' OR c.description_source CONTAINS $desc_source THEN c.description_source
                    ELSE c.description_source + '; ' + $desc_source END,
                c.nullable = COALESCE($nullable, c.nullable),
                c.pk_constraint = COALESCE($pk_constraint, c.pk_constraint)
            MERGE (t)-[:HAS_COLUMN]->(c)
            RETURN c
            """
            
            await self.query(col_query, {
                "db": db,
                "schema": schema,
                "table_name": normalized_table,
                "fqn": fqn,
                "col_name": normalized_col,
                "dtype": dtype,
                "description": col_description,
                "desc_source": description_source,
                "nullable": col.get("nullable"),
                "pk_constraint": col.get("pk_constraint"),
            })
            columns_created += 1
        
        # FK 관계 생성 (FK_TO_TABLE + FK_TO)
        fk_created = 0
        if fk_relationships:
            for fk in fk_relationships:
                fk_result = await self.add_fk_relationship(
                    from_table=table_name,
                    to_table=fk.get("to_table", ""),
                    fk_column=fk.get("fk_column", ""),
                    pk_column=fk.get("pk_column", ""),
                    source_info=source_info
                )
                if fk_result.get("created"):
                    fk_created += 1
        
        return {
            "table_name": normalized_table,
            "db": db,
            "schema": schema,
            "table_created": True,
            "columns_created": columns_created,
            "fk_created": fk_created,
        }

    async def add_fk_relationship(
        self,
        from_table: str,
        to_table: str,
        fk_column: str,
        pk_column: str,
        source_info: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """
        FK 관계 추가 (DBMS 표준: FK_TO_TABLE + FK_TO)
        
        Args:
            from_table: 소스 테이블명
            to_table: 타겟 테이블명
            fk_column: FK 컬럼명
            pk_column: PK 컬럼명
            source_info: 출처 정보 {db, schema}
        
        Returns:
            처리 결과
        """
        source_info = source_info or {}
        db = source_info.get("db", "default")
        schema = source_info.get("schema", "PUBLIC")
        
        # 이름 정규화 정책 적용
        from_table_norm = normalize_name(from_table)
        to_table_norm = normalize_name(to_table)
        fk_column_norm = normalize_name(fk_column)
        pk_column_norm = normalize_name(pk_column)
        
        # 타겟 테이블이 없으면 생성 (DBMS 표준 PK)
        ensure_target_query = """
        MERGE (t:Table {db: $db, schema: $schema, name: $to_table})
        ON CREATE SET 
            t.description_source = 'runtime_resolution_ref',
            t.table_type = 'TABLE'
        RETURN t
        """
        
        await self.query(ensure_target_query, {
            "db": db,
            "schema": schema,
            "to_table": to_table_norm,
        })
        
        # FK_TO_TABLE 관계 생성 (DBMS 표준: sourceColumn, targetColumn 속성)
        fk_table_query = """
        MATCH (t1:Table {db: $db, schema: $schema, name: $from_table})
        MATCH (t2:Table {db: $db, schema: $schema, name: $to_table})
        MERGE (t1)-[r:FK_TO_TABLE {sourceColumn: $fk_column, targetColumn: $pk_column}]->(t2)
        ON CREATE SET r.type = 'many_to_one', r.source = 'runtime_resolution'
        RETURN r, r.source = 'runtime_resolution' as is_new
        """
        
        result = await self.query(fk_table_query, {
            "db": db,
            "schema": schema,
            "from_table": from_table_norm,
            "to_table": to_table_norm,
            "fk_column": fk_column_norm,
            "pk_column": pk_column_norm,
        })
        
        is_new = result.records[0].get("is_new", False) if result.records else False
        
        # FK_TO 관계도 생성 (Column → Column)
        from_fqn = f"{db}.{schema}.{from_table_norm}.{fk_column_norm}"
        to_fqn = f"{db}.{schema}.{to_table_norm}.{pk_column_norm}"
        
        fk_column_query = """
        MATCH (c1:Column {fqn: $from_fqn})
        MATCH (c2:Column {fqn: $to_fqn})
        MERGE (c1)-[r:FK_TO]->(c2)
        ON CREATE SET r.source = 'runtime_resolution'
        RETURN r
        """
        
        await self.query(fk_column_query, {
            "from_fqn": from_fqn,
            "to_fqn": to_fqn,
        })
        
        return {
            "from_table": from_table_norm,
            "to_table": to_table_norm,
            "fk_column": fk_column_norm,
            "pk_column": pk_column_norm,
            "created": is_new,
        }

    # =========================================================================
    # Unique Constraints (플랜 규칙)
    # =========================================================================

    async def ensure_unique_constraints(self) -> Dict[str, bool]:
        """
        플랜 규칙: Unique Constraint 생성
        
        - TABLE(name)
        - COLUMN(table_name, name)
        
        Returns:
            {"table_constraint": bool, "column_constraint": bool}
        """
        results = {"table_constraint": False, "column_constraint": False}
        
        # TABLE(name) unique constraint
        try:
            await self.query("""
                CREATE CONSTRAINT table_name_unique IF NOT EXISTS
                FOR (t:TABLE) REQUIRE t.name IS UNIQUE
            """)
            results["table_constraint"] = True
        except Exception:
            pass
        
        # COLUMN(table_name, name) unique constraint
        try:
            await self.query("""
                CREATE CONSTRAINT column_unique IF NOT EXISTS
                FOR (c:COLUMN) REQUIRE (c.table_name, c.name) IS UNIQUE
            """)
            results["column_constraint"] = True
        except Exception:
            pass
        
        return results

    # =========================================================================
    # Status 업데이트 함수 (플랜 규칙)
    # =========================================================================

    async def set_status_success(
        self,
        element_id: str,
        evidence_path: str
    ) -> Dict[str, Any]:
        """
        성공 상태로 업데이트 (deepagents_attempts 증가 안 함)
        
        플랜 규칙:
        - deepagents_status = 'success'
        - deepagents_status_reason = null
        - deepagents_evidence_path = evidence_path
        - deepagents_last_processed_at = datetime()
        """
        query = """
        MATCH (n) WHERE elementId(n) = $element_id
        SET n.deepagents_status = 'success',
            n.deepagents_status_reason = null,
            n.deepagents_evidence_path = $evidence_path,
            n.deepagents_last_processed_at = datetime()
        RETURN n
        """
        
        result = await self.query(query, {
            "element_id": element_id,
            "evidence_path": evidence_path,
        })
        
        return {
            "element_id": element_id,
            "status": "success",
            "updated": len(result.records) > 0,
        }

    async def set_status_review(
        self,
        element_id: str,
        reason: str,
        evidence_path: str
    ) -> Dict[str, Any]:
        """
        리뷰 상태로 업데이트 (deepagents_attempts +1)
        
        플랜 규칙:
        - deepagents_status = 'review'
        - deepagents_status_reason = reason
        - deepagents_evidence_path = evidence_path
        - deepagents_last_processed_at = datetime()
        - deepagents_attempts = +1
        """
        query = """
        MATCH (n) WHERE elementId(n) = $element_id
        SET n.deepagents_status = 'review',
            n.deepagents_status_reason = $reason,
            n.deepagents_evidence_path = $evidence_path,
            n.deepagents_last_processed_at = datetime(),
            n.deepagents_attempts = coalesce(n.deepagents_attempts, 0) + 1
        RETURN n
        """
        
        result = await self.query(query, {
            "element_id": element_id,
            "reason": reason,
            "evidence_path": evidence_path,
        })
        
        return {
            "element_id": element_id,
            "status": "review",
            "reason": reason,
            "updated": len(result.records) > 0,
        }

    async def set_status_failed(
        self,
        element_id: str,
        reason: str
    ) -> Dict[str, Any]:
        """
        실패 상태로 업데이트 (deepagents_attempts +1)
        
        플랜 규칙:
        - deepagents_status = 'failed'
        - deepagents_status_reason = reason
        - deepagents_last_processed_at = datetime()
        - deepagents_attempts = +1
        """
        query = """
        MATCH (n) WHERE elementId(n) = $element_id
        SET n.deepagents_status = 'failed',
            n.deepagents_status_reason = $reason,
            n.deepagents_last_processed_at = datetime(),
            n.deepagents_attempts = coalesce(n.deepagents_attempts, 0) + 1
        RETURN n
        """
        
        result = await self.query(query, {
            "element_id": element_id,
            "reason": reason,
        })
        
        return {
            "element_id": element_id,
            "status": "failed",
            "reason": reason,
            "updated": len(result.records) > 0,
        }

    async def set_status_working(
        self,
        element_id: str,
        run_id: str
    ) -> Dict[str, Any]:
        """
        작업 중 상태로 업데이트
        """
        query = """
        MATCH (n) WHERE elementId(n) = $element_id
        SET n.deepagents_status = 'working',
            n.deepagents_run_id = $run_id,
            n.deepagents_last_processed_at = datetime()
        RETURN n
        """
        
        result = await self.query(query, {
            "element_id": element_id,
            "run_id": run_id,
        })
        
        return {
            "element_id": element_id,
            "status": "working",
            "updated": len(result.records) > 0,
        }

    # =========================================================================
    # 트랜잭션 배치 (플랜 규칙)
    # =========================================================================

    async def apply_graph_mutations(
        self,
        mutations: List[Dict[str, Any]]
    ) -> Dict[str, Any]:
        """
        트랜잭션 배치 처리 (DBMS 표준 스키마)
        
        DBMS 표준:
        - TABLE PK: {db, schema, name} (기본값: default, PUBLIC)
        - COLUMN PK: {fqn}
        - FROM/WRITES 관계: source 속성만
        - FK: FK_TO_TABLE + FK_TO
        
        Args:
            mutations: [
                {"type": "MERGE_TABLE", "table": "ORDER_HEADER", "db": "default", "schema": "PUBLIC"},
                {"type": "MERGE_FROM", "from_element_id": "...", "to_table": "ORDER_HEADER"},
                {"type": "MERGE_WRITES", "from_element_id": "...", "to_table": "ORDER_HEADER"},
            ]
        
        Returns:
            {"success": bool, "results": [...]}
        """
        results = []
        
        async with self.driver.session() as session:
            try:
                for mut in mutations:
                    mut_type = mut.get("type", "")
                    db = mut.get("db", "default")
                    schema = mut.get("schema", "PUBLIC")
                    
                    if mut_type == "MERGE_TABLE":
                        # TABLE 노드 MERGE (DBMS 표준 PK)
                        # 이름 정규화 정책 적용
                        table_name = normalize_name(mut.get("table", ""))
                        description = mut.get("description", "")
                        desc_source = mut.get("source", "runtime_resolution")
                        
                        # description은 ON CREATE 시에만 설정 (별도 merge_description 도구로 LLM 통합)
                        query = """
                        MERGE (t:Table {db: $db, schema: $schema, name: $table_name})
                        ON CREATE SET 
                            t.description = $description,
                            t.description_source = $desc_source,
                            t.table_type = 'TABLE'
                        ON MATCH SET
                            t.description_source = CASE
                                WHEN t.description_source IS NULL THEN $desc_source
                                WHEN $desc_source = '' OR t.description_source CONTAINS $desc_source THEN t.description_source
                                ELSE t.description_source + '; ' + $desc_source END
                        RETURN t
                        """
                        await session.run(query, {
                            "db": db,
                            "schema": schema,
                            "table_name": table_name,
                            "description": description,
                            "desc_source": desc_source,
                        })
                        results.append({"type": "MERGE_TABLE", "table": table_name, "success": True})
                        
                    elif mut_type == "MERGE_FROM":
                        # FROM 관계 MERGE (source 속성만)
                        from_id = mut.get("from_element_id", "")
                        # 이름 정규화 정책 적용
                        to_table = normalize_name(mut.get("to_table", ""))
                        
                        # 먼저 name으로 검색, 없으면 db/schema/name으로 검색
                        query = """
                        MATCH (n) WHERE elementId(n) = $from_id
                        MATCH (t:Table)
                        WHERE t.name = $to_table OR (t.db = $db AND t.schema = $schema AND t.name = $to_table)
                        MERGE (n)-[r:FROM]->(t)
                        ON CREATE SET r.source = 'runtime_resolution'
                        RETURN r
                        """
                        await session.run(query, {
                            "from_id": from_id, 
                            "to_table": to_table,
                            "db": db,
                            "schema": schema,
                        })
                        results.append({"type": "MERGE_FROM", "from": from_id, "to": to_table, "success": True})
                    
                    elif mut_type == "MERGE_WRITES":
                        # WRITES 관계 MERGE (source 속성만)
                        from_id = mut.get("from_element_id", "")
                        # 이름 정규화 정책 적용
                        to_table = normalize_name(mut.get("to_table", ""))
                        
                        query = """
                        MATCH (n) WHERE elementId(n) = $from_id
                        MATCH (t:Table)
                        WHERE t.name = $to_table OR (t.db = $db AND t.schema = $schema AND t.name = $to_table)
                        MERGE (n)-[r:WRITES]->(t)
                        ON CREATE SET r.source = 'runtime_resolution'
                        RETURN r
                        """
                        await session.run(query, {
                            "from_id": from_id, 
                            "to_table": to_table,
                            "db": db,
                            "schema": schema,
                        })
                        results.append({"type": "MERGE_WRITES", "from": from_id, "to": to_table, "success": True})
                        
                    elif mut_type == "MERGE_HAS_COLUMN":
                        # HAS_COLUMN 관계 MERGE (DBMS 표준: fqn)
                        table_name = mut.get("table_name", "").upper()
                        column_name = mut.get("column_name", "").upper()
                        dtype = mut.get("dtype", mut.get("data_type", "UNKNOWN"))
                        nullable = mut.get("nullable", True)
                        pk_constraint = mut.get("pk_constraint", None)
                        description = mut.get("description", "")
                        
                        fqn = f"{db}.{schema}.{table_name}.{column_name}"
                        
                        query = """
                        MATCH (t:Table {db: $db, schema: $schema, name: $table_name})
                        MERGE (c:Column {fqn: $fqn})
                        SET c.name = $column_name,
                            c.dtype = $dtype,
                            c.description = $description,
                            c.description_source = 'runtime_resolution',
                            c.nullable = $nullable,
                            c.pk_constraint = $pk_constraint
                        MERGE (t)-[:HAS_COLUMN]->(c)
                        RETURN c
                        """
                        await session.run(query, {
                            "db": db,
                            "schema": schema,
                            "table_name": table_name,
                            "fqn": fqn,
                            "column_name": column_name,
                            "dtype": dtype,
                            "description": description,
                            "nullable": nullable,
                            "pk_constraint": pk_constraint,
                        })
                        results.append({"type": "MERGE_HAS_COLUMN", "table": table_name, "column": column_name, "success": True})
                        
                    elif mut_type == "MERGE_FK_TO":
                        # FK_TO 관계 MERGE (Column → Column)
                        from_fqn = mut.get("from_fqn", "")
                        to_fqn = mut.get("to_fqn", "")
                        
                        query = """
                        MATCH (fc:Column {fqn: $from_fqn})
                        MATCH (tc:Column {fqn: $to_fqn})
                        MERGE (fc)-[r:FK_TO]->(tc)
                        ON CREATE SET r.source = 'runtime_resolution'
                        RETURN r
                        """
                        await session.run(query, {
                            "from_fqn": from_fqn,
                            "to_fqn": to_fqn,
                        })
                        results.append({"type": "MERGE_FK_TO", "from": from_fqn, "to": to_fqn, "success": True})
                    
                    elif mut_type == "MERGE_FK_TO_TABLE":
                        # FK_TO_TABLE 관계 MERGE (Table → Table)
                        from_table = mut.get("from_table", "").upper()
                        to_table = mut.get("to_table", "").upper()
                        source_column = mut.get("source_column", "").upper()
                        target_column = mut.get("target_column", "").upper()
                        
                        query = """
                        MATCH (ft:Table {db: $db, schema: $schema, name: $from_table})
                        MATCH (tt:Table {db: $db, schema: $schema, name: $to_table})
                        MERGE (ft)-[r:FK_TO_TABLE {sourceColumn: $source_column, targetColumn: $target_column}]->(tt)
                        ON CREATE SET r.type = 'many_to_one', r.source = 'runtime_resolution'
                        RETURN r
                        """
                        await session.run(query, {
                            "db": db,
                            "schema": schema,
                            "from_table": from_table,
                            "to_table": to_table,
                            "source_column": source_column,
                            "target_column": target_column,
                        })
                        results.append({"type": "MERGE_FK_TO_TABLE", "from": from_table, "to": to_table, "success": True})
                    
                    elif mut_type == "UPDATE_SUMMARY":
                        # 노드 summary 업데이트
                        element_id = mut.get("element_id", "")
                        summary = mut.get("summary", "")
                        
                        query = """
                        MATCH (n) WHERE elementId(n) = $element_id
                        SET n.summary = $summary
                        RETURN n
                        """
                        await session.run(query, {"element_id": element_id, "summary": summary})
                        results.append({"type": "UPDATE_SUMMARY", "element_id": element_id, "success": True})
                    
                    elif mut_type == "UPDATE_TABLE_DESCRIPTION":
                        # 테이블 description 업데이트
                        table_name = mut.get("table", "").upper()
                        description = mut.get("description", "")
                        
                        query = """
                        MATCH (t:Table)
                        WHERE t.name = $table_name OR (t.db = $db AND t.schema = $schema AND t.name = $to_table)
                        SET t.description = $description
                        RETURN t
                        """
                        await session.run(query, {
                            "db": db,
                            "schema": schema,
                            "table_name": table_name,
                            "description": description,
                        })
                        results.append({"type": "UPDATE_TABLE_DESCRIPTION", "table": table_name, "success": True})
                
                return {"success": True, "results": results}
                
            except Exception as e:
                return {"success": False, "error": str(e), "results": results}

    # =========================================================================
    # 후보 수집 쿼리 (플랜 규칙)
    # =========================================================================

    async def get_resolution_candidates(
        self,
        limit: int = 500
    ) -> QueryResult:
        """
        후보 수집 쿼리 (플랜 규칙)
        
        - resolution_candidate = true
        - deepagents_status IS NULL OR IN ['working', 'failed']
        - review 24h 미경과면 제외
        - 이미 from/writes 있으면 제외
        
        ORDER BY:
        - directory, file_name, startLine ASC (청크 기반 정렬)
        """
        query = """
        MATCH (n)
        WHERE n.resolution_candidate = true
          AND (n.deepagents_status IS NULL OR n.deepagents_status IN ['working', 'failed'])
          AND NOT (n.deepagents_status = 'review' AND n.deepagents_last_processed_at > datetime() - duration('P1D'))
          AND NOT EXISTS { (n)-[:FROM|WRITES]->(:TABLE) }
        RETURN elementId(n) AS element_id,
               labels(n)[0] AS node_type,
               n.directory AS directory,
               n.file_name AS file_name,
               n.directory + '/' + n.file_name AS file_path,
               n.startLine AS start_line,
               n.endLine AS end_line,
               n.external_hints AS external_hints,
               n.missing_bindings AS missing_bindings,
               coalesce(n.confidence, 0.5) AS confidence,
               coalesce(n.token, 0) AS token
        ORDER BY n.directory ASC,
                 n.file_name ASC,
                 n.startLine ASC
        LIMIT $limit
        """
        
        return await self.query(query, {"limit": limit})

    # =========================================================================
    # 청크 단위 FROM/WRITES 연결 (단순화)
    # =========================================================================

    async def link_candidates_to_tables(
        self,
        candidate_element_ids: List[str],
        table_names: List[str],
        operation: str = "read",
        db: str = "default",
        schema: str = "PUBLIC"
    ) -> Dict[str, Any]:
        """
        청크 내 후보노드들 → 테이블들 일괄 MERGE 연결
        
        핵심 원칙:
        - 청크 내 후보들과 식별된 테이블들을 단순 연결
        - MERGE라서 중복 걱정 없음
        - 하나의 노드에서 여러 FROM 가능 (JOIN, 서브쿼리 등)
        
        Args:
            candidate_element_ids: 청크 내 후보노드 element_id 목록
            table_names: 식별된 테이블명 목록
            operation: "read" → FROM, "write" → WRITES
            db: 데이터베이스 (기본: default)
            schema: 스키마 (기본: PUBLIC)
        
        Returns:
            {"success": bool, "from_count": int, "writes_count": int}
        """
        if not candidate_element_ids or not table_names:
            return {"success": True, "from_count": 0, "writes_count": 0}
        
        from_count = 0
        writes_count = 0
        
        # 관계 타입 결정
        rel_type = "WRITES" if operation.lower() in ("write", "insert", "update", "delete") else "FROM"
        
        # 이름 정규화 적용
        normalized_tables = [normalize_name(t) for t in table_names]
        
        async with self.driver.session() as session:
            try:
                for eid in candidate_element_ids:
                    for table_name in normalized_tables:
                        if rel_type == "FROM":
                            query = """
                            MATCH (n) WHERE elementId(n) = $eid
                            MATCH (t:Table)
                            WHERE t.name = $table_name OR (t.db = $db AND t.schema = $schema AND t.name = $table_name)
                            MERGE (n)-[r:FROM]->(t)
                            ON CREATE SET r.source = 'runtime_resolution'
                            RETURN r
                            """
                            result = await session.run(query, {
                                "eid": eid,
                                "table_name": table_name,
                                "db": db,
                                "schema": schema,
                            })
                            if await result.single():
                                from_count += 1
                        else:
                            query = """
                            MATCH (n) WHERE elementId(n) = $eid
                            MATCH (t:Table)
                            WHERE t.name = $table_name OR (t.db = $db AND t.schema = $schema AND t.name = $table_name)
                            MERGE (n)-[r:WRITES]->(t)
                            ON CREATE SET r.source = 'runtime_resolution'
                            RETURN r
                            """
                            result = await session.run(query, {
                                "eid": eid,
                                "table_name": table_name,
                                "db": db,
                                "schema": schema,
                            })
                            if await result.single():
                                writes_count += 1
                
                return {
                    "success": True,
                    "from_count": from_count,
                    "writes_count": writes_count,
                }
            except Exception as e:
                return {
                    "success": False,
                    "error": str(e),
                    "from_count": from_count,
                    "writes_count": writes_count,
                }

    # =========================================================================
    # Description 업데이트 (LLM 통합 후 덮어쓰기)
    # =========================================================================

    async def update_table_description(
        self,
        table_name: str,
        description: str,
        description_source: str,
        db: str = "default",
        schema: str = "PUBLIC"
    ) -> Dict[str, Any]:
        """
        테이블 description 덮어쓰기 (LLM 통합 후 호출)
        
        Args:
            table_name: 테이블명
            description: LLM으로 통합된 description
            description_source: 출처 정보
            db, schema: DBMS 정보
        
        Returns:
            {"success": bool, "updated": bool}
        """
        normalized_table = normalize_name(table_name)
        
        query = """
        MATCH (t:Table {db: $db, schema: $schema, name: $table_name})
        SET t.description = $description,
            t.description_source = $desc_source
        RETURN t
        """
        
        result = await self.query(query, {
            "db": db,
            "schema": schema,
            "table_name": normalized_table,
            "description": description,
            "desc_source": description_source,
        })
        
        return {
            "success": True,
            "table_name": normalized_table,
            "updated": len(result.records) > 0,
        }

    async def update_column_description(
        self,
        table_name: str,
        column_name: str,
        description: str,
        description_source: str,
        db: str = "default",
        schema: str = "PUBLIC"
    ) -> Dict[str, Any]:
        """
        컬럼 description 덮어쓰기 (LLM 통합 후 호출)
        
        Args:
            table_name: 테이블명
            column_name: 컬럼명
            description: LLM으로 통합된 description
            description_source: 출처 정보
            db, schema: DBMS 정보
        
        Returns:
            {"success": bool, "updated": bool}
        """
        normalized_table = normalize_name(table_name)
        normalized_col = normalize_name(column_name)
        fqn = f"{db}.{schema}.{normalized_table}.{normalized_col}"
        
        query = """
        MATCH (c:Column {fqn: $fqn})
        SET c.description = $description,
            c.description_source = $desc_source
        RETURN c
        """
        
        result = await self.query(query, {
            "fqn": fqn,
            "description": description,
            "desc_source": description_source,
        })
        
        return {
            "success": True,
            "fqn": fqn,
            "updated": len(result.records) > 0,
        }

    async def get_table_description(
        self,
        table_name: str,
        db: str = "default",
        schema: str = "PUBLIC"
    ) -> Optional[str]:
        """
        테이블 기존 description 조회 (LLM 통합용)
        
        Args:
            table_name: 테이블명
            db, schema: DBMS 정보
        
        Returns:
            기존 description 또는 None
        """
        normalized_table = normalize_name(table_name)
        
        query = """
        MATCH (t:Table {db: $db, schema: $schema, name: $table_name})
        RETURN t.description AS description
        """
        
        result = await self.query(query, {
            "db": db,
            "schema": schema,
            "table_name": normalized_table,
        })
        
        if result.records:
            return result.records[0].get("description")
        return None

    async def get_column_description(
        self,
        table_name: str,
        column_name: str,
        db: str = "default",
        schema: str = "PUBLIC"
    ) -> Optional[str]:
        """
        컬럼 기존 description 조회 (LLM 통합용)
        
        Args:
            table_name: 테이블명
            column_name: 컬럼명
            db, schema: DBMS 정보
        
        Returns:
            기존 description 또는 None
        """
        normalized_table = normalize_name(table_name)
        normalized_col = normalize_name(column_name)
        fqn = f"{db}.{schema}.{normalized_table}.{normalized_col}"
        
        query = """
        MATCH (c:Column {fqn: $fqn})
        RETURN c.description AS description
        """
        
        result = await self.query(query, {"fqn": fqn})
        
        if result.records:
            return result.records[0].get("description")
        return None

