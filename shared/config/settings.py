"""ROBO_* 환경변수 단일 진입점.

설계 원칙
─────────
- 모든 경계 상수 (임계값 / 배치 크기 / URI / 비용 한도) 는
  ``ROBO_<영역>_<이름>`` 환경변수로 외부화. 코드에 매직 넘버 박지 X.
- 이 파일이 **유일한 진입점**. 모든 step / 모든 미들웨어가 여기를 import.
- 영역별 prefix: LLM / CACHE / AGENT / BUDGET / NEO4J / PIPELINE / CATALOG / LOG / DATA.
- 단위 suffix: _SECONDS / _MS / _TOKENS / _USD / _PCT / _RATIO.

새 환경변수 추가 = 이 파일 1곳만 손댐 → 코드 어디서나 ``SETTINGS.<영역>.<이름>`` 로 접근.
"""
from __future__ import annotations

import os
import math
import re
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

# .env 자동 로드 (cwd 또는 상위 디렉토리)
load_dotenv()


# ── 헬퍼: 환경변수 → 타입별 캐스팅 ────────────────────────────────

def _getenv_str(key: str, default: str) -> str:
    return os.environ.get(key, default)


def _getenv_int(key: str, default: int) -> int:
    raw = os.environ.get(key)
    if raw is None:
        return default
    return int(raw)


def _getenv_float(key: str, default: float) -> float:
    raw = os.environ.get(key)
    if raw is None:
        return default
    return float(raw)


def _getenv_bool(key: str, default: bool) -> bool:
    raw = os.environ.get(key)
    if raw is None:
        return default
    normalized = raw.strip().lower()
    if normalized in ("true", "1", "yes", "on"):
        return True
    if normalized in ("false", "0", "no", "off"):
        return False
    raise ValueError(
        f"{key} must be one of true/false, 1/0, yes/no, on/off; got {raw!r}"
    )


def _require_non_blank(name: str, value: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-blank string")


def _require_positive_int(name: str, value: int) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"{name} must be a positive integer; got {value!r}")


def _require_nonnegative_int(name: str, value: int) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{name} must be a non-negative integer; got {value!r}")


def _require_finite_number(name: str, value: float, *, minimum: float, inclusive: bool) -> None:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(f"{name} must be a finite number; got {value!r}")
    valid = value >= minimum if inclusive else value > minimum
    if not valid:
        operator = ">=" if inclusive else ">"
        raise ValueError(f"{name} must be {operator} {minimum}; got {value!r}")


# ── 영역별 Settings dataclass ──────────────────────────────

@dataclass(frozen=True)
class AnalysisLlmSettings:
    """ROBO_LLM_* — config 이름 + 비밀만.

    ``.env`` 에 박는 것:
    - ``ROBO_LLM_CONFIG``: ``llm/configs/<name>.yaml`` 의 ``<name>`` (필수)
    - ``ROBO_LLM_API_KEY``: 비밀 (secret manager 친화 — yaml 에 박지 X)

    그 외 모든 설정 (provider / model / sampling / extra_body / reasoning_effort 등) 은
    ``llm/configs/<name>.yaml`` 안에 있음. 자세한 건 ``llm/configs/README.md``.
    """
    config: str = field(default_factory=lambda: _getenv_str("ROBO_LLM_CONFIG", ""))
    api_key: str = field(default_factory=lambda: _getenv_str("ROBO_LLM_API_KEY", ""))
    # 생성 중 반복 폭주 실시간 차단 (llm/repetition_guard.py, spec 080). 스트리밍 수신을
    # 전제하므로 스트리밍/usage 이상 진단 시에만 끄는 킬스위치 — 기본 ON.
    repetition_guard: bool = field(
        default_factory=lambda: _getenv_bool("ROBO_LLM_REPETITION_GUARD", True)
    )
    # 영속 응답 캐시 (llm/response_cache.py). 실행환경 지문이 봉인됐을 때만 안전하게
    # 재사용할 수 있으므로 기본 OFF다.
    response_cache: bool = field(
        default_factory=lambda: _getenv_bool("ROBO_LLM_RESPONSE_CACHE", False)
    )
    # 모델 weights/revision·tokenizer·quantization·serving runtime·provider hard limit을
    # 함께 봉인한 외부 capability 문서의 SHA-256. 모델 이름이나 endpoint만으로 대체 금지.
    model_runtime_capabilities_sha256: str = field(
        default_factory=lambda: _getenv_str(
            "ROBO_LLM_MODEL_RUNTIME_CAPABILITIES_SHA256", "",
        ).strip().lower()
    )
    model_runtime_capabilities_ref: str = field(
        default_factory=lambda: _getenv_str(
            "ROBO_LLM_MODEL_RUNTIME_CAPABILITIES_REF", "",
        ).strip()
    )
    # 응답 캐시 용량 상한(MB). 키가 프롬프트 전체라서 프롬프트·코드를 고칠 때마다 옛 항목이
    # 다시는 안 읽히는 채로 남는다 — 상한이 없으면 코퍼스 옆에서 무한히 쌓인다.
    # 초과 시 오래 안 쓴 것부터 지운다(LRU). 0 이하면 정리 안 함.
    response_cache_max_mb: int = field(
        default_factory=lambda: _getenv_int("ROBO_LLM_RESPONSE_CACHE_MAX_MB", 512)
    )

    def __post_init__(self) -> None:
        if (
            self.model_runtime_capabilities_sha256
            and re.fullmatch(r"[0-9a-f]{64}", self.model_runtime_capabilities_sha256) is None
        ):
            raise ValueError(
                "ROBO_LLM_MODEL_RUNTIME_CAPABILITIES_SHA256 must be a lowercase SHA-256 hex digest"
            )


