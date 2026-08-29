"""노드 식별자 단일진실 — **네임스페이스 + 위치 기반** (spec 047).

★ 왜 네임스페이스인가 (실제로 터진 사고)
─────────────────────────────────────────
종전엔 DB 세계와 코드 세계가 **구분 표식 없이 같은 점 표기**를 썼다:

    테이블 cart               → "cart"               ┐ 같은 id!
    파일   cart.c             → "cart"               ┘
    컬럼   member.member_id   → "member.member_id"   ┐ 같은 id!
    구조체 필드 member_id     → "member.member_id"   ┘

Neo4j 의 UNIQUE 제약은 **라벨별**이라 이걸 못 막는다. 그 결과 무라벨
``MATCH (n {id: …})`` 가 두 노드를 **모두** 잡아 잘못된 엣지(TABLE→HAS_MEMBER→코드노드
144개)와 라벨(:SERVICE 5개)이 박혔고, 프론트는 두 노드를 하나로 합쳐 그렸다.

**해법**: 접두사 ``code:`` / ``db:`` 로 두 세계를 가른다. 구분자 ``:`` 는
**파일명(Windows 금지)에도 SQL/C 식별자에도 들어갈 수 없다** → 우연히 안 겹치는 게 아니라
**구조적으로 겹칠 수 없다.**

★ 왜 위치 기반인가
──────────────────
유일성은 **지어내는** 게 아니라 **이미 유일한 것에서 가져오는** 것이다:

- **파일시스템**이 파일 경로의 유일성을 보장한다 → 경로를 **확장자까지** 담는다
  (종전엔 확장자를 버려 ``foo.c`` 와 ``foo.h`` 가 같은 id 가 됐다).
- **언어**가 "한 컨테이너 안 같은 이름 금지"를 보장한다 → 컨테이너 경로를 담는다
  (종전엔 구조체 이름이 빠져 구조체 필드와 전역변수가 겹쳤다).
- **DB**가 db·스키마·테이블·컬럼의 유일성을 보장한다.

★ id 는 **불투명한 열쇠**다 (spec 047 FR-006)
────────────────────────────────────────────
**id 를 잘라 부모·이름을 알아내지 마라.** 소속은 노드 속성(``owner_id``)이나
관계(``HAS_COLUMN``/``HAS_MEMBER``)로 찾는다. id 를 뜯는 순간 id 규칙에 묶여
겹침을 영원히 못 고친다 (그게 이 스펙 이전의 상태였다).
"""
from __future__ import annotations

import hashlib
import unicodedata
from pathlib import Path, PurePosixPath, PureWindowsPath
from urllib.parse import quote


# ── 네임스페이스 ────────────────────────────────────────────────
# ':' 는 파일명(Windows 금지)·SQL/C 식별자에 들어갈 수 없다 → 두 세계가 섞일 방법이 없다.
CODE_NS = "code:"
DB_NS = "db:"
FILE_NS = "file:"
EXTERNAL_NS = "external:"

# 컨테이너 경로 구분자 (파일 → 구조체 → 필드).
SEP = ":"

# 동명 충돌(Java 오버로드) 시 붙이는 위치 접미 — **경로 구분자와 달라야** 한다.
POS = "@"


def _normalized_text(value: object, field_name: str) -> str:
    text = unicodedata.normalize("NFC", str(value)).strip()
    if not text or "\x00" in text:
        raise ValueError(f"{field_name} must be non-empty and contain no NUL")
    return text


def _relative_posix_path(rel_path: str) -> str:
    raw = _normalized_text(rel_path, "rel_path").replace("\\", "/")
    if PurePosixPath(raw).is_absolute() or PureWindowsPath(raw).is_absolute() or PureWindowsPath(raw).drive:
        raise ValueError(f"rel_path must be relative: {rel_path!r}")
    parts = [part for part in raw.split("/") if part not in ("", ".")]
    if not parts or any(part == ".." for part in parts):
        raise ValueError(f"rel_path escapes the project root: {rel_path!r}")
    return "/".join(parts)


def _semantic_hash(*parts: str) -> str:
    payload = "".join(f"{len(part)}:{part}" for part in parts).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def project_file_id(project: str, rel_path: str) -> str:
    """Return the stable file identity for one project-relative path."""
    project_name = _normalized_text(project, "project")
    relative = _relative_posix_path(rel_path)
    return f"{FILE_NS}{quote(project_name, safe='')}/{quote(relative, safe='/')}"


def external_chunk_id(file_id: str, char_start: int, char_end: int) -> str:
    """Return the span-scoped identity of one external semantic chunk (spec 078)."""
    fid = _normalized_text(file_id, "file_id")
    if not fid.startswith(FILE_NS):
        raise ValueError(f"file_id must use {FILE_NS!r} namespace")
    for name, value in (("char_start", char_start), ("char_end", char_end)):
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise ValueError(f"{name} must be a non-negative integer")
    if char_end <= char_start:
        raise ValueError("char_end must be greater than char_start")
    return f"{fid}:chunk:{char_start}-{char_end}"


def derived_chunk_id(file_id: str, ordinal: int) -> str:
    """Identity for a derived-document chunk, which has no source span (spec 078)."""
    fid = _normalized_text(file_id, "file_id")
    if not fid.startswith(FILE_NS):
        raise ValueError(f"file_id must use {FILE_NS!r} namespace")
    if isinstance(ordinal, bool) or not isinstance(ordinal, int) or ordinal < 0:
        raise ValueError("ordinal must be a non-negative integer")
    return f"{fid}:chunk:derived-{ordinal}"


