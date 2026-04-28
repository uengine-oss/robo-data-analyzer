"""ROBO Analyzer 환경변수 중앙 관리

모든 환경변수를 한 곳에서 관리하여 일관성과 유지보수성을 높입니다.
"""

import os
from dataclasses import dataclass, field
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
    """data 폴더 부모 디렉토리 경로 반환.

    DOCKER_COMPOSE_CONTEXT 가 설정되어 있으면 그 값을 그대로 사용.
    아니면 robo-data-analyzer 와 같은 레벨에 위치한 data 폴더에 접근하기 위해
    robo-data-analyzer 의 부모 디렉토리를 반환한다.
    """
    if os.getenv("DOCKER_COMPOSE_CONTEXT"):
        return os.getenv("DOCKER_COMPOSE_CONTEXT")

    # config/settings.py -> config -> robo-data-analyzer -> 부모 디렉토리
    return str(Path(__file__).resolve().parents[2])


def _get_project_root() -> str:
    """robo-data-analyzer 프로젝트 루트 경로 반환 (내부 logs 폴더용)."""
    # config/settings.py -> config -> robo-data-analyzer
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
    api_key: str = field(default_factory=lambda: os.getenv("LLM_API_KEY", ""))
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
    # 모델 context 상한 (증액 clamp 용). 모델별 native context 에 맞춰 .env 에서 지정.
    max_token_ceiling: int = field(default_factory=lambda: int(os.getenv("LLM_MAX_TOKEN_CEILING", "32768")))
    # OpenRouter provider 선택 — base_url 이 OpenRouter 일 때만 적용.
    # 우선순위: sort 가 설정되면 sort, 아니면 order 사용.
    #
    # 방법 1 (권장): sort="throughput" — OpenRouter 가 실시간 throughput 으로 자동 최고속 provider 선택.
    #   slug 외울 필요 X, provider 추가/순위 변경 자동 반영. 다운 시 자동 fallback.
    # 방법 2: order=[...] — 명시적 순서 + slug 정확해야 (틀리면 silent skip).
    openrouter_provider_sort: str = field(default_factory=lambda: os.getenv("OPENROUTER_PROVIDER_SORT", "").strip())
    openrouter_provider_order: tuple = field(default_factory=lambda: tuple(
        p.strip() for p in os.getenv("OPENROUTER_PROVIDER_ORDER", "").split(",") if p.strip()
    ))
    openrouter_allow_fallbacks: bool = field(default_factory=lambda: os.getenv("OPENROUTER_ALLOW_FALLBACKS", "true").lower() == "true")

    # OpenRouter reasoning 모드 토글 — 일부 모델 (Gemma 4 31B 등) 이 reasoning 토큰을 추가 출력.
    # 분석 task 가 reasoning 을 안 쓰면 끄는 게 응답 시간 ↓ (reasoning 토큰 통째로 빠짐).
    # default true (안전 — 정확도 우선). false 로 두면 추가 속도 향상.
    openrouter_reasoning_enabled: bool = field(default_factory=lambda: os.getenv("OPENROUTER_REASONING_ENABLED", "true").lower() == "true")


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
class CatalogConfig:
    """robo-data-catalog 서비스 연동 설정.

    `service_url` 이 빈 문자열이면 catalog 연동 비활성 — Phase 3 의 테이블 샘플 보강이
    건너뛰어진다. 분석 자체는 영향 없음 (LLM 컨텍스트 보강용 옵셔널 기능).

    켤 때는 `.env` 또는 환경변수에 `CATALOG_SERVICE_URL=http://host:port` 로 명시.
    켜진 상태에서 connection refused 면 fail-fast (사용자가 활성화했으면 떠있어야 함).
    """
    service_url: str = field(default_factory=lambda: os.getenv("CATALOG_SERVICE_URL", ""))

    @property
    def enabled(self) -> bool:
        """URL 이 비어있지 않으면 catalog 연동 활성."""
        return bool(self.service_url.strip())


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
    catalog: CatalogConfig = field(default_factory=CatalogConfig)
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


# 전역 설정 인스턴스 (모듈 로딩 시 1회 인스턴스화)
settings = AnalyzerConfig()


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

