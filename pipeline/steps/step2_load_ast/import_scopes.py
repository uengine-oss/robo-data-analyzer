"""REFERENCES / INIT_BY 엣지 — 결정론 식별자 매칭 (같은 모듈 + 모듈 너머).

antlr JSON 에 "변수 참조" 노드가 없으므로, 본문 텍스트의 식별자 토큰을
Variable/Constant 이름과 매칭한다. LLM 0회 — 순수 결정론.

- REFERENCES: 본문이 참조하는 변수/상수 → CodeNode → 그 노드.
- INIT_BY:    변수/상수 정의 시 참조하는 다른 변수/상수 → (Variable|Constant) → 출처.
             "정의 시 참조" 의미: ``=`` 우변뿐 아니라 변수 size 표현식
             (``[LEN +1]``) · 상수 식 (``#define B (A+1)``) 모두 포함.
             antlr (C parser) 가 size 표현식까지 ``initValue`` 에 통합해 출력하므로
             reader 는 ``Variable.init_value`` / ``Constant.value`` 만 보면 끝.

2단 패스 (계획 §2.8):
- **Pass 1 — 같은 파일(모듈) 안.** 전 언어 공통.
- **Pass 2 — 모듈 너머.** "코드에 명시된 소유권 신호(import)" 만 따라간다 — 추측 안 함.
    · Java   : ``import`` FQN → ``소유자.멤버`` 정규식으로 그 모듈 상수 매치.
    · Python : ``import`` / ``from import`` 가 못박은 모듈의 ``모듈.이름``·맨이름 매치.
    · C      : ``#include "헤더"`` 한 헤더 모듈만 후보에 더해 맨이름 매치.
  명단에 없는 언어는 Pass 2 skip (Pass 1 은 그대로) — 안전 degrade + log 1회.

오탐 차단: 주석·문자열 리터럴 제거 후 식별자 토큰 단위 정확 일치. import 로 범위를
좁히므로 동명이인(타 모듈 같은 이름)을 오연결하지 않는다.
※ 못 잡는 것(설계상 범위, silent fail 아님): Python 인스턴스 속성 접근
  (``self._cfg.x`` — 타입 추론 필요), Java 상속 상수 (부모 클래스 상수를 맨이름으로).
"""
from __future__ import annotations

import re
from collections import defaultdict
from pathlib import Path
from typing import NamedTuple

from graph.schema.graph_nodes import Constant, Variable
from graph.schema.node_ids import SEP, child_id, code_ns_id
from pipeline.languages import EXT_TO_LANG
from shared.events.runtime_logger import log_info

_COMPONENT = "step2.reference_linker"

_IDENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_QUALIFIED = re.compile(r"([A-Za-z_][A-Za-z0-9_]*)\.([A-Za-z_][A-Za-z0-9_]*)")
_STRING = re.compile(r'"(?:\\.|[^"\\])*"' + r"|'(?:\\.|[^'\\])*'")
_COMMENT = re.compile(r"/\*.*?\*/|//[^\n]*|#[^\n]*")
_C_QUOTED = re.compile(r'^"([^"]+)"')                       # #include "x.h" — 프로젝트 헤더
_PY_FROM = re.compile(r"from\s+(\.*)([\w.]*)\s+import\s+(.+)", re.S)

# module_id → {식별자 이름: [(node_id, 라벨)]}
_Targets = dict[str, dict[str, list[tuple[str, str]]]]

# Pass 2 미지원 언어 — 언어당 1회만 log (노이즈 차단).
_warned_langs: set[str] = set()


class _Scope(NamedTuple):
    """한 파일의 Pass 2 후보 — 언어별로 셋 중 하나만 채워짐."""
    lang: str
    owner_map: dict[str, list[str]]            # Java/Python: 소유자 토큰 → module_id 들
    bare_map: dict[str, list[str]]             # Java static / Python from-import: 멤버 → module_id 들
    c_names: dict[str, list[tuple[str, str]]]  # C: include 한 헤더들의 이름 테이블 병합


def _targets_by_module(
    variables: list[Variable], constants: list[Constant],
) -> _Targets:
    """module_id → {이름: [(node_id, 라벨)]} — 매칭 후보 (같은 이름 다수면 모두)."""
    by_module: _Targets = defaultdict(lambda: defaultdict(list))
    for v in variables:
        if v.name:
            by_module[v.owner_id][v.name].append((v.id, "VARIABLE"))
    for c in constants:
        if c.name:
            by_module[c.owner_id][c.name].append((c.id, "CONSTANT"))
    return by_module


