"""
DeepAgents FSM 통합 테스트

플랜 기반 FSM 구현 테스트:
- FSM S0~S9 상태 전이
- 캐시 레이어 동작
- Evidence 관리
- Review 전이 조건

성공 기준: 후보 100개 중 60~80% 테이블 관계(from/writes) 생성
"""

from __future__ import annotations

import os
import sys
import asyncio
import tempfile
import shutil
from pathlib import Path
from typing import Dict, Any, List

import pytest

# 프로젝트 루트 경로 추가 (모듈 임포트 전에)
PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEEPAGENTS_PATH = PROJECT_ROOT / "analyzer" / "strategy" / "framework" / "deepagents"
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(DEEPAGENTS_PATH))


class TestCacheLayer:
    """캐시 레이어 테스트"""
    
    def test_hints_to_artifact_key_mybatis(self):
        """MyBatis 힌트 → artifact_key 변환"""
        from analyzer.strategy.framework.deepagents.support.cache_layer import hints_to_artifact_key
        
        # namespace + queryId
        hints = ["namespace=OrderMapper", "queryId=createOrder"]
        key = hints_to_artifact_key(hints)
        assert key == "mybatis:ordermapper#createorder"
        
        # queryId만 (file_path 보조키)
        hints = ["queryId=selectById"]
        key = hints_to_artifact_key(hints, "/src/OrderService.java")
        assert key == "mybatis:orderservice#selectbyid"
        
        # queryId만 (file_path 없음)
        key = hints_to_artifact_key(hints)
        assert key == "mybatis:unknown#selectbyid"
    
    def test_hints_to_artifact_key_ofbiz(self):
        """OFBiz 힌트 → artifact_key 변환"""
        from analyzer.strategy.framework.deepagents.support.cache_layer import hints_to_artifact_key
        
        # entity
        hints = ["entity=OrderHeader"]
        key = hints_to_artifact_key(hints)
        assert key == "ofbiz:entity:orderheader"
        
        # service
        hints = ["service=createOrder"]
        key = hints_to_artifact_key(hints)
        assert key == "ofbiz:service:createorder"
    
    def test_hints_to_artifact_key_config(self):
        """Config 힌트 → artifact_key 변환"""
        from analyzer.strategy.framework.deepagents.support.cache_layer import hints_to_artifact_key
        
        hints = ["configKey=spring.datasource.url"]
        key = hints_to_artifact_key(hints)
        assert key == "config:spring.datasource.url"
    
    def test_needs_namespace_lookup(self):
        """queryId-only 케이스 판별"""
        from analyzer.strategy.framework.deepagents.support.cache_layer import needs_namespace_lookup
        
        # namespace 확정됨
        assert not needs_namespace_lookup("mybatis:ordermapper#createorder")
        
        # unknown → 확정 필요
        assert needs_namespace_lookup("mybatis:unknown#createorder")
        
        # file_path 기반 → 확정 필요 (mapper로 끝나지 않음)
        assert needs_namespace_lookup("mybatis:orderservice#selectbyid")
        
        # OFBiz → 확정 불필요
        assert not needs_namespace_lookup("ofbiz:entity:orderheader")
    
    def test_cache_layer_set_get(self):
        """캐시 레이어 저장/조회"""
        from analyzer.strategy.framework.deepagents.support.cache_layer import CacheLayer
        
        with tempfile.TemporaryDirectory() as tmpdir:
            cache = CacheLayer(tmpdir)
            
            # 저장
            key = "mybatis:ordermapper#createorder"
            data = {"tables": [{"name": "ORDER_HEADER", "operation": "write"}]}
            cache.set(key, data)
            
            # 조회 (인메모리)
            entry, source = cache.get(key)
            assert source == "memory"
            assert entry.data == data
            
            # 인메모리 클리어 후 파일에서 조회
            cache.clear_memory()
            entry, source = cache.get(key)
            assert source == "file"
            assert entry.data == data


