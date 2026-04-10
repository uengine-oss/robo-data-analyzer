"""DeepAgent Skill 로더.

rules/deep_agent/skills/ 디렉토리에서 SKILL.md 파일을 로드하고,
카테고리별 적절한 Skill을 선택하여 시스템 프롬프트를 구성한다.

[Skill 구조]
  rules/deep_agent/skills/
  ├── entity-analysis/SKILL.md    # CLASS/STRUCT → 엔티티 판별 + READS/WRITES + FK
  ├── table-tracking/SKILL.md     # DDL 테이블 → 코드 역추적 + FK
  ├── sql-tracking/SKILL.md       # 미매칭 SQL → 사용처 추적 + CALLS_SQL
  └── config-tracking/SKILL.md    # 미매칭 Config → 참조 추적 + USES_CONFIG

[로드 방식]
DeepAgent Skills 패턴에 따라:
  1. Discovery: SKILL.md의 YAML 프론트매터(name, description)만 읽어 목록화
  2. Match: 입력 카테고리에 맞는 Skill 선택
  3. Read: 선택된 SKILL.md 본문 전체를 시스템 프롬프트에 주입
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 상수
# ---------------------------------------------------------------------------

# Skill 디렉토리 기본 경로 (프로젝트 루트 기준)
_SKILLS_DIR = Path(__file__).resolve().parents[5] / "rules" / "deep_agent" / "skills"

# 입력 카테고리 → Skill 이름 매핑
_CATEGORY_TO_SKILL: Dict[str, str] = {
    # 엔티티 관련 (DDL 없을 때)
    "entity": "entity-analysis",
    "bean": "entity-analysis",
    "cmp_mapping": "entity-analysis",
    # SQL 관련
    "sql": "sql-tracking",
    # 설정 관련
    "config": "config-tracking",
    # DDL 테이블 추적 (별도 호출)
    "ddl_tables": "table-tracking",
}

# 카테고리 미매칭 시 기본 Skill
_DEFAULT_SKILL = "sql-tracking"


# ---------------------------------------------------------------------------
# 데이터 모델
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SkillInfo:
    """SKILL.md에서 로드된 Skill 정보."""
    name: str
    description: str
    allowed_tools: List[str]
    body: str  # 마크다운 본문 (프론트매터 제외)


# ---------------------------------------------------------------------------
# 로더
# ---------------------------------------------------------------------------


def _parse_skill_md(path: Path) -> Optional[SkillInfo]:
    """SKILL.md 파일을 파싱하여 SkillInfo로 반환한다.

    YAML 프론트매터(--- ... ---) + 마크다운 본문 구조를 파싱.
    프론트매터가 없으면 None 반환.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except Exception as e:
        logger.warning("[SKILL] 파일 읽기 실패 | path=%s error=%s", path, e)
        return None

    # YAML 프론트매터 파싱 (--- ... --- 구간)
    if not text.startswith("---"):
        logger.warning("[SKILL] 프론트매터 없음 | path=%s", path)
        return None

    parts = text.split("---", 2)
    if len(parts) < 3:
        logger.warning("[SKILL] 프론트매터 파싱 실패 | path=%s", path)
        return None

    frontmatter_text = parts[1].strip()
    body = parts[2].strip()

    # 간단한 YAML 파싱 (외부 의존성 없이)
    fm: Dict[str, str] = {}
    current_key = ""
    current_value_lines: List[str] = []

    for line in frontmatter_text.split("\n"):
        stripped = line.strip()
        if not stripped:
            continue
        if ":" in stripped and not stripped.startswith("-") and not stripped.startswith(" "):
            # 이전 키 저장
            if current_key:
                fm[current_key] = " ".join(current_value_lines).strip()
            key, _, val = stripped.partition(":")
            current_key = key.strip()
            val = val.strip()
            # YAML > 표시자 제거
            if val == ">":
                val = ""
            current_value_lines = [val] if val else []
        else:
            # 멀티라인 값 이어가기
            current_value_lines.append(stripped)
    if current_key:
        fm[current_key] = " ".join(current_value_lines).strip()

    name = fm.get("name", path.parent.name)
    description = fm.get("description", "")
    allowed_tools_str = fm.get("allowed-tools", "")
    allowed_tools = [t.strip() for t in allowed_tools_str.split(",") if t.strip()]

    return SkillInfo(
        name=name,
        description=description,
        allowed_tools=allowed_tools,
        body=body,
    )


