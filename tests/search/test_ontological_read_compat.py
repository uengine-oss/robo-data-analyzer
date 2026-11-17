"""Ontological DB 읽기 호환 — startNode() 없이 관계 방향을 얻고, 문자열 벡터를 목록으로 읽는다.

원래 고침: 59ecb4f8 (feat/claude-code-bridge). main 의 새 search 구조에 맞게 다시 옮겼다.
"""
from __future__ import annotations

import unittest
from unittest.mock import patch

from search.corpus import node_detail
from search.corpus.embed_material import as_vector


class _Result:
    def __init__(self, rows):
        self._rows = rows

    def data(self):
        return list(self._rows)


class _Session:
    def __init__(self):
        self.queries: list[str] = []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def run(self, query, **params):
        self.queries.append(query)
        if "RETURN labels(node) AS labels" in query:
            return _Result([{
                "labels": ["FUNCTION"],
                "props": {"product_id": "f1", "name": "close", "summary": "s"},
            }])
        if "<-[rel]-" in query:
            return _Result([{
                "type": "CALLS", "direction": "in", "other_id": "f0",
                "other_name": "caller", "other_labels": ["FUNCTION"],
                "other_properties": {}, "properties": {},
            }])
        if "-[rel]->" in query:
            return _Result([{
                "type": "CALLS", "direction": "out", "other_id": "f2",
                "other_name": "callee", "other_labels": ["FUNCTION"],
                "other_properties": {}, "properties": {},
            }])
        return _Result([])


class _Driver:
    def __init__(self, session):
        self._session = session

    def session(self, database=None):
        return self._session

    def close(self):
        pass


class NodeDetailDirectionTest(unittest.TestCase):
    def test_directions_come_from_patterns_not_start_node(self):
        session = _Session()
        with patch.object(node_detail, "read_driver", return_value=(_Driver(session), "db")):
            detail = node_detail.read_node_detail("f1")

        self.assertFalse(any("startNode" in query for query in session.queries))
        callees = detail["node"]["linked_context"]["callees"]
        self.assertEqual([callee["id"] for callee in callees], ["f2"])


class AsVectorTest(unittest.TestCase):
    def test_string_and_list_vectors_read_the_same(self):
        self.assertEqual(as_vector("[0.5, 1, -2]"), [0.5, 1.0, -2.0])
        self.assertEqual(as_vector([0.5, 1, -2]), [0.5, 1.0, -2.0])


if __name__ == "__main__":
    unittest.main()