class TestEvidenceManager:
    """Evidence 관리자 테스트"""
    
    def test_load_or_create(self):
        """Evidence 로드/생성"""
        from analyzer.strategy.framework.deepagents.data.evidence_manager import EvidenceManager
        
        with tempfile.TemporaryDirectory() as tmpdir:
            mgr = EvidenceManager(tmpdir, run_id="test_run")
            
            # 새로 생성
            evidence = mgr.load_or_create("element_1", "mybatis:test#query", ["hint1"])
            assert evidence.element_id == "element_1"
            assert evidence.attempts == 1  # 처리 시작 시 +1
            assert evidence.status == "working"
            
            # 다시 로드 (attempts +1)
            evidence2 = mgr.load_or_create("element_1")
            assert evidence2.attempts == 2
    
    def test_merge_table(self):
        """테이블 정보 병합"""
        from analyzer.strategy.framework.deepagents.data.evidence_manager import EvidenceManager
        
        with tempfile.TemporaryDirectory() as tmpdir:
            mgr = EvidenceManager(tmpdir, run_id="test_run")
            evidence = mgr.load_or_create("element_1")
            
            # 첫 번째 테이블 추가
            added = mgr.merge_table(evidence, "ORDER_HEADER", "write", 0.8)
            assert added
            assert len(evidence.tables) == 1
            
            # 같은 테이블, 더 높은 confidence → 업데이트
            updated = mgr.merge_table(evidence, "ORDER_HEADER", "write", 0.9)
            assert updated
            assert evidence.tables[0]["confidence"] == 0.9
            
            # 같은 테이블, 더 낮은 confidence → 무시
            ignored = mgr.merge_table(evidence, "ORDER_HEADER", "read", 0.5)
            assert not ignored
    
    def test_is_write_conflict(self):
        """write 충돌 검사"""
        from analyzer.strategy.framework.deepagents.data.evidence_manager import EvidenceManager
        
        with tempfile.TemporaryDirectory() as tmpdir:
            mgr = EvidenceManager(tmpdir, run_id="test_run")
            evidence = mgr.load_or_create("element_1")
            
            # write 1개 → 충돌 아님
            mgr.merge_table(evidence, "ORDER_HEADER", "write", 0.9, is_primary=True)
            assert not mgr.is_write_conflict(evidence)
            
            # write 2개 (primary) → 충돌
            mgr.merge_table(evidence, "ORDER_ITEM", "write", 0.9, is_primary=True)
            assert mgr.is_write_conflict(evidence)
    
    def test_status_transitions(self):
        """상태 전이"""
        from analyzer.strategy.framework.deepagents.data.evidence_manager import EvidenceManager
        
        with tempfile.TemporaryDirectory() as tmpdir:
            mgr = EvidenceManager(tmpdir, run_id="test_run")
            evidence = mgr.load_or_create("element_1")
            
            # working → success
            mgr.set_status_success(evidence)
            assert evidence.status == "success"
            
            # 새로 생성 후 review
            evidence2 = mgr.load_or_create("element_2")
            mgr.set_status_review(evidence2, "tool_limit")
            assert evidence2.status == "review"
            assert evidence2.status_reason == "tool_limit"


class TestRuntimePolicy:
    """런타임 정책 테스트"""
    
    def test_validate_slice_limit(self):
        """슬라이스 limit 검증"""
        from analyzer.strategy.framework.deepagents.support.runtime_policy import RuntimePolicy
        
        policy = RuntimePolicy()
        
        # 30KB 이하 → 그대로
        assert policy.validate_slice_limit(10000) == 10000
        
        # 30KB 초과 → 강제
        assert policy.validate_slice_limit(50000) == 30000
    
    def test_should_review(self):
        """리뷰 전이 조건"""
        from analyzer.strategy.framework.deepagents.support.runtime_policy import RuntimePolicy
        from analyzer.strategy.framework.deepagents.data.evidence_manager import EvidenceData
        
        policy = RuntimePolicy()
        
        # tool_limit
        evidence = EvidenceData(element_id="test", tool_call_count=10)
        should, reason = policy.should_review(evidence)
        assert should
        assert reason == "tool_limit"
        
        # search_fail
        evidence = EvidenceData(element_id="test", search_fail_count=2)
        should, reason = policy.should_review(evidence)
        assert should
        assert reason == "search_fail"
        
        # retry_exhausted
        evidence = EvidenceData(element_id="test", attempts=2, status="working")
        should, reason = policy.should_review(evidence)
        assert should
        assert reason == "retry_exhausted"
        
        # 조건 미충족
        evidence = EvidenceData(element_id="test", tool_call_count=5, attempts=1)
        should, reason = policy.should_review(evidence)
        assert not should
    
    def test_should_apply_writes(self):
        """writes 적용 조건"""
        from analyzer.strategy.framework.deepagents.support.runtime_policy import RuntimePolicy
        
        policy = RuntimePolicy()
        
        # confidence >= 0.8 → writes
        assert policy.should_apply_writes(0.8)
        assert policy.should_apply_writes(0.9)
        
        # confidence < 0.8 → from
        assert not policy.should_apply_writes(0.7)
        assert not policy.should_apply_writes(0.5)


