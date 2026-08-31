"""Lossless Catalog table discovery over one immutable paged snapshot."""
from __future__ import annotations

from typing import Any, Optional

import httpx


SCHEMA_VERSION = "catalog-table-discovery-page/v1"


def _required_int(payload: dict[str, Any], name: str) -> int:
    value = payload.get(name)
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"catalog discovery page has invalid {name}")
    return value


def _optional_token(payload: dict[str, Any], name: str) -> str | None:
    value = payload.get(name)
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"catalog discovery page has invalid {name}")
    return value


def _identifier_key(value: str, quoted: object, field: str) -> str:
    if not isinstance(quoted, bool):
        raise ValueError(f"catalog discovery page has invalid {field}_quoted")
    normalized = value.strip() if quoted else value.strip().casefold()
    return f"{'q' if quoted else 'u'}:{normalized}"


def _table_identity(item: object) -> tuple[str, str, str]:
    if not isinstance(item, dict):
        raise ValueError("catalog discovery page table must be an object")
    if "catalog_name" in item:
        raise ValueError("catalog discovery page must use catalog, not catalog_name")
    name = item.get("name")
    schema = item.get("schema", "")
    catalog = item.get("catalog", "")
    if not isinstance(name, str) or not name.strip():
        raise ValueError("catalog discovery page table name is missing")
    if not isinstance(schema, str) or not isinstance(catalog, str):
        raise ValueError("catalog discovery page table identity is malformed")
    identity = (
        _identifier_key(catalog, item.get("catalog_quoted", False), "catalog"),
        _identifier_key(schema, item.get("schema_quoted", False), "schema"),
        _identifier_key(name, item.get("name_quoted", False), "name"),
    )
    columns = item.get("columns", [])
    sample_rows = item.get("sample_rows", [])
    if not isinstance(columns, list) or not isinstance(sample_rows, list):
        raise ValueError("catalog discovery page table detail is malformed")
    degraded = item.get("degraded", False)
    if not isinstance(degraded, bool):
        raise ValueError("catalog discovery page table degraded flag is malformed")
    if any(not isinstance(row, dict) for row in sample_rows):
        raise ValueError("catalog discovery page sample row must be an object")
    column_identities: set[str] = set()
    for column in columns:
        if not isinstance(column, dict):
            raise ValueError("catalog discovery page column must be an object")
        column_name = column.get("name")
        if not isinstance(column_name, str) or not column_name.strip():
            raise ValueError("catalog discovery page column name is missing")
        column_identity = _identifier_key(
            column_name, column.get("name_quoted", False), "column name",
        )
        if column_identity in column_identities:
            raise RuntimeError("catalog discovery returned a duplicate column identity")
        column_identities.add(column_identity)
    return identity


async def get_complete_table_discovery(
    http: httpx.AsyncClient,
    base_url: str,
    datasource: str,
    *,
    sample_limit: int = 5,
    page_size: int = 1000,
) -> Optional[dict[str, Any]]:
    """Read every deterministic page and return one verified complete inventory.

    A 404 is nullable only for the first request. Once a snapshot exists, any
    HTTP or contract failure aborts the analysis before a partial inventory can
    reach the merge/publication path.
    """
    if not isinstance(datasource, str) or not datasource.strip():
        raise ValueError("catalog discovery datasource must be non-empty")
    if isinstance(sample_limit, bool) or not isinstance(sample_limit, int) or sample_limit < 0:
        raise ValueError("catalog discovery sample_limit must be a non-negative integer")
    if isinstance(page_size, bool) or not isinstance(page_size, int) or page_size <= 0:
        raise ValueError("catalog discovery page_size must be a positive integer")
    url = f"{base_url}/tables/discovery"
    snapshot: str | None = None
    cursor: str | None = None
    requested_cursors: set[str] = set()
    identities: set[tuple[str, str, str]] = set()
    tables: list[dict[str, Any]] = []
    total_tables: int | None = None
    page_index = 0

    while True:
        params: dict[str, Any] = {
            "datasource": datasource,
            "sample_limit": sample_limit,
            "page_size": page_size,
        }
        if cursor is not None:
            if cursor in requested_cursors:
                raise RuntimeError("catalog discovery cursor cycle detected")
            requested_cursors.add(cursor)
            params["snapshot"] = snapshot
            params["cursor"] = cursor

        response = await http.get(url, params=params)
        if response.status_code == 404 and page_index == 0:
            return None
        if response.status_code != 200:
            raise RuntimeError(
                "catalog discovery page non-200 response "
                f"at page {page_index}: status={response.status_code}"
            )
        payload = response.json()
        if not isinstance(payload, dict):
            raise ValueError("catalog discovery page response must be an object")
        if payload.get("schema_version") != SCHEMA_VERSION:
            raise ValueError("catalog discovery page schema_version mismatch")
        if "truncated" in payload:
            raise ValueError("catalog discovery page must not use truncated prefixes")
        if payload.get("datasource") != datasource:
            raise ValueError("catalog discovery page datasource mismatch")

        current_snapshot = payload.get("snapshot")
        if not isinstance(current_snapshot, str) or not current_snapshot.strip():
            raise ValueError("catalog discovery page snapshot is missing")
        if snapshot is None:
            snapshot = current_snapshot
        elif current_snapshot != snapshot:
            raise RuntimeError("catalog discovery snapshot changed between pages")

        current_page = _required_int(payload, "page_index")
        if current_page != page_index:
            raise RuntimeError(
                "catalog discovery page index is not contiguous: "
                f"expected={page_index} actual={current_page}"
            )
        current_total = _required_int(payload, "total_tables")
        if total_tables is None:
            total_tables = current_total
        elif current_total != total_tables:
            raise RuntimeError("catalog discovery total_tables changed between pages")

        page_tables = payload.get("tables")
        if not isinstance(page_tables, list):
            raise ValueError("catalog discovery page tables must be a list")
        next_cursor = _optional_token(payload, "next_cursor")
        if not page_tables and len(tables) < total_tables:
            raise RuntimeError("catalog discovery returned an empty nonterminal page")

        for item in page_tables:
            identity = _table_identity(item)
            if identity in identities:
                raise RuntimeError("catalog discovery returned a duplicate table identity")
            identities.add(identity)
            tables.append(item)

        if len(tables) > total_tables:
            raise RuntimeError("catalog discovery returned more tables than total_tables")
        if len(tables) == total_tables:
            if next_cursor is not None:
                raise RuntimeError("catalog discovery terminal page has next_cursor")
            return {
                "schema_version": SCHEMA_VERSION,
                "datasource": datasource,
                "snapshot": snapshot,
                "total_tables": total_tables,
                "tables": tables,
            }
        if next_cursor is None:
            raise RuntimeError("catalog discovery closed before total_tables was reached")

        cursor = next_cursor
        page_index += 1
