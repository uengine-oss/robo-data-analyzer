"""YAML 기반 온톨로지 역할 매핑 로더.

언어별 노드 타입을 역할(module, function, variable 등)로 매핑.
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

        self._paradigm: Dict[str, List[str]] = data.get("paradigm", {})
        self._stereotypes: Dict[str, List[str]] = data.get("stereotypes", {})
        self._extensions: Dict[str, List[str]] = data.get("extensions", {})
        self._all_source_exts: FrozenSet[str] = frozenset(
            ext for exts in self._extensions.values() for ext in exts
        )

    def _get_role(self, role: str, lang: str) -> FrozenSet[str]:
        role_map = self._roles.get(role)
        if not role_map:
            return frozenset()
        types = role_map.get(lang)
        if types is None:
            return frozenset()
        return types

    # --- 새 역할 메서드 (범용) ---
    def module_types(self, lang: str) -> FrozenSet[str]:
        return self._get_role("module", lang)

    def function_types(self, lang: str) -> FrozenSet[str]:
        return self._get_role("function", lang)

    def variable_types(self, lang: str) -> FrozenSet[str]:
        return self._get_role("variable", lang)

    def constant_types(self, lang: str) -> FrozenSet[str]:
        return self._get_role("constant", lang)

    # --- 하위호환 별칭 ---
    def container_types(self, lang: str) -> FrozenSet[str]:
        return self.module_types(lang)

    def method_types(self, lang: str) -> FrozenSet[str]:
        return self.function_types(lang)

    def field_types(self, lang: str) -> FrozenSet[str]:
        return self.variable_types(lang) | self.constant_types(lang)

    # --- 기존 메서드 ---
    def silent_types(self, lang: str) -> FrozenSet[str]:
        return self._get_role("silent", lang)

    def internal_types(self, lang: str) -> FrozenSet[str]:
        return self._get_role("internal", lang)

    def call_types(self, lang: str) -> FrozenSet[str]:
        return self._get_role("call", lang)

    def import_types(self, lang: str) -> FrozenSet[str]:
        return self._get_role("import", lang)

    def analyzable_types(self, lang: str) -> FrozenSet[str]:
        return self.function_types(lang) | self.variable_types(lang) | self.constant_types(lang)

    def graph_node_types(self, lang: str) -> FrozenSet[str]:
        return self.module_types(lang) | self.function_types(lang) | self.variable_types(lang) | self.constant_types(lang)

    # --- 패러다임 ---
    def is_oop(self, lang: str) -> bool:
        return lang in self._paradigm.get("oop", [])

    def is_procedural(self, lang: str) -> bool:
        return lang in self._paradigm.get("procedural", [])

    def paradigm(self, lang: str) -> str:
        for p, langs in self._paradigm.items():
            if lang in langs:
                return p
        return "unknown"

    # --- 스테레오타입 ---
    def valid_stereotypes(self, lang: str) -> FrozenSet[str]:
        """해당 언어에서 허용되는 스테레오타입 목록."""
        common = self._stereotypes.get("common", [])
        if self.is_oop(lang):
            return frozenset(common + self._stereotypes.get("oop", []))
        return frozenset(common)

    def is_valid_stereotype(self, stereotype: str, lang: str) -> bool:
        """해당 스테레오타입이 이 언어에서 유효한지."""
        return stereotype in self.valid_stereotypes(lang)

    # --- 서브레이블 역할 역매핑: AST 타입 → primary 라벨 ---
    def primary_label_for(self, node_type: str, lang: str) -> str:
        """AST 노드 타입에 대한 Neo4j primary 라벨 반환."""
        if node_type in self.module_types(lang):
            return "MODULE"
        if node_type in self.function_types(lang):
            return "FUNCTION"
        if node_type in self.constant_types(lang):
            return "CONSTANT"
        if node_type in self.variable_types(lang):
            return "VARIABLE"
        return "MODULE"  # fallback

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
