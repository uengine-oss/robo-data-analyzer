"""DeepAgents 클라이언트

자율 에이전트 구조: 모든 에이전트에 모든 도구 제공.
어떤 도구를 사용할지는 에이전트가 프롬프트 지침에 따라 자율적으로 판단합니다.

메모리 관리: 에이전트가 직접 read_memory/write_memory로 자율 관리.
시스템이 자동으로 해주지 않습니다.

프롬프트 관리: RuleLoader를 통해 rules/deepagents/에서 로드.
input_schema 검증 및 Jinja2 렌더링 지원.
"""

import json
import os
import re
from datetime import datetime
from typing import Any, Dict, List, Optional

from deepagents import create_deep_agent
from langchain_core.messages import HumanMessage

from analyzer.strategy.framework.nontarget.utils.nontarget_logger import (
    log_nontarget,
    log_agent_action,
)
from config.settings import settings
from util.rule_loader import RuleLoader


# [제거됨] AGENT_TOOLS_MAP - 모든 에이전트에 모든 도구 제공
# 어떤 도구를 쓸지는 에이전트가 프롬프트 지침에 따라 자율 판단


class DeepAgentsClient:
    """DeepAgents 클라이언트 (싱글톤)
    
    RuleLoader를 사용하여 rules/deepagents/에서 프롬프트를 로드합니다.
    input_schema 검증 및 Jinja2 렌더링을 지원합니다.
    """
    
    _instance: Optional["DeepAgentsClient"] = None
    _rule_loader: Optional[RuleLoader] = None
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance
    
    def __init__(self):
        if self._rule_loader is None:
            try:
                self._rule_loader = RuleLoader(target_lang="deepagents")
                log_nontarget("DEEPAGENTS", "RuleLoader 초기화 완료 (rules/deepagents/)")
            except FileNotFoundError as e:
                log_nontarget("DEEPAGENTS", f"RuleLoader 초기화 실패: {e}")
                self._rule_loader = None
    
    def get_rendered_prompt(self, agent_name: str, inputs: Dict[str, Any]) -> str:
        """RuleLoader로 프롬프트 렌더링
        
        Args:
            agent_name: 에이전트 이름 (main_agent, chunker_agent 등)
            inputs: 입력값 딕셔너리 (input_schema에 맞게)
            
        Returns:
            렌더링된 프롬프트 문자열
            
        Raises:
            ValueError: 필수 필드 누락 또는 렌더링 오류
        """
        if self._rule_loader is None:
            raise RuntimeError("RuleLoader가 초기화되지 않았습니다")
        
        return self._rule_loader.render_prompt(agent_name, inputs)
    
    def get_rendered_task(self, agent_name: str, inputs: Dict[str, Any]) -> str:
        """RuleLoader로 task 렌더링 (user_input으로 사용)
        
        Args:
            agent_name: 에이전트 이름 (main_agent, chunker_agent 등)
            inputs: 입력값 딕셔너리 (input_schema에 맞게)
        
        Returns:
            렌더링된 task 문자열
        """
        if self._rule_loader is None:
            raise RuntimeError("RuleLoader가 초기화되지 않았습니다")
        
        return self._rule_loader.render_task(agent_name, inputs)
    
    # [제거됨] _load_memory_context - 에이전트가 직접 read_memory로 자율 관리
    # 시스템이 자동으로 주입하지 않음
    
    def create_agent(
        self,
        agent_name: str,
        ctx: Optional[Any] = None,
        model: Optional[str] = None,
        inputs: Optional[Dict[str, Any]] = None,
    ) -> Any:
        """DeepAgents 에이전트 생성
        
        Args:
            agent_name: 에이전트 이름 (main_agent, chunker_agent 등)
            ctx: RuntimeCtx (client, cypher_lock) - Neo4j 도구에 필요
            model: LLM 모델명 (기본값: settings.llm.model)
            inputs: 입력값 딕셔너리 (input_schema에 맞게 검증됨)
            
        Returns:
            DeepAgents 에이전트
            
        Raises:
            ValueError: 프롬프트 없음 또는 필수 필드 누락
            RuntimeError: 에이전트 생성 실패
        """
        if self._rule_loader is None:
            raise RuntimeError("RuleLoader가 초기화되지 않았습니다")
        
        # RuleLoader로 프롬프트 렌더링 (input_schema 검증 포함)
        try:
            prompt = self.get_rendered_prompt(agent_name, inputs or {})
            log_nontarget("DEEPAGENTS", f"프롬프트 렌더링 ({agent_name}): {list((inputs or {}).keys())}")
        except Exception as e:
            log_nontarget("DEEPAGENTS", f"프롬프트 렌더링 실패 ({agent_name}): {e}")
            raise
        
        # 모델명: settings에서 가져오기 (기본값)
        if model is None:
            model = settings.llm.model
        
        # 모든 에이전트에 모든 도구 제공 - 에이전트가 자율적으로 선택
        custom_tools = []
        if ctx:
            from analyzer.strategy.framework.nontarget.tools import get_all_tools
            custom_tools = get_all_tools(ctx)
            log_nontarget("DEEPAGENTS", f"도구 주입: {agent_name} <- {[t.name for t in custom_tools]}")
        
        try:
            model_str = f"openai:{model}" if not model.startswith("openai:") else model
            
            agent = create_deep_agent(
                system_prompt=prompt,
                tools=custom_tools,
                model=model_str,
            )
            log_agent_action(agent_name, "create", {"model": model_str, "tools": [t.name for t in custom_tools]})
            return agent
        except Exception as e:
            log_nontarget("DEEPAGENTS", f"에이전트 생성 실패: {agent_name} - {e}")
            raise RuntimeError(f"에이전트 생성 실패: {agent_name}") from e
    
    def create_main_agent_with_subagents(
        self,
        ctx: Any,
        model: Optional[str] = None,
    ) -> Any:
        """서브에이전트를 포함한 메인 에이전트 생성 (1회만 호출)
        
        에이전트는 프로그램 수명 동안 1회만 생성됩니다.
        시스템 프롬프트는 정적({{ }} 변수 없음)이므로 입력값 없이 렌더링합니다.
        파일별 동적 데이터는 task 메시지(HumanMessage)로 전달됩니다.
        
        DeepAgents의 subagents 기능을 사용하여 메인 에이전트가
        task() 도구로 서브에이전트에게 작업을 위임할 수 있습니다.
        
        서브에이전트 목록:
        - chunker: 파일 구조 분석 및 블록 분할 규칙 추론
        - summarizer: 블록별 kind/keywords/summary/tables 추출
        - linker: REFERS_TO/FROM/WRITES/FK 관계 후보 탐색
        - description: Table/Column description 생성 및 FK 추론
        
        Args:
            ctx: RuntimeCtx (client, cypher_lock, trace_logger)
            model: LLM 모델명 (기본값: settings.llm.model)
            
        Returns:
            서브에이전트를 포함한 DeepAgents 에이전트
        """
        if self._rule_loader is None:
            raise RuntimeError("RuleLoader가 초기화되지 않았습니다")
        
        if model is None:
            model = settings.llm.model
        
        model_str = f"openai:{model}" if not model.startswith("openai:") else model
        
        # 도구 준비
        from analyzer.strategy.framework.nontarget.tools import get_all_tools, get_readonly_tools
        main_tools = get_all_tools(ctx) if ctx else []  # 메인: 전체 권한
        sub_tools = get_readonly_tools(ctx) if ctx else []  # 서브: 읽기 전용
        
        # ⚠️ 정적 프롬프트 렌더링 ({{ }} 변수 없음, 빈 inputs)
        empty_inputs = {}
        
        # 서브에이전트 정의 (공식 문서 방식)
        # ⚠️ 아키텍처 B: 서브에이전트는 JSON 패치 제안만 반환, Neo4j 쓰기 금지
        subagents = [
            {
                "name": "chunker",
                "description": "파일 구조 분석 및 블록 분할 규칙 추론. 500줄씩 읽으며 패턴을 점진적으로 학습하여 블록 경계를 결정합니다. JSON만 반환합니다.",
                "system_prompt": self.get_rendered_prompt("agents/chunker", empty_inputs),
                "tools": sub_tools,
                "model": model_str,
            },
            {
                "name": "summarizer",
                "description": "블록별 kind/keywords/summary/tables 추출. 테이블/컬럼 정보와 접근 모드(r/w/rw)를 식별합니다. JSON만 반환합니다.",
                "system_prompt": self.get_rendered_prompt("agents/summarizer", empty_inputs),
                "tools": sub_tools,
                "model": model_str,
            },
            {
                "name": "linker",
                "description": "REFERS_TO/FROM/WRITES/FK 관계 후보 탐색. 의미검색→키워드검색→관계추적 순서로 후보를 찾습니다. JSON 후보 목록만 반환합니다.",
                "system_prompt": self.get_rendered_prompt("agents/linker", empty_inputs),
                "tools": sub_tools,
                "model": model_str,
            },
            {
                "name": "description",
                "description": "Table/Column description 생성. DDL과 동일한 스키마로 description을 작성합니다. JSON만 반환합니다.",
                "system_prompt": self.get_rendered_prompt("agents/description", empty_inputs),
                "tools": sub_tools,
                "model": model_str,
            },
        ]
        
        try:
            agent = create_deep_agent(
                model=model_str,
                system_prompt=self.get_rendered_prompt("agents/main", empty_inputs),
                tools=main_tools,  # 메인: 전체 권한 (Neo4j 쓰기 포함)
                subagents=subagents,
                name="main-agent",
            )
            
            log_agent_action("main-agent", "create_with_subagents", {
                "model": model_str,
                "subagents": [s["name"] for s in subagents],
                "main_tools_count": len(main_tools),
                "sub_tools_count": len(sub_tools),
                "architecture": "B (서브는 JSON만, 메인은 Neo4j 쓰기)",
            })
            log_nontarget("DEEPAGENTS", f"서브에이전트 포함 메인 에이전트 생성 완료 (1회 생성): {[s['name'] for s in subagents]}")
            
            return agent
            
        except Exception as e:
            log_nontarget("DEEPAGENTS", f"서브에이전트 포함 메인 에이전트 생성 실패: {e}")
            raise RuntimeError(f"서브에이전트 포함 메인 에이전트 생성 실패") from e


