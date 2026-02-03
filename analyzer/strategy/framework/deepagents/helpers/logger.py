"""
DeepAgents 상세 로깅 모듈

모든 처리 흐름을 상세하게 기록합니다:
- 후보 수집/그룹화
- 청크 구성
- 에이전트 결정 과정
- 도구 호출 (입력/출력 전체)
- TODO/플랜
- 결과 및 에러

pytest 호환:
- 테스트 탐색기에서 실행해도 로그가 즉시 출력됩니다
- pytest 환경을 자동 감지하여 적절한 핸들러를 사용합니다

사용 예시:
    from .logger import get_logger, log
    
    log.phase_start("RuntimeResolution", run_id)
    log.candidate_start(candidate)
    log.tool_call("query_neo4j", args, result)
    log.candidate_end(status)
    log.phase_end(stats)
"""

import logging
import sys
import json
import os
import threading
from datetime import datetime
from typing import Any, Dict, List, Optional, Union


# =============================================================================
# 환경 감지
# =============================================================================

def _is_pytest_running() -> bool:
    """pytest 실행 환경인지 감지"""
    return "pytest" in sys.modules or "_pytest" in sys.modules


def _is_interactive_terminal() -> bool:
    """대화형 터미널 환경인지 감지"""
    return hasattr(sys.stdout, 'isatty') and sys.stdout.isatty()


# =============================================================================
# 로거 설정
# =============================================================================

def _setup_logger(name: str, level: int = logging.INFO) -> logging.Logger:
    """로거 생성 및 설정
    
    환경에 따라 적절한 핸들러 전략 사용:
    - pytest 환경: propagate=True로 pytest 캡처 시스템 사용
    - 일반 환경: 자체 StreamHandler 사용
    
    Args:
        name: 로거 이름
        level: 로그 레벨
    
    Returns:
        설정된 로거
    """
    logger = logging.getLogger(name)
    logger.setLevel(level)
    
    # 기존 핸들러 모두 제거 (중복 방지)
    for handler in logger.handlers[:]:
        logger.removeHandler(handler)
    
    if _is_pytest_running():
        # pytest 환경: propagate=True로 pytest 로깅 시스템 사용
        # pytest가 자체적으로 로그를 캡처하고 출력함
        logger.propagate = True
        # 핸들러는 추가하지 않음 (pytest가 처리)
    else:
        # 일반 환경: 자체 StreamHandler 사용
        logger.propagate = False
        
        handler = logging.StreamHandler(sys.stdout)
        handler.setLevel(level)
        handler.setFormatter(logging.Formatter(
            "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S"
        ))
        logger.addHandler(handler)
    
    return logger


