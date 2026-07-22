"""Closed ExternalSkill schema, timeout validation and atomic persistence."""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import re
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
import regex
import yaml

from shared.files.paths import resolve_within


_SLUG = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
_EXTENSION = re.compile(r"^\.[a-z0-9][a-z0-9._+-]*$")
_RULE_NAME = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
_UNSAFE = (
    re.compile(r"\([^)]*[+*][^)]*\)\s*(?:[+*]|\{\d)") ,
    re.compile(r"\\[1-9]"),
    re.compile(r"\(\?(?:R|0|\()"),
)


class ExternalExtractionRule(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str
    pattern: Annotated[str, Field(min_length=1, max_length=2000)]
    key_group: str
    value_group: str | None = None

    @field_validator("name")
    @classmethod
    def _valid_name(cls, value: str) -> str:
        if not _RULE_NAME.fullmatch(value):
            raise ValueError("rule name must be a lowercase identifier")
        return value

    @field_validator("key_group")
    @classmethod
    def _valid_key_group(cls, value: str) -> str:
        if not _RULE_NAME.fullmatch(value):
            raise ValueError("key_group must be a named capture group")
        return value

    @field_validator("value_group")
    @classmethod
    def _valid_value_group(cls, value: str | None) -> str | None:
        if value is not None and not _RULE_NAME.fullmatch(value):
            raise ValueError("value_group must be a named capture group")
        return value


class ExternalSkill(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str
    extensions: tuple[str, ...]
    content_signatures: tuple[Annotated[str, Field(min_length=1, max_length=120)], ...] = ()
    rules: tuple[ExternalExtractionRule, ...]
    explanation: Annotated[str, Field(max_length=500)] = ""
    provenance: Annotated[str, Field(max_length=500)] = ""

    @field_validator("name")
    @classmethod
    def _valid_slug(cls, value: str) -> str:
        if not _SLUG.fullmatch(value):
            raise ValueError("skill name must be a lowercase slug")
        return value

    @field_validator("extensions")
    @classmethod
    def _valid_extensions(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        normalized = tuple(value.lower() for value in values)
        if not normalized or len(normalized) > 12 or any(not _EXTENSION.fullmatch(value) for value in normalized):
            raise ValueError("extensions must contain 1..12 normalized suffixes")
        if len(set(normalized)) != len(normalized):
            raise ValueError("extensions must be unique")
        return normalized

    @model_validator(mode="after")
    def _bounded_unique_rules(self) -> "ExternalSkill":
        if not self.rules or len(self.rules) > 12:
            raise ValueError("rules must contain 1..12 entries")
        names = [rule.name for rule in self.rules]
        if len(names) != len(set(names)):
            raise ValueError("rule names must be unique")
        return self


@dataclass(frozen=True)
class ExternalRuleMatch:
    """One proposed span over the original text — never generated content (spec 078)."""
    key: str
    start: int                               # char offset of the full match in the source
    end: int                                 # exclusive char offset
    line: int
    rule_name: str


@dataclass(frozen=True)
class ValidatedExternalSkill:
    skill: ExternalSkill
    fingerprint: str
    is_validated: bool = True


class ExternalSkillValidationError(ValueError):
    pass


def _compiled_rule(rule: ExternalExtractionRule):
    for unsafe in _UNSAFE:
        if unsafe.search(rule.pattern):
            raise ExternalSkillValidationError(f"unsafe regex construct in rule {rule.name}")
    try:
        compiled = regex.compile(rule.pattern, regex.MULTILINE)
    except regex.error as error:
        raise ExternalSkillValidationError(f"regex compile failed for {rule.name}: {error}") from error
    required = {rule.key_group, *([rule.value_group] if rule.value_group else [])}
    missing = sorted(group for group in required if group not in compiled.groupindex)
    if missing:
        raise ExternalSkillValidationError(f"missing named capture groups for {rule.name}: {missing}")
    return compiled


def _apply_rule(
    rule: ExternalExtractionRule,
    text: str,
    *,
    timeout_seconds: float,
    max_matches: int,
) -> list[ExternalRuleMatch]:
    compiled = _compiled_rule(rule)
    output: list[ExternalRuleMatch] = []
    try:
        for match in compiled.finditer(text, timeout=timeout_seconds):
            key = (match.group(rule.key_group) or "").strip()
            if not key:
                raise ExternalSkillValidationError(f"rule {rule.name} produced an empty key")
            if len(key) > 240:
                raise ExternalSkillValidationError(f"rule {rule.name} produced an oversized key")
            if match.end() <= match.start():
                raise ExternalSkillValidationError(f"rule {rule.name} produced a zero-width span")
            line = text.count("\n", 0, match.start()) + 1
            output.append(ExternalRuleMatch(key, match.start(), match.end(), line, rule.name))
            if len(output) > max_matches:
                raise ExternalSkillValidationError(f"rule {rule.name} exceeded match limit {max_matches}")
    except TimeoutError as error:
        raise ExternalSkillValidationError(f"rule {rule.name} exceeded regex timeout") from error
    unique_keys = {item.key for item in output}
    if output and len(output) > max(100, len(unique_keys) * 20):
        raise ExternalSkillValidationError(f"rule {rule.name} caused duplicate explosion")
    return output


def _fingerprint(skill: ExternalSkill) -> str:
    payload = json.dumps(skill.model_dump(mode="json"), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def validate_external_skill(
    skill: ExternalSkill,
    positive_samples: list[str],
    *,
    negative_samples: list[str] | None = None,
    timeout_seconds: float = 0.05,
    max_matches: int = 1000,
) -> ValidatedExternalSkill:
    """Validate compile/safety/timeout/positive/negative/overfit contracts."""
    if not positive_samples:
        raise ExternalSkillValidationError("at least one positive sample is required")
    all_matches: list[ExternalRuleMatch] = []
    for text in positive_samples:
        for rule in skill.rules:
            matches = _apply_rule(rule, text, timeout_seconds=timeout_seconds, max_matches=max_matches)
            all_matches.extend(matches)
    if not all_matches:
        raise ExternalSkillValidationError("skill produced no keys from positive samples")
    for rule in skill.rules:
        for match in all_matches:
            if len(match.key) >= 4 and (match.key in rule.pattern or re.escape(match.key) in rule.pattern):
                raise ExternalSkillValidationError("skill pattern enumerates an extracted customer key")
    for text in negative_samples or []:
        for rule in skill.rules:
            if _apply_rule(rule, text, timeout_seconds=timeout_seconds, max_matches=max_matches):
                raise ExternalSkillValidationError(f"rule {rule.name} matched a negative sample")
    return ValidatedExternalSkill(skill=skill, fingerprint=_fingerprint(skill))


def apply_validated_external_skill(
    validated: ValidatedExternalSkill,
    text: str,
    *,
    timeout_seconds: float = 0.05,
    max_matches: int = 1000,
) -> list[ExternalRuleMatch]:
    if not validated.is_validated or validated.fingerprint != _fingerprint(validated.skill):
        raise ExternalSkillValidationError("external skill validation fingerprint mismatch")
    output = [
        match
        for rule in validated.skill.rules
        for match in _apply_rule(rule, text, timeout_seconds=timeout_seconds, max_matches=max_matches)
    ]
    seen: set[tuple[str, int, int]] = set()
    unique: list[ExternalRuleMatch] = []
    for item in output:
        identity = (item.key, item.start, item.end)
        if identity in seen:
            continue
        seen.add(identity)
        unique.append(item)
    return unique


def serialize_external_skill(skill: ExternalSkill) -> str:
    body = yaml.safe_dump(skill.model_dump(mode="json"), allow_unicode=True, sort_keys=False)
    return f"# External Skill: {skill.name}\n\n```yaml\n{body}```\n"


def parse_external_skill(document: str) -> ExternalSkill:
    match = re.fullmatch(r"# External Skill: [^\n]+\n\n```yaml\n(.*)```\n", document, re.DOTALL)
    if match is None:
        raise ExternalSkillValidationError("invalid external SKILL.md envelope")
    payload = yaml.safe_load(match.group(1))
    if not isinstance(payload, dict):
        raise ExternalSkillValidationError("external SKILL.md payload must be a mapping")
    return ExternalSkill.model_validate(payload)


def persist_external_skill(skill: ExternalSkill, root: Path) -> Path:
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    skill_dir = resolve_within(root, skill.name)
    skill_dir.mkdir(parents=True, exist_ok=True)
    target = skill_dir / "SKILL.md"
    temporary = skill_dir / ".SKILL.md.tmp"
    try:
        temporary.write_text(serialize_external_skill(skill), encoding="utf-8")
        temporary.replace(target)
    finally:
        temporary.unlink(missing_ok=True)
    return target


def load_external_skills(root: Path) -> list[ExternalSkill]:
    root = Path(root)
    if not root.is_dir():
        return []
    return [
        parse_external_skill(path.read_text(encoding="utf-8"))
        for path in sorted(root.glob("*/SKILL.md"))
    ]


def skill_matches_file(skill: ExternalSkill, extension: str, text: str) -> bool:
    return extension.lower() in skill.extensions and (
        not skill.content_signatures or all(signature in text for signature in skill.content_signatures)
    )
