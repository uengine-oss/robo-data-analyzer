"""code 검사 도구 — 코드 구조 + SKILL 풀 조회.

Profile Agent 가 사용:
- ``list_functions``: 파일 안 함수/메서드 목록 (tree-sitter 파서 사용 — 정확)
- ``list_skills``: 사용 가능한 SKILL 카탈로그
- ``read_skill``: 특정 SKILL.md 본문

``list_functions`` 의 반환 형식은 Plan §4 M2 명세 준수::

    [{"name", "signature", "start_line", "end_line"}, ...]

JSON 문자열로 직렬화해서 LLM 이 ``json.loads()`` 로 파싱 가능.
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

from langchain.tools import tool


# robo-analyzer-refact/skills/ 경로 (실행 시 robo 루트 기준)
SKILLS_DIR = Path(__file__).resolve().parent.parent / "skills"

# 결과 최대 개수 (Plan §4 M2)
_MAX_FUNCTIONS = 50


@tool
def list_functions(file: str) -> str:
    """파일 안 함수 / 메서드 목록 — tree-sitter 파서로 정확 추출.

    **언제 사용**:
    - 본문보다 토큰 효율 ×5~×10 — 함수 이름 / 시그니처만 알면 충분할 때.
    - 함수명 패턴 관찰 (`mpfm_*` / `findBy*` / `xx_validation`).
    - `read_file_range(file, start_line, end_line)` 의 라인 범위 산출.
    - DB 접근 의심 함수 식별 (signature 의 인자 타입 / 반환 타입 단서).

    **인자**:
    - `file`: 파일 경로 (절대 / 상대 모두 가능).

    **결과 형식** (JSON 문자열, 최대 50개):
    ```json
    [
      {"name": "save", "signature": "save(Order order)", "start_line": 42, "end_line": 87},
      ...
    ]
    ```

    **실패 응답**:
    - `NOT_A_FILE: <path>` — 파일 아님.
    - `NO_FUNCTIONS` — tree-sitter 미지원 언어 / 함수 0개 / 파서 오류. 대체 도구: `read_file` 또는 `grep`.

    **운영 팁**:
    - 함수 50개 초과 파일은 앞쪽 50개만 반환 → 더 좁히려면 `grep` 으로 함수 이름 패턴 검색 후 위치 확인.
    - signature 의 인자명 / 타입을 도메인 단서로 활용 (`String entityName` 같은 1번째 인자가
      테이블명 후보 자주).
    """
    p = Path(file)
    if not p.is_file():
        return f"NOT_A_FILE: {file}"

    # 지연 import — 도구 정의 시점에 import 하면 순환 의존 위험.
    from analysis_flow.step2_parse_source.tree_sitter_parser import TreeSitterParser

    parser = TreeSitterParser()
    # tool 은 sync — async parser 를 sync 컨텍스트에서 run
    try:
        result = asyncio.run(parser.parse(p))
    except RuntimeError:
        # 이미 event loop 안 (드물게 발생) — 새 loop 로 처리
        loop = asyncio.new_event_loop()
        try:
            result = loop.run_until_complete(parser.parse(p))
        finally:
            loop.close()

    if not result.functions:
        return "NO_FUNCTIONS"

    items = [
        {
            "name": fn.function_id.rsplit(".", 1)[-1],
            "signature": fn.signature,
            "start_line": fn.start_line,
            "end_line": fn.end_line,
        }
        for fn in result.functions[:_MAX_FUNCTIONS]
    ]
    return json.dumps(items, ensure_ascii=False, indent=2)


@tool
def list_skills() -> str:
    """SKILL 카탈로그 — 프레임워크 / DB 접근 패턴 사전 (`skills/` 스캔).

    **언제 사용**:
    - Profile Agent Phase B: 시그널 수집 후 SKILL 매칭 시도.
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
    """SKILL.md 본문 (yaml 프론트매터 + 마크다운).

    **언제 사용**:
    - `list_skills()` 로 매칭 후보 확정 후 본문 정독.
    - SKILL.md 의 yaml `table_access_patterns` / `ddl_transform` 을 ProfileResult 에 그대로 차용.

    **인자**:
    - `name`: `list_skills()` 결과의 skill 이름.

    **결과 형식**: SKILL.md 본문 그대로 (`---` yaml 헤더 + 마크다운 본문).

    **실패 응답**:
    - `SKILL_NOT_FOUND: <name>` — 이름 오탈자 또는 SKILL.md 누락.

    **금지**:
    - 본문을 자기 말로 풀어쓰기 X — yaml 패턴은 그대로 차용. 풀어쓰면 환각.
    - 여러 SKILL 동시 read X — 가장 확실한 1개만.
    """
    skill_dir = SKILLS_DIR / name
    md = skill_dir / "SKILL.md"
    if not md.is_file():
        return f"SKILL_NOT_FOUND: {name}"
    return md.read_text(encoding="utf-8")