@dataclass(frozen=True)
class EmbeddingSettings:
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

    def __post_init__(self) -> None:
        _require_non_blank("EmbeddingSettings.provider", self.provider)
        _require_non_blank("EmbeddingSettings.model", self.model)
        _require_positive_int("EmbeddingSettings.concurrency", self.concurrency)
        _require_positive_int("EmbeddingSettings.char_budget", self.char_budget)
        _require_nonnegative_int("EmbeddingSettings.max_retries", self.max_retries)
        _require_finite_number(
            "EmbeddingSettings.timeout_seconds", self.timeout_seconds, minimum=0, inclusive=False
        )


@dataclass(frozen=True)
class SemanticSearchSettings:
    """ROBO_SEARCH_* — 검색용 생성 LLM(질의 번역) + dense 채널 (spec 044).

    분석용 LLM 과 별도 — 검색은 판단 품질이 중요해 gpt-5.4-mini 를 쓴다(사용자 결정).
    사내 GPU 전환은 TODO(전환 시 심판·번역 승자 구성의 성적 재측정 필수).

    ``dense_impl`` = ``index``(Neo4j 벡터 인덱스) | ``scan``(전량 코사인). SC-002 리콜 미달 시
    ``scan`` 으로 원복하기 위한 단일 스위치 — 두 경로가 한 곳에서 교체된다.
    """
    # GPU 통일(사용자 결정 2026-07-11): 질의 번역도 사내 Qwen(GPU)으로 — 기밀 유지·과금 0,
    # 죽은 OpenAI 키 의존 제거. gpt-5 로 되돌리려면 환경변수 `ROBO_SEARCH_LLM_CONFIG=gpt54_mini_openai`.
    llm_config: str = field(default_factory=lambda: _getenv_str("ROBO_SEARCH_LLM_CONFIG", "qwen36_sglang_local"))
    llm_api_key: str = field(default_factory=lambda: _getenv_str("ROBO_SEARCH_LLM_API_KEY", ""))
    dense_impl: str = field(default_factory=lambda: _getenv_str("ROBO_SEARCH_DENSE_IMPL", "index"))
    fetch_k: int = field(default_factory=lambda: _getenv_int("ROBO_SEARCH_FETCH_K", 100))

    def __post_init__(self) -> None:
        _require_non_blank("SemanticSearchSettings.llm_config", self.llm_config)
        if self.dense_impl not in {"index", "scan"}:
            raise ValueError(
                "SemanticSearchSettings.dense_impl must be 'index' or 'scan'; "
                f"got {self.dense_impl!r}"
            )
        _require_positive_int("SemanticSearchSettings.fetch_k", self.fetch_k)


@dataclass(frozen=True)
class AnalysisRunBudgetSettings:
    """ROBO_BUDGET_* — 비용 회로 차단기 ($400 사고 재발 방지)."""
    run_usd: float = field(default_factory=lambda: _getenv_float("ROBO_BUDGET_RUN_USD", 20.0))

    def __post_init__(self) -> None:
        _require_finite_number(
            "AnalysisRunBudgetSettings.run_usd", self.run_usd, minimum=0, inclusive=False
        )


