"""Nontarget 모듈 테스트

chunker, linker, workflow, deepagents_client 등을 테스트합니다.
"""

import asyncio
import pytest
import os
from typing import Dict, Any, List

# 테스트 대상 모듈
from analyzer.strategy.framework.nontarget.chunker import (
    extract_chunks_for_analysis,
    chunk_by_rules,
    _chunk_by_top_level_key,
    _chunk_by_delimiter,
    _chunk_by_indent,
)
from analyzer.strategy.framework.nontarget.workflow import (
    NontargetState,
    RuntimeCtx,
    make_node_functions,
)
from analyzer.strategy.framework.nontarget.deepagents_client import (
    DeepAgentsClient,
    _extract_agent_output,
    _parse_json_from_output,
)
from analyzer.strategy.framework.nontarget.correction import (
    ACorrection,
    BCorrection,
    CCorrection,
)


class TestChunker:
    """Chunker 모듈 테스트"""
    
    def test_extract_chunks_for_analysis(self):
        """전체 파일 순차 분석 테스트"""
        file_path = "rules/deepagents/chunker_agent.yaml"
        chunks = extract_chunks_for_analysis(file_path, chunk_lines=50)
        
        assert len(chunks) > 0, "청크가 생성되어야 함"
        
        # 모든 청크가 필수 필드를 가지는지 확인
        for chunk in chunks:
            assert "chunk_idx" in chunk
            assert "line_start" in chunk
            assert "line_end" in chunk
            assert "text" in chunk
            assert "is_last" in chunk
            assert "progress" in chunk
        
        # 마지막 청크만 is_last=True
        assert chunks[-1]["is_last"] == True
        for chunk in chunks[:-1]:
            assert chunk["is_last"] == False
        
        print(f"[OK] extract_chunks_for_analysis: {len(chunks)} chunks")
    
    def test_chunk_by_rules_top_level_key(self):
        """top_level_key 경계 블록화 테스트"""
        file_path = "rules/deepagents/chunker_agent.yaml"
        rules = {
            "file_type": "yaml",
            "boundary": "top_level_key",
            "max_block_lines": 100,
            "key_patterns": [r"^[a-zA-Z0-9_-]+:"],
        }
        
        blocks = chunk_by_rules(file_path, rules)
        
        assert len(blocks) > 0, "블록이 생성되어야 함"
        
        for block in blocks:
            assert "start_line" in block
            assert "end_line" in block
            assert "code" in block
            assert block["end_line"] >= block["start_line"]
        
        print(f"[OK] chunk_by_rules (top_level_key): {len(blocks)} blocks")
    
    def test_chunk_by_rules_line(self):
        """line 경계 블록화 테스트"""
        file_path = "rules/deepagents/chunker_agent.yaml"
        rules = {
            "file_type": "yaml",
            "boundary": "line",
            "max_block_lines": 50,
        }
        
        blocks = chunk_by_rules(file_path, rules)
        
        assert len(blocks) > 0, "블록이 생성되어야 함"
        
        # max_block_lines 이하여야 함 (마지막 제외)
        for block in blocks[:-1]:
            block_size = block["end_line"] - block["start_line"] + 1
            assert block_size <= 50, f"블록 크기가 max_block_lines 이하여야 함: {block_size}"
        
        print(f"[OK] chunk_by_rules (line): {len(blocks)} blocks")
    
    def test_chunk_by_rules_invalid_boundary_raises(self):
        """잘못된 boundary 타입 시 RuntimeError 발생 테스트"""
        file_path = "rules/deepagents/chunker_agent.yaml"
        rules = {
            "file_type": "yaml",
            "boundary": "invalid_boundary_type",
            "max_block_lines": 50,
        }
        
        with pytest.raises(RuntimeError, match="알 수 없는 boundary 타입"):
            chunk_by_rules(file_path, rules)
        
        print("[OK] chunk_by_rules (invalid boundary): RuntimeError raised")
    
    def test_chunk_by_rules_file_not_found_raises(self):
        """존재하지 않는 파일 시 RuntimeError 발생 테스트"""
        rules = {
            "boundary": "delimiter",
            "max_block_lines": 50,
            "delimiter": "---",
        }
        
        with pytest.raises(RuntimeError, match="파일 읽기 실패"):
            chunk_by_rules("nonexistent_file.yaml", rules)
        
        print("[OK] chunk_by_rules (file not found): RuntimeError raised")