# ============================================================================
# 에이전트 응답 파싱 헬퍼
# ============================================================================

def _extract_agent_output(result: Dict[str, Any]) -> str:
    """에이전트 응답에서 출력 텍스트 추출
    
    DeepAgents는 messages 리스트로 응답합니다.
    마지막 메시지의 content를 반환합니다.
    
    Args:
        result: agent.invoke() 결과
        
    Returns:
        출력 텍스트
    """
    # messages 형태로 응답하는 경우 (DeepAgents)
    if "messages" in result and result["messages"]:
        last_msg = result["messages"][-1]
        if hasattr(last_msg, "content"):
            return last_msg.content
        elif isinstance(last_msg, dict) and "content" in last_msg:
            return last_msg["content"]
    
    # output 형태로 응답하는 경우 (일부 LangChain 에이전트)
    if "output" in result:
        return result["output"]
    
    return str(result)


def _parse_json_from_output(output: str) -> Dict[str, Any]:
    """출력에서 JSON 파싱
    
    코드펜스 안의 JSON 또는 원시 JSON을 파싱합니다.
    
    Args:
        output: 에이전트 출력 텍스트
        
    Returns:
        파싱된 딕셔너리
        
    Raises:
        RuntimeError: JSON 파싱 실패
    """
    # 코드펜스 내 JSON 찾기
    json_match = re.search(r'```(?:json)?\s*([\s\S]*?)```', output)
    if json_match:
        json_str = json_match.group(1).strip()
    else:
        # 코드펜스 없으면 전체를 JSON으로 시도
        json_str = output.strip()
        # JSON 객체 부분만 추출 시도
        obj_match = re.search(r'\{[\s\S]*\}', json_str)
        if obj_match:
            json_str = obj_match.group(0)
    
    try:
        return json.loads(json_str)
    except json.JSONDecodeError as e:
        log_nontarget("DEEPAGENTS", f"JSON 파싱 실패: {e}\n출력: {output[:500]}")
        raise RuntimeError(f"JSON 파싱 실패") from e


