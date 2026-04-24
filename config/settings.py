"""ROBO Analyzer 환경변수 중앙 관리

모든 환경변수를 한 곳에서 관리하여 일관성과 유지보수성을 높입니다.
"""

import os
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Optional
from dotenv import load_dotenv, find_dotenv

# .env 파일 로드 (프로젝트 내부 → 상위 디렉토리 순으로 탐색)
project_root = Path(__file__).resolve().parents[1]
_env_path = project_root / ".env"
if not _env_path.exists():
    _env_path = find_dotenv(usecwd=True)
load_dotenv(dotenv_path=_env_path)


def _get_base_dir() -> str:
    """프로젝트 루트 디렉토리 경로 반환
    
    DOCKER_COMPOSE_CONTEXT가 설정되지 않은 경우,
    robo_analyzer_core와 같은 레벨에 있는 data 폴더를 찾기 위해
    robo_analyzer_core의 부모 디렉토리를 반환합니다.
    """
    if os.getenv("DOCKER_COMPOSE_CONTEXT"):
        return os.getenv("DOCKER_COMPOSE_CONTEXT")
    
    # config/settings.py -> config -> robo_analyzer_core -> Legacy-modernizer (부모)
    # robo_analyzer_core와 같은 레벨에 있는 data 폴더를 찾기 위해 부모 디렉토리 반환
    return str(Path(__file__).resolve().parents[2])


def _get_project_root() -> str:
    """프로젝트 루트 디렉토리 경로 반환 (logs 폴더용)
    
    프로젝트 내부에 logs 폴더를 생성하기 위해 사용합니다.
    """
    # config/settings.py -> config -> robo_analyzer_core (프로젝트 루트)
    return str(Path(__file__).resolve().parents[1])


@dataclass(frozen=True)
class Neo4jConfig:
    """Neo4j 연결 설정"""
    uri: str = field(default_factory=lambda: os.getenv("NEO4J_URI", "bolt://127.0.0.1:7687"))
    user: str = field(default_factory=lambda: os.getenv("NEO4J_USER", "neo4j"))
    password: str = field(default_factory=lambda: os.getenv("NEO4J_PASSWORD", "neo4j"))
    database: str = field(default_factory=lambda: os.getenv("NEO4J_DATABASE", "neo4j"))


@dataclass(frozen=True)
class LLMConfig:
    """LLM API 설정"""
    api_base: str = field(default_factory=lambda: os.getenv("LLM_API_BASE", "https://api.openai.com/v1"))
    api_key: str = field(default_factory=lambda: os.getenv("LLM_API_KEY") or os.getenv("OPENAI_API_KEY", ""))
    model: str = field(default_factory=lambda: os.getenv("LLM_MODEL", "gpt-4.1"))
    max_tokens: int = field(default_factory=lambda: int(os.getenv("LLM_MAX_TOKENS", "32768")))
    reasoning_effort: str = field(default_factory=lambda: os.getenv("LLM_REASONING_EFFORT", "medium"))
    is_custom: bool = field(default_factory=lambda: os.getenv("IS_CUSTOM_LLM", "").lower() == "true")
    company_name: Optional[str] = field(default_factory=lambda: os.getenv("COMPANY_NAME"))
    # LLM 동작 설정
    temperature: float = field(default_factory=lambda: float(os.getenv("LLM_TEMPERATURE", "0.2")))
    reasoning_models: frozenset = field(default_factory=lambda: frozenset({
        "gpt-5", "o1", "o1-pro", "o1-mini", "o1-preview", "o3", "o3-mini", "o4-mini"
    }))
    embedding_model: str = field(default_factory=lambda: os.getenv("EMBEDDING_MODEL", "text-embedding-3-small"))
    # LLM 캐싱 설정
    cache_enabled: bool = field(default_factory=lambda: os.getenv("LLM_CACHE_ENABLED", "true").lower() == "true")
    cache_db_path: str = field(default_factory=lambda: os.getenv("LLM_CACHE_DB", ".llm_cache.db"))
    # 재시도 설정 — 공용 가드레일에서 참조
    max_retries: int = field(default_factory=lambda: int(os.getenv("LLM_MAX_RETRIES", "3")))
    max_parse_retries: int = field(default_factory=lambda: int(os.getenv("LLM_MAX_PARSE_RETRIES", "5")))
    retry_base_delay: float = field(default_factory=lambda: float(os.getenv("LLM_RETRY_BASE_DELAY", "2.0")))
    # parse 실패 시 max_tokens 증액 배수 (직전 값 × N, 모델 한도까지 clamp)
    parse_retry_token_bump: float = field(default_factory=lambda: float(os.getenv("LLM_PARSE_RETRY_TOKEN_BUMP", "1.5")))
    # 모델 context 상한 (증액 clamp 용). frentis/gpt-4.1 = 32768.
    max_token_ceiling: int = field(default_factory=lambda: int(os.getenv("LLM_MAX_TOKEN_CEILING", "32768")))


