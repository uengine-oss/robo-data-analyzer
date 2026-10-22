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


def _getenv_first(keys: tuple[str, ...], default: str) -> str:
    """앞에서부터 값이 있는 env 를 쓴다 — 표준 이름 먼저, ``ROBO_*`` 는 하위 호환 별칭.

    분석기는 루트 ``.env`` 를 다른 서비스(studio · data-fabric · text2sql · process-gpt)와
    **공유**한다. 목표는 상위 ``.env`` 하나를 모든 프로젝트가 같은 이름으로 재사용하는 것이다.
    전용 이름(``ROBO_LLM_API_KEY``)을 따로 두면 같은 비밀이 두 벌이 되어 한쪽만 갱신되고
    (실측: 두 키가 서로 다른 값으로 각각 무효화돼 있었다), Neo4j 는 ``ROBO_NEO4J_URI`` 가 없어
    기본값 7687 로 붙어 **카탈로그(공용 ``NEO4J_URI``=7688)와 다른 그래프에 분석 결과를 썼다.**
    그래서 표준 이름이 기준이고, ``ROBO_*`` 는 표준 이름이 비었을 때만 읽는다.
    """
    for key in keys:
        raw = os.environ.get(key)
        if raw:
            return raw
    return default


# LLM 공급자 → 표준 키 이름 (다른 제품·SDK 관례와 같게)
_LLM_KEY_BY_PROVIDER = {
    "openai": "OPENAI_API_KEY",
    "anthropic": "ANTHROPIC_API_KEY",
    "openrouter": "OPENROUTER_API_KEY",
}


def _llm_provider_of(config_name: str) -> str:
    """``llm_configs/<name>.yaml`` 의 provider 만 가볍게 읽는다 (키 이름을 고르기 위해)."""
    if not config_name:
        return ""
    path = Path(__file__).resolve().parents[2] / "llm_configs" / f"{config_name}.yaml"
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip().startswith("provider:"):
                return line.split(":", 1)[1].strip().strip("'\"").lower()
    except OSError:
        pass
    return ""


def _resolve_llm_api_key() -> str:
    provider = _llm_provider_of(os.environ.get("ROBO_LLM_CONFIG", ""))
    standard = _LLM_KEY_BY_PROVIDER.get(provider, "LLM_API_KEY")
    return _getenv_first((standard, "LLM_API_KEY", "ROBO_LLM_API_KEY"), "")


def _getenv_bool(key: str, default: bool) -> bool:
    raw = os.environ.get(key)
    if raw is None or raw == "":
        return default
    return raw.strip().lower() in ("true", "1", "yes", "on")


# ── 영역별 Settings dataclass ──────────────────────────────

@dataclass(frozen=True)
class LLMSettings:
    """LLM — config 이름 + 비밀만.

    ``.env`` 에 박는 것:
    - ``ROBO_LLM_CONFIG``: ``llm_configs/<name>.yaml`` 의 ``<name>`` (필수)
    - 비밀은 **공급자별 표준 이름**: ``OPENAI_API_KEY`` / ``ANTHROPIC_API_KEY`` /
      ``OPENROUTER_API_KEY`` / 그 밖의 OpenAI 호환은 ``LLM_API_KEY``. 다른 서비스와 같은
      ``.env`` 를 공유하므로 같은 이름 · 같은 값이어야 한다. (``ROBO_LLM_API_KEY`` 는 하위 호환)

    그 외 모든 설정 (provider / model / sampling / extra_body / reasoning_effort 등) 은
    ``llm_configs/<name>.yaml`` 안에 있음. 자세한 건 ``llm_configs/README.md``.
    """
    config: str = field(default_factory=lambda: _getenv_str("ROBO_LLM_CONFIG", ""))
    # 공급자별 표준 이름(OPENAI_API_KEY · ANTHROPIC_API_KEY · OPENROUTER_API_KEY · LLM_API_KEY)이
    # 기준. ROBO_LLM_API_KEY 는 하위 호환 별칭 — 표준 이름이 비었을 때만 읽는다.
    api_key: str = field(default_factory=_resolve_llm_api_key)


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
    """Neo4j — 접속은 공용 NEO4J_* (하위 호환 ROBO_NEO4J_*), 배치·재시도는 ROBO_NEO4J_*."""
    # 플랫폼 공용 NEO4J_* 가 기준 — 카탈로그와 같은 그래프에 쓰기 위해. ROBO_NEO4J_* 는 하위 호환.
    uri: str = field(default_factory=lambda: _getenv_first(("NEO4J_URI", "ROBO_NEO4J_URI"), "bolt://localhost:7687"))
    user: str = field(default_factory=lambda: _getenv_first(("NEO4J_USER", "ROBO_NEO4J_USER"), "neo4j"))
    password: str = field(default_factory=lambda: _getenv_first(("NEO4J_PASSWORD", "ROBO_NEO4J_PASSWORD"), ""))
    database: str = field(default_factory=lambda: _getenv_first(("NEO4J_DATABASE", "ROBO_NEO4J_DATABASE"), "neo4j"))
    unwind_batch_size: int = field(default_factory=lambda: _getenv_int("ROBO_NEO4J_UNWIND_BATCH_SIZE", 2000))
    retry_max_attempts: int = field(default_factory=lambda: _getenv_int("ROBO_NEO4J_RETRY_MAX_ATTEMPTS", 5))
    retry_backoff_seconds: float = field(default_factory=lambda: _getenv_float("ROBO_NEO4J_RETRY_BACKOFF_SECONDS", 2.0))