# ============================================================================
# 규칙 생성 (Chunker Agent)
# ============================================================================

def infer_chunking_rules(
    sample_text: str,
    file_path: str,
    ctx: Optional[Any] = None,
    chunk_idx: int = 0,
    total_chunks: int = 1,
    pattern_memory: str = "",
) -> Dict[str, Any]:
    """DeepAgents로 블록화 규칙 추론
    
    Args:
        sample_text: 파일 샘플 (500줄 청크)
        file_path: 파일 경로
        ctx: RuntimeCtx (선택)
        chunk_idx: 현재 청크 인덱스 (0부터)
        total_chunks: 전체 청크 수
        pattern_memory: 이전 청크에서 축적된 패턴 메모리
        
    Returns:
        규칙 JSON 딕셔너리 (pattern_memory 포함)
        
    Raises:
        RuntimeError: 규칙 생성 실패
    """
    log_agent_action("chunker_agent", "infer_chunking_rules", 
                    {"file": file_path, "chunk": f"{chunk_idx + 1}/{total_chunks}"})
    
    # 입력값 준비 (input_schema에 맞게)
    inputs = {
        "file_path": file_path,
        "file_content": sample_text[:12000],  # 토큰 제한
        "chunk_idx": chunk_idx,
        "total_chunks": total_chunks,
        "line_start": 1,
        "line_end": sample_text.count('\n') + 1,
        "pattern_memory": pattern_memory,
    }
    
    client = DeepAgentsClient()
    agent = client.create_agent("agents/chunker", ctx=ctx, inputs=inputs)
    
    # YAML task 필드에서 user_input 로드
    task_message = client.get_rendered_task("agents/chunker", inputs)
    result = agent.invoke({"messages": [HumanMessage(content=task_message)]})
    output = _extract_agent_output(result)
    
    rules = _parse_json_from_output(output)
    log_nontarget("DEEPAGENTS", f"규칙 생성 성공: {file_path} (청크 {chunk_idx + 1}/{total_chunks})")
    return rules


# ============================================================================
# Summary 생성 (Summarizer Agent)
# ============================================================================

