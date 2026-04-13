"""방법 B (Deep Agent): 9개 도구 + 4-Phase 파이프라인.

[bl_search_comparison/semantic_search.py] 의 구조를 1:1 복제하되, 도메인을 변경:
- BL 검색 → 기술스택 탐지
- 5개 도구 → 9개 도구
- Neo4j 드라이버 → file_tools + NegativeMatcher + SkillLoader
"""

from __future__ import annotations

import asyncio
import json
import re
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml
from langchain_core.tools import tool

from client.llm_client import get_llm
from config.settings import settings
from test.codebase_detection_comparison.dynamic_discovery import (
    characterize_via_llm,
    collect_code_signals_for_prefix,
    save_learned_skill,
)
from test.codebase_detection_comparison.file_tools import (
    count_extensions,
    extract_imports,
    find_marker_files,
    grep_pattern,
    read_file,
)
from test.codebase_detection_comparison.negative_matcher import NegativeMatcher
from test.codebase_detection_comparison.rule_validator import RuleValidator
from test.codebase_detection_comparison.skill_loader import (
    Skill,
    discover_skills,
    get_skill_for_language,
)


MODULE_ROOT = Path(__file__).parent


@dataclass
class ToolCallLog:
    tool_name: str
    args: str
    result_preview: str


@dataclass
class DeepDetectResult:
    primary_language: str = ""
    languages: dict[str, int] = field(default_factory=dict)
    build_systems: list[str] = field(default_factory=list)
    frameworks: list[dict] = field(default_factory=list)
    orms: list[dict] = field(default_factory=list)
    databases: list[dict] = field(default_factory=list)
    proprietary_frameworks: list[dict] = field(default_factory=list)
    top_imports: dict[str, int] = field(default_factory=dict)
    skills_loaded: list[str] = field(default_factory=list)
    tool_calls: list[ToolCallLog] = field(default_factory=list)
    total_tokens: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    elapsed_sec: float = 0.0
    error: str | None = None


def _load_system_prompt() -> str:
    yaml_path = MODULE_ROOT / "stack_detection.yaml"
    data = yaml.safe_load(yaml_path.read_text(encoding="utf-8"))
    return data["system_prompt"]


