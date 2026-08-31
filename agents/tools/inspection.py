"""code 검사 도구 — 코드 구조 + SKILL 풀 조회.

Scan Agent 가 사용:
- ``make_list_code_nodes``: 파일별 코드노드/메서드 목록 도구 생성 (step2 가 파싱한 결과 기반)
- ``list_skills``: 사용 가능한 SKILL 카탈로그
- ``read_skill``: 특정 SKILL.md 본문

SKILL 은 자유 형식 문서다 — 코드가 파싱하지 않는다. 에이전트가 ``read_skill`` 로 읽고
``ScanResult`` 로 정규화하고, 코드는 ``verify_table_patterns`` 로 검증만 한다.

``list_code_nodes`` 의 반환 형식은 Plan §4 M2 명세 준수::

    [{"name", "signature", "start_line", "end_line"}, ...]

JSON 문자열로 직렬화해서 LLM 이 ``json.loads()`` 로 파싱 가능.
"""
from __future__ import annotations

import json
from pathlib import Path

from langchain.tools import tool

from shared.files.paths import PathOutsideRoot, resolve_within
from shared.files.run_cache_context import read_run_text
from graph.schema.graph_nodes import CodeNode
from pipeline.languages import statement_labels
from agents.skills.catalog import (
    FRAMEWORK_SKILLS_DIR,
    SkillCatalogError,
    build_skill_catalog,
    model_visible_skill_document,
)

# agents/skills/ 경로

_FUNCTION_PAGE_SIZE = 50


def make_list_code_nodes(
    code_nodes: list[CodeNode], *, project_root: Path | None = None,
):
    """step2 가 파싱한 CodeNode 목록 → ``list_code_nodes`` 도구 생성.

    코드노드 목록은 antlr JSON 을 step2(load_ast)가 이미 구조화한 데이터. 도구는
    파일/JSON 을 안 읽고 그 데이터를 file 별로 걸러줄 뿐.
    """
    root = project_root.resolve() if project_root is not None else None
    by_file: dict[str, list[CodeNode]] = {}
    stmt_labels = statement_labels()
    for fn in code_nodes:
        # statement 노드(RETURN/ASSIGNMENT/GOTO 등)는 목록 대상이 아니다 — 대형
        # statement가 routine 페이지를 밀어내지 않도록 routine만 보존한다.
        if fn.label in stmt_labels:
            continue
        raw_path = Path(fn.file_path)
        resolved = (
            (root / raw_path).resolve()
            if root is not None and not raw_path.is_absolute()
            else raw_path.resolve()
        )
        by_file.setdefault(str(resolved), []).append(fn)
        if root is not None:
            try:
                relative = resolved.relative_to(root).as_posix()
            except ValueError:
                continue
            by_file.setdefault(relative, []).append(fn)

    @tool
    def list_code_nodes(file: str, offset: int = 0) -> str:
        """Parser가 만든 파일별 routine 목록을 원문 재파싱 없이 반환한다.

        프로젝트 상대 `file`의 `name|signature|start_line|end_line` JSON page다.
        `next_offset`이 null이 될 때까지 읽으면 모든 routine을 한 번씩 본다.
        """
        lookup_key = Path(file).as_posix()
        fns = by_file.get(lookup_key)
        if fns is None:
            fns = by_file.get(str(Path(file).resolve()))
        if not fns:
            return "NO_FUNCTIONS"
        if offset < 0:
            return "INVALID_OFFSET: offset must be non-negative"
        ordered = sorted(fns, key=lambda f: f.start_line)
        page = ordered[offset:offset + _FUNCTION_PAGE_SIZE]
        items = [
            {
                "name": fn.name,
                "signature": fn.signature,
                "start_line": fn.start_line,
                "end_line": fn.end_line,
            }
            for fn in page
        ]
        next_offset = offset + len(items) if offset + len(items) < len(ordered) else None
        return json.dumps({
            "total": len(ordered),
            "next_offset": next_offset,
            "items": items,
        }, ensure_ascii=False, indent=2)

    return list_code_nodes


@tool
def list_skills() -> str:
    """등록된 framework/DB SKILL 이름과 한 줄 설명을 나열한다.

    관찰한 코드 신호가 모두 맞는 가장 가까운 후보 하나만 `read_skill`로 확인한다.
    """
    return build_skill_catalog(FRAMEWORK_SKILLS_DIR)


@tool
def read_skill(name: str) -> str:
    """선택한 SKILL의 의미 계약을 내부 rule ID만 제거한 원문으로 반환한다.

    `list_skills`의 정확한 이름 하나만 허용하며 경로 이탈을 거부한다. 계약 정규식은
    바꿔 쓰지 말고 그대로 사용한 뒤 `verify_table_regex`로 검증한다.
    """
    # 경로탈출 차단 — name 이 '../' 등으로 agents/skills/ 밖을 가리키면 거부.
    try:
        skill_dir = resolve_within(FRAMEWORK_SKILLS_DIR, name)
    except PathOutsideRoot:
        return f"INVALID_SKILL_NAME: {name}"
    md = skill_dir / "SKILL.md"
    if not md.is_file():
        return f"SKILL_NOT_FOUND: {name}"
    return model_visible_skill_document(read_run_text(md, encoding="utf-8"))
