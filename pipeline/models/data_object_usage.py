"""Common, provenance-preserving data-object usage evidence (spec 126)."""
from __future__ import annotations

import json
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Iterable, Literal, Mapping, Sequence

if TYPE_CHECKING:
    from pipeline.models.analysis_context import AnalysisContext


Access = Literal["READ", "WRITE"]
Resolution = Literal["unresolved", "exact"]
Producer = Literal["parser_ast", "linked_edges"]
DatabaseLinkRole = Literal["database", "domain", "connection_qualifier"]


@dataclass(frozen=True)
class QualifiedColumnUsage:
    raw_reference: str
    qualifier: str
    name: str
    evidence_line: int
    name_quoted: bool = False

    def as_prompt_dict(self) -> dict:
        return {
            "raw_reference": self.raw_reference,
            "qualifier": self.qualifier,
            "name": self.name,
            "evidence_line": self.evidence_line,
            "name_quoted": self.name_quoted,
        }


@dataclass(frozen=True)
class UnqualifiedIdentifierUsage:
    """Parser-owned SQL identifier syntax without a claimed physical table owner."""

    raw_reference: str
    name: str
    evidence_line: int
    name_quoted: bool = False

    def as_prompt_dict(self) -> dict:
        return {
            "raw_reference": self.raw_reference,
            "name": self.name,
            "evidence_line": self.evidence_line,
            "name_quoted": self.name_quoted,
        }


@dataclass(frozen=True)
class DatabaseLinkComponentEvidence:
    role: DatabaseLinkRole
    name: str
    name_quoted: bool = False

    def as_prompt_dict(self) -> dict:
        return {
            "role": self.role,
            "name": self.name,
            "name_quoted": self.name_quoted,
        }


@dataclass(frozen=True)
class DataObjectUsageEvidence:
    source_id: str
    raw_reference: str
    name: str
    access: Access
    evidence_file: str
    evidence_line: int
    datasource: str = ""
    producer: Producer = "parser_ast"
    catalog: str = ""
    catalog_quoted: bool = False
    schema: str = ""
    schema_quoted: bool = False
    name_quoted: bool = False
    database_link: str = ""
    database_link_components: tuple[DatabaseLinkComponentEvidence, ...] = ()
    alias: str = ""
    qualified_columns: tuple[QualifiedColumnUsage, ...] = ()
    table_id: str = ""
    resolution: Resolution = "unresolved"

    def resolved(self, table_id: str) -> "DataObjectUsageEvidence":
        return replace(self, table_id=table_id, resolution="exact")

    def as_prompt_dict(self) -> dict:
        return {
            "relation": "TARGET_ACCESSES_DATA_OBJECT",
            "table_id": self.table_id or None,
            "resolution": self.resolution,
            "producer": self.producer,
            "datasource": self.datasource or None,
            "raw_reference": self.raw_reference,
            "catalog": self.catalog or None,
            "schema": self.schema or None,
            "name": self.name,
            "catalog_quoted": self.catalog_quoted,
            "schema_quoted": self.schema_quoted,
            "name_quoted": self.name_quoted,
            "database_link": self.database_link or None,
            "database_link_components": [
                component.as_prompt_dict()
                for component in self.database_link_components
            ],
            "alias": self.alias or None,
            "access": self.access,
            "evidence_file": self.evidence_file,
            "evidence_line": self.evidence_line,
            "qualified_columns": [column.as_prompt_dict() for column in self.qualified_columns],
        }


