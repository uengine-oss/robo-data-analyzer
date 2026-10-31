"""Graph client contract — retries (Ontological schema races) and the Ontological MATCH dialect."""

import unittest

from neo4j.exceptions import ClientError, ServiceUnavailable, TransientError

from graph.edges.calls import EXTERNAL_PACKAGE_QUERY, EXTERNAL_QUERY, INTERNAL_QUERY
from graph.neo4j_client import dialect_of_agent, for_ontological, is_retryable
from graph.node_upsert import merge_query


def _client_error(code: str, message: str) -> ClientError:
    return ClientError._hydrate_neo4j(code=code, message=message)


class Neo4jClientRetryTest(unittest.TestCase):
    def test_transport_and_transient_errors_are_retried(self) -> None:
        self.assertTrue(is_retryable(ServiceUnavailable("down")))
        self.assertTrue(is_retryable(TransientError("deadlock")))

    def test_ontological_schema_race_is_retried(self) -> None:
        # 다른 세션이 새 타입/속성을 만드는 순간 부모 라벨 MATCH 가 본 뷰·열이 사라진다 (실측 문구).
        for message in (
            'relation "og_data.v_117" does not exist',
            "column n2.p_v4 does not exist",
        ):
            with self.subTest(message=message):
                self.assertTrue(is_retryable(
                    _client_error("Neo.ClientError.Database.DatabaseNotFound", message)
                ))

    def test_real_contract_errors_are_not_retried(self) -> None:
        for code, message in (
            ("Neo.ClientError.Database.DatabaseNotFound", "graph 'analyzer_run' does not exist"),
            ("Neo.ClientError.Statement.ArgumentError",
             "cannot add label 'ANALYZER_OWNED' to this node: a node's type is part of its identifier here"),
            ("Neo.ClientError.Statement.SyntaxError", "expected a clause keyword at offset 24"),
        ):
            with self.subTest(message=message):
                self.assertFalse(is_retryable(_client_error(code, message)))
        self.assertFalse(is_retryable(ValueError('relation "og_data.v_1" does not exist')))


class OntologicalDialectTest(unittest.TestCase):
    """부모 라벨 MATCH 는 Ontological 에서 하위 타입 합집합 뷰를 탄다 — 무라벨로 바꿔 보낸다."""

    def test_parent_label_match_becomes_label_less(self) -> None:
        adapted = for_ontological(INTERNAL_QUERY)
        self.assertIn("MATCH (caller {id: item.caller_id, graph_owner:", adapted)
        self.assertIn("MATCH (callee {id: item.callee_id, graph_owner:", adapted)
        self.assertNotIn(":ANALYZER_OWNED", adapted)
        self.assertIn("MATCH (m {id: item.callee_module", for_ontological(EXTERNAL_PACKAGE_QUERY))
        self.assertEqual(
            "OPTIONAL MATCH (x {id: $id})",
            for_ontological("OPTIONAL MATCH (x:ANALYZER_OWNED {id: $id})"),
        )

    def test_merge_and_kind_qualified_match_keep_their_labels(self) -> None:
        # MERGE 는 만들 때 라벨 사슬을 다 줘야 한다 — 바꾸면 노드가 종류 없이 생긴다.
        self.assertEqual(merge_query("METHOD"), for_ontological(merge_query("METHOD")))
        adapted = for_ontological(EXTERNAL_QUERY)
        self.assertIn("MERGE (callee:ANALYZER_OWNED:FUNCTION {", adapted)
        self.assertIn("MERGE (m:ANALYZER_OWNED:MODULE {", adapted)
        kind_match = "MATCH (n:ANALYZER_OWNED:METHOD {id: $id}) RETURN n"
        self.assertEqual(kind_match, for_ontological(kind_match))

    def test_dialect_is_read_from_the_server_agent(self) -> None:
        self.assertEqual("neo4j", dialect_of_agent("Neo4j/5.26.0"))
        self.assertEqual("neo4j", dialect_of_agent(""))
        self.assertEqual("ontological", dialect_of_agent("Ontological/0.4.0"))


if __name__ == "__main__":
    unittest.main()
