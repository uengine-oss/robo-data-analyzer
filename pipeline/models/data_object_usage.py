"""Common, provenance-preserving data-object usage evidence (spec 126)."""
from __future__ import annotations

import json
import hashlib
from dataclasses import dataclass, replace
from typing import Iterable, Literal, Mapping, Sequence


Access = Literal["READ", "WRITE"]
Resolution = Literal["unresolved", "exact"]
Producer = Literal["parser_ast", "linked_edges"]
OBJECT_REFERENCE_ID_PREFIX = "object-ref:"


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
class DataObjectUsageEvidence:
    source_id: str
    raw_reference: str
    name: str
    access: Access
    evidence_file: str
    evidence_line: int
    datasource: str = ""
    producer: Producer = "parser_ast"
    schema: str = ""
    schema_quoted: bool = False
    name_quoted: bool = False
    database_link: str = ""
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
            "schema": self.schema or None,
            "name": self.name,
            "schema_quoted": self.schema_quoted,
            "name_quoted": self.name_quoted,
            "database_link": self.database_link or None,
            "alias": self.alias or None,
            "access": self.access,
            "evidence_file": self.evidence_file,
            "evidence_line": self.evidence_line,
            "qualified_columns": [column.as_prompt_dict() for column in self.qualified_columns],
        }


def object_reference_id(datasource: str, usage: DataObjectUsageEvidence) -> str:
    identity = "\0".join((
        datasource.upper(), usage.schema.upper(), usage.name.upper(),
        usage.database_link.upper(), usage.raw_reference.upper(),
        str(usage.schema_quoted), str(usage.name_quoted),
    ))
    return f"{OBJECT_REFERENCE_ID_PREFIX}{hashlib.sha256(identity.encode('utf-8')).hexdigest()}"


def is_object_reference_id(value: str) -> bool:
    return str(value).startswith(OBJECT_REFERENCE_ID_PREFIX)


def usages_from_parser_ast(
    *,
    source_id: str,
    evidence_file: str,
    references: Sequence[Mapping[str, object]],
    qualified_columns: Sequence[Mapping[str, object]],
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
        access = _required_text(raw, "access").upper()
        if access not in ("READ", "WRITE"):
            raise ValueError(f"invalid parser data-object access: {access!r}")
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
            schema=str(raw.get("schema") or ""),
            schema_quoted=bool(raw.get("schemaQuoted", False)),
            name=name,
            name_quoted=bool(raw.get("nameQuoted", False)),
            database_link=str(raw.get("databaseLink") or ""),
            alias=str(raw.get("alias") or ""),
            access=access,  # type: ignore[arg-type]
            evidence_file=evidence_file,
            evidence_line=_positive_int(raw, "startLine"),
            qualified_columns=tuple(owned),
        ))
    unresolved = tuple(column for index, column in enumerate(columns) if index not in matched)
    return tuple(usages), unresolved


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


def exact_direct_table_ids(context: object, access: Access) -> tuple[str, ...]:
    """Return direct TABLE ids, excluding unresolved OBJECT_REFERENCE targets.

    Production contexts always carry ``data_object_usages``. The narrow fallback preserves
    older isolated tests and callers that construct AnalysisContext directly without running
    build_context_step.
    """
    extra = getattr(context, "extra", {}) or {}
    direct = (
        tuple(getattr(context, "direct_reads", ()) or ())
        if access == "READ"
        else tuple(getattr(context, "direct_writes", ()) or ())
    )
    if "data_object_usages" not in extra:
        return direct
    confirmed = {
        usage.table_id
        for usage in extra.get("data_object_usages") or ()
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
