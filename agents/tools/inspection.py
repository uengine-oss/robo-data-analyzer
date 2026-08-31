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
        """파일 안 코드노드 / 메서드 목록 — step2 가 파싱한 결과.

        **언제 사용**:
        - 본문보다 토큰 효율 ×5~×10 — 이름 / 시그니처만 알면 충분할 때.
        - 이름 패턴 관찰 (`mpfm_*` / `findBy*` / `xx_validation`).
        - `read_file_range(file, start, end)` 의 라인 범위 산출.

        **인자**: `file` — `glob_search`가 반환한 프로젝트 상대 소스 경로.

        **결과**: page 회계와 `items`를 가진 JSON. `next_offset`이 null이 될 때까지
        이어 읽으면 파일의 모든 routine을 정확히 한 번 볼 수 있다.

        **실패 응답**: `NO_FUNCTIONS` — 그 파일에 코드노드 0개. 대체 도구: `read_file` / `grep`.
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
    """SKILL 카탈로그 — 프레임워크 / DB 접근 패턴 사전 (`agents/skills/` 스캔).

    **언제 사용**:
    - Scan Agent Phase B: 시그널 수집 후 SKILL 매칭 시도.
    - 각 SKILL.md 의 description 의 트리거 시그널 ↔ 관찰값 비교.

    **결과 형식**:
    - 한 줄당 `<skill_name>: <h1 헤더 1 줄 요약>`.
    - `templates` 같은 메타 폴더 제외.

    **실패 응답**:
    - `NO_SKILLS_DIR` — agents/skills/ 디렉토리 자체 없음.
    - `NO_SKILLS` — 등록된 SKILL 0개.

    **운영 팁**:
    - description 시그널이 Phase A 관찰값과 **모두** 일치할 때만 매칭 — 부분 일치는 Phase C
      직접 정규식 작성 신호.
    - 후보 여러 개라도 가장 확실한 1개만 `read_skill` 로 본문 가져옴.
    """
    return build_skill_catalog(FRAMEWORK_SKILLS_DIR)


@tool
def read_skill(name: str) -> str:
    """SKILL.md 본문 (자유 형식 마크다운).

    **언제 사용**:
    - `list_skills()` 로 매칭 후보 확정 후 본문 정독.
    - 본문의 table-access 정규식 / 프레임워크 코드노드 목록을 읽어 `ScanResult` 로 정규화.

    **인자**:
    - `name`: `list_skills()` 결과의 skill 이름.

    **결과 형식**: 의미 계약은 그대로이며 YAML의 Analyzer 내부 `id`만 제거한 본문.

    **실패 응답**:
    - `INVALID_SKILL_NAME: <name>` — 이름에 경로문자(`../` 등) 포함 — agents/skills/ 밖.
    - `SKILL_NOT_FOUND: <name>` — 이름 오탈자 또는 SKILL.md 누락.

    **금지**:
    - 정규식은 본문 그대로 옮겨 적기 — 자기 말로 풀어쓰면 환각. (`verify_table_regex` 로 검증.)
    - 여러 SKILL 동시 read X — 가장 확실한 1개만.
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
