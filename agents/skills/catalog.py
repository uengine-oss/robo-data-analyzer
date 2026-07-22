"""Validated catalog of analyzer LLM skills — SKILL 문서의 결정론 독해·검증 계층.

목록화(build_skill_catalog)와 YAML fence 계약 파싱(load_skill_contract, spec 079)을
담당한다. SKILL.md 가 단일 진실이며, 여기서 읽은 값은 LLM 을 거치지 않는다.
"""
from __future__ import annotations

import re
from pathlib import Path

import yaml

SKILLS_ROOT = Path(__file__).resolve().parent
FRAMEWORK_SKILLS_DIR = SKILLS_ROOT / "framework"
EXTERNAL_SKILLS_DIR = SKILLS_ROOT / "external"


class SkillCatalogError(RuntimeError):
    """Raised when a present SKILL document cannot be safely cataloged."""


def build_skill_catalog(skills_dir: Path, *, prefix: str = "") -> str:
    if not skills_dir.is_dir():
        return "NO_SKILLS_DIR"
    skills: list[str] = []
    for skill_dir in sorted(skills_dir.iterdir()):
        if not skill_dir.is_dir() or skill_dir.name.startswith("_") or skill_dir.name == "templates":
            continue
        document = skill_dir / "SKILL.md"
        if not document.exists():
            continue
        try:
            lines = document.read_text(encoding="utf-8").splitlines()
        except OSError as error:
            raise SkillCatalogError(
                f"Cannot read SKILL document {document}: {type(error).__name__}: {error}"
            ) from error
        if not lines or not lines[0].lstrip("# ").strip():
            raise SkillCatalogError(f"SKILL document has no title: {document}")
        skills.append(f"{prefix}{skill_dir.name}: {lines[0].lstrip('# ').strip()}")
    return "\n".join(skills) if skills else "NO_SKILLS"


# ── SKILL YAML fence 계약 — LLM 받아쓰기 대신 결정론 파싱으로 주입한다.

_YAML_FENCE_RE = re.compile(r"```yaml\s*\n(.*?)```", re.DOTALL)

# ScanResult 로 주입하는 계약 키 (fence 에 존재하는 것만 담는다).
SKILL_CONTRACT_KEYS = ("table_access_patterns", "ddl_transform", "call_wrappers")


def load_skill_contract(
    skill_name: str, *, skills_dir: Path = FRAMEWORK_SKILLS_DIR,
) -> dict:
    """``<skills_dir>/<skill_name>/SKILL.md`` 의 YAML fence 계약을 파싱한다.

    반환: ``SKILL_CONTRACT_KEYS`` 중 fence 에 있는 키만 담은 dict.
    문서가 없으면 빈 dict (스킬 미매칭과 동일 취급 — 호출측 판단).
    fence YAML 이 깨져 있으면 ``SkillCatalogError`` — 스킬 문서 결함은 데이터
    결함이므로 조용히 넘기지 않는다 (spec 079 FR-6).
    """
    document = skills_dir / skill_name / "SKILL.md"
    if not document.is_file():
        return {}
    try:
        text = document.read_text(encoding="utf-8")
    except OSError as error:
        raise SkillCatalogError(
            f"Cannot read SKILL document {document}: {type(error).__name__}: {error}"
        ) from error
    contract: dict = {}
    for block in _YAML_FENCE_RE.findall(text):
        try:
            data = yaml.safe_load(block)
        except yaml.YAMLError as error:
            raise SkillCatalogError(
                f"SKILL yaml fence 파싱 실패: {document}: {error}"
            ) from error
        if not isinstance(data, dict):
            continue
        for key in SKILL_CONTRACT_KEYS:
            if key in data:
                contract[key] = data[key]
    return contract