def generate_summary_with_agent(
    block_code: str,
    file_type: str,
    ctx: Optional[Any] = None,
    block_role: Optional[Dict[str, Any]] = None,
    block_index: int = 0,
    file_path: str = "",
) -> Dict[str, Any]:
    """DeepAgents로 Summary 생성
    
    Args:
        block_code: 블록 코드 텍스트
        file_type: 파일 타입
        ctx: RuntimeCtx (선택)
        block_role: 청킹 단계에서 발견한 블록 역할 정보 (선택)
                   예: {"lines": "12-50", "role": "ORDER_HEADER 엔티티 정의", "entities": ["ORDER_HEADER"]}
        block_index: 블록 인덱스 (로깅용)
        file_path: 파일 경로 (선택)
        
    Returns:
        Summary 딕셔너리 {kind, keywords, summary, evidence, tables}
        
    Raises:
        RuntimeError: Summary 생성 실패
    """
    import time
    
    log_agent_action("summarizer_agent", "generate_summary", {"file_type": file_type, "block_role": block_role})
    
    # 역할 힌트 문자열 생성
    role_hint = ""
    if block_role:
        role_hint = f"라인 {block_role.get('lines', 'unknown')}, 역할: {block_role.get('role', 'unknown')}, 엔티티: {', '.join(block_role.get('entities', []))}"
    
    # 입력값 준비 (input_schema에 맞게)
    inputs = {
        "file_type": file_type,
        "block_code": block_code[:8000],  # 토큰 제한
        "block_index": block_index,
        "block_role": role_hint,
        "file_path": file_path,
    }
    
    client = DeepAgentsClient()
    agent = client.create_agent("agents/summarizer", ctx=ctx, inputs=inputs)
    
    # YAML task 필드에서 user_input 로드
    task_message = client.get_rendered_task("agents/summarizer", inputs)
    
    # 에이전트 호출 + 트레이싱
    start_time = time.time()
    result = agent.invoke({"messages": [HumanMessage(content=task_message)]})
    elapsed_ms = int((time.time() - start_time) * 1000)
    
    output = _extract_agent_output(result)
    parsed = _parse_json_from_output(output)
    
    # 에이전트 호출 트레이싱 (상세)
    trace_logger = getattr(ctx, 'trace_logger', None) if ctx else None
    if trace_logger:
        # 판단 근거 추출
        reasoning = parsed.get("reasoning", parsed.get("evidence", ""))
        
        # 입력 컨텍스트 요약
        input_context_parts = [f"파일 타입: {file_type}"]
        if block_role:
            input_context_parts.append(f"역할 힌트: {block_role.get('role', 'unknown')}")
            if block_role.get("entities"):
                input_context_parts.append(f"엔티티: {', '.join(block_role.get('entities', []))}")
        input_context = ", ".join(input_context_parts)
        
        trace_logger.log_agent_call(
            agent_name="summarizer_agent",
            prompt=task_message,  # YAML task 사용
            response=output,  # 전체 저장
            tokens_used=0,  # TODO: 토큰 추적 추가
            duration_ms=elapsed_ms,
            purpose=f"블록 {block_index} 요약 생성 - kind/keywords/summary/tables 추출",
            input_context=input_context,
            parsed_result=parsed,
            reasoning=reasoning,
        )
    
    return {
        "kind": parsed.get("kind", "unknown"),
        "keywords": parsed.get("keywords", [])[:15],
        "summary": parsed.get("summary", ""),
        "evidence": parsed.get("evidence", ""),
        "tables": parsed.get("tables", []),  # 테이블/엔티티 정보 포함
    }
# ============================================================================
# 메모리 운영 (Read/Write)
# ============================================================================

def read_memory_file(
    path: str, 
    trace_logger: Optional[Any] = None,
    usage_purpose: str = "",
    extract_info: bool = False,
) -> Optional[str]:
    """메모리 파일 읽기
    
    Args:
        path: 파일 경로 (예: mem/project/overview.md)
        trace_logger: TraceLogger 인스턴스 (선택)
        usage_purpose: 왜 이 메모리를 읽는지 (로깅용)
        extract_info: True면 핵심 정보 추출 (로깅용)
        
    Returns:
        파일 내용 또는 None
    """
    try:
        with open(path, "r", encoding="utf-8") as f:
            content = f.read()
        log_nontarget("MEMORY", f"읽기: {path}")
        
        # 트레이싱 (상세)
        if trace_logger:
            # 핵심 정보 추출 시도
            extracted_info = None
            if extract_info and content:
                # 간단한 키 정보 추출
                lines = content.split("\n")[:10]
                extracted_info = {
                    "first_lines": [l.strip() for l in lines if l.strip()][:5],
                    "length": len(content),
                    "file_type": "md" if path.endswith(".md") else "json" if path.endswith(".json") else "other",
                }
            
            # 목적 자동 추론 (없으면)
            if not usage_purpose:
                if "overview.md" in path:
                    usage_purpose = "프로젝트 전체 구조 및 기술 스택 파악"
                elif "tables.md" in path:
                    usage_purpose = "기존에 파악된 테이블/컬럼 정보 확인"
                elif "runs/" in path:
                    usage_purpose = "이전 분석 결과 참조"
                elif "rules/" in path:
                    usage_purpose = "기존 규칙 로드"
                else:
                    usage_purpose = "메모리 참조"
            
            trace_logger.log_memory_read(
                path=path,
                content_preview=content,  # 전체 내용 저장
                extracted_info=extracted_info,
                usage_purpose=usage_purpose,
            )
        
        return content
    except FileNotFoundError:
        return None
    except Exception as e:
        log_nontarget("MEMORY", f"읽기 실패: {path} - {e}")
        return None


def write_memory_file(
    path: str, 
    content: str, 
    trace_logger: Optional[Any] = None,
    write_reason: str = "",
    key_data: Optional[Dict[str, Any]] = None,
) -> bool:
    """메모리 파일 쓰기
    
    Args:
        path: 파일 경로
        content: 파일 내용
        trace_logger: TraceLogger 인스턴스 (선택)
        write_reason: 왜 이 내용을 저장하는지 (로깅용)
        key_data: 저장하는 핵심 데이터 요약 (로깅용)
        
    Returns:
        성공 여부
    """
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)
        log_nontarget("MEMORY", f"쓰기: {path}")
        
        # 트레이싱 (상세)
        if trace_logger:
            # 목적 자동 추론 (없으면)
            if not write_reason:
                if "runs/" in path:
                    write_reason = "실행 기록 저장"
                elif "rules/" in path:
                    write_reason = "청킹 규칙 저장"
                elif "topics/" in path:
                    write_reason = "주제별 정보 업데이트"
                elif "overview.md" in path:
                    write_reason = "프로젝트 개요 업데이트"
                else:
                    write_reason = "메모리 저장"
            
            trace_logger.log_memory_write(
                path=path,
                content=content,  # 전체 내용 저장
                write_reason=write_reason,
                key_data=key_data,
            )
        
        return True
    except Exception as e:
        log_nontarget("MEMORY", f"쓰기 실패: {path} - {e}")
        return False


