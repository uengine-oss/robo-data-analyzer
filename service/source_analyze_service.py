"""소스 코드 분석 서비스

소스 코드 분석 파이프라인을 관리합니다.

주요 기능:
- 파일 타입 감지
- 분석 가능한 소스 파일 탐색
- API 키 검증
- 분석 실행
"""

import logging
import os
from typing import AsyncGenerator

from fastapi import HTTPException

from analyzer.strategy import AnalyzerFactory
from client.llm_client import get_llm
from config.settings import settings


# =============================================================================
# 상수
# =============================================================================

# 전략별 소스 파일 확장자 (우선순위 순서)
STRATEGY_EXTENSIONS = {
    "framework": [".java", ".kt", ".scala", ".py", ".c", ".h", ".go", ".rs"],
    "dbms": [".sql", ".pls", ".pck", ".pkb", ".pks", ".trg", ".fnc", ".prc"],
}
DEFAULT_EXTENSIONS = [".java", ".sql"]

# target → 확장자 매핑
TARGET_TO_EXTENSION = {
    "java": ".java",
    "kotlin": ".kt",
    "scala": ".scala",
    "python": ".py",
    "c": ".c",
    "go": ".go",
    "rust": ".rs",
    "oracle": ".sql",
    "postgres": ".sql",
    "mysql": ".sql",
}


# =============================================================================
# 디렉토리 경로 관리
# =============================================================================

def get_analysis_directories(strategy: str) -> dict[str, str]:
    """분석에 필요한 디렉토리 경로 반환
    
    Args:
        strategy: 분석 전략 (framework, dbms, 필수)
        
    Returns:
        {"ddl": path, "source": path, "analysis": path}
    """
    if not strategy:
        raise ValueError("strategy는 필수 파라미터입니다.")
    return {
        "ddl": os.path.join(settings.path.data_dir, "ddl"),
        "source": os.path.join(settings.path.data_dir, "source"),
        "analysis": os.path.join(settings.path.data_dir, "analysis"),
    }


def get_directory_paths(directory: str, base_dirs: dict[str, str]) -> dict[str, str]:
    """디렉토리별 source/analysis 경로 반환
    
    Args:
        directory: 하위 디렉토리명
        base_dirs: 기본 디렉토리 경로
        
    Returns:
        {"source": path, "analysis": path}
    """
    return {
        "source": os.path.join(base_dirs["source"], directory),
        "analysis": os.path.join(base_dirs["analysis"], directory),
    }


def check_ddl_files_exist(dirs: dict[str, str]) -> bool:
    """DDL 디렉토리에 파일이 있는지 확인
    
    Args:
        dirs: 디렉토리 경로 딕셔너리
        
    Returns:
        DDL 파일 존재 여부
    """
    ddl_dir = dirs.get("ddl", "")
    if not ddl_dir or not os.path.isdir(ddl_dir):
        return False
    
    for _, _, files in os.walk(ddl_dir):
        if files:
            return True
    return False


# =============================================================================
# 파일 탐색
# =============================================================================

def discover_analyzable_files(
    strategy: str,
    target: str,
    dirs: dict[str, str]
) -> list[tuple[str, str]]:
    """analysis/ 디렉토리에서 분석 가능한 JSON 파일 목록 탐색
    
    analysis/ 디렉토리의 JSON 파일을 직접 순회하여 분석 대상 목록 생성.
    source 파일은 더 이상 필요하지 않음 (JSON에 code 속성 포함).
    DDL만 있는 경우(analysis 디렉토리가 없는 경우) 빈 리스트 반환.
    
    Args:
        strategy: 분석 전략 (framework, dbms, 필수)
        target: 타겟 언어 (java, kotlin, scala, python, oracle, postgres 등, 필수)
        dirs: 디렉토리 경로 딕셔너리 (필수)
        
    Returns:
        [(directory, file_name), ...] 형태의 튜플 리스트
        file_name은 원본 소스 파일명 (예: procedure.sql)으로 반환
    """
    if not strategy:
        raise ValueError("strategy는 필수 파라미터입니다.")
    if not target:
        raise ValueError("target은 필수 파라미터입니다.")
    if not dirs:
        raise ValueError("dirs는 필수 파라미터입니다.")
    
    analysis_dir = dirs.get("analysis", "")
    
    # DDL만 있는 경우 analysis 디렉토리가 없을 수 있음
    if not analysis_dir or not os.path.isdir(analysis_dir):
        logging.info("analysis 디렉토리가 없습니다 (DDL만 있는 경우일 수 있음): %s", analysis_dir)
        return []
    
    # target 하위 폴더 사용 (analysis/target/)
    target_analysis_dir = os.path.join(analysis_dir, "target")
    if not os.path.isdir(target_analysis_dir):
        logging.info("analysis/target 디렉토리가 없습니다: %s", target_analysis_dir)
        return []
    
    # target에 맞는 확장자 사용 (없으면 strategy의 첫 번째 확장자 사용)
    target_lower = (target or "java").lower()
    target_ext = TARGET_TO_EXTENSION.get(target_lower)
    
    if not target_ext:
        # target 매핑에 없으면 strategy의 첫 번째 확장자 사용
        extensions = STRATEGY_EXTENSIONS.get(strategy, DEFAULT_EXTENSIONS)
        target_ext = extensions[0] if extensions else ".sql"
    
    result = []
    
    # analysis/target 디렉토리를 직접 순회하여 JSON 파일 탐색
    for root, _, files in os.walk(target_analysis_dir):
        rel_dir = os.path.relpath(root, target_analysis_dir)
        directory = "" if rel_dir == "." else rel_dir
        
        for json_file in files:
            if not json_file.endswith('.json'):
                continue
            
            # JSON 파일명에서 원본 소스 파일명 추론
            base_name = os.path.splitext(json_file)[0]
            
            # target에 맞는 확장자 사용
            source_file_name = f"{base_name}{target_ext}"
            
            result.append((directory, source_file_name))
    
    result.sort()
    logging.info("분석 가능 소스 파일: %d개 (target=%s, extension=%s)", len(result), target, target_ext)
    return result


