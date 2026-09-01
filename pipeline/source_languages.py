"""지원 언어 정책의 단일 정의처.

확장자, 분석 전략 감지, AST 역할과 stereotype 검증을 함께 소유한다. 세 기능은 모두
``지원 언어가 무엇인가``라는 같은 변경 이유를 가지며 ``ontology_roles.yaml``을 데이터
단일 진실로 사용한다. parser 지원과 lexing-only 지원은 서로 다른 능력이므로 분리한다.

★ 왜 단일 정의처여야 하나 (spec 046 FR-003 — 실제로 갈라져 있던 사고):
``strategy_detector`` 가 자기 목록(``.kt .go .ts .js .rb .rs .php .scala .cpp .hpp`` 포함
14종)을 따로 들고 있어서, ``.kt`` 만 있는 프로젝트가 "framework" 로 판정된 뒤 파싱에서
전 파일 스킵 → **노드 0개짜리 분석이 "성공"으로 끝났다.** 두 곳이 같은 목록을 봐야 한다.
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

import yaml


_ONTOLOGY_PATH = Path(__file__).resolve().parent / "ontology_roles.yaml"


@lru_cache(maxsize=1)
def _load_ontology() -> dict:
    """언어 정책 데이터를 한 번 로드한다."""
    if not _ONTOLOGY_PATH.is_file():
        raise FileNotFoundError(f"ontology_roles.yaml 없음: {_ONTOLOGY_PATH}")
    data = yaml.safe_load(_ONTOLOGY_PATH.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or not data:
        raise ValueError("ontology_roles.yaml 루트는 비어 있지 않은 mapping 이어야 한다")
    return data


def _extensions(section: dict, *, owner: str) -> dict[str, str]:
    """언어→확장자 목록을 검증해 확장자→언어로 뒤집는다."""
    result: dict[str, str] = {}
    if not isinstance(section, dict):
        raise ValueError(f"source_extensions.{owner} 는 mapping 이어야 한다")
    for lang, extensions in section.items():
        if not isinstance(extensions, list) or not extensions:
            raise ValueError(
                f"source_extensions.{owner}.{lang} 는 비어 있지 않은 list 여야 한다"
            )
        for raw in extensions:
            ext = str(raw).strip().lower().lstrip(".")
            if not ext or "." in ext:
                raise ValueError(f"잘못된 소스 확장자: {raw!r} ({owner}.{lang})")
            previous = result.get(ext)
            if previous is not None:
                raise ValueError(
                    f"중복 소스 확장자: {ext!r} ({owner}.{previous}, {owner}.{lang})"
                )
            result[ext] = str(lang)
    return result


def _extension_registry() -> tuple[dict[str, str], dict[str, str], dict[str, str]]:
    registry = _load_ontology().get("source_extensions") or {}
    parser = registry.get("parser") or {}
    framework = _extensions(parser.get("framework") or {}, owner="parser.framework")
    dbms = _extensions(parser.get("dbms") or {}, owner="parser.dbms")
    lexing_only = _extensions(
        registry.get("lexing_only") or {}, owner="lexing_only"
    )
    owners: dict[str, str] = {}
    for owner, mapping in (
        ("parser.framework", framework),
        ("parser.dbms", dbms),
        ("lexing_only", lexing_only),
    ):
        for ext in mapping:
            if ext in owners:
                raise ValueError(
                    f"중복 소스 확장자: {ext!r} ({owners[ext]}, {owner})"
                )
            owners[ext] = owner
    lexicons = _load_ontology().get("source_lexicon") or {}
    for lang in set(framework.values()) | set(dbms.values()) | set(lexing_only.values()):
        if lang not in lexicons:
            raise ValueError(
                f"확장자에 연결된 source_lexicon 누락: {lang!r}"
            )
    return framework, dbms, lexing_only


# 호환 공개 이름은 유지하되 값은 ontology 데이터에서 파생한다. AST reader 소비자는 parser
# 지원 집합만 보고, 소스 링커의 lang_of는 lexing-only까지 포함한다.
EXT_TO_LANG, _DBMS_EXT_TO_LANG, _LEXING_ONLY_EXT_TO_LANG = _extension_registry()
DBMS_EXTS: frozenset[str] = frozenset(_DBMS_EXT_TO_LANG)
_SOURCE_LANG_BY_EXT = {
    **EXT_TO_LANG,
    **_DBMS_EXT_TO_LANG,
    **_LEXING_ONLY_EXT_TO_LANG,
}


# ── 파생 (점 있는 표기 — Path.suffix 비교용). 정의처는 위 두 개뿐이다. ──
FRAMEWORK_SUFFIXES: frozenset[str] = frozenset(f".{e}" for e in EXT_TO_LANG)
DBMS_SUFFIXES: frozenset[str] = frozenset(f".{e}" for e in DBMS_EXTS)


def is_supported(path_suffix: str) -> bool:
    """``Path.suffix`` (예 ``".java"``) 가 분석 가능한 소스인가."""
    s = path_suffix.lower()
    return s in FRAMEWORK_SUFFIXES or s in DBMS_SUFFIXES


class NoSupportedSourceError(RuntimeError):
    """분석 가능한 소스가 하나도 없어 전략을 결정할 수 없다."""


def detect_strategy(project_dir: Path) -> Literal["framework", "dbms"]:
    """프로젝트 확장자로 분석 전략을 결정하며, 지원 소스가 없으면 명시적으로 실패한다."""
    has_framework = False
    has_dbms = False
    for source_path in project_dir.rglob("*"):
        if not source_path.is_file():
            continue
        suffix = source_path.suffix.lower()
        if suffix in FRAMEWORK_SUFFIXES:
            has_framework = True
        elif suffix in DBMS_SUFFIXES:
            has_dbms = True
    if has_framework:
        return "framework"
    if has_dbms:
        return "dbms"
    raise NoSupportedSourceError(
        f"분석 가능한 소스가 없다: {project_dir} — "
        f"지원 확장자 = {sorted(FRAMEWORK_SUFFIXES | DBMS_SUFFIXES)} "
        "(pipeline/source_languages.py가 단일 정의처)."
    )


# 호출 가능한 AST 루틴 라벨. 일반 제어문과 구조 노드는 포함하지 않는다.
ROUTINE_LABELS: frozenset[str] = frozenset({"FUNCTION", "PROCEDURE", "METHOD", "TRIGGER"})


@lru_cache(maxsize=1)
def module_labels() -> frozenset[str]:
    """All Parser labels that represent a source container Module."""
    languages = (_load_ontology().get("roles") or {}).get("module") or {}
    return frozenset(
        str(node_type)
        for node_types in languages.values()
        for node_type in (node_types or ())
    )


@lru_cache(maxsize=None)
def role_of(node_type: str, lang: str) -> str | None:
    """ANTLR 노드 타입의 언어별 역할을 반환한다."""
    for role, languages in (_load_ontology().get("roles") or {}).items():
        if node_type in (languages.get(lang) or []):
            return role
    return None


SymbolKind = Literal["global", "field", "local", "parameter", "constant"]
_SYMBOL_KINDS = frozenset(SymbolKind.__args__)


@lru_cache(maxsize=None)
def _symbol_kind_map(lang: str) -> dict[str, SymbolKind]:
    """검증된 parser label → 언어 중립 심볼 scope."""
    data = _load_ontology()
    entries = (data.get("symbol_kinds") or {}).get(lang)
    if not isinstance(entries, dict):
        raise ValueError(f"symbol_kinds 에 {lang!r} 언어 분류가 없다")
    result: dict[str, SymbolKind] = {}
    for raw_kind, node_types in entries.items():
        if raw_kind not in _SYMBOL_KINDS:
            raise ValueError(f"지원하지 않는 symbol kind: {raw_kind!r} ({lang})")
        if not isinstance(node_types, list) or not node_types:
            raise ValueError(f"symbol_kinds.{lang}.{raw_kind} 는 비어 있지 않은 list 여야 한다")
        for node_type in node_types:
            key = str(node_type)
            if key in result:
                raise ValueError(f"중복 symbol label: {lang}.{key}")
            result[key] = raw_kind

    roles = data.get("roles") or {}
    expected = set((roles.get("variable") or {}).get(lang) or ())
    expected.update((roles.get("constant") or {}).get(lang) or ())
    if set(result) != expected:
        raise ValueError(
            f"symbol_kinds.{lang} 과 roles variable/constant 불일치: "
            f"missing={sorted(expected - set(result))}, extra={sorted(set(result) - expected)}"
        )
    return result


def symbol_kind(node_type: str, lang: str) -> SymbolKind:
    """ANTLR 선언 label의 범용 scope. 미등록 label은 추측하지 않고 실패한다."""
    kind = _symbol_kind_map(lang).get(node_type)
    if kind is None:
        raise ValueError(f"미등록 symbol label: {lang}.{node_type}")
    return kind


@lru_cache(maxsize=1)
def control_structure_labels() -> frozenset[str]:
    """Return every control-node label declared by the language ontology."""
    languages = (_load_ontology().get("roles") or {}).get("control_structure") or {}
    return frozenset(
        node_type
        for node_types in languages.values()
        for node_type in (node_types or [])
    )


@lru_cache(maxsize=1)
def statement_effect_ops() -> dict[str, str]:
    """ontology ``statement_effect_ops`` — statement 라벨 → Rule 직접 효과 op.

    선언된 statement 는 branch_inventory 가 소유 행의 효과 보기로 노출한다.
    어휘 정합(EFFECT_FUNCTIONS 부분집합)은 드리프트 테스트가 감시한다.
    """
    data = _load_ontology().get("statement_effect_ops") or {}
    return {str(label): str(op) for label, op in data.items()}


@lru_cache(maxsize=1)
def dml_effect_ops() -> dict[str, str]:
    """ontology ``dml_effect_ops`` — DML 구문 종류 → Rule 직접 효과 op (spec 119).

    step5 링커가 DML 노드마다 만든 (테이블, dml_type) 사실을 rule 효과 어휘로 옮기는
    유일한 지점이다. 미선언 dml_type 의 처리는 **소비자가 좌표와 함께 실패**한다 —
    조용히 버리면 업무 효과가 소리 없이 사라진다(헌법 IV).
    """
    data = _load_ontology().get("dml_effect_ops") or {}
    return {str(dml_type).upper(): str(op) for dml_type, op in data.items()}


@lru_cache(maxsize=1)
def callsite_labels() -> frozenset[str]:
    """ontology ``callsite_labels`` — rule 증거의 호출 좌표를 제공하는 CodeNode 라벨.

    framework 는 parser 가 호출을 노드가 아닌 call_site 로 내보내므로 이 집합이 비어 있고,
    DBMS 는 ``CALL`` 이 실제 CodeNode 다. 모든 언어 값의 합집합을 돌려준다 —
    소비자는 언어를 분기하지 않는다(spec 119).
    """
    languages = _load_ontology().get("callsite_labels") or {}
    return frozenset(
        str(label)
        for labels in languages.values()
        for label in (labels or [])
    )


def unsupported_construct_patterns(lang: str) -> dict[str, str]:
    """ontology ``unsupported_constructs`` — 결정론 해석 불가 구문의 kind → 정규식.

    커버리지 잔차 감사가 장부로 센다. 값 추적을 흉내 내는 소비자는 금지다.
    """
    data = _load_ontology().get("unsupported_constructs") or {}
    entries = data.get(lang) or {}
    return {str(kind): str(pattern) for kind, pattern in entries.items()}


def statement_labels() -> frozenset[str]:
    """Return every structural-statement label declared by the language ontology.

    parser spec 016 이 emit 하는 RETURN/THROW/RAISE/BREAK/CONTINUE/GOTO/LABEL/ASSIGNMENT.
    routine 도 branch 도 아니므로 LLM 분석 대상에서 제외되고, branch_inventory 가
    종료행·반환식·대입의 AST 정본으로 소비한다.
    """
    languages = (_load_ontology().get("roles") or {}).get("statement") or {}
    return frozenset(
        node_type
        for node_types in languages.values()
        for node_type in (node_types or [])
    )


@lru_cache(maxsize=None)
def control_role_of(node_type: str) -> str:
    """Canonical parser control-node kind → language-neutral prompt role."""
    for role, node_types in (_load_ontology().get("control_roles") or {}).items():
        if node_type in (node_types or []):
            return role
    return "decision"


@lru_cache(maxsize=1)
def control_ownership_contracts() -> dict[str, str]:
    """Return canonical controller→grouped-child ownership pairs."""
    raw = _load_ontology().get("control_ownership") or {}
    if not isinstance(raw, dict):
        raise ValueError("control_ownership 는 mapping 이어야 한다")
    result: dict[str, str] = {}
    for controller, contract in raw.items():
        if not isinstance(contract, dict) or not str(contract.get("grouped_child") or ""):
            raise ValueError(f"control_ownership.{controller}.grouped_child 가 필요하다")
        result[str(controller)] = str(contract["grouped_child"])
    return result


class UnknownSourceLexiconError(RuntimeError):
    """어휘 구문 표가 없는 언어 — 조용한 폴백 대신 명시적 실패."""


def lang_of(path_suffix: str) -> str:
    """``Path.suffix`` → lexing 언어 키(parser 미지원 별칭 포함)."""
    suffix = path_suffix.lower().lstrip(".")
    lang = _SOURCE_LANG_BY_EXT.get(suffix)
    if lang is not None:
        return lang
    raise NoSupportedSourceError(
        f"지원하지 않는 소스 어휘 확장자: {path_suffix!r} — "
        "ontology_roles.yaml source_extensions 가 단일 정의처"
    )


@lru_cache(maxsize=None)
def source_lexicon(lang: str) -> dict:
    """언어의 주석·문자열·비활성블록 구문 표(데이터). 품질 게이트는 이것만 본다."""
    table = _load_ontology().get("source_lexicon") or {}
    entry = table.get(lang)
    if not entry:
        raise UnknownSourceLexiconError(
            f"source_lexicon 에 {lang!r} 없음 — ontology_roles.yaml 에 한 줄 추가해야 한다 "
            f"(등록됨: {sorted(table)})"
        )
    return entry


@lru_cache(maxsize=None)
def complement_owner_types(lang: str) -> frozenset[str]:
    """Canonical control types whose alternative arm may own a complement node.

    The ownership is language syntax, not a heuristic over node names or source identifiers.
    Every registered lexicon must declare it explicitly so a new language cannot silently
    inherit C/Python behavior.
    """
    raw = source_lexicon(lang).get("complement_owner_types")
    if not isinstance(raw, list):
        raise ValueError(
            f"source_lexicon.{lang}.complement_owner_types 는 list 여야 한다"
        )
    values = tuple(str(node_type).strip().upper() for node_type in raw)
    if any(not value for value in values) or len(values) != len(set(values)):
        raise ValueError(
            f"잘못되거나 중복된 complement owner type: {lang}.{raw!r}"
        )
    unknown = set(values) - set(control_structure_labels())
    if unknown:
        raise ValueError(
            f"미등록 complement owner type: {lang}.{sorted(unknown)}"
        )
    return frozenset(values)


@lru_cache(maxsize=None)
def alternative_clause_types(lang: str) -> frozenset[str]:
    """부모 판정의 **본문이 아니라 대안 절**인 canonical 제어 타입 (spec 118 §2.3).

    PL/SQL parser 는 ``ELSIF``/``ELSE`` 를 ``IF`` 의 자식으로 낸다(평면 형상). 이 사실이
    없으면 조상 사슬이 부모 조건을 "성립"으로 상속해 경로가 정반대가 된다
    (실측: `FN_EDIT_YN.sql:41,50,52`). C/Java/Python 은 ``else { if … }`` 중첩이라
    빈 목록이며, 빈 목록은 "해당 없음"의 **명시**다 — 미선언은 실패다.
    """
    raw = source_lexicon(lang).get("alternative_clause_types")
    if not isinstance(raw, list):
        raise ValueError(
            f"source_lexicon.{lang}.alternative_clause_types 는 list 여야 한다"
        )
    values = tuple(str(node_type).strip().upper() for node_type in raw)
    if any(not value for value in values) or len(values) != len(set(values)):
        raise ValueError(
            f"잘못되거나 중복된 alternative clause type: {lang}.{raw!r}"
        )
    unknown = set(values) - set(control_structure_labels())
    if unknown:
        raise ValueError(
            f"미등록 alternative clause type: {lang}.{sorted(unknown)}"
        )
    return frozenset(values)


@lru_cache(maxsize=1)
def _reference_inheritance_profiles() -> dict[str, dict]:
    profiles = _load_ontology().get("reference_inheritance") or {}
    if not isinstance(profiles, dict):
        raise ValueError("reference_inheritance 는 mapping 이어야 한다")
    known = set((_load_ontology().get("source_lexicon") or {}))
    extra = set(profiles) - known
    if extra:
        raise ValueError(f"미등록 언어의 reference_inheritance: {sorted(extra)}")
    allowed_fields = {"extends_type", "implements_types"}
    for lang, profile in profiles.items():
        fields = profile.get("ast_fields") if isinstance(profile, dict) else None
        if (
            not isinstance(fields, list)
            or not fields
            or any(str(field) not in allowed_fields for field in fields)
            or len(fields) != len(set(map(str, fields)))
        ):
            raise ValueError(
                f"reference_inheritance.{lang}.ast_fields 가 유효해야 한다"
            )
    return profiles


def reference_inheritance_profile(lang: str) -> dict | None:
    """Return declared parser-AST inheritance fields, or None for no such syntax."""
    return _reference_inheritance_profiles().get(lang)


def _external_linking_section() -> dict:
    section = _load_ontology().get("external_linking")
    if not isinstance(section, dict):
        raise ValueError("external_linking 은 mapping 이어야 한다")
    return section


@lru_cache(maxsize=1)
def external_resolvers() -> dict[str, tuple[int, ...]]:
    """Return validated resolver name → zero-based address argument positions."""
    raw = _external_linking_section().get("resolvers")
    if not isinstance(raw, dict) or not raw:
        raise ValueError("external_linking.resolvers 는 비어 있지 않은 mapping 이어야 한다")
    result: dict[str, tuple[int, ...]] = {}
    for raw_name, raw_positions in raw.items():
        name = str(raw_name).strip()
        if not name or name != str(raw_name):
            raise ValueError(f"잘못된 external resolver 이름: {raw_name!r}")
        if not isinstance(raw_positions, list) or not raw_positions:
            raise ValueError(f"external resolver 위치는 비어 있지 않은 list 여야 한다: {name}")
        if any(isinstance(value, bool) or not isinstance(value, int) or value < 0
               for value in raw_positions):
            raise ValueError(f"external resolver 위치는 0 이상의 정수여야 한다: {name}")
        if len(raw_positions) != len(set(raw_positions)):
            raise ValueError(f"중복 external resolver 위치: {name}")
        result[name] = tuple(raw_positions)
    return result


def _external_name_map(key: str) -> dict[str, frozenset[str]]:
    raw = _external_linking_section().get(key)
    if not isinstance(raw, dict) or not raw:
        raise ValueError(f"external_linking.{key} 는 비어 있지 않은 mapping 이어야 한다")
    result: dict[str, frozenset[str]] = {}
    for raw_owner, raw_names in raw.items():
        owner = str(raw_owner).strip().lower()
        if not owner or not isinstance(raw_names, list) or not raw_names:
            raise ValueError(f"external_linking.{key}.{raw_owner} 는 비어 있지 않은 list 여야 한다")
        names = [str(value).strip().lower() for value in raw_names]
        if any(not value for value in names) or len(names) != len(set(names)):
            raise ValueError(f"external_linking.{key}.{raw_owner} 에 빈 값 또는 중복이 있다")
        if owner in result:
            raise ValueError(f"external_linking.{key} owner 중복: {owner}")
        result[owner] = frozenset(names)
    return result


@lru_cache(maxsize=1)
def external_addressable_attributes() -> dict[str, frozenset[str]]:
    return _external_name_map("addressable_attributes")


@lru_cache(maxsize=1)
def external_addressable_child_text() -> dict[str, frozenset[str]]:
    return _external_name_map("addressable_child_text")


@lru_cache(maxsize=1)
def external_addressable_kinds() -> frozenset[str]:
    raw = _external_linking_section().get("addressable_kinds")
    if not isinstance(raw, list) or not raw:
        raise ValueError("external_linking.addressable_kinds 는 비어 있지 않은 list 여야 한다")
    values = [str(value).strip() for value in raw]
    if any(not value for value in values) or len(values) != len(set(values)):
        raise ValueError("external_linking.addressable_kinds 에 빈 값 또는 중복이 있다")
    return frozenset(values)


@lru_cache(maxsize=1)
def scanner_hints() -> tuple[tuple[str, ...], dict[str, frozenset[str]]]:
    """Return validated root manifests and category suffixes for Scan evidence."""
    section = _load_ontology().get("scanner_hints")
    if not isinstance(section, dict):
        raise ValueError("scanner_hints 는 mapping 이어야 한다")
    raw_manifests = section.get("manifests")
    if not isinstance(raw_manifests, list) or not raw_manifests:
        raise ValueError("scanner_hints.manifests 는 비어 있지 않은 list 여야 한다")
    manifests = tuple(str(value).strip() for value in raw_manifests)
    if (any(not value or Path(value).name != value for value in manifests)
            or len(manifests) != len(set(manifests))):
        raise ValueError("scanner_hints.manifests 는 중복 없는 루트 파일명이어야 한다")

    raw_categories = section.get("categories")
    if not isinstance(raw_categories, dict) or not raw_categories:
        raise ValueError("scanner_hints.categories 는 비어 있지 않은 mapping 이어야 한다")
    categories: dict[str, frozenset[str]] = {}
    owners: dict[str, str] = {}
    for raw_category, raw_extensions in raw_categories.items():
        category = str(raw_category).strip()
        if not category or not isinstance(raw_extensions, list) or not raw_extensions:
            raise ValueError(f"scanner_hints.categories.{raw_category} 는 비어 있지 않은 list 여야 한다")
        suffixes: set[str] = set()
        for raw_extension in raw_extensions:
            extension = str(raw_extension).strip().lower().lstrip(".")
            if not extension or "." in extension:
                raise ValueError(f"잘못된 scanner 확장자: {raw_extension!r}")
            suffix = f".{extension}"
            if suffix in suffixes:
                raise ValueError(f"scanner category 내부 확장자 중복: {category}.{extension}")
            previous = owners.get(suffix)
            if previous is not None:
                raise ValueError(f"scanner category 확장자 중복: {extension} ({previous}, {category})")
            suffixes.add(suffix)
            owners[suffix] = category
        categories[category] = frozenset(suffixes)
    return manifests, categories


@lru_cache(maxsize=1)
def external_file_categories() -> tuple[dict[str, str], dict[str, str]]:
    """Return validated suffix/name → external text inventory categories."""
    section = _load_ontology().get("external_file_categories")
    if not isinstance(section, dict) or not section:
        raise ValueError("external_file_categories 는 비어 있지 않은 mapping 이어야 한다")
    allowed_categories = frozenset({"artifact_xml", "config", "document"})
    suffix_categories: dict[str, str] = {}
    name_categories: dict[str, str] = {}
    for raw_category, raw_rules in section.items():
        category = str(raw_category).strip()
        if category not in allowed_categories or not isinstance(raw_rules, dict):
            raise ValueError(f"지원하지 않는 external file category: {raw_category!r}")
        raw_suffixes = raw_rules.get("suffixes")
        raw_names = raw_rules.get("exact_names")
        if not isinstance(raw_suffixes, list) or not isinstance(raw_names, list):
            raise ValueError(
                f"external_file_categories.{category} suffixes/exact_names 는 list 여야 한다"
            )
        if not raw_suffixes and not raw_names:
            raise ValueError(f"external_file_categories.{category} 규칙이 비어 있다")
        for raw_suffix in raw_suffixes:
            normalized = str(raw_suffix).strip().lower().lstrip(".")
            if not normalized or "." in normalized:
                raise ValueError(f"잘못된 external file 확장자: {raw_suffix!r}")
            suffix = f".{normalized}"
            previous = suffix_categories.get(suffix)
            if previous is not None:
                raise ValueError(
                    f"external file 확장자 중복: {suffix} ({previous}, {category})"
                )
            if suffix in FRAMEWORK_SUFFIXES or suffix in DBMS_SUFFIXES:
                raise ValueError(f"source/external file 확장자 중복: {suffix}")
            suffix_categories[suffix] = category
        for raw_name in raw_names:
            name = str(raw_name).strip().lower()
            if not name or Path(name).name != name:
                raise ValueError(f"잘못된 external file exact name: {raw_name!r}")
            previous = name_categories.get(name)
            if previous is not None:
                raise ValueError(
                    f"external file 이름 중복: {name} ({previous}, {category})"
                )
            name_categories[name] = category
    return suffix_categories, name_categories


@lru_cache(maxsize=1)
def _stereotype_values() -> dict[str, tuple[str, ...]]:
    data = _load_ontology()
    return {
        "framework": tuple(data.get("framework") or []),
        "dbms": tuple(data.get("dbms") or []),
    }


@lru_cache(maxsize=1)
def _stereotype_pools() -> dict[str, frozenset[str]]:
    return {
        strategy: frozenset(values)
        for strategy, values in _stereotype_values().items()
    }


def stereotype_values(strategy: str) -> tuple[str, ...]:
    """프롬프트와 parser가 함께 쓰는 순서 보존 폐쇄형 라벨 목록."""
    values = _stereotype_values().get(strategy)
    if values is None:
        raise ValueError(f"unknown stereotype strategy: {strategy!r}")
    return values


def stereotype_prompt_options(strategy: str) -> str:
    """ontology의 정확 라벨과 범용 정의를 prompt 선택표로 렌더링한다."""
    entries = _load_ontology().get(strategy) or {}
    if isinstance(entries, dict):
        return "\n".join(
            f"- `{value}`: {str(description).strip()}"
            for value, description in entries.items()
        )
    return "\n".join(f"- `{value}`" for value in stereotype_values(strategy))


def is_valid_stereotype(stereotype: str, strategy: str) -> bool:
    """LLM stereotype이 전략별 허용 목록에 속하는지 판정한다."""
    return not stereotype or stereotype in _stereotype_pools().get(strategy, frozenset())


def require_stereotype(stereotype: str, strategy: str) -> str:
    """Return one strategy-owned stereotype or fail without semantic erasure."""
    if not stereotype or not is_valid_stereotype(stereotype, strategy):
        raise ValueError(
            f"invalid {strategy} stereotype: {stereotype!r}; "
            f"allowed={list(stereotype_values(strategy))!r}"
        )
    return stereotype


@lru_cache(maxsize=1)
def _stereotype_rules() -> dict:
    return _load_ontology().get("stereotype_rules") or {}


def stereotype_priority_text() -> str:
    """동점 해소 우선순위의 프롬프트 렌더 — 데이터(ontology)가 단일 정의처다."""
    return " › ".join(str(v) for v in (_stereotype_rules().get("priority") or ()))


def exclude_contradicted_stereotype(
    stereotype: str, *, has_writes: bool
) -> tuple[str, str | None]:
    """결정론 근거와 모순인 stereotype 을 ``Unclassified``로 닫는다.

    실측(spec 116 7단계): 쓰기 근거가 있는 함수가 Query 류로 서술되는 오답은 의미
    판단이 아니라 사실 모순이다. 모순된 역할을 공개하지 않되, 계약된 ontology의
    불확정 값을 써서 summary/stereotype 결합 존재 규칙을 보존한다.
    """
    forbidden = set(_stereotype_rules().get("write_forbidden") or ())
    if has_writes and stereotype in forbidden:
        return "Unclassified", f"{stereotype} 는 결정론 쓰기 근거와 모순 (write_forbidden)"
    return stereotype, None


@lru_cache(maxsize=1)
def _module_stereotype_values() -> dict[str, tuple[str, ...]]:
    data = _load_ontology()
    return {
        "framework": tuple(data.get("module_framework") or []),
        "dbms": tuple(data.get("module_dbms") or []),
    }


def module_stereotype_values(strategy: str) -> tuple[str, ...]:
    """Legacy module role labels retained only so graph cleanup can remove them."""
    values = _module_stereotype_values().get(strategy)
    if values is None:
        raise ValueError(f"unknown module stereotype strategy: {strategy!r}")
    return values
