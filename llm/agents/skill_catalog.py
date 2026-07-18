"""Validated catalog of analyzer LLM skills."""
from __future__ import annotations

from pathlib import Path

SKILLS_DIR = Path(__file__).resolve().parent.parent / "skills"


class SkillCatalogError(RuntimeError):
    """Raised when a present SKILL document cannot be safely cataloged."""


def build_skill_catalog(skills_dir: Path = SKILLS_DIR, *, prefix: str = "") -> str:
    if not skills_dir.is_dir():
        return "NO_SKILLS_DIR"
    skills: list[str] = []
    for skill_dir in sorted(skills_dir.iterdir()):
        if not skill_dir.is_dir() or skill_dir.name.startswith("_"):
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
