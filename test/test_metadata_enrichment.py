"""메타데이터 보강 테스트 스크립트

Text2SQL API를 통해 샘플 데이터를 조회하고,
LLM으로 테이블/컬럼 설명을 생성하며,
FK 관계를 추론하는 기능을 테스트합니다.
"""

import asyncio
import os
import sys

# Windows 인코딩 문제 해결
if sys.platform == 'win32':
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')

# 프로젝트 루트를 경로에 추가
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from client.neo4j_client import Neo4jClient
from analyzer.strategy.base_analyzer import AnalysisStats
from analyzer.strategy.dbms.pipeline.phase_metadata import run_phase_metadata
from analyzer.strategy.dbms.pipeline.phases import run_phase_ddl
from config.settings import settings
from service.source_analyze_service import get_analysis_directories


def print_section(title: str, char: str = "="):
    """섹션 구분선 출력"""
    print(f"\n{char * 80}")
    print(f"  {title}")
    print(f"{char * 80}\n")


def print_table_info(table_name: str, schema: str, description: str = None):
    """테이블 정보 출력"""
    print(f"  📊 테이블: {schema}.{table_name}")
    if description:
        print(f"     설명: {description}")
    else:
        print(f"     설명: (없음)")


def print_fk_relationship(from_table: str, from_schema: str, from_column: str,
                          to_table: str, to_schema: str, to_column: str,
                          match_ratio: float, similarity: float = None):
    """FK 관계 출력"""
    print(f"  🔗 FK 관계:")
    print(f"     {from_schema}.{from_table}.{from_column}")
    print(f"     → {to_schema}.{to_table}.{to_column}")
    if similarity:
        print(f"     유사도: {similarity:.1%}")
    print(f"     매칭 비율: {match_ratio:.1%}")




async def query_table_descriptions(client):
    """보강된 테이블 설명 조회 (이번 세션에서 보강된 것만)"""
    query = """
    MATCH (t:Table)
    WHERE t.description IS NOT NULL 
      AND t.description <> '' 
      AND t.description <> 'N/A'
      AND t.description_source = 'sample_data_inference'
    RETURN t.schema AS schema, t.name AS table_name, t.description AS description
    ORDER BY t.schema, t.name
    """
    results = await client.execute_queries([query])
    return results[0] if results else []


async def query_column_descriptions(client):
    """보강된 컬럼 설명 조회 (이번 세션에서 보강된 것만)"""
    query = """
    MATCH (t:Table)-[:HAS_COLUMN]->(c:Column)
    WHERE c.description IS NOT NULL 
      AND c.description <> '' 
      AND c.description <> 'N/A'
      AND c.description_source = 'sample_data_inference'
    RETURN t.schema AS schema, t.name AS table_name, 
           c.name AS column_name, c.description AS description
    ORDER BY t.schema, t.name, c.name
    """
    results = await client.execute_queries([query])
    return results[0] if results else []


async def query_fk_relationships(client):
    """FK 관계 조회"""
    query = """
    MATCH (t1:Table)-[r:FK_TO_TABLE]->(t2:Table)
    WHERE r.source = 'sample_data_inference'
    RETURN t1.schema AS from_schema, t1.name AS from_table, r.sourceColumn AS from_column,
           t2.schema AS to_schema, t2.name AS to_table, r.targetColumn AS to_column,
           r.similarity AS similarity, r.match_ratio AS match_ratio
    ORDER BY t1.schema, t1.name, t2.schema, t2.name
    """
    results = await client.execute_queries([query])
    return results[0] if results else []