def list_memory_dir(path: str) -> List[str]:
    """메모리 디렉토리 목록
    
    Args:
        path: 디렉토리 경로
        
    Returns:
        파일명 리스트
    """
    try:
        if os.path.isdir(path):
            return sorted(os.listdir(path))
        return []
    except Exception:
        return []


def save_run_record(
    file_path: str,
    blocks_count: int,
    summaries: Optional[List[Dict[str, Any]]] = None,
    tables_created: Optional[List[str]] = None,
    tables_updated: Optional[List[str]] = None,
    relations_created: Optional[List[Dict[str, Any]]] = None,
    error: Optional[str] = None,
    trace_logger: Optional[Any] = None,
) -> None:
    """실행 기록 저장 (mem/runs/) - 확장된 버전
    
    Args:
        file_path: 분석 대상 파일 경로
        blocks_count: 생성된 블록 수
        summaries: 각 블록의 요약 정보 (선택)
        tables_created: 신규 생성된 테이블명 목록 (선택)
        tables_updated: 보강된 테이블명 목록 (선택)
        relations_created: 생성된 관계 정보 (선택)
        error: 오류 메시지 (있으면)
        trace_logger: TraceLogger 인스턴스 (선택)
    """
    timestamp = datetime.now().strftime("%Y-%m-%dT%H-%M-%S")
    topic = os.path.basename(file_path).replace(".", "_")
    run_filename = f"{timestamp}__{topic}.md"
    # [변경됨] 새 폴더 구조: mem/archive/
    run_path = os.path.join("mem", "archive", run_filename)
    
    status = "❌ 오류" if error else "✓ 완료"
    
    # 요약 정보 구성
    summaries_section = ""
    if summaries:
        kinds_count: Dict[str, int] = {}
        all_keywords: List[str] = []
        all_tables: List[str] = []
        
        for s in summaries:
            kind = s.get("kind", "unknown")
            kinds_count[kind] = kinds_count.get(kind, 0) + 1
            all_keywords.extend(s.get("keywords", [])[:3])  # 각 블록에서 3개씩
            for tbl in s.get("tables", []):
                if tbl.get("name"):
                    all_tables.append(tbl.get("name"))
        
        summaries_section = f"""
## Summaries
- kind 분포: {', '.join(f'{k}={v}' for k,v in kinds_count.items())}
- 주요 키워드: {', '.join(list(set(all_keywords))[:10])}
- 발견된 테이블: {', '.join(all_tables) if all_tables else '없음'}
"""
    
    # 테이블 정보 구성
    tables_section = ""
    if tables_created or tables_updated:
        tables_section = "\n## Tables\n"
        if tables_created:
            tables_section += f"- 신규 생성: {', '.join(tables_created)}\n"
        if tables_updated:
            tables_section += f"- 보강: {', '.join(tables_updated)}\n"
    
    # 관계 정보 구성
    relations_section = ""
    if relations_created:
        relations_section = f"\n## Relations\n- 생성된 관계: {len(relations_created)}개\n"
        # 유형별 카운트
        rel_types: Dict[str, int] = {}
        for rel in relations_created:
            rel_type = rel.get("type", "REFERS_TO")
            rel_types[rel_type] = rel_types.get(rel_type, 0) + 1
        for rel_type, count in rel_types.items():
            relations_section += f"  - {rel_type}: {count}건\n"
    
    content = f"""# Run: {topic} ({timestamp})

## Task
비타겟 파일 분석: {file_path}

## Result
- 상태: {status}
- 블록 수: {blocks_count}
{summaries_section}{tables_section}{relations_section}
## Evidence Pointers
- NONTARGET_FILE (file={file_path})
- NONTARGET_BLOCK x{blocks_count}

## Notes / Uncertainty
{f"- 오류: {error}" if error else "- 없음"}
"""
    
    write_memory_file(run_path, content, trace_logger, 
                     write_reason="실행 기록 저장",
                     key_data={"file": file_path, "blocks": blocks_count, "status": status})


# ============================================================================
# Topics 디렉토리 업데이트 함수
# ============================================================================