class DeepAgentsLogger:
    """DeepAgents 상세 로거
    
    모든 처리 과정을 상세히 기록합니다.
    
    환경별 동작:
    - pytest: propagate=True로 pytest 로깅 시스템과 통합
    - 일반: 자체 StreamHandler로 stdout 출력
    """
    
    # 컬러 코드 (터미널용)
    COLORS = {
        'HEADER': '\033[95m',
        'BLUE': '\033[94m',
        'CYAN': '\033[96m',
        'GREEN': '\033[92m',
        'YELLOW': '\033[93m',
        'RED': '\033[91m',
        'BOLD': '\033[1m',
        'DIM': '\033[2m',
        'RESET': '\033[0m',
    }
    
    def __init__(self, name: str = "deepagents", level: int = logging.INFO, verbose: bool = True):
        self.logger = _setup_logger(name, level)
        self._current_candidate_id = None
        self._current_file = None
        self._indent_level = 0
        self.verbose = verbose
        self._tool_call_count = 0
        self._start_time = None
        self._lock = threading.Lock()  # 병렬 호출 시 카운트 보호
        self._name = name
        self._level = level
        self._recent_tool_calls = {}  # 중복 호출 감지용: {(tool_name, args_hash): count}
    
    def reconfigure(self, level: int = None, verbose: bool = None):
        """로거 재설정
        
        환경이 변경되었을 때 호출하여 핸들러를 재설정합니다.
        """
        if level is not None:
            self._level = level
        if verbose is not None:
            self.verbose = verbose
        
        self.logger = _setup_logger(self._name, self._level)
    
    def _indent(self, text: str) -> str:
        """들여쓰기 적용"""
        prefix = "    " * self._indent_level
        return f"{prefix}{text}"
    
    def _colorize(self, text: str, color: str) -> str:
        """색상 적용 (터미널용)"""
        if not _is_interactive_terminal():
            return text
        return f"{self.COLORS.get(color, '')}{text}{self.COLORS['RESET']}"
    
    def _format_json(self, obj: Any, max_len: int = 500) -> str:
        """JSON 포맷팅 (읽기 쉽게)"""
        try:
            if obj is None:
                return "null"
            if isinstance(obj, str):
                return obj[:max_len] + ("..." if len(obj) > max_len else "")
            formatted = json.dumps(obj, ensure_ascii=False, indent=2, default=str)
            if len(formatted) > max_len:
                return formatted[:max_len] + "\n... (truncated)"
            return formatted
        except:
            return str(obj)[:max_len]
    
    def _to_dict(self, obj: Any) -> Dict[str, Any]:
        """객체를 dict로 변환"""
        if obj is None:
            return {}
        if isinstance(obj, dict):
            return obj
        if hasattr(obj, 'content'):
            content = obj.content
            if isinstance(content, dict):
                return content
            if isinstance(content, str):
                try:
                    parsed = json.loads(content)
                    if isinstance(parsed, dict):
                        return parsed
                except:
                    pass
        if isinstance(obj, str):
            try:
                parsed = json.loads(obj)
                if isinstance(parsed, dict):
                    return parsed
            except:
                pass
        return {}
    
    # =========================================================================
    # Phase 로깅
    # =========================================================================
    
    def phase_start(self, phase_name: str, run_id: str, config: Optional[Dict] = None):
        """Phase 시작 - 상세 설정 표시"""
        self._start_time = datetime.now()
        self._tool_call_count = 0
        
        self.logger.info("")
        self.logger.info("╔" + "═" * 68 + "╗")
        self.logger.info(f"║ 🚀 Phase: {phase_name:<56} ║")
        self.logger.info(f"║    Run ID: {run_id:<54} ║")
        self.logger.info("╠" + "═" * 68 + "╣")
        
        if config:
            self.logger.info("║ 📋 설정:                                                           ║")
            for key, value in list(config.items())[:5]:
                line = f"    • {key}: {value}"
                self.logger.info(f"║ {line:<66} ║")
        
        self.logger.info("╚" + "═" * 68 + "╝")
        self.logger.info("")
    
    def phase_end(self, stats: Dict[str, Any], duration_seconds: float):
        """Phase 종료 - 전체 통계"""
        self.logger.info("")
        self.logger.info("╔" + "═" * 68 + "╗")
        self.logger.info(f"║ 📊 Phase 완료                                                      ║")
        self.logger.info("╠" + "═" * 68 + "╣")
        self.logger.info(f"║    ⏱️  소요 시간: {duration_seconds:.1f}초" + " " * (47 - len(f"{duration_seconds:.1f}")) + "║")
        self.logger.info(f"║    🔧 총 도구 호출: {self._tool_call_count}회" + " " * (46 - len(str(self._tool_call_count))) + "║")
        self.logger.info("╠" + "═" * 68 + "╣")
        self.logger.info(f"║    ✅ 성공: {stats.get('success', 0)}" + " " * (55 - len(str(stats.get('success', 0)))) + "║")
        self.logger.info(f"║    ❌ 실패: {stats.get('failed', 0)}" + " " * (55 - len(str(stats.get('failed', 0)))) + "║")
        self.logger.info(f"║    ⏳ 검토대기: {stats.get('pending_review', 0)}" + " " * (51 - len(str(stats.get('pending_review', 0)))) + "║")
        self.logger.info("╚" + "═" * 68 + "╝")
    
    # =========================================================================
    # 후보 수집 로깅
    # =========================================================================
    
    def candidates_collected(self, count: int, from_queue: bool = False):
        """수집된 후보 수 - 상세"""
        source = "큐" if from_queue else "Neo4j"
        self.logger.info(f"📋 {source}에서 {count}개 후보 수집됨")
    
    def candidate_details(self, candidates: List[Dict], title: str = "후보 목록"):
        """후보 상세 목록"""
        if not self.verbose or not candidates:
            return
        
        self.logger.info(f"")
        self.logger.info(f"┌─ {title} ({len(candidates)}개) ─────────────────────────")
        for i, c in enumerate(candidates[:10], 1):
            if isinstance(c, dict):
                file_path = c.get('file_path', 'unknown')
                hints = c.get('external_hints', [])[:2]
                confidence = c.get('confidence', 0)
                self.logger.info(f"│  {i}. {file_path}")
                self.logger.info(f"│     힌트: {', '.join(hints) if hints else '(없음)'}")
                self.logger.info(f"│     신뢰도: {confidence:.0%}")
            else:
                self.logger.info(f"│  {i}. {str(c)[:60]}")
        if len(candidates) > 10:
            self.logger.info(f"│  ... 외 {len(candidates) - 10}개 더")
        self.logger.info("└" + "─" * 55)
    
    # =========================================================================
    # 청크/파일 그룹 로깅
    # =========================================================================
    
    def chunk_building_start(self, total: int, max_tokens: int):
        """청크 빌딩 시작"""
        self.logger.info(f"")
        self.logger.info(f"📦 청크 구성 시작")
        self.logger.info(f"   총 후보: {total}개")
        self.logger.info(f"   청크당 최대 토큰: {max_tokens}")
    
    def chunk_formed(self, chunk_id: int, candidates: Union[int, List], tokens: int, candidate_details: List = None):
        """청크 형성 - 상세"""
        count = candidates if isinstance(candidates, int) else len(candidates)
        self.logger.info(f"")
        self.logger.info(f"   📦 청크 #{chunk_id}")
        self.logger.info(f"      후보 수: {count}개")
        self.logger.info(f"      토큰: {tokens}")
        
        if self.verbose and candidate_details:
            for detail in candidate_details[:3]:
                if isinstance(detail, dict):
                    eid = detail.get('element_id', '?')[:25]
                    hints = detail.get('hints', [])[:2]
                    self.logger.info(f"      • {eid}...")
                    if hints:
                        self.logger.info(f"        힌트: {', '.join(hints)}")
    
    def chunk_building_complete(self, chunks: int, avg_tokens: float):
        """청크 빌딩 완료"""
        self.logger.info(f"")
        self.logger.info(f"   ✅ 청크 구성 완료")
        self.logger.info(f"      총 {chunks}개 청크 생성")
        self.logger.info(f"      평균 {avg_tokens:.0f} 토큰/청크")
    
    def log_file_group_start(self, file_path: str, count: int, tokens: int = 0):
        """파일 그룹 처리 시작"""
        self.logger.info(f"📁 파일 그룹: {file_path}")
        self.logger.info(f"   후보: {count}개")
        if tokens:
            self.logger.info(f"   토큰: ~{tokens}")
    
    def log_file_group_end(self, file_path: str, count: int, success: int, pending: int, duration: float):
        """파일 그룹 처리 종료"""
        self.logger.info(f"   ────────────────────────────────────────────────────────")
        self.logger.info(f"   완료: {success}성공, {pending}대기 ({duration:.1f}초)")
    
    def file_group_start(self, file_path: str, count: int):
        """파일 그룹 시작 (간단 버전)"""
        self.logger.info(f"📁 {file_path}: {count}개 후보")
    
    def file_group_end(self, file_path: str, success: int, failed: int, pending: int):
        """파일 그룹 종료 (간단 버전)"""
        self.logger.info(f"   → 결과: 성공={success}, 실패={failed}, 대기={pending}")
    
    # =========================================================================
    # 후보 처리 로깅
    # =========================================================================
    
    def candidate_start(self, candidate: Dict[str, Any], chunk_id: Optional[int] = None):
        """후보 처리 시작 - 상세"""
        self._current_candidate_id = candidate.get("element_id", "?")[:30]
        self._current_file = candidate.get("file_path", "?")
        self._indent_level = 0
        
        hints = candidate.get("external_hints", [])
        missing = candidate.get("missing_bindings", [])
        confidence = candidate.get("confidence", 0)
        line_start = candidate.get("line_start", 0)
        line_end = candidate.get("line_end", 0)
        
        self.logger.info("")
        self.logger.info("┌" + "─" * 58 + "┐")
        self.logger.info(f"│ 🔍 후보 분석 시작" + " " * 39 + "│")
        self.logger.info("├" + "─" * 58 + "┤")
        self.logger.info(f"│   파일: {self._current_file[:45]:<45} │")
        if line_start and line_end:
            self.logger.info(f"│   위치: {line_start}-{line_end}행" + " " * (45 - len(f"{line_start}-{line_end}행")) + "│")
        self.logger.info(f"│   신뢰도: {confidence:.0%}" + " " * (47 - len(f"{confidence:.0%}")) + "│")
        self.logger.info("├" + "─" * 58 + "┤")
        
        if hints:
            self.logger.info(f"│   📌 외부 힌트 ({len(hints)}개):" + " " * (39 - len(str(len(hints)))) + "│")
            for hint in hints[:5]:
                hint_str = str(hint)[:45]
                self.logger.info(f"│      • {hint_str:<48} │")
        
        if missing:
            self.logger.info(f"│   ⚠️  미해결 바인딩 ({len(missing)}개):" + " " * (35 - len(str(len(missing)))) + "│")
            for bind in missing[:5]:
                bind_str = str(bind)[:45]
                self.logger.info(f"│      • {bind_str:<48} │")
        
        self.logger.info("└" + "─" * 58 + "┘")
    
    def candidate_end(self, status: str, result: Optional[Dict] = None, error: Optional[str] = None):
        """후보 처리 종료 - 상세"""
        result_dict = self._to_dict(result) if result else {}
        
        self.logger.info("")
        
        if status == 'success':
            self.logger.info("┌─ ✅ 처리 성공 ─────────────────────────────────────────┐")
            if result_dict.get('tables_found'):
                tables = result_dict['tables_found']
                self.logger.info(f"│   발견된 테이블: {', '.join(tables[:3]):<39} │")
            if result_dict.get('patch_id'):
                self.logger.info(f"│   패치 ID: {result_dict['patch_id'][:20]:<45} │")
            if result_dict.get('artifact_key'):
                self.logger.info(f"│   Artifact: {result_dict['artifact_key'][:40]:<44} │")
            self.logger.info("└" + "─" * 56 + "┘")
            
        elif status == 'pending_review':
            self.logger.info("┌─ ⏳ 검토 대기 ─────────────────────────────────────────┐")
            if result_dict.get('reasons'):
                reasons = result_dict['reasons']
                if isinstance(reasons, list):
                    for reason in reasons[:3]:
                        self.logger.info(f"│   사유: {str(reason)[:47]:<47} │")
                else:
                    self.logger.info(f"│   사유: {str(reasons)[:47]:<47} │")
            if result_dict.get('tables_found'):
                tables = result_dict['tables_found']
                self.logger.info(f"│   관련 테이블: {', '.join(tables[:3]):<41} │")
            self.logger.info("└" + "─" * 56 + "┘")
            
        elif status == 'failed':
            self.logger.info("┌─ ❌ 처리 실패 ─────────────────────────────────────────┐")
            if error:
                error_lines = [error[i:i+47] for i in range(0, min(len(error), 200), 47)]
                for line in error_lines:
                    self.logger.info(f"│   {line:<53} │")
            self.logger.info("└" + "─" * 56 + "┘")
        else:
            self.logger.info(f"📋 처리 결과: {status}")
            if result_dict:
                self.logger.info(f"   상세: {self._format_json(result_dict, 200)}")
        
        self._current_candidate_id = None
        self._current_file = None
        self._indent_level = 0
    
    # =========================================================================
    # 도구 호출 로깅 (상세)
    # =========================================================================
    
    def tool_call(self, tool_name: str, args: Dict[str, Any], result: Any = None, 
                  error: str = None, duration_ms: int = 0):
        """도구 호출 - 상세 입력/출력 (Lock으로 병렬 처리 시 카운트 보호 + 한 번에 출력)"""
        parsed_args = self._parse_args(args)
        
        # 중복 호출 감지 (같은 도구 + 같은 인자)
        args_hash = self._hash_args(tool_name, parsed_args)
        is_duplicate = False
        
        with self._lock:
            self._tool_call_count += 1
            call_num = self._tool_call_count
            
            # 중복 호출 체크
            if args_hash in self._recent_tool_calls:
                self._recent_tool_calls[args_hash] += 1
                is_duplicate = True
            else:
                self._recent_tool_calls[args_hash] = 1
        
        parsed_result = self._parse_result(result)
        
        lines = []
        lines.append("")
        
        # 중복 호출 시 경고 표시
        if is_duplicate:
            lines.append(f"⚠️ [{call_num}] {tool_name} (중복 호출 #{self._recent_tool_calls[args_hash]})")
        else:
            lines.append(f"🔧 [{call_num}] {tool_name}")
        
        if parsed_args:
            lines.append(f"   📥 입력:")
            for key, value in list(parsed_args.items())[:5]:
                value_str = str(value)
                if len(value_str) > 80:
                    value_str = value_str[:80] + "..."
                lines.append(f"      {key}: {value_str}")
        
        if error:
            lines.append(f"   ❌ 에러: {error[:100]}")
            self.logger.info("\n".join(lines))
            return
        
        if parsed_result is not None:
            lines.append(f"   📤 결과:")
            result_str = self._format_result_for_log(tool_name, parsed_result)
            for line in result_str.split('\n')[:10]:
                lines.append(f"      {line}")
        
        if duration_ms > 0:
            lines.append(f"   ⏱️  {duration_ms}ms")
        
        self.logger.info("\n".join(lines))
    
    def _hash_args(self, tool_name: str, args: Dict[str, Any]) -> str:
        """도구 이름과 인자를 해시하여 중복 감지용 키 생성"""
        try:
            args_str = json.dumps(args, sort_keys=True, default=str)
        except:
            args_str = str(args)
        return f"{tool_name}:{args_str}"
    
    def reset_duplicate_tracking(self):
        """중복 호출 추적 초기화 (새 후보 처리 시작 시 호출)"""
        with self._lock:
            self._recent_tool_calls.clear()
    
    def _parse_args(self, args: Dict[str, Any]) -> Dict[str, Any]:
        """도구 인자 파싱"""
        if not args:
            return {}
        
        if 'raw' in args and isinstance(args['raw'], str):
            try:
                import ast
                return ast.literal_eval(args['raw'])
            except:
                try:
                    return json.loads(args['raw'])
                except:
                    pass
        
        return args
    
    def _parse_result(self, result: Any) -> Any:
        """도구 결과 파싱"""
        if result is None:
            return None
        
        if hasattr(result, 'content'):
            content = result.content
            if isinstance(content, str):
                try:
                    return json.loads(content)
                except:
                    return content
            return content
        
        if isinstance(result, str):
            try:
                return json.loads(result)
            except:
                return result
        
        return result
    
    def _format_result_for_log(self, tool_name: str, result: Any) -> str:
        """도구별 결과 포맷팅"""
        if result is None:
            return "(없음)"
        
        neo4j_tools = {
            'apply_graph_mutations', 'set_status_success', 'set_status_review', 'set_status_failed',
            'create_table_schema', 'enrich_table_schema', 'add_fk_relationship',
            'query_neo4j', 'search_nodes_by_keyword', 'get_node_children'
        }
        
        if tool_name in neo4j_tools:
            return self._format_neo4j_result(tool_name, result)
        
        if isinstance(result, list):
            if len(result) == 0:
                return "(빈 리스트)"
            count = len(result)
            sample = result[:3]
            lines = [f"총 {count}개 항목"]
            for i, item in enumerate(sample, 1):
                item_str = str(item)[:60]
                lines.append(f"  [{i}] {item_str}")
            if count > 3:
                lines.append(f"  ... 외 {count - 3}개")
            return '\n'.join(lines)
        
        if isinstance(result, dict):
            lines = []
            for key, value in list(result.items())[:5]:
                value_str = str(value)[:50]
                lines.append(f"{key}: {value_str}")
            return '\n'.join(lines)
        
        if isinstance(result, str):
            if len(result) > 200:
                return result[:200] + "..."
            return result
        
        if isinstance(result, bool):
            return "✓ True" if result else "✗ False"
        
        return str(result)[:200]
    
    def _format_neo4j_result(self, tool_name: str, result: Any) -> str:
        """Neo4j 도구 결과 상세 포맷팅"""
        lines = []
        result_dict = self._to_dict(result) if not isinstance(result, dict) else result
        
        if tool_name == 'apply_graph_mutations':
            lines.append("📌 그래프 배치 적용됨")
            if isinstance(result_dict, dict):
                if result_dict.get('success'):
                    lines.append("  ✅ 성공")
                if result_dict.get('results'):
                    results = result_dict['results']
                    lines.append(f"  적용된 mutation: {len(results)}개")
                    for r in results[:3]:
                        if isinstance(r, dict):
                            lines.append(f"    • {r.get('type', '?')}")
                if result_dict.get('error'):
                    lines.append(f"  ❌ 에러: {result_dict['error']}")
            else:
                lines.append(f"  {str(result)[:100]}")
                
        elif tool_name == 'set_status_success':
            lines.append("✅ 상태 업데이트: success")
            if isinstance(result_dict, dict) and result_dict.get('element_id'):
                lines.append(f"  노드: {str(result_dict['element_id'])[:30]}...")
            else:
                lines.append(f"  {str(result)[:100]}")
                
        elif tool_name == 'set_status_review':
            lines.append("⏳ 상태 업데이트: review")
            if isinstance(result_dict, dict):
                if result_dict.get('element_id'):
                    lines.append(f"  노드: {str(result_dict['element_id'])[:30]}...")
                if result_dict.get('reason'):
                    lines.append(f"  사유: {result_dict['reason']}")
            else:
                lines.append(f"  {str(result)[:100]}")
                
        elif tool_name == 'set_status_failed':
            lines.append("❌ 상태 업데이트: failed")
            if isinstance(result_dict, dict):
                if result_dict.get('element_id'):
                    lines.append(f"  노드: {str(result_dict['element_id'])[:30]}...")
                if result_dict.get('reason'):
                    lines.append(f"  사유: {result_dict['reason']}")
            else:
                lines.append(f"  {str(result)[:100]}")
                
        elif tool_name == 'create_table_schema':
            lines.append("🗄️ 테이블 스키마 생성됨")
            if isinstance(result_dict, dict):
                if result_dict.get('table_name'):
                    lines.append(f"  테이블명: {result_dict['table_name']}")
                if result_dict.get('columns'):
                    cols = result_dict['columns']
                    lines.append(f"  컬럼 수: {len(cols) if isinstance(cols, list) else '?'}개")
                if result_dict.get('node_id'):
                    lines.append(f"  노드 ID: {str(result_dict['node_id'])[:30]}...")
            else:
                lines.append(f"  {str(result)[:100]}")
                
        elif tool_name == 'enrich_table_schema':
            lines.append("📝 테이블 스키마 보강됨")
            if isinstance(result_dict, dict):
                if result_dict.get('table_name'):
                    lines.append(f"  테이블명: {result_dict['table_name']}")
                if result_dict.get('columns_added'):
                    lines.append(f"  추가된 컬럼: {result_dict['columns_added']}개")
                if result_dict.get('properties_updated'):
                    lines.append(f"  업데이트된 속성: {result_dict['properties_updated']}")
            else:
                lines.append(f"  {str(result)[:100]}")
                
        elif tool_name == 'add_fk_relationship':
            lines.append("🔗 FK 관계 생성됨")
            if isinstance(result_dict, dict):
                from_t = result_dict.get('from_table', '?')
                to_t = result_dict.get('to_table', '?')
                fk_col = result_dict.get('fk_column', '?')
                lines.append(f"  {from_t}.{fk_col} → {to_t}")
            else:
                lines.append(f"  {str(result)[:100]}")
                
        elif tool_name == 'query_neo4j':
            if isinstance(result, list):
                lines.append(f"총 {len(result)}개 결과")
                for i, row in enumerate(result[:3], 1):
                    row_str = str(row)[:60]
                    lines.append(f"  [{i}] {row_str}")
                if len(result) > 3:
                    lines.append(f"  ... 외 {len(result) - 3}개")
            else:
                lines.append(f"  {str(result)[:100]}")
                
        elif tool_name in ('search_nodes_by_keyword', 'get_node_children'):
            if isinstance(result, list):
                lines.append(f"총 {len(result)}개 노드 발견")
                for i, node in enumerate(result[:3], 1):
                    if isinstance(node, dict):
                        node_id = str(node.get('element_id', node.get('id', '?')))[:25]
                        node_type = node.get('label', node.get('type', '?'))
                        lines.append(f"  [{i}] {node_type}: {node_id}...")
                    else:
                        lines.append(f"  [{i}] {str(node)[:50]}")
                if len(result) > 3:
                    lines.append(f"  ... 외 {len(result) - 3}개")
            else:
                lines.append(f"  {str(result)[:100]}")
        else:
            lines.append(str(result)[:150])
        
        return '\n'.join(lines) if lines else "(결과 없음)"
    
    # =========================================================================
    # 계획/TODO 로깅
    # =========================================================================
    
    def log_plan(self, plan: str, hints: List[str] = None):
        """계획 로깅"""
        self.logger.info("")
        self.logger.info("┌─ 📋 분석 계획 ─────────────────────────────────────────┐")
        
        plan_lines = plan.split('\n') if plan else ["(계획 없음)"]
        for line in plan_lines[:10]:
            line = line.strip()[:50]
            self.logger.info(f"│   {line:<53} │")
        
        if hints:
            self.logger.info("├" + "─" * 56 + "┤")
            self.logger.info(f"│   🎯 분석 대상:" + " " * 40 + "│")
            for hint in hints[:5]:
                hint_str = str(hint)[:50]
                self.logger.info(f"│      • {hint_str:<48} │")
        
        self.logger.info("└" + "─" * 56 + "┘")
    
    def log_todo_list(self, todos: List[Dict[str, Any]]):
        """TODO 목록 로깅"""
        if not todos:
            self.logger.info("📝 TODO: (없음)")
            return
        
        self.logger.info("")
        self.logger.info("┌─ 📝 TODO 목록 ─────────────────────────────────────────┐")
        
        for i, todo in enumerate(todos[:10], 1):
            if isinstance(todo, dict):
                status = todo.get('status', todo.get('state', 'pending'))
                content = todo.get('content', todo.get('text', todo.get('task', todo.get('description', ''))))
                
                if not content:
                    content = str(todo)[:50]
                else:
                    content = str(content)[:50]
                
                if status in ('completed', 'done', 'complete'):
                    marker = "✓"
                elif status in ('in_progress', 'running', 'active'):
                    marker = "○"
                else:
                    marker = " "
                
                self.logger.info(f"│   [{marker}] {i}. {content:<47} │")
            elif isinstance(todo, str):
                self.logger.info(f"│   [ ] {i}. {todo[:50]:<47} │")
            else:
                self.logger.info(f"│   [ ] {i}. {str(todo)[:50]:<47} │")
        
        if len(todos) > 10:
            self.logger.info(f"│   ... 외 {len(todos) - 10}개 더" + " " * 43 + "│")
        
        self.logger.info("└" + "─" * 56 + "┘")
    
    # =========================================================================
    # Neo4j 작업 요약 로깅
    # =========================================================================
    
    def neo4j_relationship_created(self, rel_type: str, source: str, target: str, 
                                    properties: Dict[str, Any] = None):
        """Neo4j 관계 생성 로깅"""
        self.logger.info("")
        self.logger.info("┌─ 🔗 관계 생성됨 ───────────────────────────────────────┐")
        self.logger.info(f"│   타입: {rel_type:<48} │")
        self.logger.info(f"│   소스: {source[:45]:<48} │")
        self.logger.info(f"│   타겟: {target[:45]:<48} │")
        if properties:
            self.logger.info("├" + "─" * 56 + "┤")
            self.logger.info(f"│   속성:" + " " * 48 + "│")
            for key, value in list(properties.items())[:5]:
                prop_str = f"{key}: {str(value)[:35]}"
                self.logger.info(f"│      {prop_str:<50} │")
        self.logger.info("└" + "─" * 56 + "┘")
    
    def neo4j_node_created(self, node_type: str, node_id: str, 
                           properties: Dict[str, Any] = None):
        """Neo4j 노드 생성 로깅"""
        self.logger.info("")
        self.logger.info("┌─ 📦 노드 생성됨 ───────────────────────────────────────┐")
        self.logger.info(f"│   타입: {node_type:<48} │")
        self.logger.info(f"│   ID: {node_id[:48]:<50} │")
        if properties:
            self.logger.info("├" + "─" * 56 + "┤")
            self.logger.info(f"│   속성:" + " " * 48 + "│")
            for key, value in list(properties.items())[:5]:
                prop_str = f"{key}: {str(value)[:35]}"
                self.logger.info(f"│      {prop_str:<50} │")
        self.logger.info("└" + "─" * 56 + "┘")
    
    def neo4j_node_updated(self, node_id: str, updated_properties: Dict[str, Any]):
        """Neo4j 노드 속성 업데이트 로깅"""
        self.logger.info("")
        self.logger.info("┌─ 📝 노드 업데이트됨 ────────────────────────────────────┐")
        self.logger.info(f"│   노드 ID: {node_id[:45]:<45} │")
        if updated_properties:
            self.logger.info("├" + "─" * 56 + "┤")
            self.logger.info(f"│   변경된 속성:" + " " * 41 + "│")
            for key, value in list(updated_properties.items())[:5]:
                prop_str = f"{key}: {str(value)[:35]}"
                self.logger.info(f"│      {prop_str:<50} │")
        self.logger.info("└" + "─" * 56 + "┘")
    
    def neo4j_patch_summary(self, patch_id: str, action: str, 
                            relationships: List[Dict] = None,
                            nodes_created: List[Dict] = None,
                            nodes_updated: List[Dict] = None):
        """Neo4j 패치 작업 요약 로깅"""
        self.logger.info("")
        self.logger.info("╔" + "═" * 56 + "╗")
        self.logger.info(f"║ 🏷️ 패치 {action}: {patch_id[:35]:<35} ║")
        self.logger.info("╠" + "═" * 56 + "╣")
        
        if relationships:
            self.logger.info(f"║ 🔗 생성된 관계 ({len(relationships)}개):" + " " * 33 + "║")
            for rel in relationships[:3]:
                rel_type = rel.get('type', '?')
                source = str(rel.get('source', '?'))[:15]
                target = str(rel.get('target', '?'))[:15]
                self.logger.info(f"║    {source} --[{rel_type}]--> {target:<20} ║")
            if len(relationships) > 3:
                self.logger.info(f"║    ... 외 {len(relationships) - 3}개" + " " * 40 + "║")
        
        if nodes_created:
            self.logger.info(f"║ 📦 생성된 노드 ({len(nodes_created)}개):" + " " * 33 + "║")
            for node in nodes_created[:3]:
                node_type = node.get('type', '?')
                node_name = str(node.get('name', node.get('id', '?')))[:30]
                self.logger.info(f"║    [{node_type}] {node_name:<42} ║")
            if len(nodes_created) > 3:
                self.logger.info(f"║    ... 외 {len(nodes_created) - 3}개" + " " * 40 + "║")
        
        if nodes_updated:
            self.logger.info(f"║ 📝 업데이트된 노드 ({len(nodes_updated)}개):" + " " * 29 + "║")
            for node in nodes_updated[:3]:
                node_id = str(node.get('id', '?'))[:25]
                props = list(node.get('properties', {}).keys())[:2]
                props_str = ', '.join(props) if props else '?'
                self.logger.info(f"║    {node_id}... ({props_str})" + " " * (20 - len(props_str)) + "║")
            if len(nodes_updated) > 3:
                self.logger.info(f"║    ... 외 {len(nodes_updated) - 3}개" + " " * 40 + "║")
        
        self.logger.info("╚" + "═" * 56 + "╝")
    
    def neo4j_work_summary(self, total_relationships: int, total_nodes_created: int,
                           total_nodes_updated: int, tables_found: List[str] = None):
        """Neo4j 전체 작업 요약"""
        self.logger.info("")
        self.logger.info("┏━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┓")
        self.logger.info("┃ 📊 Neo4j 작업 요약                                      ┃")
        self.logger.info("┣━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┫")
        self.logger.info(f"┃   🔗 생성된 관계: {total_relationships:<38} ┃")
        self.logger.info(f"┃   📦 생성된 노드: {total_nodes_created:<38} ┃")
        self.logger.info(f"┃   📝 업데이트된 노드: {total_nodes_updated:<34} ┃")
        
        if tables_found:
            self.logger.info("┣━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┫")
            self.logger.info(f"┃   🗄️ 발견된 테이블 ({len(tables_found)}개):" + " " * 28 + "┃")
            for table in tables_found[:5]:
                self.logger.info(f"┃      • {table:<48} ┃")
            if len(tables_found) > 5:
                self.logger.info(f"┃      ... 외 {len(tables_found) - 5}개" + " " * 38 + "┃")
        
        self.logger.info("┗━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┛")
    
    # =========================================================================
    # 에이전트 관련 로깅
    # =========================================================================
    
    def agent_thinking(self, thought: str):
        """에이전트 사고 과정"""
        self.logger.info(f"💭 에이전트 사고:")
        for line in thought.split('\n')[:5]:
            self.logger.info(f"   {line[:70]}")
    
    def agent_decision(self, decision: str, reason: str = ""):
        """에이전트 결정"""
        self.logger.info(f"🎯 결정: {decision}")
        if reason:
            self.logger.info(f"   이유: {reason[:100]}")
    
    def log_subagent_handoff(self, from_agent: str, to_agent: str, reason: str = ""):
        """서브에이전트 위임"""
        self.logger.info(f"")
        self.logger.info(f"🤝 에이전트 위임: {from_agent} → {to_agent}")
        if reason:
            self.logger.info(f"   태스크: {reason[:80]}")
    
    # =========================================================================
    # 특정 작업 로깅
    # =========================================================================
    
    def evidence_created(self, patch_id: str, file: str = ""):
        """에비던스 생성"""
        self.logger.info(f"📝 에비던스 생성: {patch_id}")
        if file:
            self.logger.info(f"   파일: {file}")
    
    def ddl_check(self, table: str, exists: bool, details: str = ""):
        """DDL 확인"""
        status = "✓ 존재" if exists else "✗ 없음"
        self.logger.info(f"📊 DDL 확인: {table} → {status}")
        if details:
            self.logger.info(f"   {details}")
    
    def commit_lock_acquired(self, node_id: str):
        """커밋 락 획득"""
        self.logger.info(f"🔒 락 획득: {node_id[:30]}...")
    
    def commit_lock_released(self, node_id: str):
        """커밋 락 해제"""
        self.logger.info(f"🔓 락 해제: {node_id[:30]}...")
    
    def review_queue_added(self, patch_id: str, reasons: List[str]):
        """리뷰 큐 추가"""
        reasons_str = ', '.join(reasons[:3]) if reasons else "(이유 없음)"
        self.logger.info(f"⏳ 검토 대기 추가: {reasons_str}")
    
    def memory_check(self, path: str, found: bool, pattern: Dict = None):
        """메모리 확인"""
        status = "✓ 발견" if found else "✗ 없음"
        self.logger.info(f"🧠 메모리 확인: {path} → {status}")
        if found and pattern and self.verbose:
            self.logger.info(f"   패턴: {self._format_json(pattern, 100)}")
    
    def memory_save(self, path: str, pattern: Dict):
        """메모리 저장"""
        self.logger.info(f"🧠 메모리 저장: {path}")
        if self.verbose:
            self.logger.info(f"   패턴: {self._format_json(pattern, 100)}")
    
    def log_analysis_result(self, tables: List[str], confidence: float, evidence: List[str] = None):
        """분석 결과"""
        self.logger.info(f"")
        self.logger.info(f"📊 분석 결과:")
        self.logger.info(f"   테이블: {', '.join(tables[:5])}")
        self.logger.info(f"   신뢰도: {confidence:.1%}")
        if evidence:
            self.logger.info(f"   증거: {len(evidence)}개")
    
    # =========================================================================
    # 유틸리티 메서드
    # =========================================================================
    
    def custom(self, message: str):
        """커스텀 메시지"""
        self.logger.info(message)
    
    def debug(self, message: str):
        """디버그 메시지"""
        self.logger.debug(message)
    
    def warning(self, message: str):
        """경고 메시지"""
        self.logger.warning(f"⚠️ {message}")
    
    def error(self, message: str):
        """에러 메시지"""
        self.logger.error(f"❌ {message}")
    
    def separator(self, char: str = "─", width: int = 60):
        """구분선 출력"""
        self.logger.info(char * width)
    
    def blank_line(self):
        """빈 줄"""
        self.logger.info("")
    
    # 레거시 호환 메서드들
    def candidates_query(self, query: str):
        self.logger.info(f"🔍 쿼리: {query[:80]}...")
    
    def step_start(self, step: str):
        self.logger.info(f"→ {step}")
    
    def step_end(self, step: str, duration_ms: int = 0):
        if duration_ms:
            self.logger.info(f"← {step} ({duration_ms}ms)")
        else:
            self.logger.info(f"← {step}")
    
    def log_tool_detail(self, tool: str, input_summary: str, output_summary: str, duration_ms: int = 0):
        self.tool_call(tool, {"summary": input_summary}, output_summary, duration_ms=duration_ms)


