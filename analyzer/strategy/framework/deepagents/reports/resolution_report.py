"""
Resolution Report (모니터링/보고서 기능)

런타임 해석 과정의 모니터링 및 보고서 생성.
각 후보의 처리 흐름, 도구 호출, 결과를 기록합니다.

사용 예시:
    from analyzer.strategy.framework.deepagents.resolution_report import (
        ResolutionReport,
        ReportCollector,
    )
    
    # 콜백으로 보고서 수집
    collector = ReportCollector()
    result = agent.invoke(
        {"messages": [...]},
        config={"callbacks": [collector]}
    )
    
    # 보고서 생성
    report = collector.build_report(run_id="run_001")
    
    # 마크다운으로 출력
    print(report.to_markdown())
    
    # 파일로 저장 (기본값: framework/deepagents/resources/reports)
    from pathlib import Path
    current_file = Path(__file__).resolve()
    reports_dir = current_file.parent.parent / "resources" / "reports"
    report.save(str(reports_dir))
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional
from pathlib import Path
import json
import time
import logging


@dataclass
class ToolCallReport:
    """도구 호출 보고서"""
    tool_name: str
    args: Dict[str, Any]
    result: Optional[Any] = None
    error: Optional[str] = None
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    duration_ms: int = 0
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "tool_name": self.tool_name,
            "args": self.args,
            "result": str(self.result)[:500] if self.result else None,  # 결과 축약
            "error": self.error,
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "completed_at": self.completed_at.isoformat() if self.completed_at else None,
            "duration_ms": self.duration_ms,
        }


@dataclass
class StepReport:
    """단계별 보고서 (서브에이전트 단위)"""
    subagent: str  # locator, slicer, confirmer, verifier, general-purpose
    tool_calls: List[ToolCallReport] = field(default_factory=list)
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    duration_ms: int = 0
    message_count: int = 0
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "subagent": self.subagent,
            "tool_calls": [tc.to_dict() for tc in self.tool_calls],
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "completed_at": self.completed_at.isoformat() if self.completed_at else None,
            "duration_ms": self.duration_ms,
            "message_count": self.message_count,
        }


@dataclass
class CandidateReport:
    """개별 후보 처리 보고서"""
    element_id: str
    external_hints: List[str] = field(default_factory=list)
    file_path: Optional[str] = None
    line_start: Optional[int] = None
    line_end: Optional[int] = None
    
    # 처리 단계
    steps: List[StepReport] = field(default_factory=list)
    
    # 결과
    status: str = "pending"  # pending, success, failed, pending_review
    result: Optional[Dict[str, Any]] = None
    error: Optional[str] = None
    confidence: float = 0.0
    
    # Evidence
    evidence_id: Optional[str] = None
    patch_id: Optional[str] = None
    
    # 시간
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    duration_ms: int = 0
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "element_id": self.element_id,
            "external_hints": self.external_hints,
            "file_path": self.file_path,
            "line_start": self.line_start,
            "line_end": self.line_end,
            "steps": [s.to_dict() for s in self.steps],
            "status": self.status,
            "result": self.result,
            "error": self.error,
            "confidence": self.confidence,
            "evidence_id": self.evidence_id,
            "patch_id": self.patch_id,
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "completed_at": self.completed_at.isoformat() if self.completed_at else None,
            "duration_ms": self.duration_ms,
        }


@dataclass
class ResolutionReport:
    """런타임 해석 보고서"""
    run_id: str
    started_at: datetime
    completed_at: Optional[datetime] = None
    
    # 후보별 상세
    candidates: List[CandidateReport] = field(default_factory=list)
    
    # 통계
    total: int = 0
    success: int = 0
    failed: int = 0
    pending_review: int = 0
    
    # 추가 통계
    total_tool_calls: int = 0
    total_llm_calls: int = 0
    evidence_created: int = 0
    ddl_missing: int = 0
    lock_conflicts: int = 0
    
    # 에러 목록
    errors: List[str] = field(default_factory=list)
    
    # 플랜/메모리 (Phase 3: 플랜/Todo 기록)
    todos: List[Dict[str, Any]] = field(default_factory=list)  # 에이전트 플랜 기록
    memory_usage: List[Dict[str, Any]] = field(default_factory=list)  # 메모리 활용 이력
    
    @property
    def duration_seconds(self) -> float:
        if self.completed_at and self.started_at:
            return (self.completed_at - self.started_at).total_seconds()
        return 0.0
    
    def add_candidate(self, candidate: CandidateReport):
        """후보 추가 및 통계 업데이트"""
        self.candidates.append(candidate)
        self.total += 1
        
        if candidate.status == "success":
            self.success += 1
        elif candidate.status == "failed":
            self.failed += 1
        elif candidate.status == "pending_review":
            self.pending_review += 1
        
        # 도구 호출 카운트
        for step in candidate.steps:
            self.total_tool_calls += len(step.tool_calls)
        
        if candidate.evidence_id:
            self.evidence_created += 1
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "run_id": self.run_id,
            "started_at": self.started_at.isoformat(),
            "completed_at": self.completed_at.isoformat() if self.completed_at else None,
            "duration_seconds": self.duration_seconds,
            "statistics": {
                "total": self.total,
                "success": self.success,
                "failed": self.failed,
                "pending_review": self.pending_review,
                "success_rate": (self.success / self.total * 100) if self.total > 0 else 0,
            },
            "tool_statistics": {
                "total_tool_calls": self.total_tool_calls,
                "total_llm_calls": self.total_llm_calls,
                "evidence_created": self.evidence_created,
                "ddl_missing": self.ddl_missing,
                "lock_conflicts": self.lock_conflicts,
            },
            "candidates": [c.to_dict() for c in self.candidates],
            "errors": self.errors,
            "todos": self.todos,
            "memory_usage": self.memory_usage,
        }
    
    def to_json(self, indent: int = 2) -> str:
        """JSON 보고서 생성"""
        return json.dumps(self.to_dict(), indent=indent, ensure_ascii=False)
    
    def to_markdown(self) -> str:
        """마크다운 보고서 생성 (상세 버전)"""
        lines = []
        
        # =================================================================
        # 헤더
        # =================================================================
        lines.append("# 🔍 Runtime Resolution Report")
        lines.append("")
        lines.append(f"> **Run ID:** `{self.run_id}`")
        lines.append(f"> **Started:** {self.started_at.strftime('%Y-%m-%d %H:%M:%S')}")
        if self.completed_at:
            lines.append(f"> **Completed:** {self.completed_at.strftime('%Y-%m-%d %H:%M:%S')}")
            lines.append(f"> **Duration:** {self.duration_seconds:.2f} seconds")
        lines.append("")
        
        # =================================================================
        # 시각적 요약
        # =================================================================
        lines.append("---")
        lines.append("")
        lines.append("## 📊 Executive Summary")
        lines.append("")
        
        # 진행률 바 시각화
        if self.total > 0:
            success_pct = int(self.success / self.total * 20)
            failed_pct = int(self.failed / self.total * 20)
            review_pct = int(self.pending_review / self.total * 20)
            remaining = 20 - success_pct - failed_pct - review_pct
            
            bar = "🟢" * success_pct + "🔴" * failed_pct + "🟡" * review_pct + "⚪" * remaining
            lines.append(f"**Progress:** {bar}")
            lines.append("")
        
        # 통계 테이블
        lines.append("| Metric | Count | Percentage |")
        lines.append("|:-------|------:|:-----------|")
        lines.append(f"| ✅ Success | {self.success} | {self.success / self.total * 100:.1f}% |" if self.total > 0 else f"| ✅ Success | {self.success} | - |")
        lines.append(f"| ❌ Failed | {self.failed} | {self.failed / self.total * 100:.1f}% |" if self.total > 0 else f"| ❌ Failed | {self.failed} | - |")
        lines.append(f"| ⏳ Pending Review | {self.pending_review} | {self.pending_review / self.total * 100:.1f}% |" if self.total > 0 else f"| ⏳ Pending Review | {self.pending_review} | - |")
        lines.append(f"| **📋 Total** | **{self.total}** | **100%** |")
        lines.append("")
        
        # 도구 통계
        lines.append("### 🔧 Tool Usage")
        lines.append("")
        lines.append(f"- **Total Tool Calls:** {self.total_tool_calls}")
        lines.append(f"- **Total LLM Calls:** {self.total_llm_calls}")
        lines.append(f"- **Evidence Created:** {self.evidence_created}")
        if self.ddl_missing > 0:
            lines.append(f"- ⚠️ **DDL Missing:** {self.ddl_missing}")
        if self.lock_conflicts > 0:
            lines.append(f"- ⚠️ **Lock Conflicts:** {self.lock_conflicts}")
        lines.append("")
        
        # =================================================================
        # 힌트 분석 (집계)
        # =================================================================
        all_hints = []
        for c in self.candidates:
            all_hints.extend(c.external_hints)
        
        if all_hints:
            lines.append("---")
            lines.append("")
            lines.append("## 🏷️ Hints Analysis")
            lines.append("")
            
            # 힌트 유형별 집계
            hint_types = {}
            for hint in all_hints:
                hint_type = hint.split("=")[0] if "=" in hint else "unknown"
                hint_types[hint_type] = hint_types.get(hint_type, 0) + 1
            
            lines.append("| Hint Type | Count | Examples |")
            lines.append("|:----------|------:|:---------|")
            for hint_type, count in sorted(hint_types.items(), key=lambda x: -x[1]):
                examples = [h for h in all_hints if h.startswith(hint_type + "=")][:3]
                examples_str = ", ".join([f"`{e}`" for e in examples])
                lines.append(f"| {hint_type} | {count} | {examples_str} |")
            lines.append("")
        
        # =================================================================
        # 후보별 상세
        # =================================================================
        lines.append("---")
        lines.append("")
        lines.append("## 📋 Candidate Details")
        lines.append("")
        
        for i, candidate in enumerate(self.candidates, 1):
            status_emoji = {
                "success": "✅",
                "failed": "❌",
                "pending_review": "⏳",
                "pending": "⏸️",
                "evidence_created": "📝",
            }.get(candidate.status, "❓")
            
            # 접이식 섹션 (HTML details 태그)
            element_id_short = candidate.element_id[:30] + "..." if len(candidate.element_id) > 30 else candidate.element_id
            lines.append(f"<details>")
            lines.append(f"<summary><strong>{i}. {status_emoji} {element_id_short}</strong></summary>")
            lines.append("")
            
            # 기본 정보 카드
            lines.append("#### 📌 Basic Info")
            lines.append("")
            lines.append(f"| Field | Value |")
            lines.append("|:------|:------|")
            lines.append(f"| Element ID | `{candidate.element_id}` |")
            if candidate.file_path:
                lines.append(f"| File | `{candidate.file_path}` |")
            if candidate.line_start:
                lines.append(f"| Lines | {candidate.line_start} - {candidate.line_end or candidate.line_start} |")
            lines.append(f"| Confidence | {candidate.confidence:.2f} |")
            lines.append(f"| Duration | {candidate.duration_ms} ms |")
            lines.append(f"| Status | **{candidate.status.upper()}** |")
            lines.append("")
            
            # 힌트
            if candidate.external_hints:
                lines.append("#### 🏷️ External Hints")
                lines.append("")
                for hint in candidate.external_hints:
                    lines.append(f"- `{hint}`")
                lines.append("")
            
            # 처리 흐름 (시퀀스 다이어그램 스타일)
            if candidate.steps:
                lines.append("#### 🔄 Processing Flow")
                lines.append("")
                lines.append("```")
                for step_idx, step in enumerate(candidate.steps):
                    prefix = "├──" if step_idx < len(candidate.steps) - 1 else "└──"
                    lines.append(f"{prefix} 📦 {step.subagent.upper()} ({step.duration_ms}ms)")
                    
                    for tc_idx, tc in enumerate(step.tool_calls):
                        is_last = tc_idx == len(step.tool_calls) - 1
                        tc_prefix = "    └──" if is_last else "    ├──"
                        
                        # 도구 결과 요약
                        if tc.error:
                            result_indicator = "❌ ERROR"
                        elif tc.result:
                            result_preview = str(tc.result)[:30].replace("\n", " ")
                            result_indicator = f"→ {result_preview}..."
                        else:
                            result_indicator = "→ (no result)"
                        
                        lines.append(f"{tc_prefix} 🔧 {tc.tool_name} {result_indicator}")
                lines.append("```")
                lines.append("")
            
            # 결과 상세
            if candidate.result:
                lines.append("#### 📤 Result")
                lines.append("")
                lines.append("```json")
                try:
                    result_json = json.dumps(candidate.result, indent=2, ensure_ascii=False)
                    # 너무 길면 축약
                    if len(result_json) > 1000:
                        lines.append(result_json[:1000])
                        lines.append("... (truncated)")
                    else:
                        lines.append(result_json)
                except:
                    lines.append(str(candidate.result)[:1000])
                lines.append("```")
                lines.append("")
            
            # 에러
            if candidate.error:
                lines.append("#### ⚠️ Error")
                lines.append("")
                lines.append(f"```")
                lines.append(candidate.error)
                lines.append("```")
                lines.append("")
            
            lines.append("</details>")
            lines.append("")
        
        # =================================================================
        # 도구 호출 통계
        # =================================================================
        tool_call_stats = {}
        for candidate in self.candidates:
            for step in candidate.steps:
                for tc in step.tool_calls:
                    if tc.tool_name not in tool_call_stats:
                        tool_call_stats[tc.tool_name] = {"count": 0, "total_ms": 0, "errors": 0}
                    tool_call_stats[tc.tool_name]["count"] += 1
                    tool_call_stats[tc.tool_name]["total_ms"] += tc.duration_ms
                    if tc.error:
                        tool_call_stats[tc.tool_name]["errors"] += 1
        
        if tool_call_stats:
            lines.append("---")
            lines.append("")
            lines.append("## 🔧 Tool Call Statistics")
            lines.append("")
            lines.append("| Tool | Calls | Avg Duration | Errors |")
            lines.append("|:-----|------:|-------------:|-------:|")
            for tool_name, stats in sorted(tool_call_stats.items(), key=lambda x: -x[1]["count"]):
                avg_ms = stats["total_ms"] / stats["count"] if stats["count"] > 0 else 0
                error_indicator = f"⚠️ {stats['errors']}" if stats['errors'] > 0 else "0"
                lines.append(f"| `{tool_name}` | {stats['count']} | {avg_ms:.0f}ms | {error_indicator} |")
            lines.append("")
        
        # =================================================================
        # 전체 에러
        # =================================================================
        if self.errors:
            lines.append("---")
            lines.append("")
            lines.append("## ❌ Errors")
            lines.append("")
            for error in self.errors:
                lines.append(f"- `{error}`")
            lines.append("")
        
        # =================================================================
        # 플랜 (Todo) 섹션
        # =================================================================
        if self.todos:
            lines.append("---")
            lines.append("")
            lines.append("## 📋 Execution Plan (Todos)")
            lines.append("")
            lines.append("| Status | Task |")
            lines.append("|:------:|:-----|")
            for todo in self.todos:
                status_icon = {
                    "pending": "⏸️",
                    "in_progress": "🔄",
                    "completed": "✅"
                }.get(todo.get("status", ""), "❓")
                lines.append(f"| {status_icon} | {todo.get('content', '')} |")
            lines.append("")
        
        # =================================================================
        # 메모리 활용 섹션
        # =================================================================
        if self.memory_usage:
            lines.append("---")
            lines.append("")
            lines.append("## 🧠 Memory Usage")
            lines.append("")
            lines.append("| Type | Key | Value |")
            lines.append("|:-----|:----|:------|")
            for usage in self.memory_usage:
                usage_type = usage.get("type", "unknown")
                key = usage.get("key", "")
                value = str(usage.get("value", ""))[:50]  # 값 축약
                lines.append(f"| {usage_type} | `{key}` | `{value}` |")
            lines.append("")
        
        # =================================================================
        # 푸터
        # =================================================================
        lines.append("---")
        lines.append("")
        lines.append(f"*Generated at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}*")
        lines.append("")
        
        return "\n".join(lines)
    
    def save(self, output_dir: str) -> Dict[str, str]:
        """보고서 파일 저장 (마크다운만, 덮어쓰기)
        
        Args:
            output_dir: 출력 디렉토리
            
        Returns:
            저장된 파일 경로 딕셔너리 {"markdown": "..."}
        """
        output_path = Path(output_dir)
        output_path.mkdir(parents=True, exist_ok=True)
        
        # 마크다운만 저장 (고정 파일명, 덮어쓰기)
        md_path = output_path / "resolution_report.md"
        with open(md_path, "w", encoding="utf-8") as f:
            f.write(self.to_markdown())
        
        return {
            "markdown": str(md_path),
        }


# =============================================================================
# 콜백 핸들러 (LangChain 콜백 기반)
# =============================================================================

try:
    from langchain_core.callbacks import BaseCallbackHandler
    from langchain_core.outputs import LLMResult
    LANGCHAIN_AVAILABLE = True
except ImportError:
    BaseCallbackHandler = object
    LLMResult = None
    LANGCHAIN_AVAILABLE = False


class ReportCollector(BaseCallbackHandler if LANGCHAIN_AVAILABLE else object):
    """도구 호출 수집 콜백
    
    LangChain 콜백 핸들러로 에이전트의 도구 호출을 수집하여
    ResolutionReport를 생성합니다.
    
    사용 예시:
        collector = ReportCollector()
        result = agent.invoke(
            {"messages": [...]},
            config={"callbacks": [collector]}
        )
        report = collector.build_report(run_id="run_001")
    """
    
    def __init__(self):
        if LANGCHAIN_AVAILABLE:
            super().__init__()
        
        self.run_id: Optional[str] = None
        self.started_at: Optional[datetime] = None
        
        # 현재 처리 중인 후보
        self.current_candidate: Optional[CandidateReport] = None
        self.current_step: Optional[StepReport] = None
        self.current_tool_call: Optional[ToolCallReport] = None
        
        # 수집된 후보들
        self.candidates: List[CandidateReport] = []
        
        # 통계
        self.llm_call_count: int = 0
        self.errors: List[str] = []
        
        # 플랜/메모리 (Phase 3)
        self.todos: List[Dict[str, Any]] = []
        self.memory_usage: List[Dict[str, Any]] = []
        
        # 타이밍
        self._tool_start_times: Dict[str, float] = {}
    
    def start_run(self, run_id: str):
        """실행 시작"""
        self.run_id = run_id
        self.started_at = datetime.now()
        self.candidates = []
        self.llm_call_count = 0
        self.errors = []
        self.todos = []
        self.memory_usage = []
    
    def start_candidate(self, candidate_info: Dict[str, Any]):
        """후보 처리 시작"""
        self.current_candidate = CandidateReport(
            element_id=candidate_info.get("element_id", "unknown"),
            external_hints=candidate_info.get("external_hints", []),
            file_path=candidate_info.get("file_path"),
            line_start=candidate_info.get("line_start"),
            line_end=candidate_info.get("line_end"),
            started_at=datetime.now(),
        )
        self.current_step = None
    
    def start_step(self, subagent: str):
        """단계 시작 (서브에이전트)"""
        if self.current_step:
            # 이전 단계 종료
            self.current_step.completed_at = datetime.now()
            if self.current_step.started_at:
                self.current_step.duration_ms = int(
                    (self.current_step.completed_at - self.current_step.started_at).total_seconds() * 1000
                )
        
        self.current_step = StepReport(
            subagent=subagent,
            started_at=datetime.now(),
        )
        if self.current_candidate:
            self.current_candidate.steps.append(self.current_step)
    
    def end_candidate(self, status: str, result: Optional[Dict] = None, error: Optional[str] = None):
        """후보 처리 종료"""
        if self.current_candidate:
            self.current_candidate.status = status
            self.current_candidate.result = result
            self.current_candidate.error = error
            self.current_candidate.completed_at = datetime.now()
            
            if self.current_candidate.started_at:
                self.current_candidate.duration_ms = int(
                    (self.current_candidate.completed_at - self.current_candidate.started_at).total_seconds() * 1000
                )
            
            self.candidates.append(self.current_candidate)
            self.current_candidate = None
            self.current_step = None
    
    # LangChain 콜백 메서드
    def on_tool_start(
        self,
        serialized: Dict[str, Any],
        input_str: str,
        **kwargs: Any,
    ) -> None:
        """도구 시작 콜백"""
        tool_name = serialized.get("name", "unknown")
        run_id = kwargs.get("run_id", "")
        
        # 타이밍 기록
        self._tool_start_times[str(run_id)] = time.time()
        
        # 도구 인자 파싱
        try:
            args = json.loads(input_str) if input_str else {}
        except json.JSONDecodeError:
            args = {"raw": input_str}
        
        self.current_tool_call = ToolCallReport(
            tool_name=tool_name,
            args=args,
            started_at=datetime.now(),
        )
        # 로깅은 on_tool_end에서 결과와 함께 출력 (중복 방지)
    
    def on_tool_end(self, output: str, **kwargs: Any) -> None:
        """도구 종료 콜백"""
        if self.current_tool_call:
            run_id = kwargs.get("run_id", "")
            start_time = self._tool_start_times.pop(str(run_id), None)
            
            self.current_tool_call.result = output
            self.current_tool_call.completed_at = datetime.now()
            
            if start_time:
                self.current_tool_call.duration_ms = int((time.time() - start_time) * 1000)
            
            # 로깅 (도구 결과 출력)
            try:
                from ..helpers.logger import get_logger
                log = get_logger()
                log.tool_call(
                    self.current_tool_call.tool_name, 
                    self.current_tool_call.args,
                    result=output,
                    duration_ms=self.current_tool_call.duration_ms
                )
            except ImportError:
                pass
            
            if self.current_step:
                self.current_step.tool_calls.append(self.current_tool_call)
            
            self.current_tool_call = None
    
    def on_tool_error(self, error: BaseException, **kwargs: Any) -> None:
        """도구 에러 콜백"""
        if self.current_tool_call:
            self.current_tool_call.error = str(error)
            self.current_tool_call.completed_at = datetime.now()
            
            # 로깅 (도구 에러 출력)
            try:
                from ..helpers.logger import get_logger
                log = get_logger()
                log.tool_call(
                    self.current_tool_call.tool_name,
                    self.current_tool_call.args,
                    error=str(error)
                )
            except ImportError:
                pass
            
            if self.current_step:
                self.current_step.tool_calls.append(self.current_tool_call)
            
            self.current_tool_call = None
        
        self.errors.append(f"Tool error: {error}")
    
    def on_llm_start(self, serialized: Dict[str, Any], prompts: List[str], **kwargs: Any) -> None:
        """LLM 호출 시작 콜백"""
        self.llm_call_count += 1
    
    def on_llm_end(self, response: Any, **kwargs: Any) -> None:
        """LLM 호출 종료 콜백"""
        pass
    
    def on_llm_error(self, error: BaseException, **kwargs: Any) -> None:
        """LLM 에러 콜백"""
        self.errors.append(f"LLM error: {error}")
    
    def record_todos(self, todos: List[Dict[str, Any]]) -> None:
        """플랜(Todo) 기록
        
        Args:
            todos: 에이전트 상태에서 추출한 todo 목록
                   [{"content": "...", "status": "pending|in_progress|completed"}, ...]
        """
        # 기존 todos를 새 todos로 대체 (최신 상태 유지)
        self.todos = todos.copy() if todos else []
    
    def record_memory_usage(self, memory_calls: List[Dict[str, Any]]) -> None:
        """메모리 활용 이력 기록
        
        Args:
            memory_calls: /memories/ 경로 관련 도구 호출 목록
                          [{"name": "read_file|edit_file", "args": {"path": "..."}}, ...]
        """
        for call in memory_calls:
            args = call.get("args", {})
            path = args.get("path", "")
            
            # 경로에서 키 추출
            key = path.split("/")[-1] if "/" in path else path
            
            usage = {
                "type": "read" if call.get("name") == "read_file" else "write",
                "key": key,
                "path": path,
                "value": args.get("content", "")[:100] if "content" in args else "(read)",
            }
            self.memory_usage.append(usage)
    
    def build_report(self, run_id: Optional[str] = None) -> ResolutionReport:
        """수집된 데이터로 보고서 생성"""
        report = ResolutionReport(
            run_id=run_id or self.run_id or "unknown",
            started_at=self.started_at or datetime.now(),
            completed_at=datetime.now(),
        )
        
        for candidate in self.candidates:
            report.add_candidate(candidate)
        
        report.total_llm_calls = self.llm_call_count
        report.errors = self.errors.copy()
        report.todos = self.todos.copy()
        report.memory_usage = self.memory_usage.copy()
        
        return report


# =============================================================================
# 편의 함수
# =============================================================================

def create_report_from_phase_result(
    run_id: str,
    phase_result: Any,
    candidates: Optional[List[Dict]] = None,
) -> ResolutionReport:
    """PhaseResult에서 보고서 생성
    
    Args:
        run_id: 실행 ID
        phase_result: RuntimeResolutionPhase.run() 결과
        candidates: 후보 정보 목록 (옵션)
        
    Returns:
        ResolutionReport 인스턴스
    """
    report = ResolutionReport(
        run_id=run_id,
        started_at=phase_result.started_at,
        completed_at=phase_result.completed_at,
        total=phase_result.total_candidates,
        success=phase_result.success,
        failed=phase_result.failed,
        pending_review=phase_result.pending_review,
        evidence_created=phase_result.evidence_created,
        ddl_missing=phase_result.ddl_missing,
        lock_conflicts=phase_result.lock_conflicts,
        errors=phase_result.errors,
    )
    
    # 후보 정보가 제공된 경우 추가
    if candidates:
        for c in candidates:
            candidate_report = CandidateReport(
                element_id=c.get("element_id", "unknown"),
                external_hints=c.get("external_hints", []),
                status=c.get("status", "unknown"),
                confidence=c.get("confidence", 0.0),
            )
            report.candidates.append(candidate_report)
    
    return report

