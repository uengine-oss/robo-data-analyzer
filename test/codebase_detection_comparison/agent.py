"""Deep Agent 구성 — cache_lookup / quick_collect / submit_report + file-tracker.

모든 분석 과정이 이 에이전트 루프 안에서만 일어난다.
직접 LLM 호출 경로 없음. "즉시 submit" 은 에이전트의 LLM 이 turn 2 에서
submit_report 도구를 고르는 것일 뿐, 여전히 Deep Agent 내부.
"""
from __future__ import annotations

import json
import logging
from dataclasses import asdict
from pathlib import Path
from typing import Any, Optional

from deepagents import create_deep_agent
from deepagents.backends.filesystem import FilesystemBackend
from langchain_core.tools import tool

from client.llm_client import get_llm
from config.settings import settings

from .collector import CodebaseInfo, collect, render_analysis_context
from .tools.cache import cache_key_from_info, read_cache, write_cache

logger = logging.getLogger(__name__)

MODULE_ROOT = Path(__file__).resolve().parent
PROMPTS_DIR = MODULE_ROOT / "prompts"
SKILLS_DIR = MODULE_ROOT / "skills"


SYSTEM_PROMPT = """너는 코드베이스 분석 전문가다.

## 임무
주어진 프로젝트가 어떤 프레임워크를 쓰고 DB 에 어떻게 접근하는지 자연어 1~2문단으로 설명.

## 1 단계 — 수집
`quick_collect()` 를 반드시 먼저 호출. 확장자/매니페스트/설정/import 빈도/대표 파일 뼈대 (import + 함수 시그니처) 를 한 번에 받는다.
응답이 "CACHE_HIT" 로 시작하면 포함된 summary 를 그대로 `submit_report` 에 전달하고 종료.

## 2 단계 — 판단
quick_collect 결과로 프레임워크/DB 접근 패턴이 **명확**하면 즉시 `submit_report(summary=...)` 호출.
명확 기준: 매니페스트에 프레임워크 의존성 명시, 또는 뼈대에서 결정적 API/어노테이션/매크로 관찰.

## 3 단계 — 필요할 때만 추가 탐색
모호하면 아래 도구로 **튀어나온 단어만** 추적:
- `read_file(path)` — 관심 파일 본문 직접 읽기 (뼈대에서 본 함수/헤더의 정체 확인용)
- `grep(pattern)` — 특정 단어 쓰이는 위치 찾기
- `ls(path)`, `glob(pattern)` — 폴더 구조
- `task("file-tracker", description=...)` — 복잡한 위임이 필요할 때만

**절대 규칙**:
- 수집 결과에서 **새로 튀어나온 단어/접두어** 가 있을 때만 추가 도구 호출.
- 같은 파일 두 번 읽지 마라.
- 총 도구 호출 **5 회 이내**.
- 2 번 연속 새 단서를 못 찾으면 바로 submit_report (현재 증거로 결론).

## 답변 (summary 인자)
자연어 1~2문단. JSON/마크다운 헤더/리스트 금지. 줄글.
반드시 포함: 주 언어, 프레임워크 이름, DB 접근 방식, 테이블명 규칙(있으면), DB 종류(있으면).
모르는 프레임워크도 증거 기반 추정 + "추정" 명시. "알 수 없음" 회피.
"""


def _read_prompt(name: str) -> str:
    return (PROMPTS_DIR / name).read_text(encoding="utf-8")


def _info_to_meta(info: CodebaseInfo) -> dict[str, Any]:
    d = asdict(info)
    # 긴 텍스트는 메타에 저장하지 않음
    for k in ("manifest_content", "config_content", "representative_skeleton"):
        d.pop(k, None)
    return d


def create_agent(root_path: Path, api_key: str = "", use_cache: bool = True):
    """Deep Agent + holder 반환. holder 로 결과 수거.

    use_cache=False 이면 캐시 조회/저장을 모두 건너뛴다 (순수 로직 검증용).
    """

    holder: dict[str, Any] = {
        "info": None,
        "summary": None,
        "cache_key": None,
        "cache_hit": False,
        "use_cache": use_cache,
    }

    context_template = _read_prompt("analysis_context.txt")
    tracker_prompt = _read_prompt("file_tracker_system.txt")

    @tool
    def quick_collect() -> str:
        """5 가지 결정론 정보 수집 + 캐시 조회. 캐시 히트 시 이전 summary 를 그대로 반환하니
        그대로 submit_report 에 전달하라. 미스 시 렌더링된 컨텍스트 문자열 반환."""
        info = collect(str(root_path))
        holder["info"] = info
        key = cache_key_from_info(info)
        holder["cache_key"] = key
        if use_cache:
            cached = read_cache(key)
            if cached and cached.get("summary"):
                holder["cache_hit"] = True
                return (
                    "CACHE_HIT — 이 summary 를 그대로 submit_report 에 전달하라.\n\n"
                    f"{cached['summary']}"
                )
        return render_analysis_context(info, context_template)

    @tool
    def submit_report(summary: str) -> str:
        """최종 자연어 요약 제출. 1~2문단 줄글만 전달. 캐시 저장은 자동으로 처리됨."""
        holder["summary"] = summary
        key = holder.get("cache_key") or ""
        if use_cache and key and holder.get("info") and not holder.get("cache_hit"):
            try:
                meta = _info_to_meta(holder["info"])
                write_cache(key, summary, meta=meta)
            except Exception as e:
                logger.warning("캐시 저장 실패: %s", e)
        return "submitted"

    file_tracker = {
        "name": "file-tracker",
        "description": (
            "특정 접두어/네임스페이스에 대해 실제 소스 파일을 열어 심층 조사한다. "
            "파일 최대 2 개 + grep 1~2 회. JSON 요약만 반환."
        ),
        "system_prompt": tracker_prompt,
        "model": settings.llm.cheap_model,
    }

    llm = get_llm(api_key=api_key or settings.llm.api_key, model=settings.llm.model)
    backend = FilesystemBackend(root_dir=str(root_path))

    agent = create_deep_agent(
        model=llm,
        tools=[quick_collect, submit_report],
        subagents=[file_tracker],
        skills=[str(SKILLS_DIR)],
        backend=backend,
        system_prompt=SYSTEM_PROMPT,
    )
    return agent, holder


def count_tool_calls(result: dict[str, Any]) -> int:
    count = 0
    for msg in result.get("messages", []):
        tc = getattr(msg, "tool_calls", None) or []
        count += len(tc)
    return count