def _file_indexes(
    variables: list[Variable], constants: list[Constant],
) -> tuple[dict[str, set[str]], dict[str, set[str]]]:
    """파일 경로 → module_id 집합, 파일명(basename) → module_id 집합.

    경로 인덱스는 Python import 의 모듈 환원에, basename 인덱스는 C ``#include``
    헤더 환원에 쓰인다. 한 파일에 여러 모듈(Java 다중 클래스 등)이 올 수 있다.
    """
    by_path: dict[str, set[str]] = defaultdict(set)
    by_basename: dict[str, set[str]] = defaultdict(set)
    for node in (*variables, *constants):
        if not node.file_path:
            continue
        norm = node.file_path.replace("\\", "/")
        by_path[norm].add(node.owner_id)
        by_basename[norm.rsplit("/", 1)[-1]].add(node.owner_id)
    return by_path, by_basename


def _imports_by_file(import_refs: list[dict]) -> dict[str, list[str]]:
    """``{name, file, line}`` 목록 → 파일별 import name 목록."""
    out: dict[str, list[str]] = defaultdict(list)
    for ref in import_refs:
        name = (ref.get("name") or "").strip()
        file = ref.get("file") or ""
        if name and file:
            out[file].append(name)
    return out


def _build_scopes(
    import_refs: list[dict],
    targets: _Targets,
    modules_by_file: dict[str, set[str]],
    basename_index: dict[str, set[str]],
) -> dict[str, _Scope]:
    """파일별 Pass 2 후보(_Scope) — import 를 언어별로 해석해 1회 산출."""
    scopes: dict[str, _Scope] = {}
    for file_path, names in _imports_by_file(import_refs).items():
        lang = _lang_of(file_path)
        if lang == "java":
            own = modules_by_file.get(file_path.replace("\\", "/"), set())
            owner_map, bare_map = _java_maps(names, own, targets)
            scopes[file_path] = _Scope("java", owner_map, bare_map, {})
        elif lang == "python":
            owner_map, bare_map = _python_maps(file_path, names, modules_by_file)
            scopes[file_path] = _Scope("python", owner_map, bare_map, {})
        elif lang == "c":
            c_names = _c_names(names, basename_index, targets)
            scopes[file_path] = _Scope("c", {}, {}, c_names)
        else:
            _warn_unsupported(lang)
    return scopes


def _lang_of(file_path: str) -> str | None:
    """Return the registered language for a source path suffix."""
    return EXT_TO_LANG.get(Path(file_path).suffix.lstrip(".").lower())


def _warn_unsupported(lang: str | None) -> None:
    """Log an unsupported pass-2 language once while preserving pass 1."""
    key = lang or "(unknown)"
    if key not in _warned_langs:
        _warned_langs.add(key)
        log_info(
            _COMPONENT,
            "pass2_unsupported",
            "module-boundary reference linking unsupported; same-module linking remains active",
            lang=key,
        )


# ── Pass 2 언어별 import 해석 ────────────────────────────────

def _container_and_name(node_id: str) -> tuple[str, str]:
    """노드 id → (컨테이너 id, 마지막 이름). ``node_ids`` 의 경로 규약만 쓰는 언어 무관 분해."""
    container, separator, name = node_id.rpartition(SEP)
    return (container, name) if separator else ("", node_id)


def _java_maps(
    names: list[str], own_module_ids: set[str], targets: _Targets,
) -> tuple[dict[str, list[str]], dict[str, list[str]]]:
    """Java import 문법 해석 → (소유자 simpleName → module_id, static 멤버 → 소유 module_id).

    언어별인 것은 **import 문법**뿐이다. id 는 언어 무관 단일 규약(``node_ids``)을 그대로
    쓴다 — ``code_ns_id(패키지)`` 아래 ``child_id(…, 클래스)``. 여기서 id 를 따로 만들면
    규약이 두 곳에 살게 되므로 반드시 그 함수들만 쓴다.

    ``import static`` 은 멤버를 맨이름으로 들여오고, ``.*`` 와일드카드·같은 패키지
    클래스도 후보에 포함한다.
    """
    owner_map: dict[str, list[str]] = defaultdict(list)
    bare_map: dict[str, list[str]] = defaultdict(list)
    for name in names:
        if name.startswith("static"):                  # import static pkg.Cls.MEMBER
            owner, _, member = name[len("static"):].strip().rpartition(".")
            package, _, simple = owner.rpartition(".")
            if simple and member:
                bare_map[member].append(child_id(code_ns_id(package), simple))
        elif name.endswith(".*"):                       # import pkg.*
            package_id = code_ns_id(name[:-2])
            for mid in targets:
                container, simple = _container_and_name(mid)
                if container == package_id and simple:
                    owner_map[simple].append(mid)
        else:                                           # import pkg.Cls
            package, _, simple = name.rpartition(".")
            if simple:
                owner_map[simple].append(child_id(code_ns_id(package), simple))
    # 같은 패키지 클래스 — import 없이 simpleName 으로 쓰인다.
    packages = {container
                for container, _ in map(_container_and_name, own_module_ids) if container}
    for mid in targets:
        container, simple = _container_and_name(mid)
        if container in packages and simple:
            owner_map[simple].append(mid)
    return dict(owner_map), dict(bare_map)