def update_topic_tables(
    tables_created: List[str],
    tables_updated: List[str],
    table_details: Optional[List[Dict[str, Any]]] = None,
    trace_logger: Optional[Any] = None,
) -> bool:
    """mem/context/topics/tables.md 업데이트
    
    Args:
        tables_created: 신규 생성된 테이블명 목록
        tables_updated: 보강된 테이블명 목록
        table_details: 테이블 상세 정보 (선택)
        trace_logger: TraceLogger 인스턴스 (선택)
        
    Returns:
        성공 여부
    """
    # [변경됨] 새 폴더 구조: mem/context/topics/
    topics_path = "mem/context/topics/tables.md"
    
    # 기존 내용 로드
    existing = read_memory_file(topics_path, trace_logger, 
                                usage_purpose="기존 테이블 정보 확인") or ""
    
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    
    # 추가할 내용 구성
    new_section = f"\n---\n\n## 업데이트: {timestamp}\n\n"
    
    if tables_created:
        new_section += "### 신규 테이블\n"
        for tbl in tables_created:
            new_section += f"- `{tbl}`\n"
        new_section += "\n"
    
    if tables_updated:
        new_section += "### 보강된 테이블\n"
        for tbl in tables_updated:
            new_section += f"- `{tbl}`\n"
        new_section += "\n"
    
    # 상세 정보 추가
    if table_details:
        new_section += "### 테이블 상세\n\n"
        for detail in table_details[:10]:  # 최대 10개
            name = detail.get("name", "")
            entity_name = detail.get("entity_name", "")
            columns = detail.get("columns", [])
            description = detail.get("description", "")
            
            new_section += f"#### {name}"
            if entity_name and entity_name != name:
                new_section += f" (entity: {entity_name})"
            new_section += "\n"
            
            if description:
                new_section += f"- 설명: {description}\n"
            if columns:
                col_names = [c.get("name", "") for c in columns[:5]]
                new_section += f"- 컬럼: {', '.join(col_names)}"
                if len(columns) > 5:
                    new_section += f" 외 {len(columns) - 5}개"
                new_section += "\n"
            new_section += "\n"
    
    # 파일 업데이트
    updated_content = existing + new_section
    return write_memory_file(topics_path, updated_content, trace_logger,
                           write_reason="테이블 정보 업데이트",
                           key_data={"created": len(tables_created), "updated": len(tables_updated)})


def update_topic_relations(
    relations: List[Dict[str, Any]],
    trace_logger: Optional[Any] = None,
) -> bool:
    """mem/context/topics/relations.md 업데이트
    
    Args:
        relations: 생성된 관계 정보 [{type, source, target, role}, ...]
        trace_logger: TraceLogger 인스턴스 (선택)
        
    Returns:
        성공 여부
    """
    # [변경됨] 새 폴더 구조: mem/context/topics/
    topics_path = "mem/context/topics/relations.md"
    
    # 기존 내용 로드
    existing = read_memory_file(topics_path, trace_logger,
                                usage_purpose="기존 관계 정보 확인") or ""
    
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    
    # 관계 유형별 그룹화
    by_type: Dict[str, List[Dict[str, Any]]] = {}
    for rel in relations:
        rel_type = rel.get("type", "REFERS_TO")
        if rel_type not in by_type:
            by_type[rel_type] = []
        by_type[rel_type].append(rel)
    
    # 추가할 내용 구성
    new_section = f"\n---\n\n## 업데이트: {timestamp}\n\n"
    new_section += f"총 {len(relations)}개 관계 생성\n\n"
    
    for rel_type, rels in by_type.items():
        new_section += f"### {rel_type} ({len(rels)}건)\n"
        for rel in rels[:5]:  # 유형별 최대 5개
            source = rel.get("source", "")
            target = rel.get("target", "")
            role = rel.get("role", "")
            new_section += f"- {source} → {target}"
            if role:
                new_section += f" ({role})"
            new_section += "\n"
        if len(rels) > 5:
            new_section += f"- ... 외 {len(rels) - 5}건\n"
        new_section += "\n"
    
    # 파일 업데이트
    updated_content = existing + new_section
    return write_memory_file(topics_path, updated_content, trace_logger,
                           write_reason="관계 정보 업데이트",
                           key_data={"total": len(relations), "types": list(by_type.keys())})


def update_topic_files(
    file_path: str,
    file_type: str,
    blocks_count: int,
    trace_logger: Optional[Any] = None,
) -> bool:
    """mem/context/topics/files.md 업데이트
    
    Args:
        file_path: 분석된 파일 경로
        file_type: 파일 타입 (xml, yaml 등)
        blocks_count: 블록 수
        trace_logger: TraceLogger 인스턴스 (선택)
        
    Returns:
        성공 여부
    """
    # [변경됨] 새 폴더 구조: mem/context/topics/
    topics_path = "mem/context/topics/files.md"
    
    # 기존 내용 로드
    existing = read_memory_file(topics_path, trace_logger,
                                usage_purpose="기존 파일 목록 확인") or ""
    
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    filename = os.path.basename(file_path)
    
    # 이미 있는지 확인
    if filename in existing:
        # 업데이트만
        return True
    
    # 추가할 내용 구성
    new_entry = f"- `{filename}` ({file_type}, {blocks_count}블록) - {timestamp}\n"
    
    # 파일 끝에 추가
    updated_content = existing + new_entry
    return write_memory_file(topics_path, updated_content, trace_logger,
                           write_reason="분석된 파일 기록",
                           key_data={"file": filename, "type": file_type})


# [제거됨] count_runs - 파일 개수 기준 → 토큰 기준으로 변경
# 에이전트가 check_context_limit 도구로 직접 확인


# ============================================================================
# 범용 메모리 업데이트 함수 (모든 단계에서 사용 가능)
# ============================================================================