def build_detection_tools(
    root_path: Path,
    skills_by_language: dict[str, Skill],
    negative_matcher: NegativeMatcher,
    collector: dict[str, Any],
    lock: threading.Lock,
    api_key: str = "",
):
    """DeepAgent 에게 건네줄 12개 도구 생성.

    Phase 1~4 기본 9개 + Phase 3.5 동적 발견 3개.
    """

    # ── Phase 1: Discovery ──
    @tool
    def count_file_extensions() -> str:
        """루트 아래 확장자별 파일 수를 반환합니다. 반드시 첫 도구로 호출하세요.

        결과는 빈도 내림차순 JSON. 예: {".py": 120, ".md": 30}.
        ★ .c/.h 많으면 C, .py 많으면 Python, .ec/.pc 보이면 Pro*C/ProFrame 의심."""
        counts = count_extensions(root_path)
        return json.dumps(counts, ensure_ascii=False)

    @tool
    def find_marker_files_tool() -> str:
        """빌드/의존성 마커 파일을 탐색합니다 (package.json, pom.xml, requirements.txt,
        Makefile, go.mod, Cargo.toml 등). 발견된 상대경로 리스트 JSON 반환."""
        markers = [
            "package.json", "pom.xml", "build.gradle", "build.gradle.kts",
            "requirements.txt", "pyproject.toml", "Pipfile", "setup.py",
            "go.mod", "Cargo.toml", "Gemfile", "composer.json",
            "Makefile", "makefile", "CMakeLists.txt",
            "Dockerfile", "docker-compose.yml", "docker-compose.yaml",
            "schema.prisma", "tsconfig.json",
        ]
        found = find_marker_files(root_path, markers)
        return json.dumps(found, ensure_ascii=False)

    # ── Phase 2: Dependency + Config ──
    @tool
    def read_manifest(relative_path: str) -> str:
        """의존성 매니페스트 파일의 앞 200줄을 반환합니다.
        (pom.xml, package.json, requirements.txt, go.mod, Cargo.toml 등)
        ★ 매니페스트 한 파일에 기술 스택의 80%가 들어있습니다."""
        try:
            content = read_file(root_path / relative_path, limit=200)
        except (IOError, OSError) as err:
            return json.dumps({"error": str(err)})
        return content

    @tool
    def read_config_file(relative_path: str) -> str:
        """설정 파일의 앞 100줄을 반환합니다.
        (application.yml, .env, schema.prisma, docker-compose.yml, settings.py, Makefile 등)
        ★ jdbc:postgresql://, neo4j://, DATABASE_URL 같은 DB 연결문자열을 찾으세요."""
        try:
            content = read_file(root_path / relative_path, limit=100)
        except (IOError, OSError) as err:
            return json.dumps({"error": str(err)})
        return content

    # ── Phase 3: Pattern Detection ──
    @tool
    def grep_imports(extensions: str) -> str:
        """소스 파일들의 import/include 행을 추출해 빈도 JSON 을 반환합니다.

        extensions: 콤마 구분 확장자 리스트. 예: '.py,.pyi' 또는 '.c,.h'.
        ★ 결과를 그대로 detect_proprietary 에 넣으세요."""
        ext_list = [e.strip() for e in extensions.split(",") if e.strip()]
        freq = extract_imports(root_path, ext_list)
        # 결과가 너무 크면 상위 50개로 절삭.
        top = dict(list(freq.items())[:50])
        with lock:
            collector["last_import_frequency"] = freq
        return json.dumps(top, ensure_ascii=False)

    @tool
    def grep_pattern_tool(pattern: str, extensions: str, max_hits: int = 20) -> str:
        """소스 파일들에서 정규식 패턴 매칭. 히트 목록 JSON 반환 ({file,line,text}).

        ★ 'EXEC SQL', '@Entity', 'mpfmdbio\\(', 'FastAPI\\(' 같은 API 호출 확인용."""
        ext_list = [e.strip() for e in extensions.split(",") if e.strip()]
        hits = grep_pattern(root_path, pattern, ext_list, max_hits=max_hits)
        return json.dumps(hits, ensure_ascii=False)

    @tool
    def load_skill(language: str) -> str:
        """주 언어 탐지 후 해당 언어의 **탐색 매뉴얼** 을 받습니다.

        language: 'Python' | 'Java' | 'Kotlin' | 'C' | 'Pro*C' | 'TypeScript' | 'Go' | 'unknown'
        반환: config_patterns, import_regex, SKILL.md 매뉴얼.
        ★ 이건 정답지가 아니라 '어디를 먼저 보라'는 가이드. 실제 탐지는 직접 증거 수집으로."""
        skill = get_skill_for_language(skills_by_language, language)
        if skill is None:
            return json.dumps({"error": f"skill not found for language '{language}'"})

        with lock:
            collector.setdefault("skills_loaded", [])
            if skill.name not in collector["skills_loaded"]:
                collector["skills_loaded"].append(skill.name)

        return json.dumps(
            {
                "skill_name": skill.name,
                "language_names": skill.language_names,
                "source_extensions": skill.source_extensions,
                "config_patterns": skill.config_patterns,
                "import_regex": skill.import_regex,
                "exploration_manual": skill.skill_md[:4000],
            },
            ensure_ascii=False,
        )

    @tool
    def detect_proprietary(import_frequency_json: str) -> str:
        """네거티브 매칭. 표준 라이브러리에 매칭 안 되는 반복 접두어를
        독점/커스텀 프레임워크 후보로 반환합니다.

        import_frequency_json: grep_imports 의 JSON 결과. 예: '{"fastapi": 12, ...}'.
        ★ ProFrame 같은 독점 프레임워크는 오직 이 도구로만 잡힙니다."""
        try:
            freq = json.loads(import_frequency_json)
        except json.JSONDecodeError:
            freq = {}

        # collector 에 저장된 전체 빈도 우선 사용 (grep_imports 절삭 방지).
        full = collector.get("last_import_frequency", freq)
        candidates = negative_matcher.detect_unknown_namespaces(full)
        return json.dumps(candidates, ensure_ascii=False)

    # ── Phase 3.5: Dynamic Discovery (호기심 기반 탐색) ──
    @tool
    def read_file_range(relative_path: str, start_line: int = 1, end_line: int = 120) -> str:
        """파일의 특정 줄 범위를 읽습니다. 관심 있는 함수·구조체·매크로의 정의부나
        사용처를 좁혀서 확인할 때 사용합니다.

        예: read_file_range('zapamcom10060.c', 700, 750)
        ★ 파일 전체가 아니라 **필요한 부분만** 읽어 토큰 절약."""
        full_path = root_path / relative_path.lstrip("/").lstrip("\\")
        if not full_path.exists():
            # 부분 경로일 수도 있으니 glob 으로 찾아봄.
            from pathlib import Path as _P
            candidates = list(root_path.rglob(_P(relative_path).name))
            if not candidates:
                return json.dumps({"error": f"file not found: {relative_path}"})
            full_path = candidates[0]
        try:
            with full_path.open("r", encoding="utf-8", errors="ignore") as f:
                lines = f.readlines()
        except (IOError, OSError) as err:
            return json.dumps({"error": str(err)})
        start = max(0, start_line - 1)
        end = min(len(lines), end_line)
        snippet = "".join(
            f"{i+1:5d} | {line}" for i, line in enumerate(lines[start:end], start=start)
        )
        return snippet or "(empty range)"

    @tool
    def find_definition(symbol: str, extensions: str = ".c,.h,.py,.java,.ts,.go") -> str:
        """함수/구조체/매크로의 정의부(선언 위치)를 찾습니다.

        예: find_definition('mpfmdbio', '.c,.h')
            → [{file, line, text}] 반환
        ★ 궁금한 이름을 봤을 때 '이건 어디서 정의됐지?' 를 확인하는 도구."""
        ext_list = [e.strip() for e in extensions.split(",") if e.strip()]
        # 정의부 후보 정규식: 함수 정의 / typedef / #define / class / def
        patterns = [
            rf"(?:^|\n)\s*(?:static\s+)?[\w\*]+\s+{re.escape(symbol)}\s*\(",  # C 함수 정의
            rf"(?:^|\n)\s*typedef\s+[^;]+\b{re.escape(symbol)}\s*;",           # typedef
            rf"(?:^|\n)\s*struct\s+{re.escape(symbol)}\s*\{{",                 # struct
            rf"(?:^|\n)\s*#define\s+{re.escape(symbol)}\b",                    # define
            rf"(?:^|\n)\s*class\s+{re.escape(symbol)}\b",                      # class
            rf"(?:^|\n)\s*def\s+{re.escape(symbol)}\s*\(",                      # python
        ]
        combined = "|".join(f"({p})" for p in patterns)
        hits = grep_pattern(root_path, combined, ext_list, max_hits=10)
        return json.dumps(hits, ensure_ascii=False)

    @tool
    def collect_unknown_evidence(prefix: str) -> str:
        """미지의 접두어(prefix)로 시작하는 헤더/소스 파일 5개를 샘플링해,
        각 파일에서 14가지 힌트(주석·typedef·함수명·매크로·API·도메인어·자연어 등)를
        구조화 JSON 으로 반환합니다. 사전 규칙 없이 실제 코드만 봅니다.

        ★ detect_proprietary 로 나온 prefix 중 Skill 에 없던 것에 대해 호출하세요."""
        evidence = collect_code_signals_for_prefix(
            root_path, prefix, count=5
        )
        with lock:
            collector.setdefault("dynamic_evidence", {})[prefix] = evidence
        # 요약만 반환 (전체는 collector 에 저장).
        return json.dumps(
            {
                "prefix": prefix,
                "sampled_files": evidence["sampled_files"],
                "summary": evidence["aggregate_summary"],
            },
            ensure_ascii=False,
        )

    @tool
    def characterize_unknown_library(prefix: str) -> str:
        """collect_unknown_evidence 로 모은 증거를 별도 LLM 에 제출해
        라이브러리 역할·아키텍처·벤더·추정명을 추론합니다.

        반환: {library_role, architecture_clues, db_access_style,
               likely_region, likely_vendor, canonical_name_guess,
               confidence, reasoning, evidence_used}
        ★ 사전 지식 없이 증거만으로 '이건 뭐다' 라는 가설을 생성합니다."""
        evidence = collector.get("dynamic_evidence", {}).get(prefix)
        if not evidence:
            return json.dumps(
                {"error": "증거 없음. collect_unknown_evidence 를 먼저 호출하세요."}
            )
        characterization = characterize_via_llm(evidence, api_key=api_key)
        with lock:
            collector.setdefault("dynamic_characterizations", {})[prefix] = characterization
        return json.dumps(characterization, ensure_ascii=False)

    @tool
    def save_learned_skill_tool(prefix: str) -> str:
        """characterize_unknown_library 결과를 skills/learned/<prefix>_<date>.json
        으로 저장합니다. 다음 실행부터 자동 로드되어 $0 매치.

        confidence ≥ 0.6 인 경우에만 호출 권장."""
        evidence = collector.get("dynamic_evidence", {}).get(prefix)
        characterization = collector.get("dynamic_characterizations", {}).get(prefix)
        if not evidence or not characterization:
            return json.dumps(
                {"error": "증거 또는 특성 분석 없음. 앞선 도구들을 먼저 호출."}
            )
        if characterization.get("error"):
            return json.dumps({"error": "특성 분석 실패: " + str(characterization)})
        try:
            out_path = save_learned_skill(
                prefix, characterization, evidence, MODULE_ROOT / "skills" / "learned"
            )
        except Exception as exc:  # noqa: BLE001
            return json.dumps({"error": f"저장 실패: {exc}"})
        with lock:
            collector.setdefault("learned_skills_saved", []).append(out_path)
        return json.dumps({"saved": out_path})

    # ── Phase 4: Synthesis ──
    @tool
    def submit_detection(result_json: str) -> str:
        """최종 탐지 결과를 제출합니다. 반드시 마지막에 한 번 호출하세요.

        형식:
        {
          "primary_language": "...",
          "frameworks": [{"name":"...","confidence":0.95,"evidence":"..."}],
          "orms": [...],
          "databases": [...],
          "proprietary_frameworks": [{"prefix":"pfm","reason":"..."}]
        }"""
        try:
            data = json.loads(result_json)
        except json.JSONDecodeError as err:
            return f"JSON 파싱 실패: {err}. 올바른 JSON 을 전달하세요."
        with lock:
            collector["submission"] = data
        return f"제출 완료: {len(data.get('frameworks', []))} frameworks, {len(data.get('proprietary_frameworks', []))} proprietary"

    return [
        count_file_extensions,
        find_marker_files_tool,
        read_manifest,
        read_config_file,
        grep_imports,
        grep_pattern_tool,
        load_skill,
        detect_proprietary,
        submit_detection,
        read_file_range,
        find_definition,
        collect_unknown_evidence,
        characterize_unknown_library,
        save_learned_skill_tool,
    ]


