"""Skill 플러그인 로더 — Dynamic Discovery 버전.

Skill 은 더 이상 "프레임워크 답안지" 가 아니다. 이제 Skill = (언어 식별자 + 탐색 매뉴얼).
- meta.json : 언어 이름·확장자·매니페스트 패턴·import 정규식
- SKILL.md  : 사람/AI 가 읽는 탐색 전략 매뉴얼

실제 프레임워크 탐지는 dynamic_discovery.py 가 수행.

캐시: skills/learned/ 아래 동적 발견된 규칙(framework_rule 스키마)만 여기서 같이 로드.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from test.codebase_detection_comparison.rule_validator import (
    RuleValidationError,
    RuleValidator,
)


@dataclass
class LearnedRule:
    """skills/learned/ 에 저장된 동적 발견 규칙."""

    name: str
    prefix: str
    applicable_languages: list[str]
    llm_confidence: float
    library_role: str | None
    source_file: Path


@dataclass
class Skill:
    """언어 하나의 식별 정보 + 탐색 매뉴얼."""

    name: str                       # "detect_python"
    language_names: list[str]       # ["Python"]
    source_extensions: list[str]
    config_patterns: list[str]
    import_regex: str
    skill_md: str                   # 탐색 매뉴얼 (사람·AI 공용)
    meta_extra: dict[str, Any] = field(default_factory=dict)


class SkillLoadError(RuntimeError):
    """Skill 로드 실패."""


def _load_json(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def load_skill_from_dir(skill_dir: Path, validator: RuleValidator) -> Skill:
    """단일 skills/detect_<lang>/ 폴더를 Skill 로 변환."""

    meta_path = skill_dir / "meta.json"
    if not meta_path.exists():
        raise SkillLoadError(f"meta.json missing in {skill_dir}")

    validator.validate_file(meta_path, "skill_meta")
    meta = _load_json(meta_path)

    skill_md_path = skill_dir / "SKILL.md"
    skill_md = skill_md_path.read_text(encoding="utf-8") if skill_md_path.exists() else ""

    return Skill(
        name=skill_dir.name,
        language_names=meta["language_names"],
        source_extensions=meta["source_extensions"],
        config_patterns=meta.get("config_patterns", []),
        import_regex=meta["import_regex"],
        skill_md=skill_md,
        meta_extra={
            k: v
            for k, v in meta.items()
            if k
            not in {
                "$schema",
                "language_names",
                "source_extensions",
                "config_patterns",
                "import_regex",
            }
        },
    )


def load_learned_rules(
    learned_dir: Path, validator: RuleValidator
) -> list[LearnedRule]:
    """skills/learned/ 의 동적 발견 규칙을 로드. 없어도 정상."""

    rules: list[LearnedRule] = []
    if not learned_dir.exists():
        return rules
    for path in sorted(learned_dir.glob("*.json")):
        try:
            validator.validate_file(path, "framework_rule")
        except RuleValidationError:
            continue  # 검증 실패한 캐시는 조용히 건너뜀
        data = _load_json(path)
        evidence = data.get("evidence", {})
        imports = evidence.get("imports", [])
        prefix = imports[0]["prefix"] if imports else path.stem
        meta = data.get("metadata", {})
        rules.append(
            LearnedRule(
                name=data["name"],
                prefix=prefix,
                applicable_languages=data["applicable_languages"],
                llm_confidence=float(meta.get("llm_confidence", 0.0) or 0.0),
                library_role=meta.get("library_role"),
                source_file=path,
            )
        )
    return rules


def discover_skills(
    skills_dir: str | Path, validator: RuleValidator
) -> dict[str, Skill]:
    """skills/detect_*/ 폴더를 자동 스캔. 언어별 매핑 dict 반환.

    shared/ 와 rules/{frameworks,orms,databases}/ 는 더 이상 사용하지 않음.
    """

    skills_dir = Path(skills_dir)
    if not skills_dir.exists():
        raise SkillLoadError(f"skills directory not found: {skills_dir}")

    skills_by_name: dict[str, Skill] = {}
    for sub in sorted(skills_dir.iterdir()):
        if not sub.is_dir() or not sub.name.startswith("detect_"):
            continue
        skill = load_skill_from_dir(sub, validator)
        skills_by_name[skill.name] = skill

    by_language: dict[str, Skill] = {}
    for skill in skills_by_name.values():
        for lang in skill.language_names:
            by_language[lang] = skill
    return by_language


def get_skill_for_language(skills: dict[str, Skill], language: str) -> Skill | None:
    return skills.get(language) or skills.get("unknown")


def list_all_skills(skills: dict[str, Skill]) -> list[Skill]:
    unique = {id(s): s for s in skills.values()}
    return sorted(unique.values(), key=lambda s: s.name)
