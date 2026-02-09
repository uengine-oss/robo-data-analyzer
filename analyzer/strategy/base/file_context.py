"""파일 분석 컨텍스트 및 상태 관리

Analyzer 전략 간 공유하는 파일 처리 상태 추적 모듈.

주요 구성:
- FileStatus: 파일 분석 상태 Enum
- FileAnalysisContext: 파일 분석 컨텍스트 데이터 클래스
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, List, Optional


class FileStatus(Enum):
    """파일 분석 상태
    
    상태 흐름:
    PENDING → PH1_OK → PH2_OK (정상)
    PENDING → PH1_FAIL (Phase 1 실패 → Phase 2 스킵)
    PENDING → PH1_OK → PH2_FAIL (Phase 2 실패)
    """
    PENDING = "PENDING"      # 대기 중
    PH1_OK = "PH1_OK"       # Phase 1 성공
    PH1_FAIL = "PH1_FAIL"   # Phase 1 실패 → Phase 2 스킵
    PH2_OK = "PH2_OK"       # Phase 2 성공
    PH2_FAIL = "PH2_FAIL"   # Phase 2 실패
    SKIPPED = "SKIPPED"     # 스킵됨


@dataclass
class FileAnalysisContext:
    """파일 분석 컨텍스트
    
    파일별 상태 추적으로 토큰 절감:
    - Phase1 실패 파일은 Phase2 LLM 호출을 스킵
    - 실패 사유를 기록하여 최종 리포트에 포함
    
    Attributes:
        directory: 파일이 위치한 디렉토리
        file_name: 파일명
        ast_data: 파싱된 AST JSON 데이터
        source_content: 원본 소스 파일 내용 (AST JSON에 code가 없을 때 fallback)
        processor: AST 프로세서 인스턴스 (Phase 1에서 생성, Phase 2에서 재사용)
        status: 현재 파일 상태
        error_message: 실패 시 에러 메시지
    """
    directory: str
    file_name: str
    ast_data: dict
    source_content: str = ""  # 소스 파일 원본 (startLine/endLine으로 코드 추출용)
    processor: Optional[Any] = None  # DbmsAstProcessor | FrameworkAstProcessor
    status: FileStatus = field(default=FileStatus.PENDING)
    error_message: str = ""