@dataclass(frozen=True)
class AnalysisGraphDatabaseSettings:
    """ROBO_NEO4J_* — 클라이언트 + UNWIND 배치 + 재시도."""
    uri: str = field(default_factory=lambda: _getenv_str("ROBO_NEO4J_URI", "bolt://localhost:7687"))
    user: str = field(default_factory=lambda: _getenv_str("ROBO_NEO4J_USER", "neo4j"))
    password: str = field(default_factory=lambda: _getenv_str("ROBO_NEO4J_PASSWORD", ""))
    database: str = field(default_factory=lambda: _getenv_str("ROBO_NEO4J_DATABASE", "neo4j"))
    # (옛 unwind_batch_size 삭제 — 소비처 0건. neo4j_client 는 이 값을 읽지도 강제하지도
    #  않았다(docstring 에서만 언급) → 설정이 거짓말을 했다. spec 046 FR-010.)
    retry_max_attempts: int = field(default_factory=lambda: _getenv_int("ROBO_NEO4J_RETRY_MAX_ATTEMPTS", 5))
    retry_backoff_seconds: float = field(default_factory=lambda: _getenv_float("ROBO_NEO4J_RETRY_BACKOFF_SECONDS", 2.0))

    def __post_init__(self) -> None:
        _require_non_blank("AnalysisGraphDatabaseSettings.uri", self.uri)
        _require_non_blank("AnalysisGraphDatabaseSettings.user", self.user)
        _require_non_blank("AnalysisGraphDatabaseSettings.database", self.database)
        if self.database.lower() == "system":
            raise ValueError("AnalysisGraphDatabaseSettings.database cannot be system")
        _require_positive_int(
            "AnalysisGraphDatabaseSettings.retry_max_attempts", self.retry_max_attempts
        )
        _require_finite_number(
            "AnalysisGraphDatabaseSettings.retry_backoff_seconds",
            self.retry_backoff_seconds,
            minimum=0,
            inclusive=True,
        )


@dataclass(frozen=True)
class AnalysisPipelineSettings:
    """ROBO_PIPELINE_* — 동시성 / 배치 / 큰 코드노드 임계."""
    # 기본 64 = GPU 서버 동시 요청 상한 (sglang max_running_requests 실측).
    max_concurrency: int = field(default_factory=lambda: _getenv_int("ROBO_PIPELINE_MAX_CONCURRENCY", 64))
    # Code-node preparation can remain wide while the current GPU admits only the
    # measured number of simultaneous generation requests.
    llm_max_concurrency: int = field(
        default_factory=lambda: _getenv_int("ROBO_PIPELINE_LLM_MAX_CONCURRENCY", 32)
    )
    # DBMS step7 배치(🅕) — 같은 부모 형제를 한 LLM 콜로 묶을 때의 입력 토큰 상한.
    # 작게(기본 1500) 잡아 출력 폭주 차단 — 한 노드가 이 한도 초과면 단독 배치.
    batch_max_tokens: int = field(default_factory=lambda: _getenv_int("ROBO_PIPELINE_BATCH_MAX_TOKENS", 1500))
    # 임베딩 step on/off — Framework/DBMS 공통. 기본 ON (운영=full 동작).
    # 테스트/디버그 시 .env 로 off (느린 모델 로드 회피).
    # 모델 자체는 LLM 영역 (llm/embedder.py) 책임 — analysis_pipeline 설정에는 두지 않는다.
    embed_enabled: bool = field(default_factory=lambda: _getenv_bool("ROBO_PIPELINE_EMBED_ENABLED", True))

    def __post_init__(self) -> None:
        for name in (
            "max_concurrency",
            "llm_max_concurrency",
            "batch_max_tokens",
        ):
            _require_positive_int(f"AnalysisPipelineSettings.{name}", getattr(self, name))


@dataclass(frozen=True)
class CatalogApiSettings:
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
    #   이 환경변수는 CLI(analyze_data_dir.py)/테스트에서 "어느 DB 를 샘플링"할지 줄 때만 사용.
    #   비어 있으면(기본) 샘플 보강 skip. 운영 코드 경로는 이 값을 읽지 않는다.
    # ─────────────────────────────────────────────────────────────
    test_datasource: str = field(default_factory=lambda: _getenv_str("ROBO_CATALOG_TEST_DATASOURCE", ""))

    def __post_init__(self) -> None:
        _require_finite_number(
            "CatalogApiSettings.timeout_seconds", self.timeout_seconds, minimum=0, inclusive=False
        )


@dataclass(frozen=True)
class RuntimeEventSettings:
    """런타임 이벤트의 표준 로그 레벨과 선택적 파일 기록 정책.

    ``file_logging_enabled`` 정책:
    - 개발 / 테스트 (``.env``): ``true`` — ``logs/analyzer.log``
      + ``error.log`` 파일 핸들러 attach. ``tail -f`` 로 진행 추적 가능.
    - 운영 (``.env.production``): ``false`` — stderr 만. 디스크 I/O 0, 성능 영향 0.

    ``log_level`` 은 root logger 의 레벨 + 파일 핸들러의 기본 레벨. ``error.log`` 만
    내부적으로 ``WARNING+`` 으로 강제 (logger 모듈 책임).
    """
    # (옛 mask_secrets 삭제 — **거짓 설정**이었다: 소비처 0건이고 마스킹은
    #  cache_message_filters 가 **무조건** 수행한다. on/off 를 고를 수 있는 척했다. FR-010.)
    file_logging_enabled: bool = field(
        default_factory=lambda: _getenv_bool("ROBO_RUNTIME_EVENT_FILE_ENABLED", True)
    )
    log_level: str = field(default_factory=lambda: _getenv_str("ROBO_RUNTIME_EVENT_LEVEL", "INFO"))

    def __post_init__(self) -> None:
        if not isinstance(self.log_level, str) or self.log_level.strip().upper() not in {
            "DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"
        }:
            raise ValueError(
                "RuntimeEventSettings.log_level must be DEBUG, INFO, WARNING, ERROR, or CRITICAL; "
                f"got {self.log_level!r}"
            )


