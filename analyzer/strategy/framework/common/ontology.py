"""YAML 기반 온톨로지 역할 매핑 로더.

언어별 노드 타입을 역할(container, method, field 등)로 매핑.
싱글턴 패턴으로 한 번만 파싱하고 frozenset으로 캐싱.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Dict, FrozenSet, List

import yaml


class OntologyRoles:
    """YAML 기반 온톨로지 역할 매핑."""

    def __init__(self, yaml_path: Path):
        with open(yaml_path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)

        self._roles: Dict[str, Dict[str, FrozenSet[str]]] = {}
        for role_name, lang_map in data.get("roles", {}).items():
            self._roles[role_name] = {
                lang: frozenset(types) for lang, types in lang_map.items()
            }

        self._extensions: Dict[str, List[str]] = data.get("extensions", {})
        self._all_source_exts: FrozenSet[str] = frozenset(
            ext for exts in self._extensions.values() for ext in exts
        )

    def _get_role(self, role: str, lang: str) -> FrozenSet[str]:
        role_map = self._roles.get(role)
        if not role_map:
            raise ValueError(f"알 수 없는 역할: {role}")
        types = role_map.get(lang)
        if types is None:
            raise ValueError(
                f"미지원 언어 '{lang}' (역할: {role}). "
                f"지원 언어: {sorted(role_map.keys())}"
            )
        return types

    def container_types(self, lang: str) -> FrozenSet[str]:
        return self._get_role("container", lang)

    def method_types(self, lang: str) -> FrozenSet[str]:
        return self._get_role("method", lang)

    def field_types(self, lang: str) -> FrozenSet[str]:
        return self._get_role("field", lang)

    def silent_types(self, lang: str) -> FrozenSet[str]:
        return self._get_role("silent", lang)

    def internal_types(self, lang: str) -> FrozenSet[str]:
        return self._get_role("internal", lang)

    def call_types(self, lang: str) -> FrozenSet[str]:
        return self._get_role("call", lang)

    def import_types(self, lang: str) -> FrozenSet[str]:
        return self._get_role("import", lang)

    def analyzable_types(self, lang: str) -> FrozenSet[str]:
        return self.method_types(lang) | self.field_types(lang)

    def graph_node_types(self, lang: str) -> FrozenSet[str]:
        return self.container_types(lang) | self.method_types(lang) | self.field_types(lang)

    def source_extensions(self, lang: str) -> List[str]:
        exts = self._extensions.get(lang)
        if exts is None:
            raise ValueError(
                f"미지원 언어 '{lang}' (extensions). "
                f"지원 언어: {sorted(self._extensions.keys())}"
            )
        return exts

    def all_source_extensions(self) -> FrozenSet[str]:
        return self._all_source_exts

    def supported_languages(self) -> List[str]:
        langs = set()
        for role_map in self._roles.values():
            langs.update(role_map.keys())
        return sorted(langs)


@lru_cache(maxsize=1)
def get_ontology() -> OntologyRoles:
    """모듈 레벨 싱글턴."""
    yaml_path = Path(__file__).parent / "ontology_roles.yaml"
    return OntologyRoles(yaml_path)
