"""tree-sitter 기반 AST 저수준 헬퍼.

확장자 빈도로 주 언어 탐지 → tree-sitter-language-pack 파서로 파싱 →
import/include / 함수 시그니처 / 함수 본문 등을 뽑는다.
언어 미지원 / 파싱 실패 시 raw head fallback 으로 안전.
"""

from __future__ import annotations

import logging
import random
from collections import Counter
from pathlib import Path
from typing import Iterable, Optional

from analyzer.strategy.framework.common.constants import SKIP_DIRS

try:
    from tree_sitter_language_pack import get_parser
    _BACKEND_OK = True
except ImportError:
    _BACKEND_OK = False

logger = logging.getLogger(__name__)


# 확장자 → tree-sitter 언어 이름이 다를 때만 alias
_ALIAS_EXT_TO_LANG = {
    ".py": "python", ".js": "javascript", ".jsx": "javascript",
    ".ts": "typescript", ".tsx": "tsx",
    ".rb": "ruby", ".rs": "rust", ".go": "go",
    ".java": "java", ".kt": "kotlin", ".kts": "kotlin",
    ".c": "c", ".h": "c", ".cpp": "cpp", ".cc": "cpp", ".cxx": "cpp",
    ".hpp": "cpp", ".hh": "cpp", ".cs": "c_sharp",
    ".php": "php", ".swift": "swift", ".scala": "scala",
    ".sh": "bash", ".bash": "bash", ".zsh": "bash",
    ".lua": "lua", ".pl": "perl", ".r": "r", ".m": "objc",
    ".sql": "sql", ".yml": "yaml", ".yaml": "yaml",
    ".json": "json", ".toml": "toml",
}


IMPORT_NODE_TYPES = frozenset({
    "preproc_include",
    "import_declaration", "import_spec",
    "import_statement", "import_from_statement",
    "use_declaration", "use_item", "use_list",
    "import_clause", "import_header",
    "using_directive", "namespace_use_declaration",
    "import_selector", "include_statement",
})


_SIGNATURE_NODE_SUBSTRINGS = (
    "function_definition", "function_declaration", "function_declarator",
    "method_definition", "method_declaration",
    "class_definition", "class_declaration",
    "struct_specifier", "struct_declaration",
    "interface_declaration", "trait_declaration",
    "type_declaration", "type_alias", "type_definition",
    "enum_declaration", "enum_specifier",
    "macro_definition", "preproc_def",
    "decorator",
)


_FUNCTION_NODE_TYPES = frozenset({
    "function_definition", "function_declaration", "method_definition",
    "method_declaration",
})


def _iter_source_files(root: Path) -> Iterable[Path]:
    for p in root.rglob("*"):
        if not p.is_file():
            continue
        if any(part in SKIP_DIRS for part in p.parts):
            continue
        if p.suffix.lower() in _ALIAS_EXT_TO_LANG:
            yield p


def detect_primary_language(root: str) -> Optional[str]:
    counter: Counter = Counter()
    for p in _iter_source_files(Path(root)):
        counter[p.suffix.lower()] += 1
    if not counter:
        return None
    top_ext, _ = counter.most_common(1)[0]
    return _ALIAS_EXT_TO_LANG.get(top_ext)


def collect_source_files(root: str, language: Optional[str] = None) -> list[Path]:
    lang = language or detect_primary_language(root)
    if not lang:
        return []
    exts = [ext for ext, ln in _ALIAS_EXT_TO_LANG.items() if ln == lang]
    return [p for p in _iter_source_files(Path(root)) if p.suffix.lower() in exts]


def language_extensions(language: str) -> list[str]:
    return [ext for ext, ln in _ALIAS_EXT_TO_LANG.items() if ln == language]


def _fallback_head(path: Path, n: int) -> str:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    return "\n".join(text.splitlines()[:n])


def _extract_imports_from_tree(source: bytes, parser) -> list[str]:
    try:
        tree = parser.parse(source)
    except Exception:
        return []
    results: list[str] = []
    stack = [tree.root_node]
    while stack:
        node = stack.pop()
        if node.type in IMPORT_NODE_TYPES:
            txt = source[node.start_byte:node.end_byte].decode("utf-8", errors="ignore")
            line = txt.splitlines()[0].strip() if txt else ""
            if line:
                results.append(line)
        stack.extend(node.children)
    return results


def extract_skeleton(path: Path, max_items: int = 80) -> str:
    """파일에서 뼈대만 뽑아 문자열로 반환.

    포함: 앞쪽 주석 블록, 모든 import/include, 함수/클래스/구조체/타입 선언의 첫 줄,
    매크로 정의, 데코레이터. 파서 없으면 raw 앞 N 줄 fallback.
    """
    if not _BACKEND_OK:
        return _fallback_head(path, 80)
    lang = _ALIAS_EXT_TO_LANG.get(path.suffix.lower())
    if not lang:
        return _fallback_head(path, 80)

    try:
        data = path.read_bytes()
        parser = get_parser(lang)
        tree = parser.parse(data)
    except Exception:
        return _fallback_head(path, 80)

    lines = data.decode("utf-8", errors="ignore").splitlines()
    if not lines:
        return ""

    kept: list[tuple[int, str]] = []
    seen_lines: set[int] = set()

    for i, line in enumerate(lines[:30]):
        stripped = line.strip()
        if not stripped:
            kept.append((i, line))
            seen_lines.add(i)
            continue
        if stripped.startswith(("/*", "*", "*/", "//", "#", '"""', "'''", ";")):
            kept.append((i, line))
            seen_lines.add(i)
            continue
        break

    stack = [tree.root_node]
    while stack:
        node = stack.pop()
        node_type = node.type
        is_import = node_type in IMPORT_NODE_TYPES
        is_signature = any(sub in node_type for sub in _SIGNATURE_NODE_SUBSTRINGS)
        if is_import or is_signature:
            start_line = node.start_point[0]
            if start_line in seen_lines:
                stack.extend(node.children)
                continue
            if is_import:
                end_line = min(node.end_point[0], start_line + 2)
                for ln in range(start_line, end_line + 1):
                    if 0 <= ln < len(lines) and ln not in seen_lines:
                        kept.append((ln, lines[ln]))
                        seen_lines.add(ln)
            else:
                if 0 <= start_line < len(lines):
                    kept.append((start_line, lines[start_line]))
                    seen_lines.add(start_line)
            if not is_signature:
                stack.extend(node.children)
        else:
            stack.extend(node.children)
        if len(kept) >= max_items:
            break

    kept.sort(key=lambda x: x[0])
    if len(kept) > max_items:
        kept = kept[:max_items]
    return "\n".join(line for _, line in kept)


