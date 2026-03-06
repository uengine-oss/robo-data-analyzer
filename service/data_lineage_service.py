"""데이터 리니지 서비스

데이터 리니지 그래프 조회 및 분석 기능을 제공합니다.

주요 기능:
- 리니지 그래프 조회
- SQL에서 리니지 추출
"""

import logging

from client.neo4j_client import Neo4jClient
from analyzer.strategy.dbms.linking.lineage_analyzer import analyze_lineage_from_sql as _analyze_lineage


logger = logging.getLogger(__name__)


async def fetch_lineage_graph() -> dict:
    """데이터 리니지 그래프 조회
    
    Returns:
        {"nodes": [...], "edges": [...], "stats": {...}}
    """
    client = Neo4jClient()
    try:
        # DataSource, ETLProcess 노드 조회
        node_query = """
            MATCH (__cy_n__)
            WHERE (__cy_n__:DataSource OR __cy_n__:ETLProcess)
            RETURN __cy_n__.name AS name,
                   labels(__cy_n__)[0] AS nodeType,
                   elementId(__cy_n__) AS id,
                   properties(__cy_n__) AS properties
            ORDER BY nodeType, name
        """
        
        # 관계 조회 쿼리
        rel_query = """
            MATCH (__cy_src__)-[__cy_r__]->(__cy_tgt__)
            WHERE (__cy_src__:DataSource OR __cy_src__:ETLProcess)
              AND (__cy_tgt__:DataSource OR __cy_tgt__:ETLProcess)
              AND type(__cy_r__) IN ['DATA_FLOW_TO', 'TRANSFORMS_TO']
            RETURN elementId(__cy_r__) AS id,
                   elementId(__cy_src__) AS source,
                   elementId(__cy_tgt__) AS target,
                   type(__cy_r__) AS relType,
                   properties(__cy_r__) AS properties
        """
        
        # 통계 쿼리
        stats_query = """
            MATCH (__cy_n__)
            WHERE (__cy_n__:DataSource OR __cy_n__:ETLProcess)
            WITH 
                sum(CASE WHEN __cy_n__:ETLProcess THEN 1 ELSE 0 END) AS etlCount,
                sum(CASE WHEN __cy_n__:DataSource AND __cy_n__.type = 'SOURCE' THEN 1 ELSE 0 END) AS sourceCount,
                sum(CASE WHEN __cy_n__:DataSource AND __cy_n__.type = 'TARGET' THEN 1 ELSE 0 END) AS targetCount
            RETURN etlCount, sourceCount, targetCount
        """
        
        # 쿼리 실행
        node_result, rel_result, stats_result = await client.execute_queries([
            node_query, rel_query, stats_query
        ])
        
        # 응답 변환
        nodes = []
        for record in node_result:
            node_type = record.get("nodeType", "Unknown")
            if node_type == "DataSource":
                node_type = record.get("properties", {}).get("type", "SOURCE")
            elif node_type == "ETLProcess":
                node_type = "ETL"
            
            nodes.append({
                "id": record["id"],
                "name": record["name"],
                "type": node_type,
                "properties": record.get("properties", {})
            })
        
        edges = []
        for record in rel_result:
            edges.append({
                "id": record["id"],
                "source": record["source"],
                "target": record["target"],
                "type": record["relType"],
                "properties": record.get("properties", {})
            })
        
        stats = {}
        if stats_result:
            stats = {
                "etlCount": stats_result[0].get("etlCount", 0),
                "sourceCount": stats_result[0].get("sourceCount", 0),
                "targetCount": stats_result[0].get("targetCount", 0),
                "flowCount": len(edges)
            }
        
        return {
            "nodes": nodes,
            "edges": edges,
            "stats": stats
        }
    finally:
        await client.close()


async def analyze_sql_lineage(
    sql_content: str,
    file_name: str = "",
    dbms: str = "oracle"
) -> dict:
    """ETL 코드에서 데이터 리니지 추출 및 Neo4j 저장
    
    Args:
        sql_content: SQL 소스 코드
        file_name: 파일명
        dbms: DBMS 타입
        
    Returns:
        {"lineages": [...], "stats": {...}}
    """
    lineage_list, stats = await _analyze_lineage(
        sql_content=sql_content,
        file_name=file_name,
        dbms=dbms,
    )
    
    # 응답 변환
    lineages = [
        {
            "etl_name": l.etl_name,
            "source_tables": l.source_tables,
            "target_tables": l.target_tables,
            "operation_type": l.operation_type,
        }
        for l in lineage_list
    ]
    
    return {
        "lineages": lineages,
        "stats": stats
    }