def update_topic_memory(
    topic: str,
    new_info: Dict[str, Any],
    trace_logger: Optional[Any] = None,
    merge_strategy: str = "append",
) -> bool:
    """주제별 메모리 업데이트 (자동 병합/수정 지원)
    
    모든 워크플로우 단계에서 언제든 호출 가능합니다.
    기존 정보와 새 정보를 비교하여 자동으로 병합하거나 수정합니다.
    
    Args:
        topic: 주제 이름 (tables, classes, config, relations, files)
        new_info: 새로운 정보 딕셔너리
        trace_logger: TraceLogger 인스턴스 (선택)
        merge_strategy: 
            - "append": 기존 내용에 추가
            - "replace": 완전히 대체
            - "merge": 중복 제거하며 병합
        
    Returns:
        성공 여부
    """
    # [변경됨] 새 폴더 구조: mem/context/topics/
    topic_path = f"mem/context/topics/{topic}.md"
    
    # 기존 내용 로드
    existing = read_memory_file(topic_path, trace_logger,
                               usage_purpose=f"기존 {topic} 정보 확인")
    
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    
    # 새 정보를 마크다운 형식으로 변환
    new_section = _format_topic_info(topic, new_info, timestamp)
    
    # 병합 전략에 따른 처리
    if merge_strategy == "replace":
        updated_content = new_section
    elif merge_strategy == "append":
        updated_content = (existing or "") + "\n\n" + new_section
    else:  # merge
        updated_content = _merge_topic_info(existing or "", new_section, topic)
    
    success = write_memory_file(topic_path, updated_content, trace_logger,
                               write_reason=f"{topic} 정보 업데이트 ({merge_strategy})",
                               key_data=new_info)
    
    if success:
        log_nontarget("MEMORY", f"주제 메모리 업데이트: {topic} ({merge_strategy})")
    
    return success


def _format_topic_info(topic: str, info: Dict[str, Any], timestamp: str) -> str:
    """주제별 정보를 마크다운으로 포맷"""
    
    if topic == "tables":
        lines = [f"\n---\n\n## 업데이트: {timestamp}\n"]
        
        tables = info.get("tables", [])
        if tables:
            lines.append("### 테이블 정보\n")
            for tbl in tables:
                name = tbl.get("name", "")
                entity_name = tbl.get("entity_name", "")
                description = tbl.get("description", "")
                columns = tbl.get("columns", [])
                
                lines.append(f"#### {name}")
                if entity_name and entity_name != name:
                    lines.append(f" (entity: {entity_name})")
                lines.append("\n")
                
                if description:
                    lines.append(f"- 설명: {description}\n")
                if columns:
                    col_names = [c.get("name", "") for c in columns[:10]]
                    lines.append(f"- 컬럼: {', '.join(col_names)}")
                    if len(columns) > 10:
                        lines.append(f" 외 {len(columns) - 10}개")
                    lines.append("\n")
                lines.append("\n")
        
        relations = info.get("relations", [])
        if relations:
            lines.append("### FK 관계\n")
            for rel in relations:
                from_table = rel.get("from_table", "")
                to_table = rel.get("to_table", "")
                rel_type = rel.get("type", "")
                lines.append(f"- {from_table} → {to_table} ({rel_type})\n")
        
        return "".join(lines)
    
    elif topic == "classes":
        lines = [f"\n---\n\n## 업데이트: {timestamp}\n"]
        
        classes = info.get("classes", [])
        for cls in classes:
            lines.append(f"- `{cls.get('name', '')}`: {cls.get('description', '')}\n")
        
        methods = info.get("methods", [])
        if methods:
            lines.append("\n### 메서드\n")
            for method in methods[:10]:
                lines.append(f"- `{method.get('name', '')}`: {method.get('description', '')}\n")
        
        return "".join(lines)
    
    elif topic == "config":
        lines = [f"\n---\n\n## 업데이트: {timestamp}\n"]
        
        keys = info.get("keys", [])
        for key in keys:
            lines.append(f"- `{key.get('name', '')}`: {key.get('value', '')} ({key.get('description', '')})\n")
        
        return "".join(lines)
    
    elif topic == "relations":
        lines = [f"\n---\n\n## 업데이트: {timestamp}\n"]
        
        relations = info.get("relations", [])
        by_type: Dict[str, List[Dict[str, Any]]] = {}
        for rel in relations:
            rel_type = rel.get("type", "REFERS_TO")
            if rel_type not in by_type:
                by_type[rel_type] = []
            by_type[rel_type].append(rel)
        
        for rel_type, rels in by_type.items():
            lines.append(f"\n### {rel_type} ({len(rels)}건)\n")
            for rel in rels[:10]:
                source = rel.get("source", "")
                target = rel.get("target", "")
                lines.append(f"- {source} → {target}\n")
            if len(rels) > 10:
                lines.append(f"- ... 외 {len(rels) - 10}건\n")
        
        return "".join(lines)
    
    elif topic == "files":
        lines = [f"\n---\n\n## 업데이트: {timestamp}\n"]
        
        files = info.get("files", [])
        for f in files:
            lines.append(f"- `{f.get('name', '')}` ({f.get('type', '')}, {f.get('blocks', 0)}블록)\n")
        
        return "".join(lines)
    
    else:
        # 기본 포맷
        return f"\n---\n\n## 업데이트: {timestamp}\n\n{json.dumps(info, ensure_ascii=False, indent=2)}\n"