def extract_function_signatures(path: Path, max_sigs: int = 30) -> list[str]:
    """파일의 함수·메서드 시그니처 (첫 3 줄 요약) 리스트."""
    if not _BACKEND_OK:
        return []
    lang = _ALIAS_EXT_TO_LANG.get(path.suffix.lower())
    if not lang:
        return []
    try:
        data = path.read_bytes()
        if len(data) > 5_000_000:
            return []
        parser = get_parser(lang)
        tree = parser.parse(data)
    except Exception:
        return []
    text = data.decode("utf-8", errors="ignore").splitlines()
    sigs: list[str] = []
    seen_starts: set[int] = set()
    stack = [tree.root_node]
    while stack:
        node = stack.pop()
        if node.type in _FUNCTION_NODE_TYPES:
            start = node.start_point[0]
            end = node.end_point[0]
            if start in seen_starts or end - start < 3:
                stack.extend(node.children)
                continue
            seen_starts.add(start)
            window = "\n".join(text[start:min(start + 3, end + 1)])
            window = " ".join(window.split())[:160]
            if window:
                sigs.append(window)
                if len(sigs) >= max_sigs:
                    break
        stack.extend(node.children)
    return sigs


def extract_function_body(
    path: Path,
    function_name: str,
    max_tokens: int = 1500,
) -> tuple[str, int, bool]:
    """파일에서 ``function_name`` 이름의 함수 본문 1 개 추출.

    시그니처 헤더 (최대 첫 5 줄) 에 이름이 들어있는 첫 함수를 가져온다.
    tiktoken 으로 토큰 카운트 → 초과 시 앞부분만 잘라 반환.

    Returns: (body_text, token_count, truncated)
    """
    if not _BACKEND_OK:
        return ("", 0, False)
    lang = _ALIAS_EXT_TO_LANG.get(path.suffix.lower())
    if not lang:
        return ("", 0, False)
    try:
        data = path.read_bytes()
        if len(data) > 5_000_000:
            return ("", 0, False)
        parser = get_parser(lang)
        tree = parser.parse(data)
    except Exception:
        return ("", 0, False)

    lines = data.decode("utf-8", errors="ignore").splitlines()
    target = function_name.strip()
    if not target:
        return ("", 0, False)

    stack = [tree.root_node]
    while stack:
        node = stack.pop()
        if node.type in _FUNCTION_NODE_TYPES:
            start = node.start_point[0]
            end = node.end_point[0]
            header_window = (
                "\n".join(lines[start:min(start + 5, end + 1)])
                if 0 <= start < len(lines) else ""
            )
            if target in header_window:
                body = "\n".join(lines[start:end + 1])
                try:
                    import tiktoken
                    enc = tiktoken.get_encoding("cl100k_base")
                    tokens = enc.encode(body)
                    n_tokens = len(tokens)
                    if n_tokens > max_tokens:
                        truncated = enc.decode(tokens[:max_tokens])
                        return (truncated, max_tokens, True)
                    return (body, n_tokens, False)
                except Exception:
                    fallback = max_tokens // 4
                    if (end - start + 1) > fallback:
                        return ("\n".join(lines[start:start + fallback]), fallback, True)
                    return (body, len(body) // 4, False)
        stack.extend(node.children)
    return ("", 0, False)


def extract_imports_sample(
    root: str,
    sample_size: int = 30,
    top_n: int = 20,
    seed: int = 42,
) -> tuple[dict[str, int], int, Optional[str]]:
    """랜덤 sample_size 개 주 언어 파일 파싱 → import 빈도 top_n.

    Returns: (frequency_dict, sampled_file_count, primary_language)
    """
    if not _BACKEND_OK:
        return {}, 0, None
    lang = detect_primary_language(root)
    if not lang:
        return {}, 0, None
    files = collect_source_files(root, language=lang)
    if not files:
        return {}, 0, lang

    rng = random.Random(seed)
    if len(files) > sample_size:
        files = rng.sample(files, sample_size)

    try:
        parser = get_parser(lang)
    except Exception:
        return {}, 0, lang

    counter: Counter = Counter()
    parsed = 0
    for p in files:
        try:
            data = p.read_bytes()
        except OSError:
            continue
        if len(data) > 512 * 1024:
            continue
        imports = _extract_imports_from_tree(data, parser)
        if imports:
            parsed += 1
            counter.update(imports)
    return dict(counter.most_common(top_n)), parsed, lang