def published_external_chunk_id(
    source_file_id: str,
    external_key: str,
    content_sha256: str,
) -> str:
    """Identity of one published exact-key external source record."""
    file_id = _normalized_text(source_file_id, "source_file_id")
    key = _normalized_text(external_key, "external_key")
    digest = _normalized_text(content_sha256, "content_sha256").lower()
    if len(digest) != 64 or any(char not in "0123456789abcdef" for char in digest):
        raise ValueError("content_sha256 must be a lowercase SHA-256 digest")
    return f"{EXTERNAL_NS}{_semantic_hash(file_id, key, digest)}"


def fqn(*parts: object) -> str:
    """비어있지 않은 조각만 ``.`` 으로 이어 붙인다 (빈 db·스키마 등은 생략).

    DB 세계 내부의 점 표기 전용 헬퍼. **네임스페이스는 붙이지 않는다** —
    뿌리(스키마·테이블)는 ``db_id`` 를 쓰고, 그 아래(컬럼)는 이미 접두사가 붙은
    부모 id 를 첫 조각으로 넘긴다.

    fqn("", "RWIS", "RDD01DD_TB")   -> "RWIS.RDD01DD_TB"
    fqn("db:member", "member_id")   -> "db:member.member_id"
    """
    segs = [str(p).strip() for p in parts if p is not None]
    return ".".join(s for s in segs if s)


def db_id(*parts: object) -> str:
    """DB 노드 id — ``db:`` 네임스페이스 + ``db.schema.name`` (빈 조각 생략).

    db_id("", "", "cart")            -> "db:cart"
    db_id("prod", "PUBLIC", "USERS") -> "db:prod.PUBLIC.USERS"
    """
    return DB_NS + fqn(*parts)


def sql_identifier_id_part(value: object, quoted: bool) -> str:
    """Canonical opaque ID segment for one SQL identifier."""
    text = unicodedata.normalize("NFC", str(value or "").strip())
    if quoted and len(text) >= 2 and text.startswith('"') and text.endswith('"'):
        text = text[1:-1].replace('""', '"')
    canonical = text if quoted else text.upper()
    encoded = quote(canonical, safe="").replace(".", "%2E")
    return f"Q~{encoded}" if quoted else encoded


def sql_schema_id(
    db: object,
    schema: object,
    schema_quoted: bool = False,
    *,
    catalog: object = "",
    catalog_quoted: bool = False,
) -> str:
    return db_id(
        db,
        sql_identifier_id_part(catalog, catalog_quoted)
        if str(catalog or "").strip() else "",
        sql_identifier_id_part(schema, schema_quoted),
    )


def sql_table_id(
    db: object,
    schema: object,
    table: object,
    *,
    schema_quoted: bool = False,
    table_quoted: bool = False,
    catalog: object = "",
    catalog_quoted: bool = False,
) -> str:
    return db_id(
        db,
        sql_identifier_id_part(catalog, catalog_quoted)
        if str(catalog or "").strip() else "",
        sql_identifier_id_part(schema, schema_quoted) if str(schema or "").strip() else "",
        sql_identifier_id_part(table, table_quoted),
    )


def sql_column_id(table_id: str, column: object, column_quoted: bool = False) -> str:
    return fqn(table_id, sql_identifier_id_part(column, column_quoted))


def code_file_id(rel_path: str) -> str:
    """소스 파일 노드 id — ``code:`` + **확장자를 포함한** 소스 상대경로.

    파일시스템이 경로의 유일성을 보장하므로 그걸 그대로 쓴다.
    (종전엔 확장자를 버려 ``foo.c`` 와 ``foo.h`` 가 같은 id 가 됐다.)

    code_file_id("cart.c")        -> "code:cart.c"
    code_file_id("sub/dir/foo.h") -> "code:sub/dir/foo.h"
    """
    rel = Path(str(rel_path).replace("\\", "/")).as_posix().strip("/")
    return CODE_NS + rel


def code_ns_id(namespace: str) -> str:
    """네임스페이스형 코드 컨테이너 id (Java 패키지 등) — ``code:com.example``.

    Java 는 파일이 아니라 **패키지+클래스**가 유일성의 원천이다 (언어가 보장).
    빈 네임스페이스(default 패키지)면 ``code:`` 만 남고, 자식은 ``child_id`` 가 이어붙인다.
    """
    return CODE_NS + str(namespace).strip()


def child_id(container_id: str, name: str) -> str:
    """컨테이너 안의 심볼 id — ``<컨테이너>:<이름>``.

    컨테이너 경로가 끝까지 반영된다::

        파일         code:member.c
        구조체       code:member.c:member_row_t
        구조체 필드  code:member.c:member_row_t:member_id
        전역변수     code:member.c:member_id            ← 필드와 안 겹친다
    """
    n = str(name).strip()
    if not container_id:
        return CODE_NS + n
    return f"{container_id}{SEP}{n}"


def positioned(base: str, start_line: int) -> str:
    """동명 충돌 시 붙이는 위치 접미 — ``<base>@<줄>``.

    Java 오버로드처럼 **같은 컨테이너에 같은 이름**이 합법인 경우에만 필요하다.
    접미 구분자 ``@`` 는 경로 구분자 ``:`` 와 달라서 경로 해석을 흐리지 않는다.
    """
    return f"{base}{POS}{start_line}"


def external_id(kind: str, value: object) -> str:
    """Return an opaque, namespaced id for an object outside the analysis scope."""
    encoded = quote(str(value).strip(), safe="")
    return f"{CODE_NS}external{SEP}{kind.strip().lower()}{SEP}{encoded}"
