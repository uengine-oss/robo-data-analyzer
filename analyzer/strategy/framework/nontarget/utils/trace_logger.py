"""상세 트레이스 로거

파일별로 처리 과정의 모든 단계를 JSON 파일로 기록하고,
완료 시 마크다운 보고서를 생성합니다.

로그 구조:
logs/trace/
  {timestamp}__{file_name}/
    00_workflow_start.json
    01_load_rules.json
    ...
    REPORT.md
"""

import json
import os
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional


class TraceLogger:
    """파일별 상세 트레이스 로거"""
    
    def __init__(self, file_path: str, base_dir: str = "logs/trace"):
        """
        Args:
            file_path: 처리 대상 파일 경로
            base_dir: 트레이스 로그 기본 디렉토리
        """
        self.file_path = file_path
        self.file_name = Path(file_path).stem.replace(".", "_").replace("-", "_")
        self.start_time = datetime.now()
        self.timestamp = self.start_time.strftime("%Y-%m-%dT%H-%M-%S")
        
        # 트레이스 디렉토리 생성
        self.trace_dir = Path(base_dir) / f"{self.timestamp}__{self.file_name}"
        self.trace_dir.mkdir(parents=True, exist_ok=True)
        
        self.step_counter = 0
        self.steps: List[Dict[str, Any]] = []
        
        # 통계 수집용
        self.stats = {
            "tool_calls": {},
            "agent_calls": {},
            "tables_identified": [],
            "tables_created": [],
            "tables_updated": [],
            "refers_to_created": [],
            "memory_ops": [],
            "errors": [],
        }
    
    def _next_step_file(self, step_name: str) -> Path:
        """다음 단계 파일 경로 생성"""
        file_name = f"{self.step_counter:02d}_{step_name}.json"
        self.step_counter += 1
        return self.trace_dir / file_name
    
    def _write_step(self, step_name: str, data: Dict[str, Any]) -> None:
        """단계 로그 파일 저장"""
        file_path = self._next_step_file(step_name)
        record = {
            "timestamp": datetime.now().isoformat(),
            "step": step_name,
            "step_number": self.step_counter - 1,
            **data,
        }
        self.steps.append(record)
        
        with open(file_path, "w", encoding="utf-8") as f:
            json.dump(record, f, ensure_ascii=False, indent=2)
    
    # ========================================================================
    # 워크플로우 로그
    # ========================================================================
    
    def log_workflow_start(self, file_path: str, initial_state: Dict[str, Any]) -> None:
        """워크플로우 시작 로그"""
        self._write_step("workflow_start", {
            "file_path": file_path,
            "initial_state": {k: v for k, v in initial_state.items() if k != "client"},
        })
    
    def log_workflow_node(self, node_name: str, state: Dict[str, Any], duration_ms: int = 0) -> None:
        """워크플로우 노드 실행 로그"""
        # state에서 직렬화 불가능한 것 제외
        safe_state = {}
        for k, v in state.items():
            if k in ("client", "cypher_lock"):
                continue
            if k == "blocks":
                safe_state[k] = [
                    {
                        "start_line": b.get("start_line"),
                        "end_line": b.get("end_line"),
                        "id": b.get("id"),
                    }
                    for b in (v or [])
                ]
            elif k == "summaries":
                safe_state[k] = [
                    {
                        "kind": s.get("kind"),
                        "keywords": s.get("keywords", [])[:5],
                    }
                    for s in (v or [])
                ]
            else:
                safe_state[k] = v
        
        self._write_step(f"node_{node_name}", {
            "node": node_name,
            "state": safe_state,
            "duration_ms": duration_ms,
        })
    
    def log_workflow_complete(self, final_state: Dict[str, Any], error: Optional[str] = None) -> None:
        """워크플로우 완료 로그"""
        end_time = datetime.now()
        duration_sec = (end_time - self.start_time).total_seconds()
        
        self._write_step("workflow_complete", {
            "status": "error" if error else "success",
            "error": error,
            "duration_sec": duration_sec,
            "blocks_count": len(final_state.get("blocks", [])),
            "summaries_count": len(final_state.get("summaries", [])),
        })
    
    # ========================================================================
    # 도구 호출 로그
    # ========================================================================
    
    def log_tool_call(
        self,
        tool_name: str,
        input_data: Dict[str, Any],
        output_data: Any,
        duration_ms: int,
        context: str = "",
        conclusion: str = "",
        error: Optional[str] = None,
        truncate: bool = False,
    ) -> None:
        """도구 호출 로그
        
        Args:
            tool_name: 도구 이름
            input_data: 입력 데이터 (전체 저장)
            output_data: 출력 데이터 (전체 저장)
            duration_ms: 소요 시간 (밀리초)
            context: 왜 이 도구를 호출했는지 설명
            conclusion: 결과에서 얻은 결론
            error: 오류 메시지 (있을 경우)
            truncate: True면 출력 데이터 2000자 제한 (기본: False)
        """
        # 통계 업데이트
        if tool_name not in self.stats["tool_calls"]:
            self.stats["tool_calls"][tool_name] = {"count": 0, "total_ms": 0, "errors": 0}
        self.stats["tool_calls"][tool_name]["count"] += 1
        self.stats["tool_calls"][tool_name]["total_ms"] += duration_ms
        if error:
            self.stats["tool_calls"][tool_name]["errors"] += 1
        
        # 출력 데이터 처리
        output_str = str(output_data)
        if truncate and len(output_str) > 2000:
            output_str = output_str[:2000] + "... (truncated)"
        
        self._write_step(f"tool_{tool_name}", {
            "tool": tool_name,
            "context": context,  # 호출 이유
            "input": input_data,  # 전체 저장
            "output": output_str,  # 전체 저장 (truncate=False 기본)
            "conclusion": conclusion,  # 결과에서 얻은 결론
            "duration_ms": duration_ms,
            "error": error,
        })
    
    # ========================================================================
    # 에이전트 로그
    # ========================================================================
    
    def log_agent_call(
        self,
        agent_name: str,
        prompt: str,
        response: str,
        tokens_used: int = 0,
        duration_ms: int = 0,
        purpose: str = "",
        input_context: str = "",
        parsed_result: Optional[Dict[str, Any]] = None,
        reasoning: str = "",
        truncate: bool = False,
    ) -> None:
        """에이전트 호출 로그
        
        Args:
            agent_name: 에이전트 이름
            prompt: 전체 프롬프트
            response: 전체 응답
            tokens_used: 사용된 토큰 수
            duration_ms: 소요 시간 (밀리초)
            purpose: 호출 목적 (왜 이 에이전트를 호출했는지)
            input_context: 에이전트에게 제공한 컨텍스트 요약
            parsed_result: 파싱된 결과 (JSON 객체)
            reasoning: 에이전트의 판단 근거 (응답에서 추출)
            truncate: True면 프롬프트/응답 3000자 제한 (기본: False)
        """
        # 통계 업데이트
        if agent_name not in self.stats["agent_calls"]:
            self.stats["agent_calls"][agent_name] = {"count": 0, "total_tokens": 0, "total_ms": 0}
        self.stats["agent_calls"][agent_name]["count"] += 1
        self.stats["agent_calls"][agent_name]["total_tokens"] += tokens_used
        self.stats["agent_calls"][agent_name]["total_ms"] += duration_ms
        
        # 프롬프트/응답 처리
        prompt_output = prompt[:3000] + "..." if truncate and len(prompt) > 3000 else prompt
        response_output = response[:3000] + "..." if truncate and len(response) > 3000 else response
        
        self._write_step(f"agent_{agent_name}", {
            "agent": agent_name,
            "purpose": purpose,  # 호출 목적
            "input_context": input_context,  # 제공된 컨텍스트 요약
            "prompt": prompt_output,  # 전체 프롬프트
            "response": response_output,  # 전체 응답
            "parsed_result": parsed_result,  # 파싱된 결과
            "reasoning": reasoning,  # 판단 근거
            "tokens_used": tokens_used,
            "duration_ms": duration_ms,
        })
    
    # ========================================================================
    # 테이블 처리 로그
    # ========================================================================
    
    def log_table_identified(self, tables: List[Dict[str, Any]], source_block_id: str) -> None:
        """테이블 식별 로그"""
        self.stats["tables_identified"].extend([t.get("name") for t in tables])
        
        self._write_step("table_identified", {
            "tables": tables,
            "source_block_id": source_block_id,
            "count": len(tables),
        })
    
    def log_table_created(
        self,
        table_name: str,
        schema: str,
        columns: List[Dict[str, Any]],
        block_id: str,
    ) -> None:
        """신규 테이블 생성 로그"""
        self.stats["tables_created"].append(table_name)
        
        self._write_step("table_created", {
            "action": "CREATE",
            "table_name": table_name,
            "schema": schema,
            "columns": columns,
            "source_block_id": block_id,
        })
    
    def log_table_updated(
        self,
        table_name: str,
        changes: Dict[str, Any],
        block_id: str,
    ) -> None:
        """기존 테이블 보강 로그"""
        self.stats["tables_updated"].append(table_name)
        
        self._write_step("table_updated", {
            "action": "UPDATE",
            "table_name": table_name,
            "changes": changes,
            "source_block_id": block_id,
        })
    
    # ========================================================================
    # REFERS_TO 연결 로그
    # ========================================================================
    
    def log_refers_to_created(
        self,
        source_id: str,
        source_type: str,
        target_id: str,
        target_type: str,
        role: str,
        confidence: float,
        evidence: str,
    ) -> None:
        """REFERS_TO 관계 생성 로그"""
        self.stats["refers_to_created"].append({
            "source": source_type,
            "target": target_type,
            "role": role,
        })
        
        self._write_step("refers_to_created", {
            "source_id": source_id,
            "source_type": source_type,
            "target_id": target_id,
            "target_type": target_type,
            "role": role,
            "confidence": confidence,
            "evidence": evidence,
        })
    
    # ========================================================================
    # 메모리 조작 로그
    # ========================================================================
    
    def log_memory_read(
        self, 
        path: str, 
        content_preview: str,
        extracted_info: Optional[Dict[str, Any]] = None,
        usage_purpose: str = "",
    ) -> None:
        """메모리 읽기 로그
        
        Args:
            path: 메모리 파일 경로
            content_preview: 내용 미리보기 (전체 내용도 가능)
            extracted_info: 읽은 내용에서 추출한 핵심 정보
            usage_purpose: 왜 이 메모리를 읽었는지
        """
        self.stats["memory_ops"].append({"op": "read", "path": path})
        
        self._write_step("memory_read", {
            "operation": "READ",
            "path": path,
            "content": content_preview,  # 전체 내용 저장
            "extracted_info": extracted_info,  # 추출된 핵심 정보
            "usage_purpose": usage_purpose,  # 읽은 목적
        })
    
    def log_memory_write(
        self, 
        path: str, 
        content: str,
        write_reason: str = "",
        key_data: Optional[Dict[str, Any]] = None,
    ) -> None:
        """메모리 쓰기 로그
        
        Args:
            path: 메모리 파일 경로
            content: 저장할 내용 (전체)
            write_reason: 왜 이 내용을 저장하는지
            key_data: 저장하는 핵심 데이터 요약
        """
        self.stats["memory_ops"].append({"op": "write", "path": path})
        
        self._write_step("memory_write", {
            "operation": "WRITE",
            "path": path,
            "content": content,  # 전체 내용 저장
            "write_reason": write_reason,  # 저장 이유
            "key_data": key_data,  # 핵심 데이터 요약
        })
    
    # ========================================================================
    # 청킹/요약 로그
    # ========================================================================
    
    def log_pattern_discovery(
        self,
        chunk_idx: int,
        total_chunks: int,
        line_start: int,
        line_end: int,
        pattern_memory: Dict[str, Any],
    ) -> None:
        """패턴 발견 로그 - 각 청크 분석 후 호출
        
        Args:
            chunk_idx: 현재 청크 인덱스
            total_chunks: 전체 청크 수
            line_start: 청크 시작 라인
            line_end: 청크 종료 라인
            pattern_memory: 축적된 패턴 메모리
        """
        self._write_step(f"pattern_discovery_{chunk_idx}", {
            "chunk_idx": chunk_idx,
            "total_chunks": total_chunks,
            "line_range": f"{line_start}-{line_end}",
            "discovered_patterns": pattern_memory.get("discovered_patterns", []),
            "confirmed_patterns": pattern_memory.get("confirmed_patterns", []),
            "rejected_patterns": pattern_memory.get("rejected_patterns", []),
            "key_patterns": pattern_memory.get("key_patterns", []),
            "boundary_type": pattern_memory.get("boundary_type", "line"),
            "confidence": pattern_memory.get("confidence", 0.5),
            "block_roles": pattern_memory.get("block_roles", []),
            "analyzed_lines": pattern_memory.get("analyzed_lines", 0),
            "total_lines": pattern_memory.get("total_lines", 0),
        })
    
    def log_pattern_analysis_step(
        self,
        chunk_idx: int,
        chunk_content: str,
        previous_patterns: List[str],
        agent_prompt: str,
        agent_response: str,
        new_discoveries: Dict[str, Any],
        reasoning: str,
        line_start: int = 0,
        line_end: int = 0,
    ) -> None:
        """패턴 분석 단계 상세 로그 - 청크별 에이전트 분석 과정 기록
        
        Args:
            chunk_idx: 현재 청크 인덱스
            chunk_content: 분석 중인 청크 내용 (전체 저장)
            previous_patterns: 이전까지 발견된 패턴 목록
            agent_prompt: 에이전트에게 보낸 프롬프트 전체
            agent_response: 에이전트 응답 전체
            new_discoveries: 새로 발견된 것들 (패턴, 블록 역할 등)
            reasoning: 에이전트의 판단 근거
            line_start: 청크 시작 라인
            line_end: 청크 종료 라인
        """
        self._write_step(f"pattern_analysis_step_{chunk_idx}", {
            "chunk_idx": chunk_idx,
            "line_range": f"{line_start}-{line_end}",
            "chunk_content": chunk_content,  # 전체 저장 (truncate 없음)
            "previous_patterns": previous_patterns,
            "agent_prompt": agent_prompt,  # 전체 저장
            "agent_response": agent_response,  # 전체 저장
            "new_discoveries": new_discoveries,
            "reasoning": reasoning,
        })
    
    def log_chunk_result(self, blocks: List[Dict[str, Any]], rules: Dict[str, Any]) -> None:
        """청킹 결과 로그"""
        self._write_step("chunk_result", {
            "rules": rules,
            "blocks_count": len(blocks),
            "blocks": [
                {
                    "index": i,
                    "start_line": b.get("start_line"),
                    "end_line": b.get("end_line"),
                    "lines": b.get("end_line", 0) - b.get("start_line", 0) + 1,
                }
                for i, b in enumerate(blocks)
            ],
        })
    
    def log_summarize_result(self, block_index: int, summary: Dict[str, Any]) -> None:
        """요약 결과 로그"""
        self._write_step(f"summarize_block_{block_index}", {
            "block_index": block_index,
            "kind": summary.get("kind"),
            "keywords": summary.get("keywords", []),
            "summary": summary.get("summary", ""),
            "evidence": summary.get("evidence", ""),
            "tables": summary.get("tables", []),
        })
    
    # ========================================================================
    # 오류 로그
    # ========================================================================
    
    # ========================================================================
    # TODO 추적 로그 (아키텍처 B)
    # ========================================================================
    
    def log_todo_design(self, todos: List[Dict[str, Any]], agent_name: str = "main_agent") -> None:
        """TODO 설계 로그
        
        Args:
            todos: TODO 목록 [{"id": "...", "status": "pending|in_progress|completed", "content": "..."}, ...]
            agent_name: TODO를 설계한 에이전트 이름
        """
        self._write_step("todo_design", {
            "agent": agent_name,
            "todos": todos,
            "total_count": len(todos),
            "pending_count": len([t for t in todos if t.get("status") == "pending"]),
            "in_progress_count": len([t for t in todos if t.get("status") == "in_progress"]),
            "completed_count": len([t for t in todos if t.get("status") == "completed"]),
        })
    
    def log_todo_progress(
        self,
        todo_id: str,
        old_status: str,
        new_status: str,
        agent_name: str = "main_agent",
        notes: str = "",
    ) -> None:
        """TODO 진행 상태 변경 로그
        
        Args:
            todo_id: TODO ID
            old_status: 이전 상태
            new_status: 새 상태
            agent_name: 상태를 변경한 에이전트 이름
            notes: 변경 이유 또는 메모
        """
        self._write_step(f"todo_progress_{todo_id}", {
            "agent": agent_name,
            "todo_id": todo_id,
            "status_change": f"{old_status} → {new_status}",
            "old_status": old_status,
            "new_status": new_status,
            "notes": notes,
        })
    
    # ========================================================================
    # 에이전트 위임 로그 (아키텍처 B)
    # ========================================================================
    
    def log_subagent_delegation(
        self,
        delegator: str,
        subagent: str,
        task_description: str,
        input_summary: Optional[Dict[str, Any]] = None,
        result_summary: Optional[Dict[str, Any]] = None,
        duration_ms: int = 0,
    ) -> None:
        """서브에이전트 위임 로그 (아키텍처 B)
        
        Args:
            delegator: 위임한 에이전트 (보통 "main_agent")
            subagent: 위임받은 서브에이전트 (chunker, summarizer, linker, description)
            task_description: 위임한 작업 설명
            input_summary: 입력 요약 (파일 경로, 블록 수 등)
            result_summary: 결과 요약 (생성된 블록 수, 발견된 테이블 수 등)
            duration_ms: 소요 시간
        """
        self._write_step(f"subagent_delegation_{subagent}", {
            "delegator": delegator,
            "subagent": subagent,
            "task": task_description,
            "input_summary": input_summary,
            "result_summary": result_summary,
            "duration_ms": duration_ms,
        })
    
    def log_relation_creation(
        self,
        relation_type: str,
        source_id: str,
        target_id: str,
        confidence: Optional[float] = None,
        evidence: Optional[str] = None,
        agent_name: str = "main_agent",
    ) -> None:
        """관계 생성 로그 (아키텍처 B: 메인에이전트가 생성)
        
        Args:
            relation_type: REFERS_TO, FROM, WRITES, FK_TO_TABLE, FK_TO
            source_id: 소스 노드 ID
            target_id: 타겟 노드 ID
            confidence: 신뢰도 (REFERS_TO의 경우)
            evidence: 근거
            agent_name: 관계를 생성한 에이전트 (보통 "main_agent")
        """
        self._write_step(f"relation_created_{relation_type}", {
            "agent": agent_name,
            "relation_type": relation_type,
            "source_id": source_id,
            "target_id": target_id,
            "confidence": confidence,
            "evidence": evidence,
        })
    
    def log_error(self, phase: str, error: str, detail: Optional[Dict[str, Any]] = None) -> None:
        """오류 로그"""
        self.stats["errors"].append({"phase": phase, "error": error})
        
        self._write_step("error", {
            "phase": phase,
            "error": error,
            "detail": detail,
        })
    
    def log_phase_start(self, phase_name: str, description: str = "") -> None:
        """페이즈 시작 마커
        
        Args:
            phase_name: 페이즈 이름 (예: load_rules, sample, chunk, summarize)
            description: 페이즈 설명
        """
        self._write_step(f"phase_start_{phase_name}", {
            "phase": phase_name,
            "action": "START",
            "description": description,
        })
    
    def log_phase_conclusion(
        self,
        phase_name: str,
        inputs: Dict[str, Any],
        outputs: Dict[str, Any],
        decisions_made: List[str],
        next_step_hint: str = "",
    ) -> None:
        """페이즈 결론 로그
        
        Args:
            phase_name: 페이즈 이름
            inputs: 이 페이즈의 입력 데이터
            outputs: 이 페이즈의 출력 데이터
            decisions_made: 내린 결정들의 목록
            next_step_hint: 다음 단계에 전달할 힌트
        """
        self._write_step(f"phase_conclusion_{phase_name}", {
            "phase": phase_name,
            "action": "CONCLUSION",
            "inputs": inputs,
            "outputs": outputs,
            "decisions_made": decisions_made,
            "next_step_hint": next_step_hint,
        })
    
    # ========================================================================
    # 보고서 생성 헬퍼 함수
    # ========================================================================
    
    # 메모리 경로 → 역할 매핑
    MEMORY_ROLE_MAP = {
        "overview.md": "프로젝트 전체 구조 및 기술 스택 파악",
        "tables.md": "기존에 파악된 테이블/컬럼 정보 확인",
        "config.md": "설정 파일 및 환경 정보 확인",
        "classes.md": "클래스/인터페이스 구조 확인",
        "nontarget_files.md": "이전에 분석한 비타겟 파일 목록 확인",
    }
    
    def _summarize_memory_context(self, memory_reads: List[Dict[str, Any]]) -> str:
        """메모리 참조를 자연어로 설명"""
        if not memory_reads:
            return ""
        
        lines = [
            "## 1. 메모리 참조 (분석 전 컨텍스트 수집)",
            "",
            "분석을 시작하기 전, 다음 메모리에서 기존 정보를 참조했습니다:",
            "",
        ]
        
        # 중복 제거 및 그룹화
        seen_paths = set()
        overview_reads = []
        topic_reads = []
        run_reads = []
        
        for mem in memory_reads:
            path = mem.get("path", "")
            if path in seen_paths:
                continue
            seen_paths.add(path)
            
            content_preview = mem.get("content_preview", "")[:200]
            
            if "overview.md" in path:
                overview_reads.append((path, content_preview))
            elif "topics/" in path:
                topic_reads.append((path, content_preview))
            elif "runs/" in path:
                run_reads.append((path, content_preview))
        
        # 프로젝트 개요
        for path, preview in overview_reads:
            role = self.MEMORY_ROLE_MAP.get("overview.md", "프로젝트 정보 확인")
            lines.append(f"- **{path}**: {role}")
            if preview:
                # 첫 줄 또는 의미 있는 부분 추출
                first_line = preview.split("\n")[0].strip()
                if first_line and not first_line.startswith("#"):
                    lines.append(f"  - 내용 미리보기: \"{first_line[:100]}...\"")
            lines.append("")
        
        # 토픽별 메모리
        if topic_reads:
            lines.append("### 주제별 메모리")
            for path, preview in topic_reads:
                # 파일명에서 역할 추출
                filename = Path(path).name
                role = self.MEMORY_ROLE_MAP.get(filename, "관련 정보 확인")
                lines.append(f"- **{filename}**: {role}")
            lines.append("")
        
        # 이전 분석 결과
        if run_reads:
            lines.append("### 이전 분석 결과 참조")
            for path, preview in run_reads[:5]:  # 최대 5개만 표시
                filename = Path(path).name
                # 파일명에서 분석 대상 추출 (예: 2026-02-05T15-55-14__order-services_xml.md)
                parts = filename.replace(".md", "").split("__")
                if len(parts) >= 2:
                    target_file = parts[1].replace("_", ".")
                    lines.append(f"- **{target_file}**: 이전 분석에서 발견된 정보 참조")
                else:
                    lines.append(f"- **{filename}**: 이전 분석 결과 참조")
            if len(run_reads) > 5:
                lines.append(f"- ... 외 {len(run_reads) - 5}개 더")
            lines.append("")
        
        return "\n".join(lines)
    
    def _summarize_pattern_analysis_steps(self, analysis_steps: List[Dict[str, Any]]) -> str:
        """패턴 분석 단계를 상세하게 설명"""
        if not analysis_steps:
            return ""
        
        lines = [
            "### 패턴 발견 과정 상세",
            "",
        ]
        
        for step in analysis_steps:
            chunk_idx = step.get("chunk_idx", 0)
            line_range = step.get("line_range", "")
            previous_patterns = step.get("previous_patterns", [])
            new_discoveries = step.get("new_discoveries", {})
            reasoning = step.get("reasoning", "")
            chunk_content = step.get("chunk_content", "")
            
            lines.append(f"#### 청크 {chunk_idx + 1} 분석 (라인 {line_range})")
            lines.append("")
            
            # 분석 대상 코드 샘플
            if chunk_content:
                sample_lines = chunk_content.split("\n")[:5]
                lines.append("**분석 대상 (처음 5줄):**")
                lines.append("```")
                for line in sample_lines:
                    lines.append(line[:100])  # 100자 제한
                lines.append("```")
                lines.append("")
            
            # 이전 패턴 상태
            if previous_patterns:
                lines.append(f"**이전까지 발견된 패턴**: {', '.join(str(p) for p in previous_patterns[:5])}")
            else:
                lines.append("**이전까지 발견된 패턴**: (없음 - 첫 번째 청크)")
            lines.append("")
            
            # 에이전트 판단
            if reasoning:
                lines.append("**에이전트 판단:**")
                lines.append(f"> {reasoning[:500]}")
                if len(reasoning) > 500:
                    lines.append("> ... (생략)")
                lines.append("")
            
            # 새로 발견된 것들
            if new_discoveries:
                lines.append("**새로 발견:**")
                new_discovered = new_discoveries.get("new_discovered", [])
                newly_confirmed = new_discoveries.get("newly_confirmed", [])
                block_roles = new_discoveries.get("block_roles", [])
                
                if new_discovered:
                    lines.append(f"- 발견 패턴: {', '.join(str(p) for p in new_discovered[:3])}")
                if newly_confirmed:
                    lines.append(f"- 확정된 패턴: {', '.join(str(p) for p in newly_confirmed[:3])}")
                if block_roles:
                    for role in block_roles[:3]:
                        if isinstance(role, dict):
                            role_lines = role.get("lines", "")
                            role_desc = role.get("role", "")
                            if role_desc:
                                lines.append(f"- 블록 역할: 라인 {role_lines} - {role_desc}")
                lines.append("")
        
        return "\n".join(lines)
    
    def _summarize_agent_calls(self, agent_steps: List[Dict[str, Any]]) -> str:
        """에이전트 호출을 상세하게 설명"""
        if not agent_steps:
            return ""
        
        lines = [
            "### 에이전트 호출 상세",
            "",
        ]
        
        for step in agent_steps:
            agent_name = step.get("agent", "")
            purpose = step.get("purpose", "")
            input_context = step.get("input_context", "")
            reasoning = step.get("reasoning", "")
            parsed_result = step.get("parsed_result", {})
            duration_ms = step.get("duration_ms", 0)
            
            lines.append(f"#### {agent_name}")
            lines.append("")
            
            if purpose:
                lines.append(f"**목적**: {purpose}")
                lines.append("")
            
            if input_context:
                lines.append(f"**제공된 컨텍스트**: {input_context}")
                lines.append("")
            
            if reasoning:
                lines.append("**에이전트 판단:**")
                lines.append(f"> {reasoning[:400]}")
                if len(reasoning) > 400:
                    lines.append("> ... (생략)")
                lines.append("")
            
            if parsed_result:
                lines.append("**결과:**")
                # 결과 요약
                if isinstance(parsed_result, dict):
                    for key, value in list(parsed_result.items())[:5]:
                        if isinstance(value, list):
                            lines.append(f"- {key}: {len(value)}개")
                        elif isinstance(value, str) and len(value) > 100:
                            lines.append(f"- {key}: {value[:100]}...")
                        else:
                            lines.append(f"- {key}: {value}")
                lines.append("")
            
            lines.append(f"*소요 시간: {duration_ms}ms*")
            lines.append("")
        
        return "\n".join(lines)
    
    def _summarize_tool_calls(self, tool_steps: List[Dict[str, Any]]) -> str:
        """툴 호출을 상세하게 설명"""
        if not tool_steps:
            return ""
        
        lines = [
            "### 도구 호출 상세",
            "",
        ]
        
        for step in tool_steps:
            tool_name = step.get("tool", "")
            context = step.get("context", "")
            input_data = step.get("input", {})
            output_data = step.get("output", "")
            conclusion = step.get("conclusion", "")
            duration_ms = step.get("duration_ms", 0)
            error = step.get("error", "")
            
            lines.append(f"#### {tool_name}")
            lines.append("")
            
            if context:
                lines.append(f"**호출 이유**: {context}")
                lines.append("")
            
            if input_data:
                lines.append("**입력:**")
                lines.append("```json")
                if isinstance(input_data, dict):
                    for key, value in input_data.items():
                        if isinstance(value, str) and len(value) > 200:
                            lines.append(f'  "{key}": "{value[:200]}..."')
                        else:
                            lines.append(f'  "{key}": {value}')
                else:
                    lines.append(str(input_data)[:500])
                lines.append("```")
                lines.append("")
            
            if output_data:
                lines.append(f"**결과** ({duration_ms}ms):")
                output_str = str(output_data)
                if len(output_str) > 300:
                    lines.append(f"```\n{output_str[:300]}...\n```")
                else:
                    lines.append(f"```\n{output_str}\n```")
                lines.append("")
            
            if conclusion:
                lines.append(f"**결론**: {conclusion}")
                lines.append("")
            
            if error:
                lines.append(f"**오류**: {error}")
                lines.append("")
        
        return "\n".join(lines)
    
    def _summarize_phase_conclusions(self, conclusion_steps: List[Dict[str, Any]]) -> str:
        """페이즈 결론을 설명"""
        if not conclusion_steps:
            return ""
        
        lines = [
            "### 페이즈별 결론",
            "",
        ]
        
        for step in conclusion_steps:
            phase = step.get("phase", "")
            inputs = step.get("inputs", {})
            outputs = step.get("outputs", {})
            decisions = step.get("decisions_made", [])
            next_hint = step.get("next_step_hint", "")
            
            lines.append(f"#### {phase}")
            lines.append("")
            
            if decisions:
                lines.append("**결정:**")
                for decision in decisions[:5]:
                    lines.append(f"- {decision}")
                lines.append("")
            
            if outputs:
                lines.append("**출력:**")
                for key, value in list(outputs.items())[:5]:
                    if isinstance(value, list):
                        lines.append(f"- {key}: {len(value)}개")
                    else:
                        lines.append(f"- {key}: {value}")
                lines.append("")
            
            if next_hint:
                lines.append(f"**다음 단계**: {next_hint}")
                lines.append("")
        
        return "\n".join(lines)
    
    def _summarize_chunking_process(self, pattern_steps: List[Dict[str, Any]], chunk_result: Optional[Dict[str, Any]]) -> str:
        """청킹 과정을 자연어로 설명"""
        lines = [
            "## 2. 파일 구조 분석 (청킹)",
            "",
        ]
        
        # 패턴 발견 과정
        if pattern_steps:
            lines.append("파일을 500줄씩 순차적으로 분석하여 패턴을 발견했습니다:")
            lines.append("")
            
            # 마지막 패턴 상태 (최종 결과)
            last_pattern = pattern_steps[-1] if pattern_steps else {}
            
            discovered = last_pattern.get("discovered_patterns", [])
            confirmed = last_pattern.get("confirmed_patterns", [])
            key_patterns = last_pattern.get("key_patterns", [])
            boundary_type = last_pattern.get("boundary_type", "line")
            confidence = last_pattern.get("confidence", 0.5)
            block_roles = last_pattern.get("block_roles", [])
            
            if confirmed:
                lines.append(f"- **확정된 패턴**: {', '.join(str(p) for p in confirmed[:5])}")
            if key_patterns:
                lines.append(f"- **핵심 경계 패턴**: {', '.join(str(p) for p in key_patterns[:3])}")
            lines.append(f"- **블록 경계 타입**: {boundary_type}")
            lines.append(f"- **신뢰도**: {confidence:.1%}")
            
            if block_roles:
                lines.append("")
                lines.append("### 발견된 블록 역할")
                for role_info in block_roles[:10]:  # 최대 10개
                    if isinstance(role_info, dict):
                        role_lines = role_info.get("lines", "")
                        role_desc = role_info.get("role", "")
                        entities = role_info.get("entities", [])
                        if role_desc:
                            entity_str = f" (엔티티: {', '.join(entities)})" if entities else ""
                            lines.append(f"- 라인 {role_lines}: {role_desc}{entity_str}")
            lines.append("")
        
        # 청킹 결과
        if chunk_result:
            rules = chunk_result.get("rules", {})
            blocks = chunk_result.get("blocks", [])
            
            lines.append(f"### 청킹 결과: 총 {len(blocks)}개 블록으로 분리")
            lines.append("")
            
            if rules:
                boundary = rules.get("boundary", "unknown")
                lines.append(f"- **경계 규칙**: {boundary}")
            
            lines.append("")
            lines.append("| 블록 | 라인 범위 | 크기 |")
            lines.append("|------|----------|------|")
            for block in blocks[:10]:  # 최대 10개
                idx = block.get("index", 0)
                start = block.get("start_line", 0)
                end = block.get("end_line", 0)
                size = block.get("lines", end - start + 1)
                lines.append(f"| Block {idx} | {start}-{end}행 | {size}줄 |")
            if len(blocks) > 10:
                lines.append(f"| ... | ... | {len(blocks) - 10}개 더 |")
            lines.append("")
        else:
            lines.append("규칙이 메모리에서 로드되어 패턴 발견 과정이 생략되었습니다.")
            lines.append("")
        
        return "\n".join(lines)
    
    def _summarize_blocks(self, summarize_steps: List[Dict[str, Any]]) -> str:
        """각 블록 요약을 자연어로 설명"""
        if not summarize_steps:
            return ""
        
        lines = [
            "## 3. 블록별 요약 결과",
            "",
        ]
        
        for step in summarize_steps:
            block_idx = step.get("block_index", 0)
            kind = step.get("kind", "unknown")
            summary = step.get("summary", "")
            keywords = step.get("keywords", [])
            tables = step.get("tables", [])
            evidence = step.get("evidence", "")
            
            lines.append(f"### Block {block_idx}: {kind}")
            lines.append("")
            
            if summary:
                lines.append(f"**요약**: {summary}")
                lines.append("")
            
            if keywords:
                # 주요 키워드 5개만 표시
                kw_display = keywords[:5]
                lines.append(f"**주요 키워드**: {', '.join(kw_display)}")
                if len(keywords) > 5:
                    lines.append(f"  (외 {len(keywords) - 5}개)")
                lines.append("")
            
            if tables:
                lines.append("**발견된 테이블**:")
                for tbl in tables:
                    tbl_name = tbl.get("name", "")
                    entity_name = tbl.get("entity_name", "")
                    columns = tbl.get("columns", [])
                    description = tbl.get("description", "")
                    access_mode = tbl.get("access_mode", "")
                    
                    name_display = f"{tbl_name}"
                    if entity_name and entity_name != tbl_name:
                        name_display += f" (논리명: {entity_name})"
                    
                    lines.append(f"- **{name_display}**")
                    if description:
                        lines.append(f"  - 설명: {description}")
                    if access_mode:
                        lines.append(f"  - 접근 모드: {access_mode}")
                    if columns:
                        col_names = [c.get("name", "") for c in columns[:5]]
                        lines.append(f"  - 컬럼 ({len(columns)}개): {', '.join(col_names)}")
                        if len(columns) > 5:
                            lines.append(f"    외 {len(columns) - 5}개...")
                lines.append("")
            
            if evidence:
                lines.append(f"**근거**: {evidence[:200]}...")
                lines.append("")
        
        return "\n".join(lines)
    
    def _summarize_table_processing(self, table_steps: List[Dict[str, Any]]) -> str:
        """테이블 처리 결과를 자연어로 설명"""
        created_tables = [s for s in table_steps if s.get("action") == "CREATE"]
        updated_tables = [s for s in table_steps if s.get("action") == "UPDATE"]
        
        if not created_tables and not updated_tables:
            return ""
        
        lines = [
            "## 4. 테이블 처리 결과",
            "",
        ]
        
        # 요약 테이블
        lines.append("| 테이블명 | 액션 | 컬럼 수 | 스키마 |")
        lines.append("|---------|------|--------|-------|")
        
        for tbl in created_tables:
            name = tbl.get("table_name", "")
            columns = tbl.get("columns", [])
            schema = tbl.get("schema", "public")
            lines.append(f"| {name} | 신규 생성 | {len(columns)}개 | {schema} |")
        
        for tbl in updated_tables:
            name = tbl.get("table_name", "")
            changes = tbl.get("changes", {})
            new_cols = changes.get("new_columns", [])
            schema = changes.get("schema", "public")
            lines.append(f"| {name} | 보강 | +{len(new_cols)}개 | {schema} |")
        
        lines.append("")
        
        # 상세 정보
        if created_tables:
            lines.append("### 신규 생성된 테이블 상세")
            lines.append("")
            
            for tbl in created_tables:
                name = tbl.get("table_name", "")
                columns = tbl.get("columns", [])
                
                lines.append(f"#### {name}")
                lines.append("")
                lines.append("| 컬럼명 | 타입 | 설명 |")
                lines.append("|-------|------|------|")
                
                for col in columns[:15]:  # 최대 15개
                    col_name = col.get("name", "")
                    dtype = col.get("dtype", "")
                    desc = col.get("description", "")[:50]
                    lines.append(f"| {col_name} | {dtype} | {desc} |")
                
                if len(columns) > 15:
                    lines.append(f"| ... | ... | 외 {len(columns) - 15}개 컬럼 |")
                lines.append("")
        
        if updated_tables:
            lines.append("### 보강된 테이블")
            lines.append("")
            
            for tbl in updated_tables:
                name = tbl.get("table_name", "")
                changes = tbl.get("changes", {})
                new_cols = changes.get("new_columns", [])
                updated_desc = changes.get("description", "")
                
                lines.append(f"#### {name}")
                if updated_desc:
                    lines.append(f"- 설명 업데이트: {updated_desc[:100]}...")
                if new_cols:
                    lines.append(f"- 추가된 컬럼: {', '.join(c.get('name', '') for c in new_cols[:5])}")
                    if len(new_cols) > 5:
                        lines.append(f"  외 {len(new_cols) - 5}개")
                lines.append("")
        
        return "\n".join(lines)
    
    def _summarize_linking(self, link_steps: List[Dict[str, Any]], agent_steps: List[Dict[str, Any]]) -> str:
        """연결 결과를 자연어로 설명"""
        if not link_steps:
            return ""
        
        lines = [
            "## 5. 관계 연결 결과",
            "",
        ]
        
        # 관계 유형별 그룹화
        relations_by_type: Dict[str, List[Dict[str, Any]]] = {}
        for rel in link_steps:
            source_type = rel.get("source_type", "")
            target_type = rel.get("target_type", "")
            role = rel.get("role", "")
            key = f"{source_type} → {target_type}"
            
            if key not in relations_by_type:
                relations_by_type[key] = []
            relations_by_type[key].append(rel)
        
        # 주요 관계 유형별 요약
        lines.append("### 관계 유형별 요약")
        lines.append("")
        lines.append("| 관계 유형 | 건수 | 주요 역할 |")
        lines.append("|----------|------|----------|")
        
        for rel_type, rels in relations_by_type.items():
            roles = set(r.get("role", "") for r in rels)
            roles_str = ", ".join(list(roles)[:3])
            lines.append(f"| {rel_type} | {len(rels)}건 | {roles_str} |")
        lines.append("")
        
        # 타겟 노드 연결 상세 (METHOD, CLASS 등)
        target_connections = [r for r in link_steps if r.get("source_type") in ("METHOD", "CLASS", "INTERFACE")]
        if target_connections:
            lines.append("### 타겟 노드 연결 상세")
            lines.append("")
            
            # 역할별로 그룹화하여 표시
            by_role: Dict[str, List[str]] = {}
            for rel in target_connections[:20]:  # 최대 20개
                role = rel.get("role", "unknown")
                source_type = rel.get("source_type", "")
                source_id = rel.get("source_id", "")
                
                # ID에서 의미 있는 부분 추출
                if ":" in source_id:
                    parts = source_id.split(":")
                    display_id = parts[-1][:30] if len(parts) > 1 else source_id[:30]
                else:
                    display_id = source_id[:30]
                
                if role not in by_role:
                    by_role[role] = []
                by_role[role].append(f"{source_type}[{display_id}]")
            
            for role, sources in by_role.items():
                lines.append(f"**{role}**:")
                for src in sources[:5]:
                    lines.append(f"  - {src}")
                if len(sources) > 5:
                    lines.append(f"  - ... 외 {len(sources) - 5}개")
                lines.append("")
        
        # FK 관계
        fk_relations = [r for r in link_steps if "FK" in r.get("role", "").upper() or r.get("target_type") == "Table"]
        if fk_relations:
            lines.append("### FROM/WRITES 관계")
            lines.append("")
            
            from_rels = [r for r in fk_relations if "FROM" in str(r)]
            writes_rels = [r for r in fk_relations if "WRITES" in str(r)]
            
            if from_rels:
                lines.append("**FROM (읽기)**:")
                for rel in from_rels[:5]:
                    src = rel.get("source_type", "")
                    tgt = rel.get("target_type", "")
                    lines.append(f"  - {src} → {tgt}")
                lines.append("")
            
            if writes_rels:
                lines.append("**WRITES (쓰기)**:")
                for rel in writes_rels[:5]:
                    src = rel.get("source_type", "")
                    tgt = rel.get("target_type", "")
                    lines.append(f"  - {src} → {tgt}")
                lines.append("")
        
        # Linker Agent 판단 근거
        if agent_steps:
            lines.append("### Linker Agent 판단 근거")
            lines.append("")
            
            for agent in agent_steps[:2]:  # 최대 2개
                response = agent.get("response", "")
                # JSON 응답에서 핵심 내용 추출 시도
                if response:
                    lines.append(f"```")
                    lines.append(response[:500])
                    if len(response) > 500:
                        lines.append("... (생략)")
                    lines.append("```")
                    lines.append("")
        
        return "\n".join(lines)
    
    # ========================================================================
    # 보고서 생성
    # ========================================================================
    
    def generate_report(self) -> str:
        """자연어 기반 마크다운 보고서 생성
        
        JSON 로그에 저장된 정보를 분석하여 핵심 인사이트를 
        자연어로 풀어서 설명하는 보고서를 생성합니다.
        
        보고서 구조:
        1. 요약 (전체 결과 테이블)
        2. 메모리 참조 (컨텍스트 수집)
        3. 파일 구조 분석 (청킹)
           - 패턴 발견 과정 상세
        4. 블록별 요약 결과
        5. 테이블 처리 결과
        6. 관계 연결 결과
        7. 결론
        
        부록:
        - 에이전트 호출 상세
        - 도구 호출 상세
        - 페이즈별 결론
        """
        end_time = datetime.now()
        duration_sec = (end_time - self.start_time).total_seconds()
        
        # 데이터 분류
        memory_reads = [s for s in self.steps if s.get("step") == "memory_read"]
        pattern_steps = [s for s in self.steps if "pattern_discovery" in s.get("step", "")]
        pattern_analysis_steps = [s for s in self.steps if "pattern_analysis_step" in s.get("step", "")]
        chunk_results = [s for s in self.steps if s.get("step") == "chunk_result"]
        summarize_steps = [s for s in self.steps if "summarize_block" in s.get("step", "")]
        table_steps = [s for s in self.steps if s.get("step") in ("table_created", "table_updated")]
        link_steps = [s for s in self.steps if s.get("step") == "refers_to_created"]
        agent_linker_steps = [s for s in self.steps if "agent_linker" in s.get("step", "")]
        agent_steps = [s for s in self.steps if s.get("step", "").startswith("agent_")]
        tool_steps = [s for s in self.steps if s.get("step", "").startswith("tool_")]
        phase_conclusions = [s for s in self.steps if "phase_conclusion" in s.get("step", "")]
        
        # 보고서 헤더
        lines = [
            f"# 분석 보고서: {Path(self.file_path).name}",
            "",
            "## 요약",
            "",
            f"**처리 기간**: {self.start_time.strftime('%Y-%m-%d %H:%M:%S')} ~ {end_time.strftime('%H:%M:%S')} ({duration_sec:.1f}초)",
            "",
            "| 항목 | 결과 |",
            "|------|------|",
            f"| 분석 블록 수 | {len(summarize_steps)}개 |",
            f"| 발견 테이블 | {len(set(self.stats['tables_identified']))}개 |",
            f"| 신규 생성 테이블 | {len(set(self.stats['tables_created']))}개 |",
            f"| 보강된 테이블 | {len(set(self.stats['tables_updated']))}개 |",
            f"| 생성된 관계 | {len(self.stats['refers_to_created'])}개 |",
            f"| 참조된 메모리 | {len(set(m['path'] for m in self.stats['memory_ops'] if m['op'] == 'read'))}개 |",
            f"| 에이전트 호출 | {sum(s['count'] for s in self.stats['agent_calls'].values())}회 |",
            f"| 도구 호출 | {sum(s['count'] for s in self.stats['tool_calls'].values())}회 |",
            f"| 오류 | {len(self.stats['errors'])}개 |",
            "",
        ]
        
        # 오류가 있으면 먼저 표시
        if self.stats["errors"]:
            lines.extend([
                "### ⚠️ 오류 발생",
                "",
            ])
            for err in self.stats["errors"]:
                lines.append(f"- **{err['phase']}**: {err['error']}")
            lines.append("")
        
        # 섹션별 상세 내용
        lines.append(self._summarize_memory_context(memory_reads))
        
        chunk_result = chunk_results[0] if chunk_results else None
        lines.append(self._summarize_chunking_process(pattern_steps, chunk_result))
        
        # 패턴 분석 과정 상세 (있는 경우)
        if pattern_analysis_steps:
            lines.append(self._summarize_pattern_analysis_steps(pattern_analysis_steps))
        
        lines.append(self._summarize_blocks(summarize_steps))
        
        lines.append(self._summarize_table_processing(table_steps))
        
        lines.append(self._summarize_linking(link_steps, agent_linker_steps))
        
        # 최종 결론
        lines.extend([
            "## 6. 결론",
            "",
        ])
        
        tables_created = set(self.stats['tables_created'])
        tables_updated = set(self.stats['tables_updated'])
        total_columns = sum(
            len(s.get("columns", [])) 
            for s in table_steps 
            if s.get("action") == "CREATE"
        )
        
        if tables_created or tables_updated:
            created_str = f"{len(tables_created)}개 테이블 신규 생성" if tables_created else ""
            updated_str = f"{len(tables_updated)}개 테이블 보강" if tables_updated else ""
            conjunction = ", " if created_str and updated_str else ""
            
            lines.append(f"이 파일 분석을 통해 **{created_str}{conjunction}{updated_str}**되었습니다.")
            if total_columns > 0:
                lines.append(f"총 **{total_columns}개의 컬럼** 정보가 추출되었습니다.")
        
        relation_count = len(self.stats['refers_to_created'])
        if relation_count > 0:
            # 관계 유형별 카운트
            method_rels = len([r for r in self.stats['refers_to_created'] if r.get('source') in ('METHOD', 'CLASS')])
            table_rels = len([r for r in self.stats['refers_to_created'] if r.get('source') == 'Table' or r.get('target') == 'Table'])
            
            lines.append(f"**{relation_count}개의 관계**가 생성되어 그래프가 확장되었습니다.")
            if method_rels > 0:
                lines.append(f"  - 타겟 노드(METHOD/CLASS) 연결: {method_rels}건")
            if table_rels > 0:
                lines.append(f"  - 테이블 관련 연결: {table_rels}건")
        
        lines.append("")
        
        # 부록: 상세 정보
        lines.extend([
            "---",
            "",
            "## 부록 A: 처리 통계",
            "",
        ])
        
        # 에이전트 호출 통계
        if self.stats["agent_calls"]:
            lines.append("### 에이전트 호출 통계")
            lines.append("| 에이전트 | 호출 수 | 총 소요 시간 |")
            lines.append("|----------|---------|-------------|")
            for agent, stat in self.stats["agent_calls"].items():
                lines.append(f"| {agent} | {stat['count']}회 | {stat['total_ms']/1000:.1f}초 |")
            lines.append("")
        
        # 도구 호출 통계
        if self.stats["tool_calls"]:
            lines.append("### 도구 호출 통계")
            lines.append("| 도구 | 호출 수 | 총 소요 시간 |")
            lines.append("|------|---------|-------------|")
            for tool, stat in self.stats["tool_calls"].items():
                lines.append(f"| {tool} | {stat['count']}회 | {stat['total_ms']}ms |")
            lines.append("")
        
        # 부록 B: 에이전트 호출 상세
        if agent_steps:
            lines.extend([
                "---",
                "",
                "## 부록 B: 에이전트 호출 상세",
                "",
            ])
            lines.append(self._summarize_agent_calls(agent_steps[:10]))  # 최대 10개
        
        # 부록 C: 도구 호출 상세
        if tool_steps:
            lines.extend([
                "---",
                "",
                "## 부록 C: 도구 호출 상세",
                "",
            ])
            lines.append(self._summarize_tool_calls(tool_steps[:15]))  # 최대 15개
        
        # 부록 D: 페이즈별 결론
        if phase_conclusions:
            lines.extend([
                "---",
                "",
                "## 부록 D: 페이즈별 결론",
                "",
            ])
            lines.append(self._summarize_phase_conclusions(phase_conclusions))
        
        report_content = "\n".join(lines)
        
        # 보고서 파일 저장
        report_path = self.trace_dir / "REPORT.md"
        with open(report_path, "w", encoding="utf-8") as f:
            f.write(report_content)
        
        return report_content
    
    def get_trace_dir(self) -> str:
        """트레이스 디렉토리 경로 반환"""
        return str(self.trace_dir)

