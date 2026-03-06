"""파이프라인 제어 - 일시정지/재개/중단 기능

분석 파이프라인의 각 단계에서 일시정지, 재개, 중단을 지원합니다.
단일 세션 모드로 동작합니다.
"""

import asyncio
from enum import Enum
from typing import Optional
from dataclasses import dataclass, field
import logging

logger = logging.getLogger(__name__)


class PipelinePhase(Enum):
    """파이프라인 단계"""
    IDLE = "idle"
    DDL_PROCESSING = "ddl_processing"           # Phase 0: DDL 처리
    AST_GENERATION = "ast_generation"           # Phase 1: AST 그래프 생성
    LLM_ANALYSIS = "llm_analysis"               # Phase 2: LLM 분석
    STEREOTYPE_CLASSIFICATION = "stereotype_classification"  # Phase 2.5: 스테레오타입 분류
    TABLE_ENRICHMENT = "table_enrichment"       # Phase 3: 테이블 설명 보강
    VECTORIZING = "vectorizing"                 # Phase 4: 벡터라이징 (임베딩 생성)
    LINEAGE_ANALYSIS = "lineage_analysis"       # Phase 5: 데이터 리니지 분석
    USER_STORY = "user_story"                   # Phase 6: User Story 생성
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class PipelineAction(Enum):
    """파이프라인 제어 액션"""
    PAUSE = "pause"
    RESUME = "resume"
    STOP = "stop"


@dataclass
class PhaseInfo:
    """단계 정보"""
    phase: PipelinePhase
    name: str
    description: str
    order: int
    can_pause: bool = True

    def to_dict(self) -> dict:
        return {
            "phase": self.phase.value,
            "name": self.name,
            "description": self.description,
            "order": self.order,
            "canPause": self.can_pause,
        }


# 단계 정의
PIPELINE_PHASES = [
    PhaseInfo(PipelinePhase.DDL_PROCESSING, "DDL 처리", "DDL 파싱 → 테이블/컬럼/스키마 노드 생성", 0, True),
    PhaseInfo(PipelinePhase.AST_GENERATION, "AST 구조 생성", "프로시저/함수 파싱 → 정적 그래프 생성", 1, True),
    PhaseInfo(PipelinePhase.LLM_ANALYSIS, "AI 분석", "LLM으로 스테이트먼트-테이블 관계 식별", 2, True),
    PhaseInfo(PipelinePhase.STEREOTYPE_CLASSIFICATION, "스테레오타입 분류", "LLM으로 클래스/메서드 아키텍처 역할 분류", 2.5, True),
    PhaseInfo(PipelinePhase.TABLE_ENRICHMENT, "테이블 설명 보강", "분석 결과로 테이블/컬럼 설명 업데이트", 3, True),
    PhaseInfo(PipelinePhase.VECTORIZING, "벡터라이징", "테이블/컬럼 임베딩 생성", 4, True),
    PhaseInfo(PipelinePhase.LINEAGE_ANALYSIS, "리니지 분석", "ETL 패턴 감지 → 데이터 흐름 관계 생성", 5, True),
    PhaseInfo(PipelinePhase.USER_STORY, "User Story 생성", "분석 결과 → User Story 문서 생성", 6, False),
]


