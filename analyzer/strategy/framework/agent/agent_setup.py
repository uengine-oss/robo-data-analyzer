"""Agent 셋업 — factory + 자원 로더 (system prompt / SKILL).

scanning / linking 공용 에이전트 인프라:
  1. `build_agent_factory()` — LangChain 에이전트 빌드 환경 (state, api_key, run_dir)
     1회 초기화 + 팩토리 반환. middleware 바인딩.
  2. `load_system_prompt / load_user_template` — `rules/deep_agent/prompts/{scope}/*.md`
  3. `make_skill_tools / load_skills_for_scope` — Progressive Disclosure SKILL 로더.

호출자 (linking_agent / scanning_agent) 가 system_prompt + tools + skill_tools 를
조립해 에이전트를 빌드한다.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, List, Literal, Optional, Tuple

from langchain.agents import create_agent
from langchain_core.tools import tool as lc_tool

from analyzer.strategy.framework.agent.middleware import build_common_middleware
from analyzer.strategy.framework.analysis.chunk_bundle import (
    _get_chunk_bundle_dir_from_run_dir,
    build_bundle_span_index,
)
from client.llm_client import get_llm

logger = logging.getLogger(__name__)


# 프로젝트 루트 기준 rules/deep_agent 경로 — 모듈 이동 시 parents[N] 재계산 필요.
# 본 모듈 위치: analyzer/strategy/framework/agent/agent_setup.py (depth 5)
_BASE = Path(__file__).resolve().parents[4] / "rules" / "deep_agent"
_PROMPTS_DIR = _BASE / "prompts"
_SKILLS_DIR = _BASE / "skills"

AgentScope = Literal["scanning", "linking"]


# ═══════════════════════════════════════════════════════════════════════════
# Agent Factory — LangChain 에이전트 빌드 인프라
# ═══════════════════════════════════════════════════════════════════════════


def _ensure_bundle_indexes(state: Dict[str, Any]) -> None:
    """state에 span_index / chunk_index가 없으면 ChunkBundle로부터 빌드한다."""
    if state.get("span_index") and state.get("chunk_index"):
        return

    run_dir = state.get("run_dir")
    chunk_dir = _get_chunk_bundle_dir_from_run_dir(run_dir)

    span_index, chunk_index = build_bundle_span_index(
        chunk_bundle_dir=chunk_dir,
        artifact_index=state.get("artifact_index"),
        sql_index=state.get("sql_index"),
    )

    state["span_index"] = span_index
    state["chunk_index"] = chunk_index
    logger.info(
        "[DEEP_AGENT] ChunkBundle 인덱스 빌드 완료 | span=%d chunk=%d",
        len(span_index), len(chunk_index),
    )


def _bind_factory(model: Any) -> Callable:
    """create_agent + 공용 미들웨어를 바인딩한 에이전트 팩토리."""
    middleware = build_common_middleware(model)

    def factory(*, tools: list, system_prompt: str, **kwargs: Any) -> Any:
        return create_agent(
            model=model,
            tools=tools,
            system_prompt=system_prompt,
            middleware=middleware,
            **kwargs,
        )

    return factory


def build_agent_factory(
    state: Dict[str, Any],
    api_key: Optional[str] = None,
    run_dir: Optional[Path] = None,
) -> Tuple[Callable, Any]:
    """에이전트 실행 환경 1회 초기화 + 팩토리 반환.

    Returns:
        (factory, model) — `factory(tools=..., system_prompt=...)` 로 에이전트 생성.
    """
    if run_dir:
        state.setdefault("run_dir", str(run_dir))

    _ensure_bundle_indexes(state)

    model = get_llm(api_key=api_key) if api_key else get_llm()
    return _bind_factory(model), model


# ═══════════════════════════════════════════════════════════════════════════
# 프롬프트 로더 (system / user_template)
# ═══════════════════════════════════════════════════════════════════════════


def _strip_frontmatter(text: str) -> str:
    if text.startswith("---"):
        parts = text.split("---", 2)
        if len(parts) >= 3:
            return parts[2].strip()
    return text.strip()


def load_system_prompt(scope: AgentScope) -> str:
    """prompts/{scope}/system.md 본문을 반환. skill 안내는 system.md 안에 통합되어 있음."""
    path = _PROMPTS_DIR / scope / "system.md"
    return _strip_frontmatter(path.read_text(encoding="utf-8"))


def load_user_template(scope: AgentScope) -> str:
    """prompts/{scope}/user_template.md 본문을 반환."""
    path = _PROMPTS_DIR / scope / "user_template.md"
    return _strip_frontmatter(path.read_text(encoding="utf-8"))


# ═══════════════════════════════════════════════════════════════════════════
# SKILL 로더 — Progressive Disclosure 패턴
# ═══════════════════════════════════════════════════════════════════════════


@dataclass(frozen=True)
class SkillInfo:
    """SKILL.md에서 로드된 Skill 정보."""
    name: str
    description: str
    body: str  # 마크다운 본문 (프론트매터 제외)


def _parse_skill_md(path: Path) -> SkillInfo:
    """SKILL.md 파일을 파싱하여 SkillInfo로 반환.

    YAML 프론트매터(--- ... ---) + 마크다운 본문 구조.
    프론트매터에서 `name` · `description` 두 필드만 사용.

    잘못 작성된 SKILL.md 파일은 fail-fast — 호출자가 잡거나 전체 로드를 실패시킨다.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except Exception as e:
        logger.error("[SKILL] 파일 읽기 실패 | path=%s | %s: %s", path, type(e).__name__, e, exc_info=True)
        raise RuntimeError(f"SKILL.md 읽기 실패: {path} ({type(e).__name__}: {e})") from e

    if not text.startswith("---"):
        logger.error("[SKILL] 프론트매터 없음 | path=%s", path)
        raise ValueError(f"SKILL.md 프론트매터(--- ...) 없음: {path}")

    parts = text.split("---", 2)
    if len(parts) < 3:
        logger.error("[SKILL] 프론트매터 파싱 실패 | path=%s", path)
        raise ValueError(f"SKILL.md 프론트매터 닫기 누락(--- ... ---): {path}")

    frontmatter_text = parts[1].strip()
    body = parts[2].strip()

    # 간단한 YAML 파싱 (name, description 만 수집)
    fm: Dict[str, str] = {}
    current_key = ""
    current_value_lines: List[str] = []

    for line in frontmatter_text.split("\n"):
        stripped = line.strip()
        if not stripped:
            continue
        if ":" in stripped and not stripped.startswith("-") and not stripped.startswith(" "):
            if current_key:
                fm[current_key] = " ".join(current_value_lines).strip()
            key, _, val = stripped.partition(":")
            current_key = key.strip()
            val = val.strip()
            if val == ">":
                val = ""
            current_value_lines = [val] if val else []
        else:
            current_value_lines.append(stripped)
    if current_key:
        fm[current_key] = " ".join(current_value_lines).strip()

    name = fm.get("name", path.parent.name)
    description = fm.get("description", "")

    return SkillInfo(name=name, description=description, body=body)