class TestDeepAgentsClient:
    """DeepAgentsClient 테스트"""
    
    def test_singleton(self):
        """싱글톤 패턴 테스트"""
        client1 = DeepAgentsClient()
        client2 = DeepAgentsClient()
        
        assert client1 is client2, "싱글톤이어야 함"
        print("[OK] DeepAgentsClient singleton")
    
    def test_prompts_loaded(self):
        """프롬프트 로드 테스트"""
        client = DeepAgentsClient()
        
        expected_prompts = ["chunker_agent", "summarizer_agent", "linker_agent", "main_agent"]
        
        for name in expected_prompts:
            prompt = client.get_prompt(name)
            assert prompt, f"{name} 프롬프트가 로드되어야 함"
            assert len(prompt) > 100, f"{name} 프롬프트가 충분히 길어야 함"
        
        print(f"[OK] DeepAgentsClient prompts loaded: {list(client._prompts.keys())}")
    
    def test_parse_json_from_output(self):
        """JSON 파싱 테스트"""
        # 코드펜스 내 JSON
        output1 = """
        Some text
        ```json
        {"key": "value", "num": 123}
        ```
        More text
        """
        result1 = _parse_json_from_output(output1)
        assert result1 == {"key": "value", "num": 123}
        
        # 원시 JSON
        output2 = '{"key": "value"}'
        result2 = _parse_json_from_output(output2)
        assert result2 == {"key": "value"}
        
        print("[OK] _parse_json_from_output")
    
    def test_parse_json_invalid_raises(self):
        """잘못된 JSON 시 RuntimeError 발생 테스트"""
        output = "This is not JSON at all"
        
        with pytest.raises(RuntimeError, match="JSON 파싱 실패"):
            _parse_json_from_output(output)
        
        print("[OK] _parse_json_from_output (invalid): RuntimeError raised")


class TestCorrection:
    """Correction 모듈 테스트"""
    
    def test_a_correction_detect_too_many_blocks(self):
        """A 오류 감지: 블록 수 초과"""
        blocks = [{"start_line": i*10, "end_line": (i+1)*10-1} for i in range(350)]
        
        is_error = ACorrection.detect("test.yaml", blocks, threshold_count=300)
        
        assert is_error == True, "350개 블록은 오류로 감지되어야 함"
        print("[OK] ACorrection.detect (too many blocks)")
    
    def test_a_correction_detect_normal(self):
        """A 오류 감지: 정상 블록"""
        blocks = [{"start_line": i*50, "end_line": (i+1)*50-1} for i in range(10)]
        
        is_error = ACorrection.detect("test.yaml", blocks, threshold_count=300)
        
        assert is_error == False, "10개 블록은 정상이어야 함"
        print("[OK] ACorrection.detect (normal)")
    
    def test_b_correction_detect_keywords_missing(self):
        """B 오류 감지: 기대 키워드 누락"""
        block = {"keywords": ["datasource", "mysql"], "kind": "config"}
        expected_keywords = ["datasource", "mysql", "password"]  # password 누락
        
        is_error = BCorrection.detect(block, expected_keywords)
        
        assert is_error == True, "기대 키워드 누락은 오류로 감지되어야 함"
        print("[OK] BCorrection.detect (keywords missing)")
    
    def test_b_correction_detect_normal(self):
        """B 오류 감지: 정상 Summary"""
        block = {"keywords": ["datasource", "mysql", "password"], "kind": "config"}
        expected_keywords = ["datasource", "mysql"]
        
        is_error = BCorrection.detect(block, expected_keywords)
        
        assert is_error == False, "모든 기대 키워드가 있으면 정상"
        print("[OK] BCorrection.detect (normal)")


class TestWorkflow:
    """Workflow 모듈 테스트"""
    
    def test_runtime_ctx_creation(self):
        """RuntimeCtx 생성 테스트"""
        ctx = RuntimeCtx(client=None, cypher_lock=None)
        
        assert ctx.client is None
        assert ctx.cypher_lock is None
        print("[OK] RuntimeCtx creation")
    
    def test_nontarget_state_type(self):
        """NontargetState 타입 테스트"""
        state: NontargetState = {
            "file_path": "test.yaml",
            "rules": None,
            "blocks": [],
            "summaries": [],
            "phase": "init",
            "error": None,
        }
        
        assert state["file_path"] == "test.yaml"
        assert state["phase"] == "init"
        print("[OK] NontargetState type")
    
    def test_make_node_functions(self):
        """make_node_functions 테스트"""
        ctx = RuntimeCtx(client=None, cypher_lock=None)
        node_funcs = make_node_functions(ctx)
        
        expected_funcs = ["load_rules", "sample", "chunk", "summarize", 
                         "upsert", "link", "save_run"]
        
        for func_name in expected_funcs:
            assert func_name in node_funcs, f"{func_name} 함수가 있어야 함"
        
        print(f"[OK] make_node_functions: {list(node_funcs.keys())}")


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])