def _merge_topic_info(existing: str, new_section: str, topic: str) -> str:
    """기존 정보와 새 정보 병합 (중복 제거)"""
    # 간단한 중복 체크: 같은 테이블/클래스명이 있으면 새 것으로 대체
    # 더 정교한 병합 로직은 필요에 따라 확장
    
    # 일단 append와 동일하게 처리 (추후 정교한 병합 로직 추가 가능)
    return existing + new_section


def validate_and_correct_memory(
    client: Any,
    cypher_lock: Any,
    trace_logger: Optional[Any] = None,
) -> Dict[str, Any]:
    """메모리와 그래프 불일치 검사 및 자동 수정
    
    메모리에 저장된 정보가 Neo4j 그래프와 일치하는지 확인하고,
    불일치하는 경우 그래프 기준으로 메모리를 수정합니다.
    
    Args:
        client: Neo4j 클라이언트
        cypher_lock: 동시성 제어 락
        trace_logger: TraceLogger 인스턴스 (선택)
        
    Returns:
        {"corrected": [...], "errors": [...]}
    """
    import asyncio
    
    corrected = []
    errors = []
    
    log_nontarget("MEMORY", "메모리-그래프 일관성 검사 시작")
    
    # 1. tables.md 검증
    try:
        # [변경됨] 새 폴더 구조: mem/context/topics/
        tables_path = "mem/context/topics/tables.md"
        tables_content = read_memory_file(tables_path, trace_logger,
                                         usage_purpose="테이블 정보 검증")
        
        if client and tables_content:
            # 그래프에서 실제 테이블 목록 조회
            query = "MATCH (t:Table) RETURN t.name AS name, t.entity_name AS entity_name, t.description AS description"
            
            async def get_tables():
                if cypher_lock:
                    async with cypher_lock:
                        return await client.execute_with_params(query, {})
                return await client.execute_with_params(query, {})
            
            # 비동기 실행
            loop = asyncio.get_event_loop()
            if loop.is_running():
                # 이미 이벤트 루프가 실행 중이면 새 태스크로
                import concurrent.futures
                with concurrent.futures.ThreadPoolExecutor() as pool:
                    result = pool.submit(asyncio.run, get_tables()).result()
            else:
                result = asyncio.run(get_tables())
            
            if result:
                graph_tables = [
                    {"name": r.get("name") or r.data().get("name"), 
                     "entity_name": r.get("entity_name") or r.data().get("entity_name", ""),
                     "description": r.get("description") or r.data().get("description", "")}
                    for r in result
                ]
                
                # 메모리 갱신
                new_content = _generate_tables_md_from_graph(graph_tables)
                write_memory_file(tables_path, new_content, trace_logger,
                                 write_reason="그래프 기준 테이블 정보 동기화",
                                 key_data={"table_count": len(graph_tables)})
                
                corrected.append({"topic": "tables", "tables_synced": len(graph_tables)})
                log_nontarget("MEMORY", f"tables.md 동기화 완료: {len(graph_tables)}개 테이블")
    
    except Exception as e:
        errors.append({"topic": "tables", "error": str(e)})
        log_nontarget("MEMORY", f"tables.md 검증 실패: {e}")
    
    # 2. 기타 토픽 검증 (필요 시 확장)
    
    if trace_logger:
        trace_logger.log_memory_write(
            "mem/validation_result.json",
            json.dumps({"corrected": corrected, "errors": errors}, ensure_ascii=False),
            write_reason="메모리 검증 결과 저장",
            key_data={"corrected": len(corrected), "errors": len(errors)},
        )
    
    log_nontarget("MEMORY", f"메모리-그래프 일관성 검사 완료: {len(corrected)}개 수정, {len(errors)}개 오류")
    
    return {"corrected": corrected, "errors": errors}


def _generate_tables_md_from_graph(tables: List[Dict[str, Any]]) -> str:
    """그래프 테이블 정보에서 tables.md 생성"""
    lines = [
        "# Tables & Entities\n\n",
        f"마지막 동기화: {datetime.now().strftime('%Y-%m-%d %H:%M')}\n\n",
        "## 테이블 목록\n\n",
    ]
    
    if tables:
        for tbl in sorted(tables, key=lambda x: x.get("name", "")):
            name = tbl.get("name", "")
            entity_name = tbl.get("entity_name", "")
            description = tbl.get("description", "")
            
            line = f"### {name}"
            if entity_name and entity_name != name:
                line += f" (entity: {entity_name})"
            line += "\n"
            lines.append(line)
            
            if description:
                lines.append(f"- {description}\n")
            lines.append("\n")
    else:
        lines.append("- (테이블 없음)\n")
    
    return "".join(lines)


# [제거됨] should_update_overview - 파일 30개 기준 → 토큰 기준으로 변경
# 에이전트가 check_context_limit 도구로 직접 확인하고 compress_context로 압축


# [제거됨] summarize_runs_and_update_overview - 파일 개수 기준 요약 제거
# 에이전트가 토큰 한도 초과 시 자율적으로 compress_context 호출