@dataclass
class PipelineState:
    """파이프라인 상태"""
    current_phase: PipelinePhase = PipelinePhase.IDLE
    is_paused: bool = False
    is_stopped: bool = False
    phase_progress: int = 0  # 0-100
    phase_message: str = ""

    # 동기화용 이벤트
    _pause_event: asyncio.Event = field(default_factory=asyncio.Event)
    _resume_event: asyncio.Event = field(default_factory=asyncio.Event)

    def __post_init__(self):
        # 초기 상태: 정지되지 않음 (실행 가능)
        self._pause_event.set()  # pause_event가 set되어 있으면 실행 가능
        self._resume_event.clear()

    def to_dict(self) -> dict:
        phase_info = next((p for p in PIPELINE_PHASES if p.phase == self.current_phase), None)
        return {
            "currentPhase": self.current_phase.value,
            "phaseName": phase_info.name if phase_info else self.current_phase.value,
            "phaseOrder": phase_info.order if phase_info else -1,
            "isPaused": self.is_paused,
            "isStopped": self.is_stopped,
            "phaseProgress": self.phase_progress,
            "phaseMessage": self.phase_message,
            "phases": [p.to_dict() for p in PIPELINE_PHASES],
        }

    async def wait_if_paused(self) -> bool:
        """일시정지 상태면 대기, 중단되면 False 반환"""
        if self.is_stopped:
            return False

        if self.is_paused:
            logger.info("일시정지 대기 중...")
            # resume_event가 set될 때까지 대기
            await self._resume_event.wait()

            if self.is_stopped:
                return False

            logger.info("재개됨")

        return True

    def pause(self):
        """일시정지"""
        if not self.is_stopped:
            self.is_paused = True
            self._pause_event.clear()
            self._resume_event.clear()
            logger.info("일시정지 요청")

    def resume(self):
        """재개"""
        if self.is_paused and not self.is_stopped:
            self.is_paused = False
            self._pause_event.set()
            self._resume_event.set()
            logger.info("재개 요청")

    def stop(self):
        """중단"""
        self.is_stopped = True
        self.is_paused = False
        self._pause_event.set()  # 대기 중인 작업 깨우기
        self._resume_event.set()  # 대기 중인 작업 깨우기
        logger.info("중단 요청")

    def reset(self):
        """상태 초기화"""
        self.current_phase = PipelinePhase.IDLE
        self.is_paused = False
        self.is_stopped = False
        self.phase_progress = 0
        self.phase_message = ""
        self._pause_event.set()
        self._resume_event.clear()

    def set_phase(self, phase: PipelinePhase, message: str = "", progress: int = 0):
        """현재 단계 설정"""
        self.current_phase = phase
        self.phase_message = message
        self.phase_progress = progress
        logger.info(f"단계: {phase.value} - {message}")

    def update_progress(self, progress: int, message: str = ""):
        """진행률 업데이트"""
        self.phase_progress = min(100, max(0, progress))
        if message:
            self.phase_message = message


class PipelineController:
    """파이프라인 제어 싱글톤 (단일 세션)"""

    _instance: Optional['PipelineController'] = None
    _state: Optional[PipelineState] = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._state = PipelineState()
        return cls._instance

    def get_state(self) -> PipelineState:
        """상태 조회"""
        if self._state is None:
            self._state = PipelineState()
        return self._state

    def reset(self):
        """상태 초기화"""
        self.get_state().reset()

    def pause(self) -> bool:
        """일시정지"""
        state = self.get_state()
        if state.current_phase not in [PipelinePhase.IDLE, PipelinePhase.COMPLETED, PipelinePhase.FAILED, PipelinePhase.CANCELLED]:
            state.pause()
            return True
        return False

    def resume(self) -> bool:
        """재개"""
        state = self.get_state()
        if state.is_paused:
            state.resume()
            return True
        return False

    def stop(self) -> bool:
        """중단"""
        state = self.get_state()
        if state.current_phase not in [PipelinePhase.IDLE, PipelinePhase.COMPLETED, PipelinePhase.FAILED, PipelinePhase.CANCELLED]:
            state.stop()
            return True
        return False

    async def check_continue(self) -> bool:
        """계속 진행 가능 여부 체크

        배치 처리 등에서 간단히 호출:
            if not await pipeline_controller.check_continue():
                return  # 중단됨

        Returns:
            True: 계속 진행 가능
            False: 중단됨 (stop 호출됨)
        """
        return await self.get_state().wait_if_paused()

    def get_status(self) -> dict:
        """상태 조회"""
        return self.get_state().to_dict()

    def get_phases_info(self) -> list:
        """전체 단계 정보"""
        return [p.to_dict() for p in PIPELINE_PHASES]


# 싱글톤 인스턴스
pipeline_controller = PipelineController()