def _resolve_run_output_dir() -> Path:
    raw = os.environ.get("ROBO_RUN_OUTPUT_DIR")
    if raw is None:
        return (
            Path(__file__).resolve().parents[4]
            / "_playground" / "analyzer" / "runs" / "live"
        )
    if not raw.strip():
        raise ValueError("ROBO_RUN_OUTPUT_DIR must not be blank")
    return Path(raw)


@dataclass(frozen=True)
class AnalysisRunOutputSettings:
    """현행 분석 산출물 루트. 기본은 ``logs/``이며 실행 전에 초기화된다."""

    dir: Path = field(default_factory=_resolve_run_output_dir)

    def __post_init__(self) -> None:
        if not isinstance(self.dir, Path):
            raise ValueError(f"AnalysisRunOutputSettings.dir must be a Path; got {self.dir!r}")
        resolved = self.dir.resolve()
        cwd = Path.cwd().resolve()
        if resolved == cwd:
            raise ValueError("AnalysisRunOutputSettings.dir must not be the current 작업 디렉터리")
        if resolved == Path(resolved.anchor):
            raise ValueError("AnalysisRunOutputSettings.dir must not be a 파일시스템 루트")


# robo-data-analyzer 와 같은 레벨(형제)에 있는 ``data`` 폴더 — 실행 위치(cwd) 무관 고정 기준.
# 이 파일은 robo-data-analyzer/shared/config/settings.py에 있다.
# antlr-code-parser 도 동일하게 "자기 위치의 부모 + data" 로 이 폴더에 산출물(source/ddl/analysis)을 쌓는다.
_DEFAULT_DATA_DIR = Path(__file__).resolve().parents[3] / "data"


def _resolve_data_dir() -> Path:
    """분석 베이스 폴더. ``ROBO_DATA_DIR`` 명시 시 그걸로 override, 없으면 형제 ``data``."""
    override = os.environ.get("ROBO_DATA_DIR")
    if override is None:
        return _DEFAULT_DATA_DIR
    if not override.strip():
        raise ValueError("ROBO_DATA_DIR must not be blank")
    return Path(override)


@dataclass(frozen=True)
class AnalysisDataSettings:
    """ROBO_DATA_* — 분석 대상 베이스 (source / ddl / analysis 공유 루트).

    기본값 = robo-data-analyzer 와 같은 레벨의 ``data`` 폴더 (cwd 무관, 머신 독립).
    다른 위치를 쓰려면 ``ROBO_DATA_DIR`` 로 override.
    """
    dir: Path = field(default_factory=_resolve_data_dir)


# ── Analyzer 통합 설정 ─────────────────────────────────────

@dataclass(frozen=True)
class AnalyzerSettings:
    """모든 ROBO_* 환경변수의 단일 진입점.

    사용 예::

        from shared.config.settings import SETTINGS
        SETTINGS.analysis_llm.api_key
        SETTINGS.analysis_pipeline.max_concurrency
        SETTINGS.run_budget.run_usd

    영역 추가 시: 위에 새 Settings dataclass + 여기 필드 추가 → 즉시 전역 사용.
    """
    analysis_llm: AnalysisLlmSettings = field(default_factory=AnalysisLlmSettings)
    embedding: EmbeddingSettings = field(default_factory=EmbeddingSettings)
    semantic_search: SemanticSearchSettings = field(default_factory=SemanticSearchSettings)
    run_budget: AnalysisRunBudgetSettings = field(default_factory=AnalysisRunBudgetSettings)
    graph_database: AnalysisGraphDatabaseSettings = field(default_factory=AnalysisGraphDatabaseSettings)
    analysis_pipeline: AnalysisPipelineSettings = field(default_factory=AnalysisPipelineSettings)
    catalog_api: CatalogApiSettings = field(default_factory=CatalogApiSettings)
    observability: RuntimeEventSettings = field(default_factory=RuntimeEventSettings)
    run_output: AnalysisRunOutputSettings = field(default_factory=AnalysisRunOutputSettings)
    analysis_data: AnalysisDataSettings = field(default_factory=AnalysisDataSettings)


# 모듈 로드 시 한 번만 생성 (singleton).
# 코드 안에서는 SETTINGS.<영역>.<이름> 로만 접근.
SETTINGS = AnalyzerSettings()
