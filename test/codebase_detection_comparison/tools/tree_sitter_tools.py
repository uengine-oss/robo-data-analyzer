"""tree-sitter 기반 import/include 샘플링 추출.

확장자 빈도로 주 언어 결정 → tree-sitter-language-pack 으로 파싱 →
AST 에서 공통 import 노드만 뽑아 빈도 집계. 전수 조사 대신 랜덤 샘플링.
"""
from __future__ import annotations

import random
from collections import Counter
from pathlib import Path
from typing import Iterable, Optional

try:
    from tree_sitter_language_pack import get_parser
    _BACKEND_OK = True
except ImportError:
    _BACKEND_OK = False

SKIP_DIRS = frozenset({
    ".git", "node_modules", "__pycache__", "vendor", "target", "build",
    "dist", "bin", "obj", "venv", ".venv", ".idea", ".vscode", "logs",
    ".mypy_cache", ".pytest_cache", ".tox", "out",
})

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

# 함수/클래스/구조체/타입 선언 노드 — 범용 매칭을 위해 substring 검사 사용
SIGNATURE_NODE_SUBSTRINGS = (
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


def _iter_source_files(root: Path) -> Iterable[Path]:
    for p in root.rglob("*"):
        if not p.is_file():
            continue
        if any(part in SKIP_DIRS for part in p.parts):
            continue
        if p.suffix.lower() in _ALIAS_EXT_TO_LANG:
            yield p


def detect_primary_language(root: str) -> Optional[str]:
    """확장자 빈도 top1 을 tree-sitter 언어명으로 매핑."""
    counter: Counter = Counter()
    for p in _iter_source_files(Path(root)):
        counter[p.suffix.lower()] += 1
    if not counter:
        return None
    top_ext, _ = counter.most_common(1)[0]
    return _ALIAS_EXT_TO_LANG.get(top_ext)


def collect_source_files(root: str, language: Optional[str] = None) -> list[Path]:
    """주 언어 파일 목록. language 미지정 시 자동 탐지."""
    lang = language or detect_primary_language(root)
    if not lang:
        return []
    exts = [ext for ext, ln in _ALIAS_EXT_TO_LANG.items() if ln == lang]
    out: list[Path] = []
    for p in _iter_source_files(Path(root)):
        if p.suffix.lower() in exts:
            out.append(p)
    return out


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
    """파일에서 뼈대만 추출 — 임의 줄 자르기 대신 의미 단위.

    포함:
      - 파일 앞쪽 주석/라이선스 블록 (첫 코드 라인 전까지 최대 30 줄)
      - 모든 import/include 문
      - 함수/클래스/구조체/타입 선언의 **첫 줄** (본문 제외)
      - 매크로 정의 / 데코레이터

    어떤 언어든 tree-sitter 가 파싱 가능하면 동작. max_items 줄 초과 시 절단.
    실패/미지원 언어 → 파일 앞 80 줄 fallback.
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

    # 1) 앞쪽 주석/헤더 블록 — 첫 "의미 있는" 코드가 나올 때까지 최대 30 줄
    for i, line in enumerate(lines[:30]):
        stripped = line.strip()
        if not stripped:
            kept.append((i, line))
            seen_lines.add(i)
            continue
        # 주석으로 보이는 줄은 유지
        if stripped.startswith(("/*", "*", "*/", "//", "#", '"""', "'''", ";")):
            kept.append((i, line))
            seen_lines.add(i)
            continue
        break

    # 2) AST 에서 import + signature 노드 추출
    stack = [tree.root_node]
    while stack:
        node = stack.pop()
        node_type = node.type

        is_import = node_type in IMPORT_NODE_TYPES
        is_signature = any(sub in node_type for sub in SIGNATURE_NODE_SUBSTRINGS)

        if is_import or is_signature:
            start_line = node.start_point[0]
            if start_line in seen_lines:
                stack.extend(node.children)
                continue

            if is_import:
                # import 는 전체 (보통 한 줄)
                end_line = min(node.end_point[0], start_line + 2)
                for ln in range(start_line, end_line + 1):
                    if 0 <= ln < len(lines) and ln not in seen_lines:
                        kept.append((ln, lines[ln]))
                        seen_lines.add(ln)
            else:
                # signature 는 첫 줄만 (본문 제외)
                if 0 <= start_line < len(lines):
                    kept.append((start_line, lines[start_line]))
                    seen_lines.add(start_line)
            # signature 본문은 파고들지 않음 (import 는 자식 없음)
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


def _fallback_head(path: Path, n: int) -> str:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    return "\n".join(text.splitlines()[:n])


def extract_imports_sample(
    root: str,
    sample_size: int = 30,
    top_n: int = 20,
    seed: int = 42,
) -> tuple[dict[str, int], int, Optional[str]]:
    """랜덤 sample_size 파일만 파싱 → import 빈도 top_n.

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

    top = dict(counter.most_common(top_n))
    return top, parsed, lang
