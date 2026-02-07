"""그래프 데이터 조회/삭제 서비스

Neo4j 그래프 데이터의 조회, 삭제, 정리 기능을 제공합니다.

주요 기능:
- 그래프 데이터 존재 확인
- 전체 그래프 데이터 조회
- 관련 테이블 조회
- Neo4j/파일 데이터 삭제
"""

import logging
import os
import shutil

from fastapi import HTTPException

from client.neo4j_client import Neo4jClient
from config.settings import settings


logger = logging.getLogger(__name__)


# =============================================================================
# 그래프 데이터 조회
# =============================================================================

async def check_graph_data_exists() -> dict:
    """Neo4j에 기존 데이터 존재 여부 확인
    
    Returns:
        {"hasData": bool, "nodeCount": int}
    """
    client = Neo4jClient()
    try:
        result = await client.execute_queries([
            "MATCH (__cy_n__) RETURN count(__cy_n__) as count"
        ])
        node_count = result[0][0]["count"] if result and result[0] else 0
        
        return {
            "hasData": node_count > 0,
            "nodeCount": node_count
        }
    finally:
        await client.close()


async def fetch_graph_data() -> dict:
    """Neo4j에서 기존 그래프 데이터 조회
    
    Returns:
        {"Nodes": [...], "Relationships": [...]}
    """
    client = Neo4jClient()
    try:
        # 노드 조회 (전체 노드)
        node_query = """
            MATCH (__cy_n__)
            RETURN elementId(__cy_n__) AS nodeId, labels(__cy_n__) AS labels, properties(__cy_n__) AS props
        """
        
        # 관계 조회 (전체 관계)
        rel_query = """
            MATCH (__cy_a__)-[__cy_r__]->(__cy_b__)
            RETURN elementId(__cy_r__) AS relId, 
                   elementId(__cy_a__) AS startId, 
                   elementId(__cy_b__) AS endId, 
                   type(__cy_r__) AS relType, 
                   properties(__cy_r__) AS props
        """
        
        results = await client.execute_queries([node_query, rel_query])
        node_result = results[0] if results else []
        rel_result = results[1] if len(results) > 1 else []
        
        # 응답 형식 변환
        nodes = []
        for record in node_result:
            nodes.append({
                "Node ID": record["nodeId"],
                "Labels": record["labels"],
                "Properties": record["props"]
            })
        
        relationships = []
        for record in rel_result:
            relationships.append({
                "Relationship ID": record["relId"],
                "Start Node ID": record["startId"],
                "End Node ID": record["endId"],
                "Type": record["relType"],
                "Properties": record["props"]
            })
        
        return {
            "Nodes": nodes,
            "Relationships": relationships
        }
    finally:
        await client.close()


