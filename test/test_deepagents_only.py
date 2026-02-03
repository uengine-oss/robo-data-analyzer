"""
DeepAgents 런타임 해석만 독립 실행하는 테스트

이미 Neo4j에 그래프가 있는 경우, AST 분석 없이 DeepAgents 런타임 해석만 실행합니다.

필수 조건:
- Neo4j에 이미 resolution_candidate=true로 마킹된 노드가 있어야 함
- external_hints, missing_bindings, confidence 속성이 모두 있어야 함
- data/docs/ 폴더에 외부 설정 파일이 있어야 함 (선택)

사용법:
    # 기본 실행
    python -m pytest test/test_deepagents_only.py -v
    
    # 옵션 지정
    python -m pytest test/test_deepagents_only.py::test_deepagents_only \
        -v --max-candidates=100 --max-workers=4
    
    # 특정 아티팩트 타입만
    python -m pytest test/test_deepagents_only.py::test_deepagents_only \
        -v --artifact-types=mybatis_query,ofbiz_entity
"""

import os
import sys
import asyncio
from pathlib import Path

import pytest
from neo4j import AsyncGraphDatabase

# 프로젝트 루트 경로 추가
PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from config.settings import settings
from analyzer.strategy.framework.deepagents.phase.runtime_resolution_phase import (
    run_runtime_resolution_phase,
    PhaseResult
)


@pytest.fixture
def neo4j_driver():
    """Neo4j 드라이버 생성"""
    neo4j_config = settings.neo4j
    driver = AsyncGraphDatabase.driver(
        neo4j_config.uri,
        auth=(neo4j_config.user, neo4j_config.password)
    )
    yield driver
    driver.close()


@pytest.mark.asyncio
async def test_deepagents_only(
    neo4j_driver,
    max_candidates: int = 1000,
    max_workers: int = 4,
    artifact_types: str = None,
    no_hitl: bool = False,
):
    """DeepAgents 런타임 해석만 실행
    
    Args:
        neo4j_driver: Neo4j 드라이버 (fixture)
        max_candidates: 최대 처리할 후보 수 (기본값: 1000)
        max_workers: 최대 워커 수 (기본값: 4)
        artifact_types: 처리할 아티팩트 타입 (쉼표로 구분, 예: "mybatis_query,ofbiz_entity")
        no_hitl: Human-in-the-Loop 비활성화
    """
    # 경로 설정
    base_dir = Path(settings.path.data_dir) if hasattr(settings.path, 'data_dir') else Path("data")
    docs_root = str(base_dir / "docs")
    ddl_root = str(base_dir / "ddl")
    runs_root = str(base_dir / "runs")
    
    # 아티팩트 타입 파싱
    artifact_types_list = None
    if artifact_types:
        artifact_types_list = [t.strip() for t in artifact_types.split(",")]
    
    print("=" * 60)
    print("DeepAgents 런타임 해석 테스트")
    print("=" * 60)
    print(f"Neo4j URI: {settings.neo4j.uri}")
    print(f"Docs Root: {docs_root}")
    print(f"DDL Root: {ddl_root}")
    print(f"Runs Root: {runs_root}")
    print(f"Max Candidates: {max_candidates}")
    print(f"Max Workers: {max_workers}")
    print(f"Enable HITL: {not no_hitl}")
    if artifact_types_list:
        print(f"Artifact Types: {', '.join(artifact_types_list)}")
    print("=" * 60)
    print()
    
    # 후보 수 확인
    async with neo4j_driver.session() as session:
        check_query = """
        MATCH (n)
        WHERE n.resolution_candidate = true
          AND n.external_hints IS NOT NULL 
          AND size(n.external_hints) > 0
          AND n.missing_bindings IS NOT NULL
          AND n.confidence IS NOT NULL
        RETURN count(n) AS candidate_count
        """
        result = await session.run(check_query)
        record = await result.single()
        candidate_count = record["candidate_count"] if record else 0
        
        print(f"📋 Neo4j에서 발견된 후보 수: {candidate_count}")
        print()
        
        if candidate_count == 0:
            pytest.skip(
                "Neo4j에 3가지 값(external_hints, missing_bindings, confidence)이 모두 있는 "
                "resolution_candidate=true 노드가 없습니다."
            )
    
    # DeepAgents Phase 실행
    result: PhaseResult = await run_runtime_resolution_phase(
        neo4j_driver=neo4j_driver,
        docs_root=docs_root,
        ddl_root=ddl_root,
        runs_root=runs_root,
        artifact_types=artifact_types_list,
        max_workers=max_workers,
        max_candidates=max_candidates,
        enable_hitl=not no_hitl,
    )
    
    # 결과 검증
    print()
    print("=" * 60)
    print("실행 완료")
    print("=" * 60)
    print(f"처리된 후보: {result.processed}")
    print(f"성공: {result.success}")
    print(f"실패: {result.failed}")
    print(f"보류 검토: {result.pending_review}")
    print(f"성공률: {result.success_rate * 100:.1f}%")
    print()
    
    # 기본 검증
    assert result.processed > 0, "처리된 후보가 없습니다"
    assert result.processed <= max_candidates, f"처리된 후보 수가 제한을 초과했습니다 ({result.processed} > {max_candidates})"
    
    # 에러 출력 (최대 10개)
    if result.errors:
        print(f"⚠️ 에러 ({len(result.errors)}개):")
        for error in result.errors[:10]:
            print(f"  - {error}")
        if len(result.errors) > 10:
            print(f"  ... 외 {len(result.errors) - 10}개")
        print()
    
    # 성공률이 너무 낮으면 경고
    if result.success_rate < 0.3:
        print(f"⚠️ 경고: 성공률이 낮습니다 ({result.success_rate * 100:.1f}%)")
    
    print("=" * 60)


if __name__ == "__main__":
    # 커맨드라인에서 직접 실행할 때
    import argparse
    
    parser = argparse.ArgumentParser(description="DeepAgents 런타임 해석만 실행")
    parser.add_argument("--max-candidates", type=int, default=1000, help="최대 처리할 후보 수")
    parser.add_argument("--max-workers", type=int, default=4, help="최대 워커 수")
    parser.add_argument("--artifact-types", type=str, help="처리할 아티팩트 타입 (쉼표로 구분)")
    parser.add_argument("--no-hitl", action="store_true", help="Human-in-the-Loop 비활성화")
    
    args = parser.parse_args()
    
    # Neo4j 드라이버 생성
    neo4j_config = settings.neo4j
    driver = AsyncGraphDatabase.driver(
        neo4j_config.uri,
        auth=(neo4j_config.user, neo4j_config.password)
    )
    
    try:
        asyncio.run(test_deepagents_only(
            neo4j_driver=driver,
            max_candidates=args.max_candidates,
            max_workers=args.max_workers,
            artifact_types=args.artifact_types,
            no_hitl=args.no_hitl,
        ))
    finally:
        asyncio.run(driver.close())

