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
  소유권 모델과 해당 언어의 import 문법은 ``ontology_roles.yaml.reference_imports``가
  선언한다. 선언 없이 import 단서가 들어오면 Pass 2를 생략하지 않고 명시 실패한다.

오탐 차단: 주석·문자열 리터럴 제거 후 식별자 토큰 단위 정확 일치. import 로 범위를
좁히므로 동명이인(타 모듈 같은 이름)을 오연결하지 않는다.
※ 못 잡는 것(설계상 범위, silent fail 아님): 선언 노드가 없는 인스턴스 속성 접근
(``self._cfg.x`` 등 — 타입 추론 필요). 상속 선언은 별도 ontology profile과 token matcher가
같은 파일/import 단독 후보로 해소한다.
"""
from __future__ import annotations

import re
from collections import defaultdict
from pathlib import Path
from typing import NamedTuple

from graph.schema.graph_nodes import Constant, Variable
from graph.schema.node_ids import SEP, child_id, code_ns_id
from pipeline.languages import lang_of, reference_import_profile

# module_id → {식별자 이름: [(node_id, 라벨)]}
_Targets = dict[str, dict[str, list[tuple[str, str]]]]

class _Scope(NamedTuple):
    """한 파일의 Pass 2 후보 — import profile을 공통 lookup 형태로 정규화."""
    lang: str
    owner_map: dict[str, list[str]]
    bare_map: dict[str, list[str]]
    direct_names: dict[str, list[tuple[str, str]]]


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

    경로 인덱스는 source-module resolver에, basename 인덱스는 local-file resolver에
    쓰인다. 한 파일에 여러 module이 올 수 있다.
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
        lang = lang_of(Path(file_path).suffix)
        profile = reference_import_profile(lang)
        resolver = str(profile["resolver"])
        builder = _SCOPE_BUILDERS.get(resolver)
        if builder is None:
            raise ValueError(
                f"지원하지 않는 reference import resolver: {resolver!r} ({lang})"
            )
        scopes[file_path] = builder(
            lang=lang,
            file_path=file_path,
            names=names,
            own_module_ids=modules_by_file.get(file_path.replace("\\", "/"), set()),
            targets=targets,
            modules_by_file=modules_by_file,
            basename_index=basename_index,
            profile=profile,
        )
    return scopes


# ── Pass 2 import 소유권 모델 ────────────────────────────────

def _container_and_name(node_id: str) -> tuple[str, str]:
    """노드 id → (컨테이너 id, 마지막 이름). ``node_ids`` 의 경로 규약만 쓰는 언어 무관 분해."""
    container, separator, name = node_id.rpartition(SEP)
    return (container, name) if separator else ("", node_id)


def _namespace_scope(*, lang: str, file_path: str, names: list[str],
                     own_module_ids: set[str], targets: _Targets,
                     modules_by_file: dict[str, set[str]],
                     basename_index: dict[str, set[str]], profile: dict) -> _Scope:
    """Namespace imports → qualified owners + directly imported members."""
    del file_path, modules_by_file, basename_index
    separator = str(profile.get("separator") or ".")
    static_prefix = str(profile.get("static_prefix") or "")
    wildcard_suffix = str(profile.get("wildcard_suffix") or "")
    owner_map: dict[str, list[str]] = defaultdict(list)
    bare_map: dict[str, list[str]] = defaultdict(list)
    for name in names:
        if static_prefix and name.startswith(static_prefix):
            owner, _, member = name[len(static_prefix):].strip().rpartition(separator)
            package, _, simple = owner.rpartition(separator)
            if simple and member:
                bare_map[member].append(child_id(code_ns_id(package), simple))
        elif wildcard_suffix and name.endswith(wildcard_suffix):
            package_id = code_ns_id(name[:-len(wildcard_suffix)])
            for mid in targets:
                container, simple = _container_and_name(mid)
                if container == package_id and simple:
                    owner_map[simple].append(mid)
        else:
            package, _, simple = name.rpartition(separator)
            if simple:
                owner_map[simple].append(child_id(code_ns_id(package), simple))
    # Namespace peers are visible by their simple owner name.
    packages = {container
                for container, _ in map(_container_and_name, own_module_ids) if container}
    for mid in targets:
        container, simple = _container_and_name(mid)
        if container in packages and simple:
            owner_map[simple].append(mid)
    return _Scope(lang, dict(owner_map), dict(bare_map), {})


def _module_path_scope(*, lang: str, file_path: str, names: list[str],
                       own_module_ids: set[str], targets: _Targets,
                       modules_by_file: dict[str, set[str]],
                       basename_index: dict[str, set[str]], profile: dict) -> _Scope:
    """Source-module paths → qualified owners + directly imported names."""
    del own_module_ids, targets, basename_index
    owner_map: dict[str, list[str]] = defaultdict(list)
    bare_map: dict[str, list[str]] = defaultdict(list)
    current = file_path.replace("\\", "/")
    from_pattern = re.compile(str(profile["from_pattern"]), re.S)
    separator = str(profile.get("separator") or ".")
    alias_keyword = str(profile.get("alias_keyword") or "as")
    list_separator = str(profile.get("list_separator") or ",")
    extension = str(profile.get("file_extension") or "").lstrip(".")
    if not extension:
        raise ValueError(f"reference_imports.{lang}.file_extension 가 필요하다")
    for name in names:
        match = from_pattern.match(name)
        if match:
            groups = match.groupdict()
            relative = groups.get("relative") or ""
            module_path = groups.get("module") or ""
            imported = groups.get("members") or ""
            tokens = [
                pair for pair in _parse_import_names(
                    imported, alias_keyword=alias_keyword, list_separator=list_separator,
                ) if pair[0] != "*"
            ]
            if module_path:
                file_mids, by_simple = _resolve_source_module(
                    current, relative, module_path, modules_by_file,
                    separator=separator, extension=extension,
                )
                for token, alias in tokens:
                    key = alias or token
                    bare_map[key].extend(file_mids)
                    owner_map[key].extend(by_simple.get(token, ()))
            else:
                for token, alias in tokens:
                    sub_mids, _ = _resolve_source_module(
                        current, relative, token, modules_by_file,
                        separator=separator, extension=extension,
                    )
                    owner_map[alias or token].extend(sub_mids)
        else:
            for token, alias in _parse_import_names(
                name, alias_keyword=alias_keyword, list_separator=list_separator,
            ):
                file_mids, _ = _resolve_source_module(
                    current, "", token, modules_by_file,
                    separator=separator, extension=extension,
                )
                key = alias or token.rpartition(separator)[2]
                owner_map[key].extend(file_mids)
    return _Scope(lang, dict(owner_map), dict(bare_map), {})


def _resolve_source_module(
    current: str, relative: str, module_path: str,
    modules_by_file: dict[str, set[str]], *, separator: str, extension: str,
) -> tuple[list[str], dict[str, list[str]]]:
    """Resolve a source-module path to module ids without guessing external packages."""
    if relative:
        base = current.rsplit("/", 1)[0] if "/" in current else ""
        for _ in range(len(relative) - 1):
            base = base.rsplit("/", 1)[0] if "/" in base else ""
        frag = f"{base}/{module_path.replace(separator, '/')}".strip("/") if module_path else base
    else:
        frag = module_path.replace(separator, "/")
    if not frag:
        return [], {}
    target = f"{frag}.{extension}"
    mids: list[str] = []
    for path, module_ids in modules_by_file.items():
        if path == target or path.endswith(f"/{target}"):
            mids.extend(module_ids)
    by_simple: dict[str, list[str]] = defaultdict(list)
    for mid in mids:
        by_simple[mid.rpartition(".")[2]].append(mid)
    return mids, dict(by_simple)


def _parse_import_names(spec: str, *, alias_keyword: str, list_separator: str):
    """Split a declarative import member list into ``(name, alias)`` pairs."""
    for part in spec.strip().strip("()").split(list_separator):
        bits = part.split()
        if len(bits) == 3 and bits[1] == alias_keyword:
            yield bits[0], bits[2]
        elif bits:
            yield bits[0], ""


def _local_file_scope(*, lang: str, file_path: str, names: list[str],
                      own_module_ids: set[str], targets: _Targets,
                      modules_by_file: dict[str, set[str]],
                      basename_index: dict[str, set[str]], profile: dict) -> _Scope:
    """Explicit local-file imports → direct names from only those files."""
    del file_path, own_module_ids, modules_by_file
    pattern = re.compile(str(profile["local_path_pattern"]))
    imported_modules: set[str] = set()
    for name in names:
        match = pattern.match(name)
        if match:
            imported = match.groupdict().get("path") or ""
            basename = imported.replace("\\", "/").rsplit("/", 1)[-1]
            imported_modules |= basename_index.get(basename, set())
    merged: dict[str, list[tuple[str, str]]] = defaultdict(list)
    for mid in imported_modules:
        for ident, entries in targets.get(mid, {}).items():
            merged[ident].extend(entries)
    return _Scope(lang, {}, {}, dict(merged))


_SCOPE_BUILDERS = {
    "namespace": _namespace_scope,
    "module_path": _module_path_scope,
    "local_file": _local_file_scope,
}


# ── 링킹 (Pass 1 + Pass 2) ──────────────────────────────────