async def fetch_related_tables(table_name: str) -> dict:
    """특정 테이블과 연결된 모든 테이블 조회 (FK_TO_TABLE 관계 포함)
    
    Args:
        table_name: 기준 테이블명
        
    Returns:
        {"base_table": str, "tables": [...], "relationships": [...]}
    """
    client = Neo4jClient()
    try:
        # FK_TO_TABLE 관계 조회
        fk_query = {
            "query": """
                MATCH (__cy_t1__:Table)-[__cy_r__:FK_TO_TABLE]->(__cy_t2__:Table)
                WHERE __cy_t1__.name = $table_name OR __cy_t2__.name = $table_name
                   OR __cy_t1__.fqn ENDS WITH $table_name OR __cy_t2__.fqn ENDS WITH $table_name
                RETURN __cy_t1__.name AS from_table, 
                       __cy_t1__.schema_name AS from_schema,
                       __cy_t1__.description AS from_desc,
                       __cy_t2__.name AS to_table, 
                       __cy_t2__.schema_name AS to_schema,
                       __cy_t2__.description AS to_desc,
                       __cy_r__.sourceColumn AS source_column,
                       __cy_r__.targetColumn AS target_column,
                       COALESCE(__cy_r__.source, 'ddl') AS source,
                       type(__cy_r__) AS rel_type
            """,
            "parameters": {"table_name": table_name}
        }
        
        # 같은 프로시저에서 참조되는 테이블 (CO_REFERENCED)
        proc_query = {
            "query": """
                MATCH (__cy_t__:Table)
                WHERE __cy_t__.name = $table_name OR __cy_t__.fqn ENDS WITH $table_name
                
                OPTIONAL MATCH (__cy_t__)<-[:FROM|WRITES]-(__cy_s1__)<-[:PARENT_OF*]-(__cy_proc__)
                OPTIONAL MATCH (__cy_proc__)-[:PARENT_OF*]->(__cy_s2__)-[:FROM|WRITES]->(__cy_t2__:Table)
                WHERE __cy_t2__ <> __cy_t__
                
                WITH __cy_t__, COLLECT(DISTINCT {
                    name: __cy_t2__.name, 
                    schema: __cy_t2__.schema_name, 
                    description: __cy_t2__.description
                }) AS proc_related
                
                RETURN __cy_t__.name AS base_table, 
                       __cy_t__.schema_name AS base_schema,
                       proc_related
            """,
            "parameters": {"table_name": table_name}
        }
        
        fk_results = await client.execute_queries([fk_query])
        proc_results = await client.execute_queries([proc_query])
        
        fk_result = fk_results[0] if fk_results else []
        proc_result = proc_results[0] if proc_results else []
        
        tables = []
        relationships = []
        seen_tables = set()
        seen_rels = set()
        
        # 기준 테이블 추가
        seen_tables.add(table_name)
        
        # FK_TO_TABLE 관계 처리
        fk_by_table_pair = {}
        
        for record in fk_result:
            from_table = record.get("from_table")
            to_table = record.get("to_table")
            source_column = record.get("source_column") or ""
            target_column = record.get("target_column") or ""
            source_type = record.get("source") or "ddl"
            
            if from_table and from_table not in seen_tables:
                seen_tables.add(from_table)
                tables.append({
                    "name": from_table,
                    "schema": record.get("from_schema") or "public",
                    "description": record.get("from_desc")
                })
            
            if to_table and to_table not in seen_tables:
                seen_tables.add(to_table)
                tables.append({
                    "name": to_table,
                    "schema": record.get("to_schema") or "public",
                    "description": record.get("to_desc")
                })
            
            pair_key = (from_table, to_table)
            if pair_key not in fk_by_table_pair:
                fk_by_table_pair[pair_key] = {
                    "source": source_type,
                    "column_pairs": []
                }
            
            if source_column or target_column:
                fk_by_table_pair[pair_key]["column_pairs"].append({
                    "source": source_column,
                    "target": target_column
                })
        
        for (from_table, to_table), data in fk_by_table_pair.items():
            rel_key = f"{from_table}->{to_table}"
            if rel_key not in seen_rels:
                seen_rels.add(rel_key)
                relationships.append({
                    "from_table": from_table,
                    "to_table": to_table,
                    "type": "FK_TO_TABLE",
                    "source": data["source"],
                    "column_pairs": data["column_pairs"]
                })
        
        # CO_REFERENCED 관계 처리
        for record in proc_result:
            base_table = record.get("base_table")
            for item in record.get("proc_related", []):
                if item.get("name") and item["name"] not in seen_tables:
                    seen_tables.add(item["name"])
                    tables.append({
                        "name": item["name"],
                        "schema": item.get("schema") or "public",
                        "description": item.get("description")
                    })
                    
                    rel_key = f"{base_table}->{item['name']}"
                    if rel_key not in seen_rels:
                        seen_rels.add(rel_key)
                        relationships.append({
                            "from_table": base_table,
                            "to_table": item["name"],
                            "type": "CO_REFERENCED",
                            "source": "procedure",
                            "column_pairs": []
                        })
        
        return {
            "base_table": table_name,
            "tables": tables,
            "relationships": relationships
        }
    finally:
        await client.close()


# =============================================================================
# 데이터 삭제
# =============================================================================

async def cleanup_neo4j_graph() -> None:
    """Neo4j 그래프 데이터 삭제 (파일 시스템 유지, DataSource 노드 제외)"""
    client = Neo4jClient()
    
    try:
        # DataSource 노드는 인제스천과 무관하므로 삭제에서 제외
        await client.execute_queries([
            "MATCH (__cy_n__) WHERE NOT __cy_n__:DataSource DETACH DELETE __cy_n__"
        ])
        logging.info("Neo4j 데이터 삭제 완료 (DataSource 제외)")
    except Exception as e:
        logging.error("Neo4j 데이터 삭제 오류: %s", e)
        raise RuntimeError(f"Neo4j 데이터 삭제 오류: {e}")
    finally:
        await client.close()


async def cleanup_all_graph_data(include_files: bool = True) -> None:
    """데이터 전체 삭제 (DataSource 노드 제외)
    
    Args:
        include_files: True면 파일 시스템도 함께 삭제, False면 Neo4j만 삭제
    """
    client = Neo4jClient()
    
    try:
        # 파일 시스템 정리 (옵션)
        if include_files:
            dir_path = settings.path.data_dir
            if os.path.exists(dir_path):
                shutil.rmtree(dir_path)
                os.makedirs(dir_path)
                logging.info("디렉토리 초기화: %s", dir_path)
        
        # Neo4j 데이터 삭제 (DataSource 노드는 인제스천과 무관하므로 제외)
        await client.execute_queries([
            "MATCH (__cy_n__) WHERE NOT __cy_n__:DataSource DETACH DELETE __cy_n__"
        ])
        logging.info("Neo4j 데이터 삭제 완료 (DataSource 제외)")
    except Exception as e:
        logging.error("데이터 삭제 오류: %s", e)
        raise RuntimeError(f"데이터 삭제 오류: {e}")
    finally:
        await client.close()


async def delete_graph_data(include_files: bool = False) -> dict:
    """사용자 데이터 삭제
    
    Args:
        include_files: 파일 시스템도 삭제할지 여부
        
    Returns:
        삭제 결과 메시지
    """
    if include_files:
        await cleanup_all_graph_data(include_files=True)
        return {"message": "모든 데이터(파일 + Neo4j)가 삭제되었습니다."}
    else:
        await cleanup_neo4j_graph()
        return {"message": "Neo4j 그래프 데이터가 삭제되었습니다. (파일은 유지됨)"}

