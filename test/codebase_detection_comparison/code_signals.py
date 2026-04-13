"""코드 파일 한 개에서 14가지 '힌트'를 구조적으로 뽑아내는 모듈.

Phase 3.5 동적 발견의 핵심. AI 가 원본 코드 전체를 읽지 않고도 핵심 신호만 받아
추론할 수 있게 한다. 토큰 절약 + 반복 노이즈 제거.

14가지 힌트 카테고리:
  ①  주석 헤더 (파일 맨 위 블록 주석)
  ②  import / #include
  ③  #define 상수
  ④  typedef / struct 정의 (C / C++), class 정의 (Java 등)
  ⑤  함수 정의 이름 (네이밍 컨벤션 관찰용)
  ⑥  매크로 호출 (PFM_TRY, EXEC SQL 등 프레임워크 제어 흐름)
  ⑦  API 호출 패턴 (mpfmdbio, GraphDatabase.driver 등)
  ⑧  에러 처리 패턴 (PFM_ERRC, try/catch 등)
  ⑨  유틸리티 함수 접두어 (mpfm_*, utils_* 등)
  ⑩  변수/도메인 용어 빈도
  ⑪  자연어 주석의 비ASCII (한국어/일본어/중국어 등)
  ⑫  Makefile / manifest 링크 옵션 (별도 처리)
  ⑬  파일명 컨벤션 (zapamcom10060 같은 패턴)
  ⑭  디렉토리 경로 단서 (PFM_DBIO/, src/main/java/ 등)

본 모듈은 ①~⑪ + ⑬ 을 파일 한 개 기준으로 뽑는다.
⑫ 는 file_tools.read_file 로 별도 처리, ⑭ 는 glob 기반.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from test.codebase_detection_comparison.file_tools import read_file


@dataclass
class CodeSignals:
    """14가지 힌트 카테고리의 구조화 결과."""

    path: str = ""
    filename_pattern: str = ""                 # ⑬
    header_comment: str = ""                   # ①
    includes: list[str] = field(default_factory=list)                # ②
    defines: list[dict] = field(default_factory=list)                # ③
    typedefs: list[str] = field(default_factory=list)                # ④
    function_names: list[str] = field(default_factory=list)          # ⑤
    macros_invoked: list[str] = field(default_factory=list)          # ⑥
    api_calls_frequent: list[dict] = field(default_factory=list)     # ⑦
    error_patterns: list[str] = field(default_factory=list)          # ⑧
    utility_prefixes: list[dict] = field(default_factory=list)       # ⑨
    domain_terms: list[str] = field(default_factory=list)            # ⑩
    natural_language: dict = field(default_factory=dict)             # ⑪
    stats: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "path": self.path,
            "⑬_filename_pattern": self.filename_pattern,
            "①_header_comment": self.header_comment,
            "②_includes": self.includes[:30],
            "③_defines": self.defines[:15],
            "④_typedefs": self.typedefs[:15],
            "⑤_function_names": self.function_names[:30],
            "⑥_macros_invoked": self.macros_invoked[:15],
            "⑦_api_calls_frequent": self.api_calls_frequent[:15],
            "⑧_error_patterns": self.error_patterns[:10],
            "⑨_utility_prefixes": self.utility_prefixes[:10],
            "⑩_domain_terms": self.domain_terms[:20],
            "⑪_natural_language": self.natural_language,
            "stats": self.stats,
        }


# 정규식 사전 컴파일 (언어 가리지 않고 대부분 쓸 수 있게).
_RE_HEADER_COMMENT_C = re.compile(r"/\*(.*?)\*/", re.DOTALL)
_RE_HEADER_COMMENT_PY = re.compile(r'^\s*(?:\'\'\'|""")(.*?)(?:\'\'\'|""")', re.DOTALL)
_RE_INCLUDE_C = re.compile(r'^\s*#include\s*[<"]([^>"]+)[>"]', re.MULTILINE)
_RE_IMPORT_PY = re.compile(r'^\s*(?:from\s+([\w.]+)|import\s+([\w.]+))', re.MULTILINE)
_RE_IMPORT_JAVA = re.compile(r'^\s*import\s+([\w.]+);?', re.MULTILINE)
_RE_DEFINE_C = re.compile(r'^\s*#define\s+([A-Z_][A-Z0-9_]*)\s+(.+?)(?://|/\*|$)', re.MULTILINE)
_RE_TYPEDEF_C = re.compile(r'typedef\s+(?:struct\s*\{[^}]*\}\s*)?([\w_]+)\s*;', re.DOTALL)
_RE_STRUCT_FWD = re.compile(r'\b(\w+_(?:io|in|out|req|res|dto|entity)_t)\b')
_RE_FUNCTION_C = re.compile(
    r'^\s*(?:static\s+)?(?:[\w\*]+\s+)+(\w+)\s*\([^;{]*\)\s*\{',
    re.MULTILINE,
)
_RE_FUNCTION_PY = re.compile(r'^\s*def\s+(\w+)\s*\(', re.MULTILINE)
_RE_FUNCTION_JAVA = re.compile(
    r'(?:public|private|protected|static|final|\s)+[\w\<\>\[\]]+\s+(\w+)\s*\(', re.MULTILINE
)
_RE_MACRO_INVOKE = re.compile(r'\b([A-Z]{2,}_[A-Z0-9_]+)\s*[\(:]', re.MULTILINE)
_RE_UPPER_CONST = re.compile(r'\b([A-Z][A-Z0-9_]{2,})\b')
_RE_CALL_GENERIC = re.compile(r'\b([a-z_][a-z_0-9]{2,})\s*\(')
_RE_NONASCII = re.compile(r'[^\x00-\x7f]')
_RE_KOREAN = re.compile(r'[\uAC00-\uD7AF]')
_RE_JAPANESE = re.compile(r'[\u3040-\u30FF]')
_RE_CHINESE = re.compile(r'[\u4E00-\u9FFF]')
# 업무 도메인처럼 보이는 변수명 (_cd, _id, _num, _amt 등 접미어 규칙).
_RE_DOMAIN_VAR = re.compile(r'\b([a-z][a-z_0-9]+_(?:cd|id|num|amt|dt|tm|yn|flag|seq|no|nm|amnt|rslt))\b')


