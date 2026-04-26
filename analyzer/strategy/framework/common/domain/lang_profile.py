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
        """언어별 표준 라이브러리 클래스/함수 목록 (frozenset).

        stdlib_file 미지정 = 해당 언어의 stdlib 추적 안 함 (의도된 빈 set).
        파일 부재/로드 실패는 fail-fast (lang_profile.yaml 에 기재된 파일은 존재해야 함).
        """
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
        except Exception as e:
            logger.error("[LangProfile] stdlib 로드 실패 | lang=%s path=%s | %s: %s", lang, path, type(e).__name__, e, exc_info=True)
            raise RuntimeError(
                f"LangProfile stdlib 로드 실패 (lang={lang}, path={path}): {type(e).__name__}: {e}"
            ) from e
        if not isinstance(data, list):
            raise ValueError(
                f"LangProfile stdlib 형식 오류 (lang={lang}, path={path}): list 기대, {type(data).__name__} 받음"
            )
        result = frozenset(data)
        self._stdlib_cache[lang] = result
        return result

    # -- param style -----------------------------------------------------

    def param_style(self, lang: str) -> str:
        """파라미터 파싱 스타일: 'type_name' | 'name_colon_type' | 'name_type'.

        yaml 에 명시 안 된 lang 은 fail-fast — lang_profile.yaml 가 단일 출처.
        """
        profile = self._get(lang)
        if "param_style" not in profile:
            raise KeyError(f"LangProfile.param_style: '{lang}' 프로필에 param_style 누락")
        return profile["param_style"]

    # -- naming ----------------------------------------------------------

    def naming_convention(self, lang: str) -> str:
        """네이밍 규칙: 'camelCase' | 'snake_case'."""
        profile = self._get(lang)
        if "naming_convention" not in profile:
            raise KeyError(f"LangProfile.naming_convention: '{lang}' 프로필에 naming_convention 누락")
        return profile["naming_convention"]

    # -- display ---------------------------------------------------------

    def display_name(self, lang: str) -> str:
        """표시용 언어 이름: 'Java', 'C', 'Python' 등."""
        profile = self._get(lang)
        if "display_name" not in profile:
            raise KeyError(f"LangProfile.display_name: '{lang}' 프로필에 display_name 누락")
        return profile["display_name"]

    def emoji(self, lang: str) -> str:
        """표시용 이모지."""
        profile = self._get(lang)
        if "emoji" not in profile:
            raise KeyError(f"LangProfile.emoji: '{lang}' 프로필에 emoji 누락")
        return profile["emoji"]

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