def _load_skills_from_dir(base: Path) -> Dict[str, SkillInfo]:
    """지정 디렉토리 아래의 모든 SKILL.md 를 로드 (잘못된 파일은 fail-fast)."""
    skills: Dict[str, SkillInfo] = {}
    if not base.exists():
        return skills
    for skill_dir in sorted(base.iterdir()):
        if not skill_dir.is_dir():
            continue
        skill_md = skill_dir / "SKILL.md"
        if not skill_md.exists():
            continue
        info = _parse_skill_md(skill_md)
        skills[info.name] = info
    return skills


_SKILL_CACHE: Dict[str, Dict[str, SkillInfo]] = {}


def load_skills_for_scope(scope: AgentScope) -> Dict[str, SkillInfo]:
    """지정 scope 의 스킬만 로드 (skills/{scope}/* — 교차 격리).

    동일 프로세스 내 중복 로드는 모듈-레벨 캐시로 회피.
    """
    if scope in _SKILL_CACHE:
        return _SKILL_CACHE[scope]

    skills = _load_skills_from_dir(_SKILLS_DIR / scope)
    logger.info(
        "[SKILL:%s] scope 로드 완료 | skills=%d (%s)",
        scope, len(skills), list(skills.keys()),
    )
    _SKILL_CACHE[scope] = skills
    return skills


def make_skill_tools(scope: AgentScope) -> list:
    """list_skills() / read_skill(name) 두 도구를 반환.

    Progressive Disclosure 패턴:
      - list_skills: 스킬 메타데이터(name, description) 목록 JSON.
      - read_skill:  지정 스킬의 본문을 반환.

    지정 scope 의 스킬만 노출 (교차 격리).
    """

    @lc_tool
    def list_skills() -> str:
        """사용 가능한 스킬 목록(JSON) 을 반환. 각 항목 = {name, description}.

        작업 중 특정 패턴/프레임워크 지식이 필요하면 먼저 이 도구로 후보를 확인한 뒤
        `read_skill(name)` 으로 본문을 로드하세요.
        """
        skills = load_skills_for_scope(scope)
        entries = [
            {"name": s.name, "description": s.description}
            for s in skills.values()
        ]
        logger.info(
            "[SKILL:%s] list_skills 호출 | 후보=%s",
            scope, [s["name"] for s in entries],
        )
        return json.dumps(entries, ensure_ascii=False)

    @lc_tool
    def read_skill(name: str) -> str:
        """지정 스킬의 본문(마크다운) 을 로드한다. 이름은 list_skills 의 결과에서 고르세요."""
        skills = load_skills_for_scope(scope)
        skill = skills.get(name)
        if not skill:
            logger.warning("[SKILL:%s] read_skill 실패 | name=%s (미존재)", scope, name)
            return f"SKILL_NOT_FOUND: '{name}'"
        logger.info(
            "[SKILL:%s] read_skill 채택 | name=%s body_chars=%d",
            scope, name, len(skill.body),
        )
        return skill.body

    return [list_skills, read_skill]


__all__ = [
    "build_agent_factory",
    "load_system_prompt",
    "load_user_template",
    "make_skill_tools",
    "load_skills_for_scope",
    "SkillInfo",
    "AgentScope",
]
