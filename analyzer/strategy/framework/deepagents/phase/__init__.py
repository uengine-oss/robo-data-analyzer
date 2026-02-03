"""
Phase 실행 모듈

Phase 실행 관련 모듈:
- main_agent: DeepAgent 생성 및 설정 (deepagents 라이브러리 필요)
- fsm_executor: FSM 상태 머신 실행
- runtime_resolution_phase: Phase 실행 및 FrameworkAnalyzer 통합
"""

# FSM Executor (외부 의존성 없음)
from .fsm_executor import (
    FSMExecutor,
    FSMState,
    FSMContext,
)

# main_agent (deepagents 라이브러리 필요)
from .main_agent import (
    create_runtime_resolution_agent,
    process_chunk,  # 청크 단위 처리 (권장)
    process_candidate,  # 레거시 호환용 (deprecated)
    resume_after_hitl,
    normalize_file_path,
)

# runtime_resolution_phase (main_agent 의존)
from .runtime_resolution_phase import (
    RuntimeResolutionPhase,
    PhaseConfig,
    PhaseResult,
    run_runtime_resolution_phase,
    process_pending_reviews,
)

__all__ = [
    # main_agent
    "create_runtime_resolution_agent",
    "process_chunk",  # 청크 단위 처리 (권장)
    "process_candidate",  # 레거시 호환용 (deprecated)
    "resume_after_hitl",
    "normalize_file_path",
    # fsm_executor
    "FSMExecutor",
    "FSMState",
    "FSMContext",
    # runtime_resolution_phase
    "RuntimeResolutionPhase",
    "PhaseConfig",
    "PhaseResult",
    "run_runtime_resolution_phase",
    "process_pending_reviews",
]

