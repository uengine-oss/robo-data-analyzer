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

from agent_tools.path_boundary import PathOutsideRoot, resolve_within
from graph.schema.graph_nodes import CodeNode

# robo-analyzer-refact/skills/ 경로 (실행 시 robo 루트 기준)
SKILLS_DIR = Path(__file__).resolve().parent.parent / "skills"

# 결과 최대 개수 (Plan §4 M2)
_MAX_FUNCTIONS = 50


def make_list_code_nodes(code_nodes: list[CodeNode]):
    """step2 가 파싱한 CodeNode 목록 → ``list_code_nodes`` 도구 생성.

    코드노드 목록은 antlr JSON 을 step2(load_ast)가 이미 구조화한 데이터. 도구는
    파일/JSON 을 안 읽고 그 데이터를 file 별로 걸러줄 뿐.
    """
    by_file: dict[str, list[CodeNode]] = {}
    for fn in code_nodes:
        by_file.setdefault(str(Path(fn.file_path).resolve()), []).append(fn)

    @tool
    def list_code_nodes(file: str) -> str:
        """파일 안 코드노드 / 메서드 목록 — step2 가 파싱한 결과.

        **언제 사용**:
        - 본문보다 토큰 효율 ×5~×10 — 이름 / 시그니처만 알면 충분할 때.
        - 이름 패턴 관찰 (`mpfm_*` / `findBy*` / `xx_validation`).
        - `read_file_range(file, start, end)` 의 라인 범위 산출.

        **인자**: `file` — 소스 파일 경로.

        **결과** (JSON 문자열, 최대 50개):
        `[{"name", "signature", "start_line", "end_line"}, ...]`

        **실패 응답**: `NO_FUNCTIONS` — 그 파일에 코드노드 0개. 대체 도구: `read_file` / `grep`.
        """
        fns = by_file.get(str(Path(file).resolve()))
        if not fns:
            return "NO_FUNCTIONS"
        items = [
            {
                "name": fn.name,
                "signature": fn.signature,
                "start_line": fn.start_line,
                "end_line": fn.end_line,
            }
            for fn in sorted(fns, key=lambda f: f.start_line)[:_MAX_FUNCTIONS]
        ]
        return json.dumps(items, ensure_ascii=False, indent=2)

    return list_code_nodes


@tool
def list_skills() -> str:
    """SKILL 카탈로그 — 프레임워크 / DB 접근 패턴 사전 (`skills/` 스캔).

    **언제 사용**:
    - Scan Agent Phase B: 시그널 수집 후 SKILL 매칭 시도.
    - 각 SKILL.md 의 description 의 트리거 시그널 ↔ 관찰값 비교.

    **결과 형식**:
    - 한 줄당 `<skill_name>: <h1 헤더 1 줄 요약>`.
    - `_templates` 같은 메타 폴더 제외.

    **실패 응답**:
    - `NO_SKILLS_DIR` — skills/ 디렉토리 자체 없음.
    - `NO_SKILLS` — 등록된 SKILL 0개.

    **운영 팁**:
    - description 시그널이 Phase A 관찰값과 **모두** 일치할 때만 매칭 — 부분 일치는 Phase C
      직접 정규식 작성 신호.
    - 후보 여러 개라도 가장 확실한 1개만 `read_skill` 로 본문 가져옴.
    """
    if not SKILLS_DIR.is_dir():
        return "NO_SKILLS_DIR"
    skills: list[str] = []
    for sub in sorted(SKILLS_DIR.iterdir()):
        if not sub.is_dir():
            continue
        if sub.name.startswith("_"):
            continue  # _templates 같은 메타 폴더 제외
        md = sub / "SKILL.md"
        if not md.exists():
            continue
        try:
            first_line = md.read_text(encoding="utf-8").splitlines()[0]
        except (OSError, IndexError):
            first_line = ""
        first_line = first_line.lstrip("# ").strip()
        skills.append(f"{sub.name}: {first_line}")
    return "\n".join(skills) if skills else "NO_SKILLS"


@tool
def read_skill(name: str) -> str:
    """SKILL.md 본문 (자유 형식 마크다운).

    **언제 사용**:
    - `list_skills()` 로 매칭 후보 확정 후 본문 정독.
    - 본문의 table-access 정규식 / 프레임워크 코드노드 목록을 읽어 `ScanResult` 로 정규화.

    **인자**:
    - `name`: `list_skills()` 결과의 skill 이름.

    **결과 형식**: SKILL.md 본문 그대로.

    **실패 응답**:
    - `INVALID_SKILL_NAME: <name>` — 이름에 경로문자(`../` 등) 포함 — skills/ 밖.
    - `SKILL_NOT_FOUND: <name>` — 이름 오탈자 또는 SKILL.md 누락.

    **금지**:
    - 정규식은 본문 그대로 옮겨 적기 — 자기 말로 풀어쓰면 환각. (`verify_table_regex` 로 검증.)
    - 여러 SKILL 동시 read X — 가장 확실한 1개만.
    """
    # 경로탈출 차단 — name 이 '../' 등으로 skills/ 밖을 가리키면 거부.
    try:
        skill_dir = resolve_within(SKILLS_DIR, name)
    except PathOutsideRoot:
        return f"INVALID_SKILL_NAME: {name}"
    md = skill_dir / "SKILL.md"
    if not md.is_file():
        return f"SKILL_NOT_FOUND: {name}"
    return md.read_text(encoding="utf-8")