def usages_from_parser_ast(
    *,
    source_id: str,
    evidence_file: str,
    references: Sequence[Mapping[str, object]],
    qualified_columns: Sequence[Mapping[str, object]],
    evidence_version: int = 1,
) -> tuple[tuple[DataObjectUsageEvidence, ...], tuple[QualifiedColumnUsage, ...]]:
    """Normalize parser spec 017 evidence and bind only exact visible qualifiers.

    The second result contains qualified references that cannot be assigned to an object owned
    by this node (for example a correlated outer alias). They remain explicit instead of being
    guessed onto a local table.
    """
    columns = tuple(_column(raw) for raw in qualified_columns)
    usages: list[DataObjectUsageEvidence] = []
    matched: set[int] = set()
    for raw in references:
        name = _required_text(raw, "name")
        catalog = str(raw.get("catalog") or "")
        catalog_quoted = bool(raw.get("catalogQuoted", False))
        if catalog and evidence_version < 4:
            raise ValueError(
                "catalog identity requires dataObjectEvidenceVersion >= 4 "
                f"at {evidence_file}:{_positive_int(raw, 'startLine')}"
            )
        if catalog_quoted and not catalog:
            raise ValueError(
                "catalogQuoted present without catalog "
                f"at {evidence_file}:{_positive_int(raw, 'startLine')}"
            )
        access = _required_text(raw, "access").upper()
        if access not in ("READ", "WRITE"):
            raise ValueError(f"invalid parser data-object access: {access!r}")
        database_link = str(raw.get("databaseLink") or "")
        database_link_components = _database_link_components(
            raw.get("databaseLinkComponents"),
        )
        if database_link and (
            evidence_version < 3 or not database_link_components
        ):
            raise ValueError(
                "databaseLinkComponents missing for database link "
                f"at {evidence_file}:{_positive_int(raw, 'startLine')} "
                f"(version={evidence_version})"
            )
        if not database_link and database_link_components:
            raise ValueError(
                "databaseLinkComponents present without databaseLink "
                f"at {evidence_file}:{_positive_int(raw, 'startLine')}"
            )
        accepted_qualifiers = {
            _canonical(name),
            _canonical(str(raw.get("alias") or "")),
        } - {""}
        owned: list[QualifiedColumnUsage] = []
        for index, column in enumerate(columns):
            local_qualifier = column.qualifier.rsplit(".", 1)[-1]
            if _canonical(column.qualifier) in accepted_qualifiers \
                    or _canonical(local_qualifier) in accepted_qualifiers:
                owned.append(column)
                matched.add(index)
        usages.append(DataObjectUsageEvidence(
            source_id=source_id,
            raw_reference=_required_text(raw, "rawReference"),
            catalog=catalog,
            catalog_quoted=catalog_quoted,
            schema=str(raw.get("schema") or ""),
            schema_quoted=bool(raw.get("schemaQuoted", False)),
            name=name,
            name_quoted=bool(raw.get("nameQuoted", False)),
            database_link=database_link,
            database_link_components=database_link_components,
            alias=str(raw.get("alias") or ""),
            access=access,  # type: ignore[arg-type]
            evidence_file=evidence_file,
            evidence_line=_positive_int(raw, "startLine"),
            qualified_columns=tuple(owned),
        ))
    unresolved = tuple(column for index, column in enumerate(columns) if index not in matched)
    return tuple(usages), unresolved


def _database_link_components(raw: object) -> tuple[DatabaseLinkComponentEvidence, ...]:
    if raw is None:
        return ()
    if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes)):
        raise ValueError("databaseLinkComponents must be an array")
    components: list[DatabaseLinkComponentEvidence] = []
    for item in raw:
        if not isinstance(item, Mapping):
            raise ValueError("databaseLinkComponents items must be objects")
        role = _required_text(item, "role")
        if role not in ("database", "domain", "connection_qualifier"):
            raise ValueError(f"invalid database-link component role: {role!r}")
        components.append(DatabaseLinkComponentEvidence(
            role=role,  # type: ignore[arg-type]
            name=_required_text(item, "name"),
            name_quoted=bool(item.get("nameQuoted", False)),
        ))
    roles = [component.role for component in components]
    if not roles or roles[0] != "database":
        raise ValueError("databaseLinkComponents must start with database")
    if any(role == "database" for role in roles[1:]):
        raise ValueError("databaseLinkComponents contains duplicate database")
    if "connection_qualifier" in roles[:-1] or roles.count("connection_qualifier") > 1:
        raise ValueError("databaseLinkComponents connection_qualifier must be last and unique")
    return tuple(components)


