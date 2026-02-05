"""테이블/컬럼 자동 생성/보강

비타겟 파일 분석에서 식별된 테이블 정보를 Neo4j에 저장합니다.

두 가지 시나리오:
1. 기존 테이블 존재 (DDL 기반): description, columns 보강
2. 테이블 없음: Table, Column 노드 신규 생성

모든 처리된 테이블/컬럼은 REFERS_TO로 비타겟 블록과 연결됩니다.
"""

import asyncio
import json
from typing import Any, Dict, List, Optional, Tuple

from langchain_core.messages import HumanMessage

from analyzer.strategy.framework.nontarget.nontarget_logger import log_nontarget
from analyzer.strategy.framework.nontarget.trace_logger import TraceLogger


class TableProcessor:
    """테이블/컬럼 자동 생성/보강 프로세서"""
    
    # 기본 스키마/DB 값
    DEFAULT_SCHEMA = "public"
    DEFAULT_DB = "default"
    
    def __init__(
        self,
        client: Any,
        cypher_lock: Optional[asyncio.Lock] = None,
        trace_logger: Optional[TraceLogger] = None,
    ):
        """
        Args:
            client: Neo4j 클라이언트
            cypher_lock: Cypher 쿼리 락
            trace_logger: 트레이스 로거 (선택)
        """
        self.client = client
        self.cypher_lock = cypher_lock
        self.trace_logger = trace_logger
    
    async def process_tables_from_summary(
        self,
        summary: Dict[str, Any],
        block_id: str,
    ) -> Dict[str, Any]:
        """Summary에서 테이블 정보 추출 및 처리
        
        Args:
            summary: summarizer_agent 출력 (tables 필드 포함)
            block_id: 비타겟 블록 elementId
            
        Returns:
            처리 결과 {created: [], updated: [], refers_to: []}
        """
        tables = summary.get("tables", [])
        if not tables:
            return {"created": [], "updated": [], "refers_to": []}
        
        if self.trace_logger:
            self.trace_logger.log_table_identified(tables, block_id)
        
        result = {
            "created": [],
            "updated": [],
            "refers_to": [],
        }
        
        for table_data in tables:
            table_name = table_data.get("name", "").strip()
            if not table_name:
                continue
            
            # schema는 항상 "public"으로 고정 (DDL 파서와 일치)
            schema = self.DEFAULT_SCHEMA
            entity_name = table_data.get("entity_name", "").strip()
            
            # 테이블 존재 여부 확인 (name 또는 entity_name으로 교차 검색)
            existing_table = await self._find_existing_table(table_name, schema, entity_name)
            
            if existing_table:
                # 시나리오 1: 기존 테이블 보강
                changes = await self._update_existing_table(
                    table_id=existing_table["id"],
                    table_data=table_data,
                    block_id=block_id,
                )
                result["updated"].append({
                    "table_name": table_name,
                    "table_id": existing_table["id"],
                    "changes": changes,
                })
                
                if self.trace_logger:
                    self.trace_logger.log_table_updated(table_name, changes, block_id)
            else:
                # 시나리오 2: 신규 테이블 생성
                table_id = await self._create_new_table(table_data, block_id)
                result["created"].append({
                    "table_name": table_name,
                    "table_id": table_id,
                })
                
                if self.trace_logger:
                    self.trace_logger.log_table_created(
                        table_name,
                        schema,
                        table_data.get("columns", []),
                        block_id,
                    )
            
            # REFERS_TO 관계 생성 (Table → NONTARGET_BLOCK)
            table_id = existing_table["id"] if existing_table else table_id
            access_mode = table_data.get("access_mode", "r")
            role = self._access_mode_to_role(access_mode)
            
            refers_to = await self._create_refers_to(
                source_id=table_id,
                source_type="Table",
                target_id=block_id,
                target_type="NONTARGET_BLOCK",
                role=role,
                evidence=f"비타겟 파일에서 {table_name} 테이블 {role}",
            )
            if refers_to:
                result["refers_to"].append(refers_to)
        
        # FK 관계 처리
        relations = summary.get("relations", [])
        fk_result = await self.create_fk_relations(relations, block_id)
        result["fk_relations"] = fk_result
        
        log_nontarget("TABLE", f"테이블 처리 완료: 생성 {len(result['created'])}, "
                     f"보강 {len(result['updated'])}, REFERS_TO {len(result['refers_to'])}, "
                     f"FK {len(fk_result.get('created', []))}")
        
        return result
    
    async def _find_existing_table(
        self,
        table_name: str,
        schema: str = "",
        entity_name: str = "",
    ) -> Optional[Dict[str, Any]]:
        """기존 테이블 노드 조회 (name 또는 entity_name 교차 검색)
        
        스키마 조건 없이 name/entity_name 중 하나라도 일치하면 찾음.
        - 입력 name이 기존 테이블의 name 또는 entity_name과 일치
        - 입력 entity_name이 기존 테이블의 name 또는 entity_name과 일치
        
        Returns:
            테이블 정보 {id, name, schema, description, entity_name} 또는 None
        """
        # 스키마 조건 제거 - name/entity_name 교차 검색만 수행
        query = """
            MATCH (t:Table)
            WHERE toLower(t.name) = toLower($name)
               OR toLower(t.name) = toLower($entity_name)
               OR toLower(coalesce(t.entity_name, '')) = toLower($name)
               OR ($entity_name <> '' AND toLower(coalesce(t.entity_name, '')) = toLower($entity_name))
            RETURN elementId(t) AS id,
                   t.name AS name,
                   t.schema AS schema,
                   t.description AS description,
                   t.entity_name AS entity_name,
                   t.db AS db,
                   t.mapping_type AS mapping_type
            LIMIT 1
        """
        params = {"name": table_name, "entity_name": entity_name or ""}
        
        try:
            if self.cypher_lock:
                async with self.cypher_lock:
                    result = await self.client.execute_with_params(query, params)
            else:
                result = await self.client.execute_with_params(query, params)
            
            if result and len(result) > 0:
                record = result[0]
                if hasattr(record, "data"):
                    return record.data()
                elif isinstance(record, dict):
                    return record
            return None
            
        except Exception as e:
            log_nontarget("TABLE", f"테이블 조회 실패: {e}")
            return None
    
    async def _update_existing_table(
        self,
        table_id: str,
        table_data: Dict[str, Any],
        block_id: str,
    ) -> Dict[str, Any]:
        """기존 테이블 보강 (description, columns, mapping_type)
        
        Returns:
            변경 내용 {description_updated, columns_added, columns_updated, mapping_type_updated}
        """
        changes = {
            "description_updated": False,
            "columns_added": [],
            "columns_updated": [],
            "mapping_type_updated": False,
        }
        
        new_description = table_data.get("description", "").strip()
        new_entity_name = table_data.get("entity_name", "").strip()
        # ofbiz, mybatis 등은 mapping_type으로 저장
        new_mapping_type = table_data.get("mapping_type", table_data.get("schema", "")).strip()
        if new_mapping_type.lower() == "public":
            new_mapping_type = ""
        columns = table_data.get("columns", [])
        
        # 1. 테이블 description, entity_name, mapping_type 업데이트
        if new_description or new_entity_name or new_mapping_type:
            update_query = """
                MATCH (t:Table) WHERE elementId(t) = $table_id
                SET t.description = CASE
                        WHEN $description <> '' AND (t.description IS NULL OR t.description = '') THEN $description
                        WHEN $description <> '' THEN t.description + ' | ' + $description
                        ELSE t.description
                    END,
                    t.description_source = CASE
                        WHEN t.description_source IS NULL THEN 'nontarget'
                        WHEN NOT t.description_source CONTAINS 'nontarget' THEN t.description_source + ',nontarget'
                        ELSE t.description_source
                    END,
                    t.entity_name = CASE
                        WHEN $entity_name <> '' AND (t.entity_name IS NULL OR t.entity_name = '') THEN $entity_name
                        ELSE t.entity_name
                    END,
                    t.mapping_type = CASE
                        WHEN $mapping_type <> '' AND (t.mapping_type IS NULL OR t.mapping_type = '') THEN $mapping_type
                        ELSE t.mapping_type
                    END
                RETURN t.description AS description
            """
            params = {"table_id": table_id, "description": new_description, "entity_name": new_entity_name, "mapping_type": new_mapping_type}
            
            try:
                if self.cypher_lock:
                    async with self.cypher_lock:
                        await self.client.execute_with_params(update_query, params)
                else:
                    await self.client.execute_with_params(update_query, params)
                if new_description:
                    changes["description_updated"] = True
                if new_entity_name:
                    changes["entity_name_updated"] = True
                if new_mapping_type:
                    changes["mapping_type_updated"] = True
            except Exception as e:
                log_nontarget("TABLE", f"테이블 업데이트 실패: {e}")
        
        # 2. 컬럼 처리
        for col in columns:
            col_name = col.get("name", "").strip()
            if not col_name:
                continue
            
            # 컬럼 존재 여부 확인
            existing_col = await self._find_existing_column(table_id, col_name)
            
            if existing_col:
                # 컬럼 보강
                updated = await self._update_column(existing_col["id"], col)
                if updated:
                    changes["columns_updated"].append(col_name)
            else:
                # 컬럼 생성
                col_id = await self._create_column(table_id, col, table_data.get("name", ""))
                if col_id:
                    changes["columns_added"].append(col_name)
                    
                    # Column → NONTARGET_BLOCK REFERS_TO 생성
                    await self._create_refers_to(
                        source_id=col_id,
                        source_type="Column",
                        target_id=block_id,
                        target_type="NONTARGET_BLOCK",
                        role="identified_in",
                        evidence=f"비타겟 파일에서 {col_name} 컬럼 식별",
                    )
        
        return changes
    
    async def _create_new_table(
        self,
        table_data: Dict[str, Any],
        block_id: str,
    ) -> Optional[str]:
        """신규 테이블 및 컬럼 생성
        
        Returns:
            생성된 테이블 elementId
        """
        table_name = table_data.get("name", "").strip()
        # schema는 항상 "public" (DDL 파서와 일치)
        schema = self.DEFAULT_SCHEMA
        db = table_data.get("db", self.DEFAULT_DB).strip()
        description = table_data.get("description", "").strip()
        columns = table_data.get("columns", [])
        
        entity_name = table_data.get("entity_name", "").strip()
        # ofbiz, mybatis 등은 mapping_type으로 저장 (schema가 아님)
        mapping_type = table_data.get("mapping_type", table_data.get("schema", "")).strip()
        # mapping_type이 "public"이면 빈 문자열로 처리 (실제 매핑 방식 아님)
        if mapping_type.lower() == "public":
            mapping_type = ""
        
        # 테이블 노드 생성 (schema 고정, mapping_type 추가)
        create_table_query = """
            MERGE (t:Table {name: $name, schema: $schema, db: $db})
            SET t.description = $description,
                t.description_source = 'nontarget',
                t.table_type = 'table',
                t.entity_name = CASE WHEN $entity_name <> '' THEN $entity_name ELSE t.entity_name END,
                t.mapping_type = CASE WHEN $mapping_type <> '' THEN $mapping_type ELSE t.mapping_type END
            RETURN elementId(t) AS table_id
        """
        params = {
            "name": table_name,
            "schema": schema,
            "db": db,
            "description": description,
            "entity_name": entity_name,
            "mapping_type": mapping_type,
        }
        
        try:
            if self.cypher_lock:
                async with self.cypher_lock:
                    result = await self.client.execute_with_params(create_table_query, params)
            else:
                result = await self.client.execute_with_params(create_table_query, params)
            
            if not result or len(result) == 0:
                log_nontarget("TABLE", f"테이블 생성 실패: {table_name}")
                return None
            
            record = result[0]
            if hasattr(record, "data"):
                table_id = record.data().get("table_id")
            elif isinstance(record, dict):
                table_id = record.get("table_id")
            else:
                table_id = None
            
            if not table_id:
                return None
            
            # 컬럼 생성
            for col in columns:
                col_id = await self._create_column(table_id, col, table_name)
                if col_id:
                    # Column → NONTARGET_BLOCK REFERS_TO 생성
                    await self._create_refers_to(
                        source_id=col_id,
                        source_type="Column",
                        target_id=block_id,
                        target_type="NONTARGET_BLOCK",
                        role="identified_in",
                        evidence=f"비타겟 파일에서 {col.get('name', '')} 컬럼 식별",
                    )
            
            log_nontarget("TABLE", f"테이블 생성 완료: {table_name} ({len(columns)}개 컬럼)")
            return table_id
            
        except Exception as e:
            log_nontarget("TABLE", f"테이블 생성 실패: {e}")
            return None
    
    async def _find_existing_column(
        self,
        table_id: str,
        column_name: str,
    ) -> Optional[Dict[str, Any]]:
        """기존 컬럼 노드 조회"""
        query = """
            MATCH (t:Table)-[:HAS_COLUMN]->(c:Column)
            WHERE elementId(t) = $table_id
              AND toLower(c.name) = toLower($column_name)
            RETURN elementId(c) AS id,
                   c.name AS name,
                   c.dtype AS dtype,
                   c.description AS description
            LIMIT 1
        """
        params = {"table_id": table_id, "column_name": column_name}
        
        try:
            if self.cypher_lock:
                async with self.cypher_lock:
                    result = await self.client.execute_with_params(query, params)
            else:
                result = await self.client.execute_with_params(query, params)
            
            if result and len(result) > 0:
                record = result[0]
                if hasattr(record, "data"):
                    return record.data()
                elif isinstance(record, dict):
                    return record
            return None
            
        except Exception as e:
            log_nontarget("TABLE", f"컬럼 조회 실패: {e}")
            return None
    
    async def _update_column(
        self,
        column_id: str,
        column_data: Dict[str, Any],
    ) -> bool:
        """컬럼 보강 (description, dtype)"""
        description = column_data.get("description", "").strip()
        dtype = column_data.get("dtype", "").strip()
        
        if not description and not dtype:
            return False
        
        # description만 업데이트 (dtype은 DDL이 우선)
        if description:
            update_query = """
                MATCH (c:Column) WHERE elementId(c) = $column_id
                SET c.description = CASE
                        WHEN c.description IS NULL OR c.description = '' THEN $description
                        ELSE c.description
                    END,
                    c.description_source = CASE
                        WHEN c.description_source IS NULL THEN 'nontarget'
                        WHEN NOT c.description_source CONTAINS 'nontarget' THEN c.description_source + ',nontarget'
                        ELSE c.description_source
                    END
                RETURN c
            """
            params = {"column_id": column_id, "description": description}
            
            try:
                if self.cypher_lock:
                    async with self.cypher_lock:
                        await self.client.execute_with_params(update_query, params)
                else:
                    await self.client.execute_with_params(update_query, params)
                return True
            except Exception as e:
                log_nontarget("TABLE", f"컬럼 업데이트 실패: {e}")
                return False
        
        return False
    
    async def _create_column(
        self,
        table_id: str,
        column_data: Dict[str, Any],
        table_name: str,
    ) -> Optional[str]:
        """컬럼 노드 생성 및 HAS_COLUMN 관계 생성"""
        col_name = column_data.get("name", "").strip()
        if not col_name:
            return None
        
        dtype = column_data.get("dtype", "VARCHAR").strip()
        description = column_data.get("description", "").strip()
        nullable = column_data.get("nullable", True)
        
        # FQN 생성 (DDL 처리와 동일 형식)
        fqn = f"{table_name}.{col_name}".lower()
        
        create_query = """
            MATCH (t:Table) WHERE elementId(t) = $table_id
            MERGE (c:Column {fqn: $fqn})
            SET c.name = $name,
                c.dtype = $dtype,
                c.description = $description,
                c.description_source = 'nontarget',
                c.nullable = $nullable
            MERGE (t)-[:HAS_COLUMN]->(c)
            RETURN elementId(c) AS column_id
        """
        params = {
            "table_id": table_id,
            "fqn": fqn,
            "name": col_name,
            "dtype": dtype,
            "description": description,
            "nullable": nullable,
        }
        
        try:
            if self.cypher_lock:
                async with self.cypher_lock:
                    result = await self.client.execute_with_params(create_query, params)
            else:
                result = await self.client.execute_with_params(create_query, params)
            
            if result and len(result) > 0:
                record = result[0]
                if hasattr(record, "data"):
                    return record.data().get("column_id")
                elif isinstance(record, dict):
                    return record.get("column_id")
            return None
            
        except Exception as e:
            log_nontarget("TABLE", f"컬럼 생성 실패: {e}")
            return None
    
    async def _create_refers_to(
        self,
        source_id: str,
        source_type: str,
        target_id: str,
        target_type: str,
        role: str,
        evidence: str,
        confidence: float = 0.9,
    ) -> Optional[Dict[str, Any]]:
        """REFERS_TO 관계 생성"""
        query = """
            MATCH (s) WHERE elementId(s) = $source_id
            MATCH (t) WHERE elementId(t) = $target_id
            MERGE (s)-[r:REFERS_TO]->(t)
            SET r.role = $role,
                r.confidence = $confidence,
                r.evidence = $evidence,
                r.source = 'nontarget_analysis'
            RETURN elementId(r) AS rel_id
        """
        params = {
            "source_id": source_id,
            "target_id": target_id,
            "role": role,
            "confidence": confidence,
            "evidence": evidence,
        }
        
        try:
            if self.cypher_lock:
                async with self.cypher_lock:
                    result = await self.client.execute_with_params(query, params)
            else:
                result = await self.client.execute_with_params(query, params)
            
            if result and len(result) > 0:
                if self.trace_logger:
                    self.trace_logger.log_refers_to_created(
                        source_id=source_id,
                        source_type=source_type,
                        target_id=target_id,
                        target_type=target_type,
                        role=role,
                        confidence=confidence,
                        evidence=evidence,
                    )
                return {
                    "source_id": source_id,
                    "source_type": source_type,
                    "target_id": target_id,
                    "target_type": target_type,
                    "role": role,
                }
            return None
            
        except Exception as e:
            log_nontarget("TABLE", f"REFERS_TO 생성 실패: {e}")
            return None
    
    def _access_mode_to_role(self, access_mode: str) -> str:
        """access_mode를 role로 변환
        
        Args:
            access_mode: "r", "w", "rw" 등
            
        Returns:
            role: "queries", "inserts", "updates", "defines" 등
        """
        access_mode = (access_mode or "r").lower()
        
        if "w" in access_mode and "r" in access_mode:
            return "queries_and_writes"
        elif "w" in access_mode:
            return "writes"
        elif "r" in access_mode:
            return "queries"
        else:
            return "references"
    
    # ========================================================================
    # FK 관계 생성
    # ========================================================================
    
    async def create_fk_relations(
        self,
        relations: List[Dict[str, Any]],
        block_id: str,
    ) -> Dict[str, Any]:
        """FK 관계 생성 (FK_TO_TABLE, FK_TO)
        
        Args:
            relations: summarizer_agent에서 추출한 FK 관계 목록
            block_id: 비타겟 블록 elementId
            
        Returns:
            생성 결과 {created: [], skipped: []}
        """
        result = {
            "created": [],
            "skipped": [],
        }
        
        if not relations:
            return result
        
        for rel in relations:
            from_table = rel.get("from_table", "").strip()
            from_column = rel.get("from_column", "").strip()
            to_table = rel.get("to_table", "").strip()
            to_column = rel.get("to_column", "").strip()
            rel_type = rel.get("type", "many_to_one")
            fk_name = rel.get("fk_name", "")
            
            if not from_table or not to_table or not from_column or not to_column:
                result["skipped"].append({"reason": "필수 필드 누락", "relation": rel})
                continue
            
            # 1. FK_TO_TABLE 관계 생성 (Table 간)
            fk_table_result = await self._create_fk_to_table(
                from_table=from_table,
                to_table=to_table,
                from_column=from_column,
                to_column=to_column,
                rel_type=rel_type,
                fk_name=fk_name,
            )
            
            # 2. FK_TO 관계 생성 (Column 간)
            fk_column_result = await self._create_fk_to_column(
                from_table=from_table,
                from_column=from_column,
                to_table=to_table,
                to_column=to_column,
            )
            
            if fk_table_result or fk_column_result:
                result["created"].append({
                    "from": f"{from_table}.{from_column}",
                    "to": f"{to_table}.{to_column}",
                    "type": rel_type,
                    "fk_to_table": bool(fk_table_result),
                    "fk_to_column": bool(fk_column_result),
                })
                
                if self.trace_logger:
                    self.trace_logger.log_refers_to_created(
                        source_id=f"{from_table}.{from_column}",
                        source_type="FK",
                        target_id=f"{to_table}.{to_column}",
                        target_type="FK",
                        role=rel_type,
                        confidence=0.9,
                        evidence=f"비타겟 분석에서 FK 관계 발견: {fk_name or 'unnamed'}",
                    )
            else:
                result["skipped"].append({
                    "reason": "테이블 또는 컬럼 노드 없음",
                    "relation": rel,
                })
        
        log_nontarget("FK", f"FK 관계 생성: {len(result['created'])}개, 스킵: {len(result['skipped'])}개")
        return result
    
    async def _create_fk_to_table(
        self,
        from_table: str,
        to_table: str,
        from_column: str,
        to_column: str,
        rel_type: str = "many_to_one",
        fk_name: str = "",
    ) -> Optional[str]:
        """FK_TO_TABLE 관계 생성 (Table 간)
        
        Returns:
            생성된 관계 ID 또는 None
        """
        query = """
            MATCH (st:Table)
            WHERE toLower(st.name) = toLower($from_table)
               OR toLower(coalesce(st.entity_name, '')) = toLower($from_table)
            MATCH (tt:Table)
            WHERE toLower(tt.name) = toLower($to_table)
               OR toLower(coalesce(tt.entity_name, '')) = toLower($to_table)
            MERGE (st)-[r:FK_TO_TABLE {sourceColumn: $from_column, targetColumn: $to_column}]->(tt)
            ON CREATE SET r.type = $rel_type, r.source = 'nontarget', r.fk_name = $fk_name
            ON MATCH SET r.source = CASE
                WHEN r.source IS NULL THEN 'nontarget'
                WHEN NOT r.source CONTAINS 'nontarget' THEN r.source + ',nontarget'
                ELSE r.source
            END
            RETURN elementId(r) AS rel_id
        """
        params = {
            "from_table": from_table,
            "to_table": to_table,
            "from_column": from_column,
            "to_column": to_column,
            "rel_type": rel_type,
            "fk_name": fk_name,
        }
        
        try:
            if self.cypher_lock:
                async with self.cypher_lock:
                    result = await self.client.execute_with_params(query, params)
            else:
                result = await self.client.execute_with_params(query, params)
            
            if result and len(result) > 0:
                return result[0].get("rel_id")
            return None
        except Exception as e:
            log_nontarget("FK", f"FK_TO_TABLE 생성 실패: {from_table} → {to_table}: {e}")
            return None
    
    async def _create_fk_to_column(
        self,
        from_table: str,
        from_column: str,
        to_table: str,
        to_column: str,
    ) -> Optional[str]:
        """FK_TO 관계 생성 (Column 간)
        
        Returns:
            생성된 관계 ID 또는 None
        """
        query = """
            MATCH (st:Table)-[:HAS_COLUMN]->(sc:Column)
            WHERE (toLower(st.name) = toLower($from_table)
                   OR toLower(coalesce(st.entity_name, '')) = toLower($from_table))
              AND toLower(sc.name) = toLower($from_column)
            MATCH (tt:Table)-[:HAS_COLUMN]->(tc:Column)
            WHERE (toLower(tt.name) = toLower($to_table)
                   OR toLower(coalesce(tt.entity_name, '')) = toLower($to_table))
              AND toLower(tc.name) = toLower($to_column)
            MERGE (sc)-[r:FK_TO]->(tc)
            ON CREATE SET r.source = 'nontarget'
            ON MATCH SET r.source = CASE
                WHEN r.source IS NULL THEN 'nontarget'
                WHEN NOT r.source CONTAINS 'nontarget' THEN r.source + ',nontarget'
                ELSE r.source
            END
            RETURN elementId(r) AS rel_id
        """
        params = {
            "from_table": from_table,
            "from_column": from_column,
            "to_table": to_table,
            "to_column": to_column,
        }
        
        try:
            if self.cypher_lock:
                async with self.cypher_lock:
                    result = await self.client.execute_with_params(query, params)
            else:
                result = await self.client.execute_with_params(query, params)
            
            if result and len(result) > 0:
                return result[0].get("rel_id")
            return None
        except Exception as e:
            log_nontarget("FK", f"FK_TO 생성 실패: {from_table}.{from_column} → {to_table}.{to_column}: {e}")
            return None
    
    async def infer_fk_relations(self) -> Dict[str, Any]:
        """컬럼명 패턴 기반 FK 관계 추론
        
        추론 규칙:
        1. 컬럼명이 {테이블명}Id 패턴 (orderId → ORDER_HEADER.orderId 참조)
        2. 컬럼명이 {테이블명}_id 패턴 (order_id → ORDER.order_id 참조)
        3. 동일 컬럼명이 다른 테이블의 PK와 일치하면 FK
        
        Returns:
            추론 결과 {inferred: [], created: [], errors: []}
        """
        import re
        
        result = {
            "inferred": [],
            "created": [],
            "errors": [],
        }
        
        if not self.client:
            log_nontarget("FK-INFER", "Neo4j 클라이언트 없음 - FK 추론 스킵")
            return result
        
        try:
            # 1. 모든 테이블과 PK 컬럼 조회
            pk_query = """
                MATCH (t:Table)-[:HAS_COLUMN]->(c:Column)
                WHERE c.pk_constraint = true OR c.description CONTAINS 'PK' OR c.description CONTAINS '기본키'
                RETURN t.name AS table_name,
                       t.entity_name AS entity_name,
                       c.name AS pk_column
            """
            if self.cypher_lock:
                async with self.cypher_lock:
                    pk_results = await self.client.execute_with_params(pk_query, {})
            else:
                pk_results = await self.client.execute_with_params(pk_query, {})
            
            # PK 맵 구성: {테이블명: [PK 컬럼들], ...}
            pk_map = {}
            entity_to_table = {}  # entity_name → table_name 맵
            for rec in (pk_results or []):
                table_name = rec.get("table_name", "")
                entity_name = rec.get("entity_name", "")
                pk_col = rec.get("pk_column", "")
                if table_name and pk_col:
                    if table_name not in pk_map:
                        pk_map[table_name] = []
                    pk_map[table_name].append(pk_col)
                if entity_name and table_name:
                    entity_to_table[entity_name.lower()] = table_name
            
            log_nontarget("FK-INFER", f"PK 정보 로드: {len(pk_map)}개 테이블")
            
            # 2. 모든 테이블의 모든 컬럼 조회 (FK 후보)
            cols_query = """
                MATCH (t:Table)-[:HAS_COLUMN]->(c:Column)
                WHERE NOT (c.pk_constraint = true)
                RETURN t.name AS table_name,
                       t.entity_name AS entity_name,
                       c.name AS column_name,
                       c.dtype AS dtype
            """
            if self.cypher_lock:
                async with self.cypher_lock:
                    cols_results = await self.client.execute_with_params(cols_query, {})
            else:
                cols_results = await self.client.execute_with_params(cols_query, {})
            
            # 3. 각 컬럼에 대해 FK 패턴 추론
            for rec in (cols_results or []):
                from_table = rec.get("table_name", "")
                from_entity = rec.get("entity_name", "")
                from_column = rec.get("column_name", "")
                dtype = rec.get("dtype", "")
                
                if not from_table or not from_column:
                    continue
                
                # FK 대상 테이블 추론
                to_table = None
                to_column = None
                inference_reason = ""
                
                # 패턴 1: {테이블명}Id (camelCase)
                # 예: orderId → ORDER 테이블
                match = re.match(r'^([a-z][a-zA-Z]*)Id$', from_column)
                if match:
                    entity_hint = match.group(1)  # order
                    # entity_hint의 첫글자 대문자로 변환하여 엔티티명 추정
                    entity_guess = entity_hint[0].upper() + entity_hint[1:]  # Order
                    
                    # entity_to_table 맵에서 찾기
                    if entity_guess.lower() in entity_to_table:
                        to_table = entity_to_table[entity_guess.lower()]
                        to_column = from_column  # orderId
                        inference_reason = f"패턴: {from_column} → {entity_guess} 엔티티"
                    else:
                        # UPPER_SNAKE_CASE로 변환하여 찾기
                        table_guess = re.sub(r'([A-Z])', r'_\1', entity_guess).upper().lstrip('_')
                        if table_guess in pk_map:
                            to_table = table_guess
                            to_column = from_column
                            inference_reason = f"패턴: {from_column} → {table_guess} 테이블"
                
                # 패턴 2: {테이블명}_id (snake_case)
                # 예: order_id → ORDER 테이블
                if not to_table:
                    match = re.match(r'^([a-z_]+)_id$', from_column)
                    if match:
                        table_hint = match.group(1).upper()  # ORDER
                        if table_hint in pk_map:
                            to_table = table_hint
                            to_column = from_column
                            inference_reason = f"패턴: {from_column} → {table_hint} 테이블"
                
                # 패턴 3: 동일 컬럼명이 다른 테이블의 PK와 일치
                if not to_table:
                    for tbl, pks in pk_map.items():
                        if tbl == from_table:
                            continue  # 자기 자신 제외
                        if from_column in pks:
                            to_table = tbl
                            to_column = from_column
                            inference_reason = f"동일 컬럼명: {from_column}이 {tbl}의 PK"
                            break
                
                # 추론 성공 시 기록
                if to_table and to_column:
                    inferred = {
                        "from_table": from_table,
                        "from_column": from_column,
                        "to_table": to_table,
                        "to_column": to_column,
                        "type": "many_to_one",
                        "reason": inference_reason,
                    }
                    result["inferred"].append(inferred)
                    
                    # FK 관계 생성
                    fk_table_result = await self._create_fk_to_table(
                        from_table=from_table,
                        to_table=to_table,
                        from_column=from_column,
                        to_column=to_column,
                        rel_type="many_to_one",
                        fk_name=f"INFERRED_{from_table}_{from_column}",
                    )
                    
                    fk_col_result = await self._create_fk_to_column(
                        from_table=from_table,
                        from_column=from_column,
                        to_table=to_table,
                        to_column=to_column,
                    )
                    
                    if fk_table_result or fk_col_result:
                        result["created"].append(inferred)
                        log_nontarget("FK-INFER", f"FK 추론 생성: {from_table}.{from_column} → {to_table}.{to_column} ({inference_reason})")
            
            log_nontarget("FK-INFER", f"FK 추론 완료: {len(result['inferred'])}개 추론, {len(result['created'])}개 생성")
            
        except Exception as e:
            result["errors"].append(str(e))
            log_nontarget("FK-INFER", f"FK 추론 오류: {e}")
        
        return result
    
    # ========================================================================
    # 타겟 노드 관계 분석
    # ========================================================================
    
    async def analyze_table_usage(self, table_id: str) -> Dict[str, Any]:
        """테이블이 타겟 노드에서 어떻게 사용되는지 분석
        
        분석 대상 관계:
        - REFERS_TO: 어떤 NONTARGET_BLOCK에서 참조
        - FROM/WRITES: 어떤 METHOD에서 조회/변경
        - USES: 어떤 FIELD/SymbolRef에서 사용
        
        Args:
            table_id: 테이블 elementId
            
        Returns:
            사용 분석 결과:
            {
                "table_name": str,
                "referred_by_blocks": [{"id", "file_path", "summary"}],
                "read_by_methods": [{"id", "name", "class", "signature"}],
                "written_by_methods": [{"id", "name", "class", "signature"}],
                "used_by_symbols": [{"id", "name", "method"}],
                "columns_usage": {"col_name": {"read_count", "write_count"}}
            }
        """
        result = {
            "table_name": "",
            "referred_by_blocks": [],
            "read_by_methods": [],
            "written_by_methods": [],
            "used_by_symbols": [],
            "columns_usage": {},
        }
        
        # 1. 테이블 기본 정보 조회
        table_query = """
            MATCH (t:Table) WHERE elementId(t) = $table_id
            RETURN t.name AS name, t.entity_name AS entity_name, t.schema AS schema
        """
        try:
            if self.cypher_lock:
                async with self.cypher_lock:
                    table_info = await self.client.execute_with_params(table_query, {"table_id": table_id})
            else:
                table_info = await self.client.execute_with_params(table_query, {"table_id": table_id})
            
            if table_info and len(table_info) > 0:
                result["table_name"] = table_info[0].get("name", "")
        except Exception as e:
            log_nontarget("TABLE-USAGE", f"테이블 정보 조회 실패: {e}")
            return result
        
        # 2. NONTARGET_BLOCK에서의 참조 (REFERS_TO)
        refers_query = """
            MATCH (t:Table) WHERE elementId(t) = $table_id
            MATCH (b:NONTARGET_BLOCK)-[:REFERS_TO]->(t)
            OPTIONAL MATCH (f:NONTARGET_FILE)-[:HAS_BLOCK]->(b)
            RETURN elementId(b) AS block_id, 
                   b.summary AS summary,
                   f.file_path AS file_path
        """
        try:
            if self.cypher_lock:
                async with self.cypher_lock:
                    refers = await self.client.execute_with_params(refers_query, {"table_id": table_id})
            else:
                refers = await self.client.execute_with_params(refers_query, {"table_id": table_id})
            
            for rec in (refers or []):
                result["referred_by_blocks"].append({
                    "id": rec.get("block_id"),
                    "file_path": rec.get("file_path", ""),
                    "summary": rec.get("summary", ""),
                })
        except Exception as e:
            log_nontarget("TABLE-USAGE", f"REFERS_TO 조회 실패: {e}")
        
        # 3. METHOD에서의 읽기 (FROM)
        from_query = """
            MATCH (t:Table) WHERE elementId(t) = $table_id
            MATCH (m:METHOD)-[:FROM]->(t)
            OPTIONAL MATCH (c:CLASS)-[:HAS_METHOD]->(m)
            RETURN elementId(m) AS method_id,
                   m.name AS name,
                   m.signature AS signature,
                   c.name AS class_name
        """
        try:
            if self.cypher_lock:
                async with self.cypher_lock:
                    reads = await self.client.execute_with_params(from_query, {"table_id": table_id})
            else:
                reads = await self.client.execute_with_params(from_query, {"table_id": table_id})
            
            for rec in (reads or []):
                result["read_by_methods"].append({
                    "id": rec.get("method_id"),
                    "name": rec.get("name", ""),
                    "class": rec.get("class_name", ""),
                    "signature": rec.get("signature", ""),
                })
        except Exception as e:
            log_nontarget("TABLE-USAGE", f"FROM 관계 조회 실패: {e}")
        
        # 4. METHOD에서의 쓰기 (WRITES)
        writes_query = """
            MATCH (t:Table) WHERE elementId(t) = $table_id
            MATCH (m:METHOD)-[:WRITES]->(t)
            OPTIONAL MATCH (c:CLASS)-[:HAS_METHOD]->(m)
            RETURN elementId(m) AS method_id,
                   m.name AS name,
                   m.signature AS signature,
                   c.name AS class_name
        """
        try:
            if self.cypher_lock:
                async with self.cypher_lock:
                    writes = await self.client.execute_with_params(writes_query, {"table_id": table_id})
            else:
                writes = await self.client.execute_with_params(writes_query, {"table_id": table_id})
            
            for rec in (writes or []):
                result["written_by_methods"].append({
                    "id": rec.get("method_id"),
                    "name": rec.get("name", ""),
                    "class": rec.get("class_name", ""),
                    "signature": rec.get("signature", ""),
                })
        except Exception as e:
            log_nontarget("TABLE-USAGE", f"WRITES 관계 조회 실패: {e}")
        
        # 5. SymbolRef/FIELD에서의 사용 (USES)
        uses_query = """
            MATCH (t:Table) WHERE elementId(t) = $table_id
            MATCH (t)-[:HAS_COLUMN]->(c:Column)
            MATCH (s)-[:USES]->(c)
            WHERE s:SymbolRef OR s:FIELD
            OPTIONAL MATCH (m:METHOD)-[:CONTAINS]->(s)
            RETURN elementId(s) AS symbol_id,
                   s.name AS name,
                   labels(s) AS labels,
                   c.name AS column_name,
                   m.name AS method_name
        """
        try:
            if self.cypher_lock:
                async with self.cypher_lock:
                    uses = await self.client.execute_with_params(uses_query, {"table_id": table_id})
            else:
                uses = await self.client.execute_with_params(uses_query, {"table_id": table_id})
            
            for rec in (uses or []):
                result["used_by_symbols"].append({
                    "id": rec.get("symbol_id"),
                    "name": rec.get("name", ""),
                    "type": rec.get("labels", [])[0] if rec.get("labels") else "unknown",
                    "column": rec.get("column_name", ""),
                    "method": rec.get("method_name", ""),
                })
                
                # 컬럼별 사용 집계
                col_name = rec.get("column_name", "")
                if col_name:
                    if col_name not in result["columns_usage"]:
                        result["columns_usage"][col_name] = {"read_count": 0, "write_count": 0}
                    result["columns_usage"][col_name]["read_count"] += 1
        except Exception as e:
            log_nontarget("TABLE-USAGE", f"USES 관계 조회 실패: {e}")
        
        log_nontarget(
            "TABLE-USAGE",
            f"테이블 {result['table_name']} 사용 분석: "
            f"blocks={len(result['referred_by_blocks'])}, "
            f"reads={len(result['read_by_methods'])}, "
            f"writes={len(result['written_by_methods'])}, "
            f"symbols={len(result['used_by_symbols'])}"
        )
        
        return result
    
    # ========================================================================
    # LLM 기반 Description 재생성
    # ========================================================================
    
    async def regenerate_description(
        self,
        table_id: str,
        new_info: str = "",
        ctx: Optional[Any] = None,
    ) -> Dict[str, Any]:
        """LLM을 사용하여 테이블/컬럼 description 재생성
        
        기존 description + 새 분석 정보 + 사용 패턴을 종합하여
        새로운 description을 생성합니다.
        
        Args:
            table_id: 테이블 elementId
            new_info: 새로 분석된 정보 (선택)
            ctx: RuntimeCtx (선택, description_agent 생성용)
            
        Returns:
            재생성 결과:
            {
                "table_description": str,
                "columns": [{"name", "description"}],
                "updated": bool
            }
        """
        # 1. 테이블 기본 정보 조회
        table_query = """
            MATCH (t:Table) WHERE elementId(t) = $table_id
            RETURN t.name AS name, 
                   t.entity_name AS entity_name,
                   t.schema AS schema,
                   t.description AS description
        """
        try:
            if self.cypher_lock:
                async with self.cypher_lock:
                    table_info = await self.client.execute_with_params(table_query, {"table_id": table_id})
            else:
                table_info = await self.client.execute_with_params(table_query, {"table_id": table_id})
        except Exception as e:
            log_nontarget("DESC-REGEN", f"테이블 정보 조회 실패: {e}")
            return {"table_description": "", "columns": [], "updated": False}
        
        if not table_info or len(table_info) == 0:
            return {"table_description": "", "columns": [], "updated": False}
        
        table_name = table_info[0].get("name", "")
        entity_name = table_info[0].get("entity_name", "")
        current_description = table_info[0].get("description", "")
        
        # 2. 컬럼 정보 조회
        columns_query = """
            MATCH (t:Table) WHERE elementId(t) = $table_id
            MATCH (t)-[:HAS_COLUMN]->(c:Column)
            RETURN c.name AS name, c.description AS description, c.dtype AS dtype
        """
        try:
            if self.cypher_lock:
                async with self.cypher_lock:
                    columns_info = await self.client.execute_with_params(columns_query, {"table_id": table_id})
            else:
                columns_info = await self.client.execute_with_params(columns_query, {"table_id": table_id})
        except Exception as e:
            log_nontarget("DESC-REGEN", f"컬럼 정보 조회 실패: {e}")
            columns_info = []
        
        columns = [
            {
                "name": col.get("name", ""),
                "description": col.get("description", ""),
                "dtype": col.get("dtype", ""),
            }
            for col in (columns_info or [])
        ]
        
        # 3. 사용 패턴 분석
        usage_analysis = await self.analyze_table_usage(table_id)
        
        # 4. yaml 템플릿 변수 준비
        template_vars = {
            "table_name": table_name,
            "entity_name": entity_name,
            "current_description": current_description,
            "new_info": new_info,
            "usage_analysis": json.dumps({
                "referred_by_blocks": usage_analysis.get("referred_by_blocks", [])[:5],
                "read_by_methods": usage_analysis.get("read_by_methods", [])[:5],
                "written_by_methods": usage_analysis.get("written_by_methods", [])[:5],
                "columns_usage": usage_analysis.get("columns_usage", {}),
            }, ensure_ascii=False, indent=2),
            "columns": json.dumps(columns[:20], ensure_ascii=False, indent=2),
        }
        
        # 5. description_agent 호출 + 트레이싱
        try:
            import time
            from analyzer.strategy.framework.nontarget.deepagents_client import DeepAgentsClient
            
            client = DeepAgentsClient()
            agent = client.create_agent("description_agent", ctx=ctx, template_vars=template_vars)
            
            # 에이전트 실행 - yaml 프롬프트에 이미 모든 지침이 포함됨
            user_input = "위 테이블 정보를 기반으로 description을 재생성하세요."
            
            start_time = time.time()
            result = agent.invoke({"messages": [HumanMessage(content=user_input)]})
            elapsed_ms = int((time.time() - start_time) * 1000)
            
            # 응답 파싱
            from analyzer.strategy.framework.nontarget.deepagents_client import _extract_agent_output, _parse_json_from_output
            output = _extract_agent_output(result)
            parsed = _parse_json_from_output(output)
            
            # 에이전트 호출 트레이싱
            if self.trace_logger:
                self.trace_logger.log_agent_call(
                    agent_name="description_agent",
                    prompt=user_input,
                    response=str(output)[:3000],
                    tokens_used=0,
                    duration_ms=elapsed_ms,
                )
            
            new_table_desc = parsed.get("table_description", "")
            new_columns = parsed.get("columns", [])
            
        except Exception as e:
            log_nontarget("DESC-REGEN", f"description_agent 호출 실패: {e}")
            return {"table_description": "", "columns": [], "updated": False}
        
        # 6. Neo4j 업데이트
        if new_table_desc:
            update_table_query = """
                MATCH (t:Table) WHERE elementId(t) = $table_id
                SET t.description = $description,
                    t.description_source = CASE
                        WHEN t.description_source IS NULL THEN 'regenerated'
                        ELSE t.description_source + ',regenerated'
                    END
                RETURN t.name AS name
            """
            try:
                if self.cypher_lock:
                    async with self.cypher_lock:
                        await self.client.execute_with_params(update_table_query, {
                            "table_id": table_id,
                            "description": new_table_desc,
                        })
                else:
                    await self.client.execute_with_params(update_table_query, {
                        "table_id": table_id,
                        "description": new_table_desc,
                    })
            except Exception as e:
                log_nontarget("DESC-REGEN", f"테이블 description 업데이트 실패: {e}")
        
        # 7. 컬럼 description 업데이트
        updated_columns = []
        for col_update in new_columns:
            col_name = col_update.get("name", "")
            col_desc = col_update.get("description", "")
            if col_name and col_desc:
                update_col_query = """
                    MATCH (t:Table) WHERE elementId(t) = $table_id
                    MATCH (t)-[:HAS_COLUMN]->(c:Column {name: $col_name})
                    SET c.description = $description
                    RETURN c.name AS name
                """
                try:
                    if self.cypher_lock:
                        async with self.cypher_lock:
                            await self.client.execute_with_params(update_col_query, {
                                "table_id": table_id,
                                "col_name": col_name,
                                "description": col_desc,
                            })
                    else:
                        await self.client.execute_with_params(update_col_query, {
                            "table_id": table_id,
                            "col_name": col_name,
                            "description": col_desc,
                        })
                    updated_columns.append({"name": col_name, "description": col_desc})
                except Exception as e:
                    log_nontarget("DESC-REGEN", f"컬럼 {col_name} description 업데이트 실패: {e}")
        
        log_nontarget(
            "DESC-REGEN",
            f"테이블 {table_name} description 재생성 완료: "
            f"table_desc={len(new_table_desc)}자, columns={len(updated_columns)}개"
        )
        
        if self.trace_logger:
            self.trace_logger.log_table_updated(
                table_name=table_name,
                changes={"description_regenerated": True, "columns_updated": len(updated_columns)},
                block_id="regeneration",
            )
        
        return {
            "table_description": new_table_desc,
            "columns": updated_columns,
            "updated": True,
        }