@dataclass(frozen=True)
class PipelineSettings:
    """ROBO_PIPELINE_* — 동시성 / 배치 / 큰 함수 임계."""
    max_concurrency: int = field(default_factory=lambda: _getenv_int("ROBO_PIPELINE_MAX_CONCURRENCY", 8))
    file_concurrency: int = field(default_factory=lambda: _getenv_int("ROBO_PIPELINE_FILE_CONCURRENCY", 4))
    big_context_threshold_tokens: int = field(
        default_factory=lambda: _getenv_int("ROBO_PIPELINE_BIG_CONTEXT_THRESHOLD_TOKENS", 1000)
    )
    # 잔여 패턴 루프 — 한 라운드 에이전트에 보내는 미해결 샘플 수 / 루프 반복 상한.
    residual_sample_size: int = field(default_factory=lambda: _getenv_int("ROBO_PIPELINE_RESIDUAL_SAMPLE_SIZE", 30))
    residual_max_rounds: int = field(default_factory=lambda: _getenv_int("ROBO_PIPELINE_RESIDUAL_MAX_ROUNDS", 3))
    # 임베딩 step on/off — Framework/DBMS 공통. 기본 ON (운영=full 동작).
    # 테스트/디버그 시 .env 로 off (느린 모델 로드 회피).
    embed_enabled: bool = field(default_factory=lambda: _getenv_bool("ROBO_PIPELINE_EMBED_ENABLED", True))


@dataclass(frozen=True)
class CatalogSettings:
    """ROBO_CATALOG_* — 외부 DB 카탈로그 연동 on/off.

    ``enabled`` 기본 ON — 카탈로그는 운영 시 동작해야 하는 기능. 테스트/디버그 때만
    ``.env`` 로 off. **ON 인데 catalog HTTP 시도가 실패하면 step 이 raise** (심각 —
    조용히 skip 하지 않음). 단 개별 테이블 404 는 정상 (그 테이블이 카탈로그에 없을 뿐).
    """
    enabled: bool = field(default_factory=lambda: _getenv_bool("ROBO_CATALOG_ENABLED", True))


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
    mask_secrets: bool = field(default_factory=lambda: _getenv_bool("ROBO_LOG_MASK_SECRETS", True))
    file_enabled: bool = field(default_factory=lambda: _getenv_bool("ROBO_LOG_FILE_ENABLED", True))
    level: str = field(default_factory=lambda: _getenv_str("ROBO_LOG_LEVEL", "INFO"))


@dataclass(frozen=True)
class DataSettings:
    """ROBO_DATA_* — 분석 대상 프로젝트 베이스.

    기본값이 사용자 홈 아래 ``ontologic/data`` 인 이유: 업로드 경로에서는 프론트가
    ``project_root`` 를 보내지 않는다(경로를 알 수 없다). 그래서 **서버가 아는 고정
    자리**가 있어야 반입된 소스를 찾을 수 있다. 상대경로 ``./data`` 는 프로세스를 어느
    디렉터리에서 띄웠느냐에 따라 달라져 그 자리 노릇을 못 한다.

    **ANTLR 파서와 같은 자리여야 한다.** 파서는 자기 체크아웃의 부모 아래
    ``data/{source,ddl,analysis}`` 에 반입한다(FileStorageService.resolveBaseDir).
    두 값이 어긋나면 업로드는 성공하는데 파이프라인이 첫 단계에서 조용히 끝난다 —
    ``source/`` 가 비어 있으니 파싱할 것이 없기 때문이다.
    """
    dir: Path = field(
        default_factory=lambda: Path(
            _getenv_str("ROBO_DATA_DIR", str(Path.home() / "ontologic" / "data"))
        ).expanduser()
    )


# ── 통합 카탈로그 ──────────────────────────────────────────

@dataclass(frozen=True)
class EnvCatalog:
    """모든 ROBO_* env 단일 진입점.

    사용 예::

        from shared.config.env_catalog import ENV
        ENV.llm.api_key
        ENV.pipeline.max_concurrency
        ENV.budget.run_usd

    영역 추가 시: 위에 새 Settings dataclass + 여기 필드 추가 → 즉시 전역 사용.
    """
    llm: LLMSettings = field(default_factory=LLMSettings)
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
