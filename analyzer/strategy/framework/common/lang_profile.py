"""YAML 기반 언어 프로필 로더.

언어별 stdlib, 파라미터 파서 스타일, 네이밍 규칙 등을 관리.
ontology.py와 동일한 싱글턴 패턴 사용.
"""

from __future__ import annotations

import json
import logging
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, FrozenSet, Optional

import yaml

logger = logging.getLogger(__name__)

_INTERNAL_DIR = Path(__file__).resolve().parent.parent / "data" / "stdlib"


class LangProfile:
    """언어별 프로필 — stdlib, param_style, naming 등."""

    def __init__(self, yaml_path: Path):
        with open(yaml_path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)

        self._profiles: Dict[str, Dict[str, Any]] = data.get("profiles", {})
        self._stdlib_cache: Dict[str, FrozenSet[str]] = {}

    def _get(self, lang: str) -> Dict[str, Any]:
        profile = self._profiles.get(lang)
        if profile is None:
            raise ValueError(
                f"미지원 언어 '{lang}'. "
                f"지원 언어: {sorted(self._profiles.keys())}"
            )
        return profile

    # -- stdlib ----------------------------------------------------------

    def stdlib_classes(self, lang: str) -> FrozenSet[str]:
        """언어별 표준 라이브러리 클래스/함수 목록 (frozenset)."""
        if lang in self._stdlib_cache:
            return self._stdlib_cache[lang]

        profile = self._get(lang)
        stdlib_file = profile.get("stdlib_file", "")
        if not stdlib_file:
            self._stdlib_cache[lang] = frozenset()
            return self._stdlib_cache[lang]

        path = _INTERNAL_DIR / stdlib_file
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
            result = frozenset(data) if isinstance(data, list) else frozenset()
        except FileNotFoundError:
            logger.debug("[LangProfile] %s 없음 — 빈 set 사용", path)
            result = frozenset()
        except Exception as e:
            logger.error("[LangProfile] 로드 실패 | path=%s error=%s", path, e, exc_info=True)
            result = frozenset()

        self._stdlib_cache[lang] = result
        return result

    # -- param style -----------------------------------------------------

    def param_style(self, lang: str) -> str:
        """파라미터 파싱 스타일: 'type_name' | 'name_colon_type' | 'name_type'."""
        return self._get(lang).get("param_style", "type_name")

    # -- naming ----------------------------------------------------------

    def naming_convention(self, lang: str) -> str:
        """네이밍 규칙: 'camelCase' | 'snake_case'."""
        return self._get(lang).get("naming_convention", "camelCase")

    # -- display ---------------------------------------------------------

    def display_name(self, lang: str) -> str:
        """표시용 언어 이름: 'Java', 'C', 'Python' 등."""
        return self._get(lang).get("display_name", lang.capitalize())

    def emoji(self, lang: str) -> str:
        """표시용 이모지."""
        return self._get(lang).get("emoji", "📄")

    # -- utility ---------------------------------------------------------

    def supported_languages(self) -> list[str]:
        return sorted(self._profiles.keys())

    def has_lang(self, lang: str) -> bool:
        return lang in self._profiles


@lru_cache(maxsize=1)
def get_lang_profile() -> LangProfile:
    """모듈 레벨 싱글턴."""
    yaml_path = Path(__file__).parent / "lang_profile.yaml"
    return LangProfile(yaml_path)
