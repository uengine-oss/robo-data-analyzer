"""exp 전용 설정. data 경로는 config.settings와 동일하게 고정."""

import os
from pathlib import Path
from typing import Optional

# 프로젝트 루트 (exp/config.py -> exp -> robo-data-analyzer)
_PROJECT_ROOT = Path(__file__).resolve().parents[1]

# .env 로드 (exp 실행 시 NEO4J_*, NEO4J_QUERY_BATCH_SIZE 등 사용)
# DeepAgent: exp.agent.deepagent에서 get_llm(api_key=...)로 프로젝트 LLM 설정을 사용하므로
# .env에 LLM_API_KEY(및 필요 시 LLM_API_BASE, LLM_MODEL)만 설정하면 됨.
try:
    from dotenv import load_dotenv
    load_dotenv(dotenv_path=_PROJECT_ROOT / ".env")
except ImportError:
    pass


def get_data_dir(override: Optional[str] = None) -> str:
    """data 폴더 경로. 실제 분석 경로(settings.path.data_dir)와 동일하게 고정. override는 무시됨."""
    from config.settings import settings
    return settings.path.data_dir


def get_source_dir(data_dir: Optional[str] = None) -> str:
    """source 폴더 경로. Step1 스캔 루트."""
    base = data_dir or get_data_dir()
    return os.path.join(base, "source")


def get_ast_target_dir(data_dir: Optional[str] = None) -> str:
    """target 폴더 경로 (AST JSON 등). source와 같은 상대 구조, 확장자만 .json."""
    base = data_dir or get_data_dir()
    return os.path.join(base, "analysis", "target")


def get_nontarget_dir(data_dir: Optional[str] = None) -> str:
    """nontarget 폴더 경로. Step1 스캔 대상 (source와 함께)."""
    base = data_dir or get_data_dir()
    return os.path.join(base, "analysis", "nontarget")


def get_chunk_bundle_dir(data_dir: Optional[str] = None) -> str:
    """data/chunk_bundles 경로 (ChunkBundle JSON). data 쪽은 읽기 전용/하위호환용."""
    base = data_dir or get_data_dir()
    return os.path.join(base, "chunk_bundles")


def get_chunk_bundle_dir_from_run_dir(run_dir: str) -> str:
    """exp/exp_run 아래 chunk_bundles 경로. 산출물·청크번들은 data가 아닌 run_dir에 기록."""
    return os.path.join(run_dir, "chunk_bundles")


# 배치/토큰 상수 (statement_node 등에서 사용)
MAX_CONTEXT_TOKEN = int(os.environ.get("EXP_MAX_CONTEXT_TOKEN", "300"))
MAX_BATCH_TOKEN = int(os.environ.get("EXP_MAX_BATCH_TOKEN", "1000"))
# 테이블/Call LLM 배치 한도 (한 요청당 최대 입력 토큰)
MAX_LLM_BATCH_TOKEN = int(os.environ.get("EXP_MAX_LLM_BATCH_TOKEN", "2000"))

# SQL→테이블: True면 키 단위 파이프라인(라운드 제거, 처리 완료된 키부터 다음 조각 진행). False면 기존 라운드 순차.
USE_SQL_PER_KEY_PIPELINE = os.environ.get("EXP_SQL_PER_KEY_PIPELINE", "true").lower() in ("1", "true", "yes")


def estimate_tokens(text: str) -> int:
    """LLM 입력 토큰 수 근사 (tiktoken 미사용). 문자열 길이 기반."""
    if not (text or "").strip():
        return 0
    return max(1, len(text) // 4)


def get_llm_parallel_workers() -> int:
    """Step5 SQL→Table LLM 병렬 처리 수. config.settings ConcurrencyConfig와 동일 (MAX_CONCURRENCY, 청크/배치 단위)."""
    from config.settings import settings
    return settings.concurrency.max_concurrency
