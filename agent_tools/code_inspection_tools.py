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
    """파일 안 함수/메서드 목록 — tree-sitter 파서 사용 (정확).

    Plan §4 M2 명세: ``[{name, signature, start_line, end_line}]`` JSON 반환.
    최대 50 개 (한도 초과 시 앞쪽 50 개만).

    tree-sitter 미지원 언어 / 미설치 환경 = ``NO_FUNCTIONS`` 또는 빈 list.
    Profile Agent 가 다른 도구 (``read_file_range`` / ``grep``) 로 대체 가능.

    Args:
        file: 파일 경로.

    Returns:
        JSON 문자열 — ``[{"name": str, "signature": str, "start_line": int, "end_line": int}]``.
        파일 없음 = ``NOT_A_FILE: <path>``, 함수 없음 = ``NO_FUNCTIONS``.
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
    """사용 가능한 SKILL 카탈로그 (``skills/`` 디렉토리 스캔).

    각 SKILL.md 의 1줄 요약 (h1 헤더) 표시.
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
    """SKILL.md 본문 (yaml + 마크다운 전체)."""
    skill_dir = SKILLS_DIR / name
    md = skill_dir / "SKILL.md"
    if not md.is_file():
        return f"SKILL_NOT_FOUND: {name}"
    return md.read_text(encoding="utf-8")