def unqualified_identifiers_from_parser_ast(
    references: Sequence[Mapping[str, object]],
) -> tuple[UnqualifiedIdentifierUsage, ...]:
    """Normalize parser evidence v2 without assigning any table ownership."""
    return tuple(
        UnqualifiedIdentifierUsage(
            raw_reference=_required_text(raw, "rawReference"),
            name=_required_text(raw, "name"),
            evidence_line=_positive_int(raw, "startLine"),
            name_quoted=bool(raw.get("nameQuoted", False)),
        )
        for raw in references
    )


def dump_data_object_usages(usages: Iterable[DataObjectUsageEvidence]) -> str:
    return json.dumps(
        [usage.as_prompt_dict() for usage in usages],
        ensure_ascii=False,
        separators=(",", ":"),
    )


def usages_from_linked_tables(
    *,
    source_id: str,
    edges: Iterable[object],
) -> tuple[DataObjectUsageEvidence, ...]:
    """Project authoritative Framework/legacy R/W occurrences without inventing coordinates."""
    result: list[DataObjectUsageEvidence] = []
    seen: set[tuple[str, str, str, int]] = set()
    for edge in edges:
        if str(getattr(edge, "source_id", "")) != source_id:
            continue
        relation = str(getattr(edge, "relation", "")).upper()
        if relation not in ("READS", "WRITES"):
            raise ValueError(f"invalid linked data-object relation: {relation!r}")
        table_id = str(getattr(edge, "target_id", "")).strip()
        evidence_file = str(getattr(edge, "evidence_file", "")).strip()
        evidence_line = int(getattr(edge, "evidence_line", 0) or 0)
        if not table_id or not evidence_file or evidence_line <= 0:
            raise ValueError(
                f"linked data-object evidence missing target/coordinate: "
                f"source={source_id!r} target={table_id!r} "
                f"file={evidence_file!r} line={evidence_line}"
            )
        access = "READ" if relation == "READS" else "WRITE"
        key = (table_id, access, evidence_file, evidence_line)
        if key in seen:
            continue
        seen.add(key)
        result.append(DataObjectUsageEvidence(
            source_id=source_id,
            raw_reference=table_id,
            name=table_id.rsplit(".", 1)[-1],
            access=access,  # type: ignore[arg-type]
            evidence_file=evidence_file,
            evidence_line=evidence_line,
            producer="linked_edges",
            table_id=table_id,
            resolution="exact",
        ))
    return tuple(result)


def exact_direct_table_ids(
    context: "AnalysisContext",
    access: Access,
) -> tuple[str, ...]:
    """Return direct physical Table IDs confirmed by Analyzer resolution."""
    direct = (
        context.direct_reads
        if access == "READ"
        else context.direct_writes
    )
    confirmed = {
        usage.table_id
        for usage in context.data_object_usages
        if usage.access == access and usage.resolution == "exact" and usage.table_id
    }
    return tuple(table_id for table_id in direct if table_id in confirmed)


def _column(raw: Mapping[str, object]) -> QualifiedColumnUsage:
    return QualifiedColumnUsage(
        raw_reference=_required_text(raw, "rawReference"),
        qualifier=_required_text(raw, "qualifier"),
        name=_required_text(raw, "name"),
        evidence_line=_positive_int(raw, "startLine"),
        name_quoted=bool(raw.get("nameQuoted", False)),
    )


def _required_text(raw: Mapping[str, object], key: str) -> str:
    value = str(raw.get(key) or "").strip()
    if not value:
        raise ValueError(f"parser data-object evidence missing {key}: {raw!r}")
    return value


def _positive_int(raw: Mapping[str, object], key: str) -> int:
    try:
        value = int(raw.get(key) or 0)
    except (TypeError, ValueError) as error:
        raise ValueError(f"parser data-object evidence invalid {key}: {raw!r}") from error
    if value <= 0:
        raise ValueError(f"parser data-object evidence non-positive {key}: {raw!r}")
    return value


def _canonical(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value.startswith('"') and value.endswith('"'):
        return f"Q:{value[1:-1].replace(chr(34) * 2, chr(34))}"
    return f"U:{value.upper()}"