@dataclass(frozen=True)
class ConcurrencyConfig:
    """병렬 처리 설정"""
    # 파일 단위 병렬 처리
    file_concurrency: int = field(default_factory=lambda: int(os.getenv("FILE_CONCURRENCY", "5")))
    
    # 청크/배치 단위 병렬 처리 (DBMS/Framework 공통)
    max_concurrency: int = field(default_factory=lambda: int(os.getenv("MAX_CONCURRENCY", "20")))
    



@dataclass(frozen=True)
class BatchConfig:
    """배치 처리 설정 (DBMS/Framework 공통). 기본값은 .env 의 실제 값과 일치."""
    max_batch_token: int = field(default_factory=lambda: int(os.getenv("MAX_BATCH_TOKEN", "2000")))
    max_summary_chunk_token: int = field(default_factory=lambda: int(os.getenv("MAX_SUMMARY_CHUNK_TOKEN", "8000")))
    # call_extract LLM 배치당 최대 호출 항목 수 — 응답 길이를 균등하게 유지해 reasoning 모델 지연을 줄인다.
    max_call_records_per_batch: int = field(default_factory=lambda: int(os.getenv("MAX_CALL_RECORDS_PER_BATCH", "80")))
    # Neo4j 쿼리 배치 사이즈 (스트리밍 단위)
    neo4j_query_batch_size: int = field(default_factory=lambda: int(os.getenv("NEO4J_QUERY_BATCH_SIZE", "30")))
    # 부모 컨텍스트 관련 설정
    max_context_token: int = field(default_factory=lambda: int(os.getenv("MAX_CONTEXT_TOKEN", "500")))
    parent_expand_threshold: int = field(default_factory=lambda: int(os.getenv("PARENT_EXPAND_THRESHOLD", "1000")))


@dataclass(frozen=True)
class PathConfig:
    """경로 설정"""
    base_dir: str = field(default_factory=_get_base_dir)
    audit_dir: str = field(default_factory=lambda: os.getenv("LLM_AUDIT_DIR") or os.path.join(_get_project_root(), "logs"))
    
    @property
    def data_dir(self) -> str:
        return os.path.join(self.base_dir, "data")
    
    @property
    def prompt_log_dir(self) -> str:
        return os.path.join(self.audit_dir, "llm_prompts")


@dataclass(frozen=True)
class DeepAgentConfig:
    """DeepAgent 전용 설정. 기본값은 .env 의 실제 값과 일치."""
    entity_batch_size: int = field(
        default_factory=lambda: int(os.getenv("DA_ENTITY_BATCH_SIZE", "15"))
    )
    default_confidence: float = field(
        default_factory=lambda: float(os.getenv("DA_DEFAULT_CONFIDENCE", "0.9"))
    )


@dataclass(frozen=True)
class AnalyzerConfig:
    """ROBO Analyzer 통합 설정"""
    neo4j: Neo4jConfig = field(default_factory=Neo4jConfig)
    llm: LLMConfig = field(default_factory=LLMConfig)
    concurrency: ConcurrencyConfig = field(default_factory=ConcurrencyConfig)
    batch: BatchConfig = field(default_factory=BatchConfig)
    path: PathConfig = field(default_factory=PathConfig)
    deep_agent: DeepAgentConfig = field(default_factory=DeepAgentConfig)
    
    # 버전
    version: str = "2.0.0"

    # 분석 결과 출력 언어
    locale: str = field(default_factory=lambda: os.getenv("EXP_LOCALE", "ko"))
    
    # API 설정
    api_prefix: str = "/robo"
    
    # 서버 설정
    host: str = field(default_factory=lambda: os.getenv("HOST", "0.0.0.0"))
    port: int = field(default_factory=lambda: int(os.getenv("PORT", "5502")))


@lru_cache(maxsize=1)
def get_settings() -> AnalyzerConfig:
    """싱글톤 설정 인스턴스 반환 (캐싱)"""
    return AnalyzerConfig()


# 전역 설정 인스턴스
settings = get_settings()


# ============================================================================
# API 키 관리 함수
# ============================================================================

def set_openai_api_key(api_key: str) -> None:
    """OPENAI_API_KEY 환경변수 설정.

    DeepAgents 라이브러리가 내부적으로 OPENAI_API_KEY 환경변수를 참조하므로,
    DeepAgent 호출 이전 시점(라우터의 API 키 검증 직후)에 1회 설정한다.

    Args:
        api_key: OpenAI API 키
    """
    os.environ["OPENAI_API_KEY"] = api_key

