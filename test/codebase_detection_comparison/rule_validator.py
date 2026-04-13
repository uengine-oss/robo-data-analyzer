"""JSON Schema 기반 규칙 검증기.

schemas/ 디렉토리의 5개 스키마로 skills/**/rules/**/*.json 및 rules/*.json 을 검증.
로드 시점에 검증 실패 시 RuleValidationError 를 즉시 raise — 런타임 예외 방지.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from jsonschema import Draft7Validator, RefResolver


SCHEMA_NAMES = (
    "framework_rule",   # skills/learned/*.json (동적 발견 캐시)
    "skill_meta",       # skills/detect_*/meta.json
    "global_rules",     # rules/*.json
)


class RuleValidationError(ValueError):
    """규칙 파일이 JSON Schema 를 통과하지 못했을 때 raise.

    메시지에 파일 경로 + 스키마명 + 모든 오류를 포함하여 개발자 즉시 진단 가능.
    """

    def __init__(self, source_file: Path, schema_name: str, errors: list[str]):
        self.source_file = source_file
        self.schema_name = schema_name
        self.errors = errors
        lines = "\n  - ".join(errors)
        super().__init__(
            f"[RuleValidationError] {source_file} (schema={schema_name}):\n  - {lines}"
        )


@dataclass
class ValidationReport:
    """배치 검증 결과 누적용 (CI 등에서 활용)."""

    passed: list[Path] = field(default_factory=list)
    failed: list[RuleValidationError] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.failed

    def summary(self) -> str:
        return f"passed={len(self.passed)} failed={len(self.failed)}"


class RuleValidator:
    """JSON Schema 로더 + 검증자."""

    def __init__(self, schemas_dir: Path):
        self.schemas_dir = Path(schemas_dir).resolve()
        self.schemas: dict[str, dict] = {}
        for name in SCHEMA_NAMES:
            path = self.schemas_dir / f"{name}.schema.json"
            with path.open("r", encoding="utf-8") as f:
                self.schemas[name] = json.load(f)

        # 파일 간 $ref 해석을 위해 base_uri 를 schemas_dir 로 설정한 공통 resolver 준비.
        store = {
            f"{name}.schema.json": schema for name, schema in self.schemas.items()
        }
        self._base_uri = self.schemas_dir.as_uri() + "/"
        self._resolvers = {
            name: RefResolver(base_uri=self._base_uri, referrer=schema, store=store)
            for name, schema in self.schemas.items()
        }

        # 스키마 자체의 유효성은 구성 시 1회 확인.
        for name, schema in self.schemas.items():
            Draft7Validator.check_schema(schema)

    def validate_rule(
        self, rule_dict: dict[str, Any], schema_name: str, source_file: Path
    ) -> None:
        """단일 규칙 검증. 실패 시 RuleValidationError raise."""

        if schema_name not in self.schemas:
            raise ValueError(f"Unknown schema: {schema_name}")

        validator = Draft7Validator(
            self.schemas[schema_name], resolver=self._resolvers[schema_name]
        )
        errors = sorted(validator.iter_errors(rule_dict), key=lambda e: e.path)
        if errors:
            msgs = [self._fmt(e) for e in errors]
            raise RuleValidationError(source_file, schema_name, msgs)

    def validate_file(self, path: Path, schema_name: str) -> None:
        """JSON 파일을 읽어 검증."""

        with Path(path).open("r", encoding="utf-8") as f:
            data = json.load(f)
        self.validate_rule(data, schema_name, Path(path))

    def validate_all(self, skills_dir: Path, rules_dir: Path) -> ValidationReport:
        """배치 검증. 전역 rules + skills/detect_*/meta.json + skills/learned/*.json."""

        report = ValidationReport()

        # 전역 규칙.
        for path in Path(rules_dir).glob("*.json"):
            try:
                self.validate_file(path, "global_rules")
                report.passed.append(path)
            except RuleValidationError as err:
                report.failed.append(err)

        # skills/detect_*/meta.json.
        for meta_path in Path(skills_dir).glob("detect_*/meta.json"):
            try:
                self.validate_file(meta_path, "skill_meta")
                report.passed.append(meta_path)
            except RuleValidationError as err:
                report.failed.append(err)

        # skills/learned/*.json (동적 발견 규칙, framework_rule 스키마).
        learned_dir = Path(skills_dir) / "learned"
        if learned_dir.exists():
            for path in learned_dir.glob("*.json"):
                try:
                    self.validate_file(path, "framework_rule")
                    report.passed.append(path)
                except RuleValidationError as err:
                    report.failed.append(err)

        return report

    @staticmethod
    def _fmt(err) -> str:
        loc = "/".join(str(p) for p in err.absolute_path) or "<root>"
        return f"{loc}: {err.message}"
