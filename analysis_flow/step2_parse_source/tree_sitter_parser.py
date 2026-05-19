"""TreeSitterParser — Framework 다중 언어 파서.

``tree_sitter_languages`` 패키지로 각 언어 grammar 자동 로딩.
파일 확장자 → 언어 매핑 → tree-sitter 파싱 → Function / Module 추출.

module_id 결정 위계:
1. 소스 안 ``package`` 선언 (Java/Kotlin/Go) → 정확한 fqn.
2. fallback — 디렉토리 마지막 3 단계.

새 언어 추가 = ``_EXT_TO_LANG`` + ``_FUNCTION_QUERIES`` + ``_MODULE_QUERIES`` 추가.
package 선언 있는 언어는 ``_PACKAGE_QUERIES`` 도 추가하면 정확도 ↑.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from data_models.graph_edges import Uses
from data_models.graph_nodes import Function, Module
from shared.logging_and_stream.logger import log_warning


# 확장자 → tree-sitter 언어 이름
_EXT_TO_LANG: dict[str, str] = {
    "java":  "java",
    "kt":    "kotlin",
    "py":    "python",
    "go":    "go",
    "c":     "c",
    "h":     "c",
    "cpp":   "cpp",
    "hpp":   "cpp",
    "cc":    "cpp",
    "ts":    "typescript",
    "tsx":   "typescript",
    "js":    "javascript",
    "jsx":   "javascript",
    "rb":    "ruby",
    "rs":    "rust",
    "php":   "php",
    "scala": "scala",
}

# 언어별 함수 query (tree-sitter S-expression)
_FUNCTION_QUERIES: dict[str, str] = {
    "java":       "(method_declaration name: (identifier) @name) @func",
    "kotlin":     "(function_declaration (simple_identifier) @name) @func",
    "python":     "(function_definition name: (identifier) @name) @func",
    "go":         "(function_declaration name: (identifier) @name) @func",
    "c":          "(function_definition declarator: (function_declarator declarator: (identifier) @name)) @func",
    "cpp":        "(function_definition declarator: (function_declarator declarator: (identifier) @name)) @func",
    "typescript": "(function_declaration name: (identifier) @name) @func",
    "javascript": "(function_declaration name: (identifier) @name) @func",
    "ruby":       "(method name: (identifier) @name) @func",
    "rust":       "(function_item name: (identifier) @name) @func",
    "php":        "(method_declaration name: (name) @name) @func",
    "scala":      "(function_definition name: (identifier) @name) @func",
}

# 언어별 클래스 / 모듈 query (옵션)
_MODULE_QUERIES: dict[str, str | None] = {
    "java":       "(class_declaration name: (identifier) @name) @mod",
    "kotlin":     "(class_declaration (type_identifier) @name) @mod",
    "python":     "(class_definition name: (identifier) @name) @mod",
    "go":         None,  # Go 는 package 단위 (파일 자체가 컨테이너)
    "c":          None,
    "cpp":        "(class_specifier name: (type_identifier) @name) @mod",
    "typescript": "(class_declaration name: (type_identifier) @name) @mod",
    "javascript": "(class_declaration name: (identifier) @name) @mod",
    "ruby":       "(class name: (constant) @name) @mod",
    "rust":       "(struct_item name: (type_identifier) @name) @mod",
    "php":        "(class_declaration name: (name) @name) @mod",
    "scala":      "(class_definition name: (identifier) @name) @mod",
}

# 언어별 package 선언 query — module_id 정확성 (Java ``package com.foo.bar;`` 등).
# 추출된 노드의 전체 텍스트가 fqn (예: ``"com.example.order"``).
_PACKAGE_QUERIES: dict[str, str] = {
    "java":   "(package_declaration (_) @pkg)",
    "kotlin": "(package_header (_) @pkg)",
    "go":     "(package_clause (package_identifier) @pkg)",
    "scala":  "(package_clause (_) @pkg)",
}

# file-level fallback module 의 식별자 suffix — 클래스 module 과 충돌 회피.
_FILE_MODULE_SUFFIX = "._file"


@dataclass
class ParseResult:
    functions: list[Function] = field(default_factory=list)
    modules: list[Module] = field(default_factory=list)
    parent_of_edges: list = field(default_factory=list)
    uses_edges: list[Uses] = field(default_factory=list)


class TreeSitterParser:
    """tree-sitter 다중 언어 파서."""

    supported_extensions: tuple[str, ...] = tuple(_EXT_TO_LANG.keys())

    async def parse(self, path: Path) -> ParseResult:
        """파일 → Function / Module 노드.

        ``tree_sitter_languages`` 미설치 또는 파싱 실패 시 빈 결과 (다른 파일은 계속).
        """
        ext = path.suffix.lstrip(".").lower()
        lang = _EXT_TO_LANG.get(ext)
        if lang is None:
            return ParseResult()

        try:
            from tree_sitter_languages import get_language, get_parser
        except ImportError as e:
            log_warning(
                "analysis_flow.step2_parse_source.tree_sitter_parser",
                "import_tree_sitter",
                "tree_sitter_languages 미설치 — 모든 소스 파일 건너뜀. pip install tree-sitter-languages",
                error=str(e),
            )
            return ParseResult()

        try:
            parser = get_parser(lang)
            language = get_language(lang)
            source = path.read_bytes()
            tree = parser.parse(source)
        except Exception as e:
            log_warning(
                "analysis_flow.step2_parse_source.tree_sitter_parser",
                "parse_file",
                "파일 파싱 실패 — 이 파일 건너뜀",
                path=str(path),
                lang=lang,
                error=f"{type(e).__name__}: {e}",
            )
            return ParseResult()

        result = ParseResult()
        file_module_id = _resolve_file_module_id(path, lang, language, tree, source)

        # 함수 추출
        fn_query_str = _FUNCTION_QUERIES.get(lang)
        if fn_query_str:
            try:
                query = language.query(fn_query_str)
                captures = query.captures(tree.root_node)
                for node, capture_name in captures:
                    if capture_name != "func":
                        continue
                    name = _extract_name(node, source)
                    if not name:
                        continue
                    start = node.start_point[0] + 1
                    end = node.end_point[0] + 1
                    fqn = f"{file_module_id}.{name}"
                    result.functions.append(Function(
                        function_id=fqn,
                        signature=_extract_signature(node, source) or name,
                        file_path=str(path),
                        start_line=start,
                        end_line=end,
                    ))
            except Exception as e:
                log_warning(
                    "analysis_flow.step2_parse_source.tree_sitter_parser",
                    "function_query",
                    "함수 query 실패 — 함수 추출 X (모듈 추출은 계속)",
                    path=str(path),
                    lang=lang,
                    error=f"{type(e).__name__}: {e}",
                )

        # 모듈 / 클래스 추출 (정확한 라인 범위 포함)
        mod_query_str = _MODULE_QUERIES.get(lang)
        if mod_query_str:
            try:
                query = language.query(mod_query_str)
                captures = query.captures(tree.root_node)
                for node, capture_name in captures:
                    if capture_name != "mod":
                        continue
                    name = _extract_name(node, source)
                    if not name:
                        continue
                    result.modules.append(Module(
                        module_id=f"{file_module_id}.{name}",
                        name=name,
                        file_path=str(path),
                        start_line=node.start_point[0] + 1,
                        end_line=node.end_point[0] + 1,
                    ))
            except Exception as e:
                log_warning(
                    "analysis_flow.step2_parse_source.tree_sitter_parser",
                    "module_query",
                    "모듈 query 실패 — file-level Module 만 등록",
                    path=str(path),
                    lang=lang,
                    error=f"{type(e).__name__}: {e}",
                )

        # 파일 자체도 Module (file-level functions 의 컨테이너 — start=0, end=0).
        # 클래스 module 과 충돌 회피하려고 ``._file`` suffix.
        result.modules.append(Module(
            module_id=file_module_id + _FILE_MODULE_SUFFIX,
            name=path.stem,
            file_path=str(path),
        ))
        return result


def _resolve_file_module_id(
    path: Path,
    lang: str,
    language,
    tree,
    source: bytes,
) -> str:
    """소스 안 package 선언 → fqn, 없으면 디렉토리 fallback.

    Java/Kotlin/Go/Scala 는 ``package`` 선언이 진실. Python 등은 디렉토리 기반.

    실패 / 미지원 시 디렉토리 마지막 3 단계 (depth 한도).
    """
    package_query = _PACKAGE_QUERIES.get(lang)
    if package_query:
        try:
            q = language.query(package_query)
            captures = q.captures(tree.root_node)
            for node, capture_name in captures:
                if capture_name != "pkg":
                    continue
                pkg_text = source[node.start_byte : node.end_byte].decode(
                    "utf-8", errors="ignore",
                ).strip().rstrip(";").strip()
                if pkg_text:
                    return f"{pkg_text}.{path.stem}"
        except Exception as e:
            log_warning(
                "analysis_flow.step2_parse_source.tree_sitter_parser",
                "package_query",
                "package 선언 추출 실패 — 디렉토리 기반 fallback",
                path=str(path),
                lang=lang,
                error=f"{type(e).__name__}: {e}",
            )
    # fallback: 디렉토리 마지막 3 단계.
    # Python 은 디렉토리가 곧 패키지라 자연스럽게 맞음.
    return ".".join(path.with_suffix("").parts[-3:])


def _extract_name(node, source: bytes) -> str:
    """노드 내부에서 식별자 추출 (재귀)."""
    for child in node.children:
        if child.type in ("identifier", "type_identifier", "simple_identifier", "name", "constant"):
            return source[child.start_byte : child.end_byte].decode("utf-8", errors="ignore")
        if child.type in ("function_declarator", "method_declarator", "init_declarator"):
            inner = _extract_name(child, source)
            if inner:
                return inner
    return ""


def _extract_signature(node, source: bytes) -> str:
    """시그니처 — 첫 줄 (이름 + 파라미터) 최대 200자."""
    text = source[node.start_byte : node.end_byte].decode("utf-8", errors="ignore")
    first_line = text.splitlines()[0] if text else ""
    return first_line.strip()[:200]