# =============================================================================
# 모듈 수준 API
# =============================================================================

_default_logger: Optional[DeepAgentsLogger] = None


def get_logger(name: str = "deepagents", verbose: bool = True) -> DeepAgentsLogger:
    """DeepAgentsLogger 인스턴스 반환 (싱글톤)
    
    pytest 환경에서도 정상 동작합니다.
    """
    global _default_logger
    if _default_logger is None:
        _default_logger = DeepAgentsLogger(name, verbose=verbose)
    return _default_logger


def configure_logger(level: int = logging.INFO, use_colors: bool = True, 
                     log_file: str = None, verbose: bool = True) -> DeepAgentsLogger:
    """로거 설정
    
    Args:
        level: 로그 레벨 (logging.INFO, logging.DEBUG 등)
        use_colors: 컬러 사용 여부 (터미널 환경에서만 적용)
        log_file: 로그 파일 경로 (현재 미사용)
        verbose: 상세 모드
    
    Returns:
        설정된 DeepAgentsLogger 인스턴스
    """
    logger = get_logger(verbose=verbose)
    logger.reconfigure(level=level, verbose=verbose)
    return logger


def reset_logger():
    """로거 싱글톤 초기화 (테스트용)"""
    global _default_logger
    _default_logger = None


# 편의를 위한 전역 변수
log = get_logger()