def load_all_skills(skills_dir: Optional[Path] = None) -> Dict[str, SkillInfo]:
    """skills 디렉토리에서 모든 SKILL.md를 로드하여 {name: SkillInfo} 딕셔너리로 반환한다."""
    base = skills_dir or _SKILLS_DIR
    skills: Dict[str, SkillInfo] = {}

    if not base.exists():
        logger.warning("[SKILL] skills 디렉토리 없음 | path=%s", base)
        return skills

    for skill_dir in sorted(base.iterdir()):
        if not skill_dir.is_dir():
            continue
        skill_md = skill_dir / "SKILL.md"
        if not skill_md.exists():
            continue
        info = _parse_skill_md(skill_md)
        if info:
            skills[info.name] = info
            logger.debug("[SKILL] 로드 완료 | name=%s tools=%s", info.name, info.allowed_tools)

    logger.info("[SKILL] 전체 로드 완료 | skills=%d (%s)", len(skills), list(skills.keys()))
    return skills


# ---------------------------------------------------------------------------
# Skill 선택
# ---------------------------------------------------------------------------


def select_skill_for_category(category: str, skills: Dict[str, SkillInfo]) -> Optional[SkillInfo]:
    """입력 카테고리에 맞는 Skill을 선택한다.

    Args:
        category: UnresolvedLink의 카테고리 (entity, sql, config 등).
        skills: load_all_skills()로 로드된 Skill 딕셔너리.

    Returns:
        매칭된 SkillInfo. 매칭 실패 시 기본 Skill 반환. Skill 자체가 없으면 None.
    """
    skill_name = _CATEGORY_TO_SKILL.get(category, _DEFAULT_SKILL)
    skill = skills.get(skill_name)

    if not skill:
        logger.warning("[SKILL] 카테고리 '%s' → Skill '%s' 없음", category, skill_name)
        # 폴백: 아무 Skill이라도 반환
        if skills:
            return next(iter(skills.values()))
        return None

    return skill


def build_system_prompt_with_skill(skill: SkillInfo) -> str:
    """Skill 본문을 시스템 프롬프트로 변환한다.

    공통 절대 규칙 + Skill 본문을 결합하여 최종 시스템 프롬프트를 구성한다.
    """
    common_rules = (
        "당신은 소스코드 분석 전문가 에이전트입니다.\n\n"
        "[절대 규칙]\n"
        "- 오직 제공된 도구만 사용하십시오.\n"
        "- 확신이 없으면 관계를 생성하지 마십시오.\n"
        "- 하나를 찾았다고 멈추지 말고 모든 사용처를 빠짐없이 찾으십시오.\n"
        "- 검색 결과가 0건이면 검색어를 바꿔 최소 2~3회 재시도하십시오.\n"
        "- source_function_id는 반드시 bundle_search에서 발견한 실제 함수의 chunk_id를 사용하십시오.\n\n"
    )
    return common_rules + skill.body


def build_skill_discovery_prompt(skills: Dict[str, SkillInfo]) -> str:
    """모든 Skill의 이름+설명 요약을 생성한다 (토큰 절약용 디스커버리).

    에이전트 시스템 프롬프트에 삽입하여 어떤 Skill이 사용 가능한지 알려주는 용도.
    실제로는 Python 코드가 카테고리 기반으로 Skill을 선택하므로, 참조용.
    """
    lines = ["[사용 가능한 Skill]"]
    for name, info in skills.items():
        lines.append(f"- {name}: {info.description}")
    return "\n".join(lines)