def _python_maps(
    file_path: str, names: list[str], modules_by_file: dict[str, set[str]],
) -> tuple[dict[str, list[str]], dict[str, list[str]]]:
    """Python import → (모듈/클래스 토큰 → module_id, from-import 맨이름 → module_id)."""
    owner_map: dict[str, list[str]] = defaultdict(list)
    bare_map: dict[str, list[str]] = defaultdict(list)
    current = file_path.replace("\\", "/")
    for name in names:
        m = _PY_FROM.match(name)
        if m:                                           # from [.]mod import a, b
            dots, mod_path, imported = m.groups()
            tokens = [(t, a) for t, a in _parse_py_names(imported) if t != "*"]
            if mod_path:                                # from mod import 상수/클래스
                file_mids, by_simple = _py_resolve(current, dots, mod_path, modules_by_file)
                for token, alias in tokens:
                    key = alias or token
                    bare_map[key].extend(file_mids)            # token = 모듈레벨 상수
                    owner_map[key].extend(by_simple.get(token, ()))  # token = 클래스
            else:                                       # from . import sub — sub 가 곧 모듈
                for token, alias in tokens:
                    sub_mids, _ = _py_resolve(current, dots, token, modules_by_file)
                    owner_map[alias or token].extend(sub_mids)
        else:                                           # import mod[.sub] [as a]
            for token, alias in _parse_py_names(name):
                file_mids, _ = _py_resolve(current, "", token, modules_by_file)
                key = alias or (token.rpartition(".")[2] if "." in token else token)
                owner_map[key].extend(file_mids)
    return dict(owner_map), dict(bare_map)


def _py_resolve(
    current: str, dots: str, mod_path: str, modules_by_file: dict[str, set[str]],
) -> tuple[list[str], dict[str, list[str]]]:
    """import 의 모듈 경로 → (그 .py 파일의 module_id 들, {마지막세그먼트: module_id 들}).

    상대 import(``dots``) 는 현재 파일 위치 기준으로 푼다. 대상 .py 를 파일 경로
    suffix 로 매칭 — 매칭 0건이면 외부 패키지(stdlib 등)이므로 빈 결과.
    """
    if dots:
        base = current.rsplit("/", 1)[0] if "/" in current else ""
        for _ in range(len(dots) - 1):                  # `.`=현재 패키지, `..`=상위 ...
            base = base.rsplit("/", 1)[0] if "/" in base else ""
        frag = f"{base}/{mod_path.replace('.', '/')}".strip("/") if mod_path else base
    else:
        frag = mod_path.replace(".", "/")
    if not frag:
        return [], {}
    target = f"{frag}.py"
    mids: list[str] = []
    for path, module_ids in modules_by_file.items():
        if path == target or path.endswith(f"/{target}"):
            mids.extend(module_ids)
    by_simple: dict[str, list[str]] = defaultdict(list)
    for mid in mids:
        by_simple[mid.rpartition(".")[2]].append(mid)
    return mids, dict(by_simple)


def _parse_py_names(spec: str):
    """``a, b as c, (d)`` → ``(이름, 별칭)`` 들. 별칭 없으면 빈 문자열."""
    for part in spec.strip().strip("()").split(","):
        bits = part.split()
        if len(bits) == 3 and bits[1] == "as":
            yield bits[0], bits[2]
        elif bits:
            yield bits[0], ""


def _c_names(
    names: list[str], basename_index: dict[str, set[str]], targets: _Targets,
) -> dict[str, list[tuple[str, str]]]:
    """C ``#include "헤더"`` → 그 헤더 모듈들의 이름 테이블 병합 (맨이름 매치용).

    ``<...>`` 시스템 헤더는 외부라 건너뛴다 — 우리 노드 집합에 없어 자연히 매칭 안 됨.
    """
    header_mids: set[str] = set()
    for name in names:
        m = _C_QUOTED.match(name)
        if m:
            header = m.group(1).replace("\\", "/").rsplit("/", 1)[-1]
            header_mids |= basename_index.get(header, set())
    merged: dict[str, list[tuple[str, str]]] = defaultdict(list)
    for mid in header_mids:
        for ident, entries in targets.get(mid, {}).items():
            merged[ident].extend(entries)
    return dict(merged)


# ── 링킹 (Pass 1 + Pass 2) ──────────────────────────────────