async def main():
    """메인 테스트 함수"""
    print_section("메타데이터 보강 테스트 시작", "=")
    
    # 환경 변수 확인
    api_key = os.getenv("LLM_API_KEY") or os.getenv("OPENAI_API_KEY")
    if not api_key:
        print("❌ 오류: LLM_API_KEY 또는 OPENAI_API_KEY 환경변수가 설정되지 않았습니다.")
        return
    
    text2sql_url = settings.metadata_enrichment.text2sql_api_url
    if not text2sql_url:
        print("❌ 오류: TEXT2SQL_API_URL 환경변수가 설정되지 않았습니다.")
        print("   .env 파일에 TEXT2SQL_API_URL=http://localhost:8000 을 추가하세요.")
        return
    
    # 환경변수에서 데이터 소스 이름 가져오기
    datasource_name = os.getenv("TEST_DATASOURCE_NAME", "").strip()
    if not datasource_name:
        # 기본값: TEST_TARGET 기반으로 생성
        test_target = os.getenv("TEST_TARGET", "postgres").strip() or "postgres"
        datasource_name = f"{test_target}_datasource"
    
    print(f"✅ LLM API 키: {'*' * 20}...{api_key[-4:] if len(api_key) > 4 else ''}")
    print(f"✅ Text2SQL URL: {text2sql_url}")
    print(f"✅ DataSource: {datasource_name}")
    
    # Neo4j 연결
    print_section("Neo4j 연결 중...", "-")
    client = Neo4jClient()
    try:
        # 연결 테스트
        test_query = "RETURN 1 AS test"
        await client.execute_queries([test_query])
        print("✅ Neo4j 연결 성공")
    except Exception as e:
        print(f"❌ Neo4j 연결 실패: {e}")
        return
    
    # 1. 실제 분석에서 사용하는 DDL 폴더 경로 사용 (data/ddl)
    dirs = get_analysis_directories("dbms")
    ddl_dir = dirs.get("ddl", "")
    
    if not ddl_dir:
        print("❌ DDL 폴더 경로를 찾을 수 없습니다.")
        print("   settings.path.data_dir이 설정되어 있는지 확인하세요.")
        return
    
    if not os.path.isdir(ddl_dir):
        print(f"❌ DDL 폴더가 없습니다: {ddl_dir}")
        print(f"   {ddl_dir} 폴더를 생성하고 DDL 파일을 추가하세요.")
        return
    
    # DDL 파일 확인 (확장자 제한 없이 모든 파일)
    ddl_files = [f for f in os.listdir(ddl_dir) if os.path.isfile(os.path.join(ddl_dir, f))]
    if not ddl_files:
        print(f"⚠️ DDL 폴더에 파일이 없습니다: {ddl_dir}")
        print("   DDL 파일을 추가하세요.")
        return
    
    print(f"✅ DDL 폴더: {ddl_dir}")
    print(f"✅ DDL 파일: {len(ddl_files)}개 발견")
    
    # 2. 실제 로직을 위한 객체 생성
    class Analyzer:
        """실제 로직을 위한 Analyzer 객체"""
        def __init__(self):
            self._cypher_lock = asyncio.Lock()
            self._ddl_schemas = set()  # DDL에서 수집된 스키마 Set
            self._ddl_table_metadata = {}  # DDL 테이블 메타데이터 캐시
        
        def emit_skip(self, message: str):
            """스킵 메시지"""
            from util.ndjson_stream import emit_message
            return emit_message(f"   ⏭️ {message}")
        
        def emit_separator(self):
            """구분선"""
            from util.ndjson_stream import emit_message
            return emit_message("")
        
        def emit_phase_header(self, phase_num: int, title: str, subtitle: str):
            """Phase 헤더"""
            from util.ndjson_stream import emit_message
            return emit_message(f"   {title} ({subtitle})")
        
        def emit_file_start(self, idx: int, total: int, filename: str):
            """파일 시작 메시지"""
            from util.ndjson_stream import emit_message
            return emit_message(f"   📄 [{idx}/{total}] {filename} 처리 중...")
    
    class Orchestrator:
        """실제 로직을 위한 Orchestrator 객체"""
        def __init__(self, api_key: str, datasource_name: str, ddl_dir: str, target: str):
            self.api_key = api_key
            self.datasource_name = datasource_name
            self.infer_fk_relations = True  # FK 관계 추론 활성화
            self.dirs = {"ddl": ddl_dir}  # DDL 폴더 경로
            self.target = target  # DDL 파서에서 사용하는 DB 타입
            self.name_case = "original"  # 대소문자 변환 옵션
    
    # 환경변수에서 target 가져오기
    test_target = os.getenv("TEST_TARGET", "postgres").strip() or "postgres"
    
    analyzer = Analyzer()
    orchestrator = Orchestrator(api_key=api_key, datasource_name=datasource_name, ddl_dir=ddl_dir, target=test_target)
    stats = AnalysisStats()
    
    # 3. DDL 파서 단계 실행
    print_section("📋 DDL 파서 단계 실행", "=")
    try:
        import json
        ddl_nodes_count = 0
        ddl_rels_count = 0
        
        async for chunk in run_phase_ddl(analyzer, client, orchestrator, stats):
            if isinstance(chunk, bytes):
                try:
                    message_str = chunk.decode('utf-8', errors='ignore').strip()
                    if message_str:
                        # JSON 형식인 경우 파싱
                        if message_str.startswith('{'):
                            try:
                                msg_obj = json.loads(message_str)
                                msg_type = msg_obj.get('type', '')
                                
                                # 메시지 타입
                                if msg_type == 'message':
                                    message = msg_obj.get('content', '')
                                    if message:
                                        print(f"  {message}")
                                
                                # 데이터 타입 (그래프 데이터)
                                elif msg_type == 'data':
                                    graph = msg_obj.get('graph', {})
                                    nodes = graph.get('Nodes', [])
                                    relationships = graph.get('Relationships', [])
                                    if nodes or relationships:
                                        ddl_nodes_count += len(nodes)
                                        ddl_rels_count += len(relationships)
                                        print(f"  📦 그래프 데이터 스트리밍: 노드 {len(nodes)}개, 관계 {len(relationships)}개")
                                
                                # 기타 이벤트
                                else:
                                    print(f"  {message_str}")
                            except:
                                print(f"  {message_str}")
                        else:
                            print(f"  {message_str}")
                except:
                    pass
        
        print("✅ DDL 파서 단계 완료")
        if ddl_nodes_count > 0 or ddl_rels_count > 0:
            print(f"  📊 총 스트리밍: 노드 {ddl_nodes_count}개, 관계 {ddl_rels_count}개")
    except Exception as e:
        print(f"❌ DDL 파서 단계 실패: {e}")
        import traceback
        traceback.print_exc()
        return
    
    # 4. 메타데이터 보강 실행
    print_section("메타데이터 보강 실행 중...", "-")
    
    try:
        import json
        fk_inference_messages = []
        metadata_nodes_count = 0
        metadata_rels_count = 0
        node_events_count = 0
        relationship_events_count = 0
        
        async for chunk in run_phase_metadata(analyzer, client, orchestrator, stats):
            # 스트리밍 데이터 처리
            if isinstance(chunk, bytes):
                try:
                    message_str = chunk.decode('utf-8', errors='ignore').strip()
                    if message_str:
                        # JSON 형식인 경우 파싱
                        if message_str.startswith('{'):
                            try:
                                msg_obj = json.loads(message_str)
                                msg_type = msg_obj.get('type', '')
                                
                                # 메시지 타입
                                if msg_type == 'message':
                                    message = msg_obj.get('content', '')
                                    # FK 추론 관련 메시지 저장 및 출력
                                    if 'FK' in message or '추론' in message or '후보' in message or '매칭' in message or 'VERIFY' in message:
                                        fk_inference_messages.append(message)
                                        print(f"  {message}")
                                    # 에러나 경고 메시지도 출력
                                    elif '⚠️' in message or '❌' in message or 'ERROR' in message or '실패' in message:
                                        print(f"  {message}")
                                    # 설명 생성 관련 메시지
                                    elif '설명' in message or 'LLM' in message or '샘플' in message:
                                        print(f"  {message}")
                                    # description 조회 관련 메시지 (보강 대상 확인)
                                    elif 'description' in message.lower() or '테이블 조회' in message or '보강 대상' in message or '보강 불필요' in message:
                                        print(f"  {message}")
                                    # Phase 진행 메시지
                                    elif 'Phase' in message or '단계' in message:
                                        print(f"  {message}")
                                
                                # 데이터 타입 (그래프 데이터)
                                elif msg_type == 'data':
                                    graph = msg_obj.get('graph', {})
                                    nodes = graph.get('Nodes', [])
                                    relationships = graph.get('Relationships', [])
                                    if nodes or relationships:
                                        metadata_nodes_count += len(nodes)
                                        metadata_rels_count += len(relationships)
                                        current_file = msg_obj.get('current_file', '')
                                        print(f"  📦 그래프 데이터 스트리밍 ({current_file}): 노드 {len(nodes)}개, 관계 {len(relationships)}개")
                                
                                # 노드 이벤트
                                elif msg_type == 'node_event':
                                    node_events_count += 1
                                    action = msg_obj.get('action', '')
                                    node_type = msg_obj.get('nodeType', '')
                                    node_name = msg_obj.get('nodeName', '')
                                    print(f"  🔹 노드 이벤트: {action} - {node_type} ({node_name})")
                                
                                # 관계 이벤트
                                elif msg_type == 'relationship_event':
                                    relationship_events_count += 1
                                    action = msg_obj.get('action', '')
                                    rel_type = msg_obj.get('relType', '')
                                    source = msg_obj.get('source', '')
                                    target = msg_obj.get('target', '')
                                    print(f"  🔗 관계 이벤트: {action} - {rel_type} ({source} → {target})")
                                
                                # Phase 이벤트
                                elif msg_type == 'phase_event':
                                    # 진행률 업데이트는 조용히 처리 (너무 많을 수 있음)
                                    pass
                                
                                # 기타
                                else:
                                    print(f"  {message_str}")
                            except:
                                print(f"  {message_str}")
                        else:
                            print(f"  {message_str}")
                except:
                    pass
        
        print("✅ 메타데이터 보강 완료")
        if metadata_nodes_count > 0 or metadata_rels_count > 0:
            print(f"  📊 총 스트리밍: 노드 {metadata_nodes_count}개, 관계 {metadata_rels_count}개")
        if node_events_count > 0:
            print(f"  📊 노드 이벤트: {node_events_count}개")
        if relationship_events_count > 0:
            print(f"  📊 관계 이벤트: {relationship_events_count}개")
        
        # FK 추론 실행 여부 확인
        if not fk_inference_messages:
            print("  ⚠️ FK 관계 추론 관련 메시지가 없습니다.")
            print(f"     - orchestrator.infer_fk_relations: {getattr(orchestrator, 'infer_fk_relations', '없음')}")
            print(f"     - settings.fk_inference_enabled: {settings.metadata_enrichment.fk_inference_enabled}")
    except Exception as e:
        print(f"❌ 메타데이터 보강 실패: {e}")
        import traceback
        traceback.print_exc()
        return
    
    # 4. 결과 조회 및 출력
    print_section("📋 보강된 테이블 설명", "=")
    table_descriptions = await query_table_descriptions(client)
    
    if table_descriptions:
        for row in table_descriptions:
            print_table_info(
                table_name=row.get("table_name", ""),
                schema=row.get("schema", ""),
                description=row.get("description", "")
            )
    else:
        print("  ℹ️ 보강된 테이블 설명이 없습니다.")
    
    print_section("📝 보강된 컬럼 설명", "=")
    column_descriptions = await query_column_descriptions(client)
    
    if column_descriptions:
        current_table = None
        for row in column_descriptions:
            schema = row.get("schema", "")
            table_name = row.get("table_name", "")
            full_table = f"{schema}.{table_name}"
            
            if current_table != full_table:
                current_table = full_table
                print(f"\n  📊 테이블: {full_table}")
            
            print(f"     • {row.get('column_name', '')}: {row.get('description', '')}")
    else:
        print("  ℹ️ 보강된 컬럼 설명이 없습니다.")
    
    print_section("🔗 추론된 FK 관계", "=")
    fk_relationships = await query_fk_relationships(client)
    
    if fk_relationships:
        for row in fk_relationships:
            print_fk_relationship(
                from_table=row.get("from_table", ""),
                from_schema=row.get("from_schema", ""),
                from_column=row.get("from_column", ""),
                to_table=row.get("to_table", ""),
                to_schema=row.get("to_schema", ""),
                to_column=row.get("to_column", ""),
                match_ratio=row.get("match_ratio", 0.0),
                similarity=row.get("similarity")
            )
            print()
    else:
        print("  ℹ️ 추론된 FK 관계가 없습니다.")
        print("     FK_INFERENCE_ENABLED=true로 설정되어 있는지 확인하세요.")
    
    # 통계 출력
    print_section("📊 통계", "=")
    print(f"  테이블 설명 보강: {getattr(stats, 'tables_enriched', 0)}개")
    print(f"  컬럼 설명 보강: {getattr(stats, 'columns_enriched', 0)}개")
    print(f"  FK 관계 추론: {getattr(stats, 'fk_relationships_inferred', 0)}개")
    
    print_section("테스트 완료", "=")
    
    # 연결 종료
    await client.close()


if __name__ == "__main__":
    asyncio.run(main())
