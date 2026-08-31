from __future__ import annotations

import unittest
from typing import Any

from integrations.catalog_discovery import (
    SCHEMA_VERSION,
    get_complete_table_discovery,
)


class _Response:
    def __init__(self, status_code: int, payload: object = None) -> None:
        self.status_code = status_code
        self._payload = payload

    def json(self) -> object:
        return self._payload


class _Http:
    def __init__(self, responses: list[_Response]) -> None:
        self.responses = list(responses)
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def get(self, url: str, *, params: dict[str, Any]) -> _Response:
        self.calls.append((url, params))
        if not self.responses:
            raise AssertionError("unexpected discovery request")
        return self.responses.pop(0)


def _page(
    index: int,
    total: int,
    tables: list[dict[str, Any]],
    next_cursor: str | None,
    *,
    snapshot: str = "sealed-snapshot",
    datasource: str = "prod",
) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "datasource": datasource,
        "snapshot": snapshot,
        "page_index": index,
        "total_tables": total,
        "tables": tables,
        "next_cursor": next_cursor,
    }


class CatalogDiscoveryPagesTest(unittest.IsolatedAsyncioTestCase):
    async def test_reads_all_pages_from_one_snapshot_in_order(self) -> None:
        http = _Http([
            _Response(200, _page(0, 2, [{"schema": "A", "name": "T1"}], "next")),
            _Response(200, _page(1, 2, [{"schema": "A", "name": "T2"}], None)),
        ])

        result = await get_complete_table_discovery(
            http, "http://catalog/robo", "prod", sample_limit=3, page_size=1,
        )

        self.assertEqual(["T1", "T2"], [item["name"] for item in result["tables"]])
        self.assertEqual(2, result["total_tables"])
        self.assertNotIn("cursor", http.calls[0][1])
        self.assertEqual("sealed-snapshot", http.calls[1][1]["snapshot"])
        self.assertEqual("next", http.calls[1][1]["cursor"])

    async def test_zero_table_snapshot_closes_on_first_page(self) -> None:
        http = _Http([_Response(200, _page(0, 0, [], None))])
        result = await get_complete_table_discovery(http, "http://catalog", "prod")
        self.assertEqual([], result["tables"])
        self.assertEqual(1, len(http.calls))

    async def test_only_first_page_404_is_nullable(self) -> None:
        self.assertIsNone(await get_complete_table_discovery(
            _Http([_Response(404)]), "http://catalog", "prod",
        ))
        http = _Http([
            _Response(200, _page(0, 2, [{"name": "T1"}], "next")),
            _Response(404),
        ])
        with self.assertRaisesRegex(RuntimeError, "page 1"):
            await get_complete_table_discovery(http, "http://catalog", "prod")

    async def test_rejects_snapshot_page_and_total_drift(self) -> None:
        cases = {
            "snapshot": _page(1, 2, [{"name": "T2"}], None, snapshot="changed"),
            "page index": _page(2, 2, [{"name": "T2"}], None),
            "total_tables": _page(1, 3, [{"name": "T2"}], "more"),
        }
        for expected, second in cases.items():
            with self.subTest(expected=expected):
                http = _Http([
                    _Response(200, _page(0, 2, [{"name": "T1"}], "next")),
                    _Response(200, second),
                ])
                with self.assertRaisesRegex(RuntimeError, expected):
                    await get_complete_table_discovery(http, "http://catalog", "prod")

    async def test_rejects_duplicate_identity_case_insensitively(self) -> None:
        http = _Http([
            _Response(200, _page(0, 2, [{"schema": "PUBLIC", "name": "Orders"}], "n")),
            _Response(200, _page(1, 2, [{"schema": "public", "name": "ORDERS"}], None)),
        ])
        with self.assertRaisesRegex(RuntimeError, "duplicate table identity"):
            await get_complete_table_discovery(http, "http://catalog", "prod")

    async def test_preserves_distinct_quoted_case_identities(self) -> None:
        http = _Http([_Response(200, _page(0, 2, [
            {"name": "Orders", "name_quoted": True},
            {"name": "ORDERS", "name_quoted": True},
        ], None))])
        result = await get_complete_table_discovery(http, "http://catalog", "prod")
        self.assertEqual(["Orders", "ORDERS"], [item["name"] for item in result["tables"]])

    async def test_rejects_early_close_and_terminal_cursor(self) -> None:
        cases = [
            (_page(0, 2, [{"name": "T1"}], None), "closed before"),
            (_page(0, 1, [{"name": "T1"}], "extra"), "terminal page"),
            (_page(0, 1, [], "next"), "empty nonterminal"),
        ]
        for page, expected in cases:
            with self.subTest(expected=expected):
                with self.assertRaisesRegex(RuntimeError, expected):
                    await get_complete_table_discovery(
                        _Http([_Response(200, page)]), "http://catalog", "prod",
                    )

    async def test_rejects_cursor_cycle(self) -> None:
        http = _Http([
            _Response(200, _page(0, 3, [{"name": "T1"}], "same")),
            _Response(200, _page(1, 3, [{"name": "T2"}], "same")),
        ])
        with self.assertRaisesRegex(RuntimeError, "cursor cycle"):
            await get_complete_table_discovery(http, "http://catalog", "prod")

    async def test_rejects_legacy_or_malformed_payloads(self) -> None:
        payloads = [
            {"datasource": "prod", "tables": [], "truncated": 0},
            {**_page(0, 0, [], None), "truncated": 0},
            _page(0, 1, [{"name": ""}], None),
            _page(0, 1, ["T1"], None),
            _page(0, 1, [{"name": "T1", "columns": [{"name": ""}]}], None),
            _page(0, 1, [{"name": "T1", "columns": [
                {"name": "C"}, {"name": "c"},
            ]}], None),
            _page(0, 1, [{"name": "T1", "degraded": "false"}], None),
            _page(0, 1, [{"name": "T1", "sample_rows": ["row"]}], None),
            _page(0, 1, [{"name": "T1"}], None, datasource="other"),
        ]
        for payload in payloads:
            with self.subTest(payload=payload):
                with self.assertRaises((ValueError, RuntimeError)):
                    await get_complete_table_discovery(
                        _Http([_Response(200, payload)]), "http://catalog", "prod",
                    )

    async def test_rejects_invalid_request_controls_before_http(self) -> None:
        cases = [
            ("", 5, 1000),
            ("prod", -1, 1000),
            ("prod", 5, 0),
            ("prod", 5, True),
        ]
        for datasource, sample_limit, page_size in cases:
            with self.subTest(
                datasource=datasource, sample_limit=sample_limit, page_size=page_size,
            ):
                http = _Http([])
                with self.assertRaises(ValueError):
                    await get_complete_table_discovery(
                        http, "http://catalog", datasource,
                        sample_limit=sample_limit, page_size=page_size,
                    )
                self.assertEqual([], http.calls)


if __name__ == "__main__":
    unittest.main()