async def deep_detect(root: str | Path, api_key: str = "", logger=None) -> DeepDetectResult:
    """방법 B 실행. DeepAgent 가 9개 도구로 4-Phase 수행."""

    from deepagents import create_deep_agent

    root_path = Path(root).resolve()
    start = time.perf_counter()
    api_key = api_key or settings.llm.api_key

    if logger:
        logger.log_phase("DEEP", "방법 B 시작 (DeepAgent + 9 tools + 4-Phase)")

    # 모듈 초기화: schemas → validator → skills → negative_matcher.
    try:
        validator = RuleValidator(MODULE_ROOT / "schemas")
        skills_by_language = discover_skills(MODULE_ROOT / "skills", validator)
        negative_matcher = NegativeMatcher(MODULE_ROOT / "rules")
        negative_matcher.augment_with_local_packages(root_path)
        if logger:
            logger.log("DEEP", f"초기화 완료: {len(skills_by_language)} 언어 매핑, "
                       f"{len(negative_matcher.known_prefixes)} 표준 prefix")
    except Exception as exc:  # noqa: BLE001
        if logger:
            logger.log("DEEP", f"초기화 실패: {exc}")
        return DeepDetectResult(
            error=f"Initialization failed: {exc}",
            elapsed_sec=time.perf_counter() - start,
        )

    collector: dict[str, Any] = {}
    lock = threading.Lock()
    tools = build_detection_tools(
        root_path, skills_by_language, negative_matcher, collector, lock, api_key=api_key
    )

    system_prompt = _load_system_prompt()
    llm = get_llm(api_key=api_key)
    agent = create_deep_agent(model=llm, tools=tools, system_prompt=system_prompt)

    user_message = (
        f"다음 코드베이스의 기술 스택(언어/프레임워크/ORM/DB/독점프레임워크)을 모두 탐지하라.\n"
        f"프로젝트 루트: {root_path}\n"
        f"반드시 4-Phase 절차를 따르고 마지막에 submit_detection 을 호출해 결과를 제출할 것."
    )

    try:
        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(
            None,
            lambda: agent.invoke({"messages": [{"role": "user", "content": user_message}]}),
        )
    except Exception as exc:  # noqa: BLE001
        return DeepDetectResult(
            error=f"Agent invoke failed: {exc}",
            elapsed_sec=time.perf_counter() - start,
        )

    # 토큰 + tool_calls 추출 (bl semantic_search.py 229-253 패턴).
    total_tokens = 0
    prompt_tokens = 0
    completion_tokens = 0
    tool_calls: list[ToolCallLog] = []

    for msg in result.get("messages", []):
        usage = getattr(msg, "usage_metadata", None)
        if usage:
            total_tokens += usage.get("total_tokens", 0)
            prompt_tokens += usage.get("input_tokens", 0)
            completion_tokens += usage.get("output_tokens", 0)

        ai_tool_calls = getattr(msg, "tool_calls", None)
        if ai_tool_calls:
            for tc in ai_tool_calls:
                tool_calls.append(
                    ToolCallLog(
                        tool_name=tc.get("name", ""),
                        args=json.dumps(tc.get("args", {}), ensure_ascii=False)[:200],
                        result_preview="",
                    )
                )

        msg_type = getattr(msg, "type", "")
        if msg_type == "tool":
            tool_name = getattr(msg, "name", "")
            content = getattr(msg, "content", "") or ""
            for tc_log in reversed(tool_calls):
                if tc_log.tool_name == tool_name and not tc_log.result_preview:
                    tc_log.result_preview = str(content)[:150]
                    break

    # 로거에 도구 호출 시퀀스 기록.
    if logger:
        logger.log("DEEP", f"DeepAgent 종료 (tokens={total_tokens}, tool_calls={len(tool_calls)})")
        for i, tc in enumerate(tool_calls, 1):
            logger.log_tool_call(i, tc.tool_name, tc.args, tc.result_preview)

    submission = collector.get("submission", {}) or {}
    return DeepDetectResult(
        primary_language=str(submission.get("primary_language", "")),
        languages=submission.get("languages", {}) or {},
        build_systems=list(submission.get("build_systems", []) or []),
        frameworks=list(submission.get("frameworks", []) or []),
        orms=list(submission.get("orms", []) or []),
        databases=list(submission.get("databases", []) or []),
        proprietary_frameworks=list(submission.get("proprietary_frameworks", []) or []),
        top_imports=collector.get("last_import_frequency", {}) or {},
        skills_loaded=list(collector.get("skills_loaded", []) or []),
        tool_calls=tool_calls,
        total_tokens=total_tokens,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        elapsed_sec=time.perf_counter() - start,
    )