def _detect_filename_pattern(path: Path) -> str:
    """파일명에서 컨벤션 추출.

    예: zapamcom10060.c → 접두어 zap + 업무코드 amcom + 일련번호 10060
        pdb_order_s0024.h → DBIO prefix pdb + table order + s0024 (SELECT #24)
    """
    name = path.stem
    # DBIO 패턴
    m = re.match(r'^(pdb|db|dao)_([\w]+?)_([siud])(\d+)$', name)
    if m:
        return f"DBIO 패턴: prefix={m.group(1)}, entity={m.group(2)}, op={m.group(3)}({ {'s':'SELECT','i':'INSERT','u':'UPDATE','d':'DELETE'}[m.group(3)]}), seq={m.group(4)}"
    # 일반 업무코드 패턴: 영문접두어 + 숫자
    m = re.match(r'^([a-z]{2,6})([a-z]+)?(\d{3,})$', name)
    if m:
        prefix, mid, seq = m.group(1), m.group(2) or "", m.group(3)
        return f"업무모듈 패턴: prefix={prefix}, mid={mid}, seq={seq}"
    return ""


def _extract_header_comment(content: str, ext: str) -> str:
    """파일 맨 위 블록 주석 추출 (최대 2000자)."""
    if ext in {".c", ".h", ".cpp", ".hpp", ".ec", ".pc", ".java", ".kt", ".ts", ".js", ".go", ".rs"}:
        m = _RE_HEADER_COMMENT_C.search(content[:5000])
        if m:
            return m.group(1).strip()[:2000]
    elif ext == ".py":
        m = _RE_HEADER_COMMENT_PY.search(content[:5000])
        if m:
            return m.group(1).strip()[:2000]
    # 라인 주석 (// 또는 #) 으로 시작하는 맨 위 블록도 헤더로 취급.
    lines = content.splitlines()[:60]
    header_lines = []
    started = False
    for line in lines:
        stripped = line.strip()
        if not stripped:
            if started:
                break
            continue
        if stripped.startswith(("//", "#", ";")) and not stripped.startswith("#include") and not stripped.startswith("#define"):
            header_lines.append(stripped.lstrip("/#; "))
            started = True
        elif started:
            break
    if header_lines:
        return "\n".join(header_lines)[:2000]
    return ""


def _extract_includes(content: str, ext: str) -> list[str]:
    if ext in {".c", ".h", ".cpp", ".hpp", ".ec", ".pc"}:
        return _RE_INCLUDE_C.findall(content)
    if ext == ".py":
        matches = _RE_IMPORT_PY.findall(content)
        return [g[0] or g[1] for g in matches]
    if ext in {".java", ".kt"}:
        return _RE_IMPORT_JAVA.findall(content)
    return []