class TestChunking:
    """청킹 로직 테스트"""
    
    def test_build_chunks_by_file(self):
        """파일 기준 청크 분할"""
        from analyzer.strategy.framework.deepagents.helpers.chunking import ChunkBuilder
        
        builder = ChunkBuilder(max_tokens=1000, max_line_distance=300)
        
        candidates = [
            {"element_id": "e1", "file_path": "/src/A.java", "start_line": 10, "end_line": 20, "token": 100, "external_hints": [], "confidence": 0.8, "db_suspect": True},
            {"element_id": "e2", "file_path": "/src/A.java", "start_line": 30, "end_line": 40, "token": 100, "external_hints": [], "confidence": 0.8, "db_suspect": True},
            {"element_id": "e3", "file_path": "/src/B.java", "start_line": 10, "end_line": 20, "token": 100, "external_hints": [], "confidence": 0.8, "db_suspect": True},
        ]
        
        chunks = builder.build_chunks(candidates)
        
        # 파일별로 분리
        assert len(chunks) == 2
        
        # A.java 청크
        a_chunk = [c for c in chunks if c.file_path == "/src/A.java"][0]
        assert len(a_chunk.candidates) == 2
        
        # B.java 청크
        b_chunk = [c for c in chunks if c.file_path == "/src/B.java"][0]
        assert len(b_chunk.candidates) == 1
    
    def test_build_chunks_token_limit(self):
        """토큰 제한 청크 분할"""
        from analyzer.strategy.framework.deepagents.helpers.chunking import ChunkBuilder
        
        builder = ChunkBuilder(max_tokens=150, max_line_distance=300)
        
        candidates = [
            {"element_id": "e1", "file_path": "/src/A.java", "start_line": 10, "end_line": 20, "token": 100, "external_hints": [], "confidence": 0.8, "db_suspect": True},
            {"element_id": "e2", "file_path": "/src/A.java", "start_line": 30, "end_line": 40, "token": 100, "external_hints": [], "confidence": 0.8, "db_suspect": True},
        ]
        
        chunks = builder.build_chunks(candidates)
        
        # 토큰 초과로 분리
        assert len(chunks) == 2
    
    def test_build_chunks_line_distance(self):
        """라인 거리 청크 분할"""
        from analyzer.strategy.framework.deepagents.helpers.chunking import ChunkBuilder
        
        builder = ChunkBuilder(max_tokens=1000, max_line_distance=50)
        
        candidates = [
            {"element_id": "e1", "file_path": "/src/A.java", "start_line": 10, "end_line": 20, "token": 100, "external_hints": [], "confidence": 0.8, "db_suspect": True},
            {"element_id": "e2", "file_path": "/src/A.java", "start_line": 100, "end_line": 110, "token": 100, "external_hints": [], "confidence": 0.8, "db_suspect": True},  # 거리 80
        ]
        
        chunks = builder.build_chunks(candidates)
        
        # 라인 거리 초과로 분리
        assert len(chunks) == 2


class TestDocsTools:
    """Docs 도구 테스트"""
    
    def test_docs_search_scope(self):
        """docs_search scope 제한"""
        sys.path.insert(0, str(DEEPAGENTS_PATH / "tools"))
        from docs_tools import SEARCH_SCOPES
        
        # scope 확인
        assert "mybatis" in SEARCH_SCOPES
        assert "ofbiz_entity" in SEARCH_SCOPES
        assert SEARCH_SCOPES["mybatis"] == "**/mapper/*.xml"
        assert SEARCH_SCOPES["ofbiz_entity"] == "**/entitydef/*.xml"
    
    def test_docs_slice_limit(self):
        """docs_slice 30KB 제한"""
        sys.path.insert(0, str(DEEPAGENTS_PATH / "tools"))
        from docs_tools import DocsTools
        
        with tempfile.TemporaryDirectory() as tmpdir:
            tools = DocsTools(tmpdir)
            
            # 제한 확인
            assert tools.MAX_SLICE_LIMIT == 30000


class TestMinimalLock:
    """최소 락 테스트"""
    
    @pytest.mark.asyncio
    async def test_acquire_release(self):
        """락 획득/해제"""
        from analyzer.strategy.framework.deepagents.support.minimal_lock import MinimalLock
        
        lock = MinimalLock()
        
        # 획득
        acquired = await lock.acquire("table:ORDER_HEADER", "owner1")
        assert acquired
        assert lock.is_locked("table:ORDER_HEADER")
        
        # 해제
        lock.release("table:ORDER_HEADER")
        assert not lock.is_locked("table:ORDER_HEADER")
    
    @pytest.mark.asyncio
    async def test_context_manager(self):
        """컨텍스트 매니저"""
        from analyzer.strategy.framework.deepagents.support.minimal_lock import MinimalLock
        
        lock = MinimalLock()
        
        async with lock.lock("table:ORDER_HEADER"):
            assert lock.is_locked("table:ORDER_HEADER")
        
        assert not lock.is_locked("table:ORDER_HEADER")


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

