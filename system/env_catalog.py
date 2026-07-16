"""ROBO_* 환경변수 단일 진입점.

설계 원칙
─────────
- 모든 경계 상수 (임계값 / 배치 크기 / max_calls / URI / 비용 한도) 는
  ``ROBO_<영역>_<이름>`` env 로 외부화. 코드에 매직 넘버 박지 X.
- 이 파일이 **유일한 진입점**. 모든 step / 모든 미들웨어가 여기를 import.
- 영역별 prefix: LLM / CACHE / AGENT / BUDGET / NEO4J / PIPELINE / CATALOG / LOG / DATA.
- 단위 suffix: _SECONDS / _MS / _TOKENS / _USD / _PCT / _RATIO.

새 env 변수 추가 = 이 파일 1곳만 손댐 → 코드 어디서나 ``ENV.<영역>.<이름>`` 로 접근.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

# .env 자동 로드 (cwd 또는 상위 디렉토리)
load_dotenv()


# ── 헬퍼: env → 타입별 캐스팅 ────────────────────────────────

def _getenv_str(key: str, default: str) -> str:
    return os.environ.get(key, default)


def _getenv_int(key: str, default: int) -> int:
    raw = os.environ.get(key)
    if raw is None or raw == "":
        return default
    return int(raw)


def _getenv_float(key: str, default: float) -> float:
    raw = os.environ.get(key)
    if raw is None or raw == "":
        return default
    return float(raw)


def _getenv_bool(key: str, default: bool) -> bool:
    raw = os.environ.get(key)
    if raw is None or raw == "":
        return default
    return raw.strip().lower() in ("true", "1", "yes", "on")


# ── 영역별 Settings dataclass ──────────────────────────────

@dataclass(frozen=True)
class LLMSettings:
    """ROBO_LLM_* — config 이름 + 비밀만.

    ``.env`` 에 박는 것:
    - ``ROBO_LLM_CONFIG``: ``llm/configs/<name>.yaml`` 의 ``<name>`` (필수)
    - ``ROBO_LLM_API_KEY``: 비밀 (secret manager 친화 — yaml 에 박지 X)

    그 외 모든 설정 (provider / model / sampling / extra_body / reasoning_effort 등) 은
    ``llm/configs/<name>.yaml`` 안에 있음. 자세한 건 ``llm/configs/README.md``.
    """
    config: str = field(default_factory=lambda: _getenv_str("ROBO_LLM_CONFIG", ""))
    api_key: str = field(default_factory=lambda: _getenv_str("ROBO_LLM_API_KEY", ""))


@dataclass(frozen=True)
class EmbedSettings:
    """ROBO_EMBED_* — 임베딩 공급자 + 전송 규율 (spec 044).

    분석용 생성 LLM(``ROBO_LLM_*``)과 **완전히 분리**한다 — 하나를 바꿀 때 다른 하나가
    조용히 따라 바뀌는 사고를 막는다.

    기본값은 2026-07-10 실측. 사내 SGLang 이 ``BAAI/bge-m3``(1024d, 정규화 제공)를 서빙하지만
    ``/v1/models`` 목록에는 없다(비공식 경로 — 운영자 확인 필요). 거부(424)의 정체는 토큰 한도가
    아니라 **GPU 메모리 부족**이며 시간이 지나면 회복된다. 그래서 동시성을 올릴수록 서로 메모리를
    뺏어 **오히려 느려진다**: 동시 8=38초 / 12=173초 / 16=333초(1만 노드 환산, 전부 손실 0).
    로컬 CPU 는 GPU 부재 시 1만 노드 171분 → 폴백 전용(경고).
    """
    provider: str = field(default_factory=lambda: _getenv_str("ROBO_EMBED_PROVIDER", "sglang"))
    model: str = field(default_factory=lambda: _getenv_str("ROBO_EMBED_MODEL", "BAAI/bge-m3"))
    # 비우면 llm/configs/<ROBO_LLM_CONFIG>.yaml 의 api_base 재사용 (같은 사내 서버).
    api_base: str = field(default_factory=lambda: _getenv_str("ROBO_EMBED_API_BASE", ""))
    api_key: str = field(default_factory=lambda: _getenv_str("ROBO_EMBED_API_KEY", ""))
    concurrency: int = field(default_factory=lambda: _getenv_int("ROBO_EMBED_CONCURRENCY", 8))
    char_budget: int = field(default_factory=lambda: _getenv_int("ROBO_EMBED_CHAR_BUDGET", 700))
    max_retries: int = field(default_factory=lambda: _getenv_int("ROBO_EMBED_MAX_RETRIES", 6))
    timeout_seconds: float = field(default_factory=lambda: _getenv_float("ROBO_EMBED_TIMEOUT_SECONDS", 180.0))
    # 서버 텍스트 한도(≈2084자, 부하 의존) 근처. 초과 텍스트만 절단+경고(손실 방지 최후수단 —
    # 정상 길이 노드는 무변경, 절단은 벡터 의미를 바꾸므로 조용히 하지 않는다).
    max_chars: int = field(default_factory=lambda: _getenv_int("ROBO_EMBED_MAX_CHARS", 2000))


@dataclass(frozen=True)
class SearchSettings:
    """ROBO_SEARCH_* — 검색용 생성 LLM(질의 번역·심판) + dense 채널 (spec 044).

    분석용 LLM 과 별도 — 검색은 판단 품질이 중요해 gpt-5.4-mini 를 쓴다(사용자 결정).
    사내 GPU 전환은 TODO(전환 시 심판·번역 승자 구성의 성적 재측정 필수).

    ``dense_impl`` = ``index``(Neo4j 벡터 인덱스) | ``scan``(전량 코사인). SC-002 리콜 미달 시
    ``scan`` 으로 원복하기 위한 단일 스위치 — 두 경로가 한 곳에서 교체된다.
    """
    # GPU 통일(사용자 결정 2026-07-11): 심판·번역도 사내 Qwen(GPU)으로 — 기밀 유지·과금 0,
    # 죽은 OpenAI 키 의존 제거. gpt-5 로 되돌리려면 env `ROBO_SEARCH_LLM_CONFIG=gpt54_mini_openai`.
    llm_config: str = field(default_factory=lambda: _getenv_str("ROBO_SEARCH_LLM_CONFIG", "qwen36_sglang_local"))
    llm_api_key: str = field(default_factory=lambda: _getenv_str("ROBO_SEARCH_LLM_API_KEY", ""))
    dense_impl: str = field(default_factory=lambda: _getenv_str("ROBO_SEARCH_DENSE_IMPL", "index"))
    fetch_k: int = field(default_factory=lambda: _getenv_int("ROBO_SEARCH_FETCH_K", 100))


@dataclass(frozen=True)
class CacheSettings:
    """ROBO_CACHE_* — SQLite LLM 응답 캐시 (운영기 OFF)."""
    enabled: bool = field(default_factory=lambda: _getenv_bool("ROBO_CACHE_ENABLED", False))


@dataclass(frozen=True)
class AgentSettings:
    """ROBO_AGENT_* — Scan Agent / 잔여 패턴 에이전트 호출 한도 + 검증 임계."""
    scan_max_calls: int = field(default_factory=lambda: _getenv_int("ROBO_AGENT_SCAN_MAX_CALLS", 30))
    residual_max_calls: int = field(default_factory=lambda: _getenv_int("ROBO_AGENT_RESIDUAL_MAX_CALLS", 15))
    context_editing_trigger_tokens: int = field(
        default_factory=lambda: _getenv_int("ROBO_AGENT_CONTEXT_EDITING_TRIGGER_TOKENS", 80000)
    )
    # Scan Agent 의 table_access_patterns 검증 — 매치된 call site 중 op_token 이
    # op_mappings 에 인식된 비율의 최소 통과선. 틀린 정규식(op_token 이 변수명)은 0 → 탈락.
    scan_regex_min_op_ratio: float = field(
        default_factory=lambda: _getenv_float("ROBO_AGENT_SCAN_REGEX_MIN_OP_RATIO", 0.5)
    )


@dataclass(frozen=True)
class BudgetSettings:
    """ROBO_BUDGET_* — 비용 회로 차단기 ($400 사고 재발 방지)."""
    run_usd: float = field(default_factory=lambda: _getenv_float("ROBO_BUDGET_RUN_USD", 20.0))


@dataclass(frozen=True)
class Neo4jSettings:
    """ROBO_NEO4J_* — 클라이언트 + UNWIND 배치 + 재시도."""
    uri: str = field(default_factory=lambda: _getenv_str("ROBO_NEO4J_URI", "bolt://localhost:7687"))
    user: str = field(default_factory=lambda: _getenv_str("ROBO_NEO4J_USER", "neo4j"))
    password: str = field(default_factory=lambda: _getenv_str("ROBO_NEO4J_PASSWORD", ""))
    database: str = field(default_factory=lambda: _getenv_str("ROBO_NEO4J_DATABASE", "neo4j"))
    # (옛 unwind_batch_size 삭제 — 소비처 0건. neo4j_client 는 이 값을 읽지도 강제하지도
    #  않았다(docstring 에서만 언급) → 설정이 거짓말을 했다. spec 046 FR-010.)
    retry_max_attempts: int = field(default_factory=lambda: _getenv_int("ROBO_NEO4J_RETRY_MAX_ATTEMPTS", 5))
    retry_backoff_seconds: float = field(default_factory=lambda: _getenv_float("ROBO_NEO4J_RETRY_BACKOFF_SECONDS", 2.0))


@dataclass(frozen=True)
class PipelineSettings:
    """ROBO_PIPELINE_* — 동시성 / 배치 / 큰 코드노드 임계."""
    max_concurrency: int = field(default_factory=lambda: _getenv_int("ROBO_PIPELINE_MAX_CONCURRENCY", 30))
    # (옛 file_concurrency 삭제 — 소비처 0건. spec 046 FR-010.)
    big_context_threshold_tokens: int = field(
        default_factory=lambda: _getenv_int("ROBO_PIPELINE_BIG_CONTEXT_THRESHOLD_TOKENS", 1000)
    )
    # 잔여 패턴 루프 — 한 라운드 에이전트에 보내는 미해결 샘플 수 / 루프 반복 상한.
    residual_sample_size: int = field(default_factory=lambda: _getenv_int("ROBO_PIPELINE_RESIDUAL_SAMPLE_SIZE", 30))
    residual_max_rounds: int = field(default_factory=lambda: _getenv_int("ROBO_PIPELINE_RESIDUAL_MAX_ROUNDS", 3))
    # DBMS step6 배치(🅕) — 같은 부모 형제를 한 LLM 콜로 묶을 때의 입력 토큰 상한.
    # 작게(기본 1500) 잡아 출력 폭주 차단 — 한 노드가 이 한도 초과면 단독 배치.
    batch_max_tokens: int = field(default_factory=lambda: _getenv_int("ROBO_PIPELINE_BATCH_MAX_TOKENS", 1500))
    # 임베딩 step on/off — Framework/DBMS 공통. 기본 ON (운영=full 동작).
    # 테스트/디버그 시 .env 로 off (느린 모델 로드 회피).
    # 모델 자체는 LLM 영역 (llm/client/embedder.py) 책임 — 분석기는 ENV 안 둠.
    embed_enabled: bool = field(default_factory=lambda: _getenv_bool("ROBO_PIPELINE_EMBED_ENABLED", True))


@dataclass(frozen=True)
class CatalogSettings:
    """ROBO_CATALOG_* — 외부 DB 카탈로그 연동 단일 진실.

    분석기는 HTTP 클라이언트 (URL 호출 + 응답 처리) 만 책임. catalog 서비스의
    인증 / dataset 식별 / 정책은 catalog 영역 — 분석기에 인자로 흐르지 X.

    - ``enabled`` 기본 ON — 카탈로그는 운영 시 동작해야 하는 기능. 테스트/디버그 때만
      ``.env`` 로 off. **ON 인데 catalog HTTP 시도가 실패하면 step 이 raise** (심각 —
      조용히 skip 하지 않음). 단 개별 자원 404 는 정상 (그 자원이 카탈로그에 없을 뿐).
    - ``base_url`` — catalog 서비스 endpoint. enabled=true 시 필수.
    - ``timeout_seconds`` — HTTP timeout (옛 매직 60/120 → 단일 진실).
    """
    enabled: bool = field(default_factory=lambda: _getenv_bool("ROBO_CATALOG_ENABLED", True))
    base_url: str = field(default_factory=lambda: _getenv_str("ROBO_CATALOG_BASE_URL", ""))
    timeout_seconds: float = field(default_factory=lambda: _getenv_float("ROBO_CATALOG_TIMEOUT_SECONDS", 120.0))
    # ─────────────────────────────────────────────────────────────
    # ⚠️ 테스트 전용 (운영 아님)
    #   운영(HTTP /analyze)은 datasource 를 AnalyzeRequest 입력으로 받는다.
    #   이 env 는 CLI(analyze_data_dir.py)/테스트에서 "어느 DB 를 샘플링"할지 줄 때만 사용.
    #   비어 있으면(기본) 샘플 보강 skip. 운영 코드 경로는 이 값을 읽지 않는다.
    # ─────────────────────────────────────────────────────────────
    test_datasource: str = field(default_factory=lambda: _getenv_str("ROBO_CATALOG_TEST_DATASOURCE", ""))


@dataclass(frozen=True)
class LogSettings:
    """ROBO_LOG_* — 로그 디렉토리 + 비밀 마스킹 + 파일 핸들러 on/off.

    ``file_enabled`` 정책:
    - 개발 / 테스트 (``.env``): ``true`` — ``logs/analysis.run/latest/analyzer.log``
      + ``error.log`` 파일 핸들러 attach. ``tail -f`` 로 진행 추적 가능.
    - 운영 (``.env.production``): ``false`` — stderr 만. 디스크 I/O 0, 성능 영향 0.

    ``level`` 은 root logger 의 레벨 + 파일 핸들러의 기본 레벨. ``error.log`` 만
    내부적으로 ``WARNING+`` 으로 강제 (logger 모듈 책임).
    """
    dir: Path = field(default_factory=lambda: Path(_getenv_str("ROBO_LOG_DIR", "./logs")))
    # (옛 mask_secrets 삭제 — **거짓 설정**이었다: 소비처 0건이고 마스킹은
    #  cache_message_filters 가 **무조건** 수행한다. on/off 를 고를 수 있는 척했다. FR-010.)
    file_enabled: bool = field(default_factory=lambda: _getenv_bool("ROBO_LOG_FILE_ENABLED", True))
    level: str = field(default_factory=lambda: _getenv_str("ROBO_LOG_LEVEL", "INFO"))


# robo-data-analyzer 와 같은 레벨(형제)에 있는 ``data`` 폴더 — 실행 위치(cwd) 무관 고정 기준.
# 이 파일: robo-data-analyzer/shared/config/env_catalog.py → parents[2] = robo-data-analyzer.
# antlr-code-parser 도 동일하게 "자기 위치의 부모 + data" 로 이 폴더에 산출물(source/ddl/analysis)을 쌓는다.
_DEFAULT_DATA_DIR = Path(__file__).resolve().parents[2].parent / "data"


def _resolve_data_dir() -> Path:
    """분석 베이스 폴더. ``ROBO_DATA_DIR`` 명시 시 그걸로 override, 없으면 형제 ``data``."""
    override = os.environ.get("ROBO_DATA_DIR", "").strip()
    return Path(override) if override else _DEFAULT_DATA_DIR


@dataclass(frozen=True)
class DataSettings:
    """ROBO_DATA_* — 분석 대상 베이스 (source / ddl / analysis 공유 루트).

    기본값 = robo-data-analyzer 와 같은 레벨의 ``data`` 폴더 (cwd 무관, 머신 독립).
    다른 위치를 쓰려면 ``ROBO_DATA_DIR`` 로 override.
    """
    dir: Path = field(default_factory=_resolve_data_dir)


# ── 통합 카탈로그 ──────────────────────────────────────────

@dataclass(frozen=True)
class EnvCatalog:
    """모든 ROBO_* env 단일 진입점.

    사용 예::

        from system.env_catalog import ENV
        ENV.llm.api_key
        ENV.pipeline.max_concurrency
        ENV.budget.run_usd

    영역 추가 시: 위에 새 Settings dataclass + 여기 필드 추가 → 즉시 전역 사용.
    """
    llm: LLMSettings = field(default_factory=LLMSettings)
    embed: EmbedSettings = field(default_factory=EmbedSettings)
    search: SearchSettings = field(default_factory=SearchSettings)
    cache: CacheSettings = field(default_factory=CacheSettings)
    agent: AgentSettings = field(default_factory=AgentSettings)
    budget: BudgetSettings = field(default_factory=BudgetSettings)
    neo4j: Neo4jSettings = field(default_factory=Neo4jSettings)
    pipeline: PipelineSettings = field(default_factory=PipelineSettings)
    catalog: CatalogSettings = field(default_factory=CatalogSettings)
    log: LogSettings = field(default_factory=LogSettings)
    data: DataSettings = field(default_factory=DataSettings)


# 모듈 로드 시 한 번만 생성 (singleton).
# 코드 안에서는 ENV.<영역>.<이름> 로만 접근.
ENV = EnvCatalog()