def _extract_defines(content: str, ext: str) -> list[dict]:
    if ext not in {".c", ".h", ".cpp", ".hpp", ".ec", ".pc"}:
        return []
    result = []
    for m in _RE_DEFINE_C.finditer(content):
        name, value = m.group(1), m.group(2).strip()
        if len(value) > 80:
            value = value[:80] + "..."
        result.append({"name": name, "value": value})
    return result


def _extract_typedefs(content: str, ext: str) -> list[str]:
    if ext not in {".c", ".h", ".cpp", ".hpp", ".ec", ".pc"}:
        return []
    result = set()
    for m in _RE_TYPEDEF_C.finditer(content):
        result.add(m.group(1))
    for m in _RE_STRUCT_FWD.finditer(content):
        result.add(m.group(1))
    return sorted(result)


def _extract_function_names(content: str, ext: str) -> list[str]:
    if ext in {".c", ".h", ".cpp", ".hpp", ".ec", ".pc"}:
        return _RE_FUNCTION_C.findall(content)
    if ext == ".py":
        return _RE_FUNCTION_PY.findall(content)
    if ext in {".java", ".kt"}:
        return _RE_FUNCTION_JAVA.findall(content)
    return []


def _extract_macros_invoked(content: str) -> list[str]:
    """UPPER_SNAKE 형태 매크로 호출. #define 된 것 제외 (대소문자로는 구분 어려우니
    일단 많이 쓰이는 것만 빈도 기반 반환)."""
    counter = Counter(_RE_MACRO_INVOKE.findall(content))
    return [name for name, _ in counter.most_common(20) if counter[name] >= 2]


def _extract_api_calls(content: str) -> list[dict]:
    """소문자 함수 호출 패턴 빈도. 라이브러리 API 추정용."""
    counter = Counter(_RE_CALL_GENERIC.findall(content))
    # 너무 흔한 제어문 제외.
    noise = {"if", "for", "while", "switch", "return", "sizeof", "printf", "sprintf",
             "strcmp", "strcpy", "strlen", "memcpy", "memset", "malloc", "free",
             "fprintf", "fopen", "fclose", "exit", "atoi", "atol"}
    return [
        {"name": name, "count": cnt}
        for name, cnt in counter.most_common(25)
        if name not in noise and cnt >= 2
    ]


def _extract_error_patterns(content: str) -> list[str]:
    """에러 처리 관련 식별자."""
    patterns = set()
    for m in _RE_UPPER_CONST.finditer(content):
        token = m.group(1)
        if any(k in token for k in ("ERR", "ERROR", "EXCEPT", "CATCH", "TRY", "FAIL", "ABORT")):
            patterns.add(token)
    # 또 try/catch/raise 키워드 빈도.
    for kw in ("try", "catch", "throw", "except", "raise", "finally"):
        if re.search(rf'\b{kw}\b', content):
            patterns.add(f"keyword:{kw}")
    return sorted(patterns)[:20]


def _extract_utility_prefixes(content: str, api_calls: list[dict]) -> list[dict]:
    """API 호출에서 반복되는 접두어 (_ 앞부분) 집계."""
    prefix_counter: Counter[str] = Counter()
    for call in api_calls:
        name = call["name"]
        if "_" in name:
            prefix = name.split("_", 1)[0]
            if len(prefix) >= 2:
                prefix_counter[prefix] += call["count"]
    return [
        {"prefix": p, "count": c}
        for p, c in prefix_counter.most_common(10)
        if c >= 3
    ]


def _extract_domain_terms(content: str) -> list[str]:
    """업무 도메인 변수 접미어 규칙으로 발견된 용어."""
    counter = Counter(_RE_DOMAIN_VAR.findall(content.lower()))
    return [name for name, _ in counter.most_common(25)]


def _detect_natural_language(content: str) -> dict:
    """한/일/중 문자 존재 확인 + 비율 통계."""
    total_non_ascii = len(_RE_NONASCII.findall(content))
    ko = len(_RE_KOREAN.findall(content))
    ja = len(_RE_JAPANESE.findall(content))
    zh = len(_RE_CHINESE.findall(content))
    info: dict[str, Any] = {
        "non_ascii_chars": total_non_ascii,
        "korean": ko,
        "japanese": ja,
        "chinese": zh,
    }
    detected = []
    if ko > 10:
        detected.append("Korean")
    if ja > 10:
        detected.append("Japanese")
    if zh > 10:
        detected.append("Chinese")
    info["detected_languages"] = detected
    return info