# =============================================================================
# API 키 검증
# =============================================================================

async def validate_llm_api_key(api_key: str) -> None:
    """LLM API 키 유효성 검증
    
    Args:
        api_key: LLM API 키
        
    Raises:
        HTTPException: 검증 실패 시
    """
    try:
        llm = get_llm(api_key=api_key)
        if not llm.invoke("ping"):
            raise HTTPException(401, "API 키 검증 실패: ping 실패")
    except HTTPException:
        raise
    except Exception as e:
        logging.error("API 키 검증 실패: %s", e)
        raise HTTPException(401, f"API 키 검증 실패: {type(e).__name__}: {e}")


# =============================================================================
# 분석 실행
# =============================================================================

async def run_source_analysis(
    file_names: list[tuple[str, str]],
    api_key: str,
    locale: str = "ko",
    strategy: str = "framework",
    target: str = "java",
    name_case: str = "original",
    datasource_name: str = "",
    infer_fk_relations: bool = False,
) -> AsyncGenerator[bytes, None]:
    """소스 파일 분석 → Neo4j 그래프 이벤트 스트리밍
    
    Args:
        file_names: [(directory, file_name), ...] 튜플 리스트
        api_key: LLM API 키
        locale: 출력 언어 (ko, en)
        strategy: 분석 전략 (framework, dbms)
        target: 타겟 언어 (java, oracle 등)
        name_case: 메타데이터 대소문자 변환 옵션
        datasource_name: 타겟 데이터 소스 이름 (Neo4j에서 연결할 DataSource 노드명)
        infer_fk_relations: FK 관계 자동 추론 여부 (기본: False)
        
    Yields:
        NDJSON 스트리밍 이벤트
    """
    # 분석 컨텍스트 생성
    context = AnalysisContext(
        api_key=api_key,
        locale=locale,
        strategy=strategy,
        target=target,
        name_case=name_case,
        datasource_name=datasource_name,
        infer_fk_relations=infer_fk_relations,
    )
    
    analyzer = AnalyzerFactory.create(strategy)
    async for chunk in analyzer.analyze(file_names=file_names, orchestrator=context):
        yield chunk


class AnalysisContext:
    """분석 실행을 위한 컨텍스트 객체
    
    BaseAnalyzer에서 사용하는 orchestrator 인터페이스를 구현합니다.
    """
    
    __slots__ = ("api_key", "locale", "strategy", "target", "name_case", "datasource_name", "infer_fk_relations", "dirs")
    
    def __init__(
        self,
        api_key: str,
        locale: str = "ko",
        strategy: str = "framework",
        target: str = "java",
        name_case: str = "original",
        datasource_name: str = "",
        infer_fk_relations: bool = False,
    ):
        self.api_key = api_key
        self.locale = locale
        self.strategy = (strategy or "framework").lower()
        self.target = (target or "java").lower()
        self.name_case = (name_case or "original").lower()
        self.datasource_name = (datasource_name or "").strip()  # 타겟 데이터 소스 이름
        self.infer_fk_relations = infer_fk_relations  # FK 관계 자동 추론 여부
        self.dirs = get_analysis_directories(strategy)
    
    def get_directory_dirs(self, directory: str) -> dict[str, str]:
        """디렉토리별 source/analysis 경로 반환"""
        return get_directory_paths(directory, self.dirs)
    
    def has_ddl_files(self) -> bool:
        """DDL 디렉토리에 파일이 있는지 확인"""
        return check_ddl_files_exist(self.dirs)

