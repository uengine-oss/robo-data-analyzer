"""분석 파이프라인 통합 테스트.

전체 분석 파이프라인을 단일 통합 테스트로 실행합니다:
- Phase 1: AST 그래프 생성 (모든 파일)
- Phase 2: LLM 분석 (모든 파일)
- Phase 3: User Story 생성
- Phase 4: 벡터라이징
- Phase 5: 리니지 분석

Neo4j, LLM API 등의 외부 의존성이 필요합니다.

지원 전략:
- dbms: SQL 파일 기반 프로시저/함수 분석
- framework: Java 파일 기반 클래스 다이어그램 분석

────────────────────────────────────────────────────────────────────
실행 방법
────────────────────────────────────────────────────────────────────

[A] 단일 분석 (메인 슬롯, 기본)
  cd robo-data-analyzer
  pytest test/test_analyzer.py::test_full_analysis_pipeline -v -s

  → .env 의 기본값 사용. 데이터는 robo/data/, Neo4j DB 는 NEO4J_DATABASE.

[B] 병렬 분석 (슬롯 격리 — 여러 분석기 동시 실행)
  슬롯별 환경변수로 격리:
    DATA_DIR        = c:/uEngine/robo/data-slots/<slot>   # 데이터 폴더 직접 지정
    NEO4J_DATABASE  = analyzer | architect | analyzer_<slot>  # Neo4j DB 격리
    LLM_CACHE_DB    = .llm_cache_<slot>.db               # LLM 캐시 격리 (선택)
    LLM_AUDIT_DIR   = logs_<slot>                         # logs/llm_prompts 격리 (선택)
    TEST_DATASOURCE = rwis_<slot>                         # catalog 격리 (선택)

  예시 — 두 SP 동시 분석 (다른 터미널 각각):
    DATA_DIR=c:/uEngine/robo/data-slots/fn_edit_yn \\
    NEO4J_DATABASE=architect \\
      pytest test/test_analyzer.py

    DATA_DIR=c:/uEngine/robo/data-slots/fn_calc \\
    NEO4J_DATABASE=analyzer \\
      pytest test/test_analyzer.py

  슬롯 폴더 구조 (DATA_DIR 가 가리키는 폴더 안):
    <slot>/
      source/<directory>/<file>.sql   # 분석 대상
      analysis/target/<directory>/    # ANTLR AST JSON 출력 위치
      ddl/                             # DDL 파일

[C] 사전 준비 — 새 Neo4j DB 사용 시 1회
  Neo4j Browser:
    :use system
    CREATE DATABASE analyzer_<slot> IF NOT EXISTS;

가이드 상세: docs/가이드/병렬분석-슬롯-가이드.md
슬롯 명명 컨벤션: data-slots/README.md
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

import pytest
import pytest_asyncio

from service.source_analyze_service import AnalysisContext, run_source_analysis, discover_analyzable_files, get_analysis_directories
from client.neo4j_client import Neo4jClient

# ==================== 프로젝트 경로 설정 ====================
PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

# ==================== 환경변수 설정 ====================
os.environ.setdefault("PYTHONIOENCODING", "utf-8")

# ==================== 테스트 설정 상수 ====================
TEST_API_KEY = os.getenv("LLM_API_KEY")
TEST_DB_NAME = os.getenv("TEST_DB_NAME", "test").strip() or "test"
TEST_LOCALE = os.getenv("TEST_LOCALE", "ko").strip() or "ko"
TEST_ANALYSIS_STRATEGY = os.getenv("TEST_ANALYSIS_STRATEGY", "dbms").strip().lower() or "dbms"
TEST_TARGET = os.getenv("TEST_TARGET", "").strip()
TEST_DATASOURCE = os.getenv("TEST_DATASOURCE", "").strip()

# target 기본값 설정 (strategy에 따라)
if not TEST_TARGET:
    if TEST_ANALYSIS_STRATEGY == "dbms":
        TEST_TARGET = "postgres"  # DBMS 기본값
    else:  # framework
        TEST_TARGET = "java"  # Framework 기본값

IS_FRAMEWORK = TEST_ANALYSIS_STRATEGY == "framework"
STRATEGY_LABEL = "FRAMEWORK" if IS_FRAMEWORK else "DBMS"


def _ensure_api_key():
    """API 키가 설정되어 있는지 확인"""
    if not TEST_API_KEY:
        pytest.skip("LLM_API_KEY가 설정되지 않았습니다")


def _ensure_datasource():
    """catalog 연동용 datasource가 설정되어 있는지 확인"""
    if not TEST_DATASOURCE:
        pytest.skip("TEST_DATASOURCE 미설정 — catalog 연동 샘플 수집 불가")


async def _clear_graph(connection: Neo4jClient):
    """Neo4j 데이터 삭제"""
    await connection.execute_queries([
        "MATCH (__cy_n__) DETACH DELETE __cy_n__"
    ])


@pytest_asyncio.fixture
async def real_neo4j() -> Neo4jClient:
    """테스트용 Neo4j 연결 생성 및 정리"""
    conn = Neo4jClient(database=TEST_DB_NAME)
    await _clear_graph(conn)

    try:
        yield conn
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_full_analysis_pipeline(real_neo4j: Neo4jClient):
    """전체 분석 파이프라인 통합 테스트
    
    모든 단계를 순차적으로 실행하고 결과를 검증합니다:
    - Phase 1: AST 그래프 생성
    - Phase 2: LLM 분석
    - Phase 3: User Story 생성
    - Phase 4: 벡터라이징
    - Phase 5: 리니지 분석
    """
    _ensure_api_key()
    _ensure_datasource()

    # 소스 파일 탐색
    try:
        dirs = get_analysis_directories(strategy=TEST_ANALYSIS_STRATEGY)
        source_files = discover_analyzable_files(
            strategy=TEST_ANALYSIS_STRATEGY,
            target=TEST_TARGET,
            dirs=dirs,
        )
    except Exception as e:
        pytest.skip(f"소스 파일 탐색 실패: {e}")
    
    if not source_files:
        pytest.skip("분석할 소스 파일이 없습니다")
    
    # 분석 실행
    start = time.perf_counter()
    event_count = 0
    
    async for chunk in run_source_analysis(
        file_names=source_files,
        api_key=TEST_API_KEY,
        locale=TEST_LOCALE,
        strategy=TEST_ANALYSIS_STRATEGY,
        target=TEST_TARGET,
        datasource_name=TEST_DATASOURCE,
    ):
        event_count += 1
    
    elapsed = time.perf_counter() - start
    
    # 이벤트 생성 확인
    assert event_count > 0, "분석 파이프라인에서 이벤트가 생성되지 않았습니다"
    
    # 결과 검증
    if IS_FRAMEWORK:
        # Framework: MODULE 노드 확인
        result = await real_neo4j.execute_queries([
            "MATCH (__cy_c__:MODULE) "
            "RETURN count(__cy_c__) AS c"
        ])
        node_count = int(result[0][0]["c"])
        assert node_count > 0, "MODULE 노드가 생성되지 않았습니다"
        print(f"\n[RESULT - {STRATEGY_LABEL}] elapsed={elapsed:.2f}s events={event_count} files={len(source_files)} classes={node_count}")
    else:
        # DBMS: PROCEDURE/FUNCTION 노드 확인
        result = await real_neo4j.execute_queries([
            "MATCH (__cy_p__) WHERE (__cy_p__:PROCEDURE OR __cy_p__:FUNCTION) "
            "RETURN count(__cy_p__) AS c"
        ])
        node_count = int(result[0][0]["c"])
        assert node_count > 0, "PROCEDURE/FUNCTION 노드가 생성되지 않았습니다"
        print(f"\n[RESULT - {STRATEGY_LABEL}] elapsed={elapsed:.2f}s events={event_count} files={len(source_files)} procedures={node_count}")