def extract_signals(path: str | Path, max_bytes: int = 30000) -> CodeSignals:
    """파일 하나에서 14가지 힌트 추출. 최대 max_bytes 만 읽음."""

    p = Path(path)
    try:
        content = read_file(p)
    except (IOError, OSError) as err:
        return CodeSignals(path=str(p), stats={"error": str(err)})

    if len(content) > max_bytes:
        content = content[:max_bytes]

    ext = p.suffix.lower()
    api_calls = _extract_api_calls(content)

    signals = CodeSignals(
        path=str(p),
        filename_pattern=_detect_filename_pattern(p),
        header_comment=_extract_header_comment(content, ext),
        includes=_extract_includes(content, ext),
        defines=_extract_defines(content, ext),
        typedefs=_extract_typedefs(content, ext),
        function_names=_extract_function_names(content, ext),
        macros_invoked=_extract_macros_invoked(content),
        api_calls_frequent=api_calls,
        error_patterns=_extract_error_patterns(content),
        utility_prefixes=_extract_utility_prefixes(content, api_calls),
        domain_terms=_extract_domain_terms(content),
        natural_language=_detect_natural_language(content),
        stats={
            "bytes_read": len(content),
            "line_count": content.count("\n"),
        },
    )
    return signals


def summarize_signals_multi(signals_list: list[CodeSignals]) -> dict:
    """여러 파일의 신호를 종합해 공통 패턴 도출.

    LLM 에게 넘겨줄 요약 JSON.
    """
    header_comments = [s.header_comment for s in signals_list if s.header_comment]

    all_typedefs: list[str] = []
    all_functions: list[str] = []
    all_macros: Counter[str] = Counter()
    all_api: Counter[str] = Counter()
    all_error: set[str] = set()
    all_util_prefix: Counter[str] = Counter()
    all_domain: Counter[str] = Counter()
    all_includes: Counter[str] = Counter()
    korean = 0
    japanese = 0
    chinese = 0

    for s in signals_list:
        all_typedefs.extend(s.typedefs)
        all_functions.extend(s.function_names)
        for m in s.macros_invoked:
            all_macros[m] += 1
        for api in s.api_calls_frequent:
            all_api[api["name"]] += api["count"]
        all_error.update(s.error_patterns)
        for up in s.utility_prefixes:
            all_util_prefix[up["prefix"]] += up["count"]
        for term in s.domain_terms:
            all_domain[term] += 1
        for inc in s.includes:
            all_includes[inc] += 1
        nl = s.natural_language
        korean += nl.get("korean", 0)
        japanese += nl.get("japanese", 0)
        chinese += nl.get("chinese", 0)

    # 함수명에서 네이밍 컨벤션 클러스터 추출 (예: a000_, b200_).
    naming_clusters: Counter[str] = Counter()
    for fn in all_functions:
        m = re.match(r'^([a-z])(\d{3})_', fn)
        if m:
            naming_clusters[f"{m.group(1)}{m.group(2)[0]}xx_*"] += 1

    languages = []
    if korean > 10: languages.append("Korean")
    if japanese > 10: languages.append("Japanese")
    if chinese > 10: languages.append("Chinese")

    return {
        "file_count": len(signals_list),
        "filename_patterns": [s.filename_pattern for s in signals_list if s.filename_pattern][:10],
        "header_comments_sample": header_comments[:3],
        "includes_top": [{"name": n, "count": c} for n, c in all_includes.most_common(15)],
        "typedefs_top": list(dict.fromkeys(all_typedefs))[:20],
        "naming_convention_clusters": [
            {"pattern": p, "count": c} for p, c in naming_clusters.most_common(8)
        ],
        "macros_top": [{"name": n, "count": c} for n, c in all_macros.most_common(12)],
        "api_calls_top": [{"name": n, "count": c} for n, c in all_api.most_common(15)],
        "error_keywords": sorted(all_error)[:20],
        "utility_prefixes_top": [
            {"prefix": p, "count": c} for p, c in all_util_prefix.most_common(8)
        ],
        "domain_terms_top": [{"term": n, "count": c} for n, c in all_domain.most_common(15)],
        "natural_languages_detected": languages,
    }
