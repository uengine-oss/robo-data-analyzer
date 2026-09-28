"""분석 락의 오류 판별 회귀 테스트.

락 자체는 DB 가 있어야 시험할 수 있지만, **무엇을 락 충돌로 볼 것인가** 는 순수
함수다. 그리고 여기가 실제로 틀렸던 자리다.

처음 구현은 메시지에 "duplicate key" 가 있으면 락 충돌로 봤다. 그런데 이 graph 에
락 라벨이 처음 생기는 순간 두 세션이 동시에 들어오면, 게이트웨이가 PostgreSQL
내부 카탈로그 충돌을 그대로 올려 보낸다 —

    duplicate key value violates unique constraint "pg_type_typname_nsp_index"

이걸 락 충돌로 읽으면 **아무도 잡지 않은 락을 "잡혀 있다"고 보고**한다. 분석이
이유 없이 거절되고, 원인은 메시지에 안 남는다.
"""
from __future__ import annotations

from neo4j.exceptions import ClientError

from graph.analysis_lock import CONSTRAINT_NAME, _is_lock_conflict, _is_transient


def _error(message: str) -> ClientError:
    return ClientError(message)


class TestLockConflictClassification:
    def test_own_constraint_violation_is_a_lock_conflict(self):
        error = _error(
            "{neo4j_code: Neo.ClientError.Statement.ArgumentError} "
            f'{{message: duplicate key value violates unique constraint "uq_236_{CONSTRAINT_NAME}"}}'
        )
        assert _is_lock_conflict(error) is True

    def test_internal_catalog_race_is_not_a_lock_conflict(self):
        """이것이 원래의 버그다 — 'duplicate key' 만 보면 참으로 읽힌다."""
        error = _error(
            "{neo4j_code: Neo.ClientError.Statement.ArgumentError} "
            '{message: duplicate key value violates unique constraint "pg_type_typname_nsp_index"}'
        )
        assert _is_lock_conflict(error) is False
        assert _is_transient(error) is True

    def test_another_labels_unique_constraint_is_not_ours(self):
        """다른 라벨의 유일 제약을 우리 락으로 착각하면 안 된다."""
        error = _error(
            'duplicate key value violates unique constraint "uq_99_some_other_unique"'
        )
        assert _is_lock_conflict(error) is False

    def test_unrelated_error_is_neither(self):
        error = _error("{message: syntax error near RETURN}")
        assert _is_lock_conflict(error) is False
        assert _is_transient(error) is False


class TestTransientClassification:
    def test_pg_class_race_is_transient(self):
        assert _is_transient(_error('relation "pg_class" duplicate')) is True

    def test_concurrent_tuple_update_is_transient(self):
        assert _is_transient(_error("tuple concurrently updated")) is True

    def test_deadlock_is_transient(self):
        assert _is_transient(_error("deadlock detected")) is True

    def test_lock_conflict_is_not_transient(self):
        """락 충돌을 일시 오류로 읽으면 **남의 락을 기다리지 않고 재시도**한다."""
        error = _error(
            f'duplicate key value violates unique constraint "uq_236_{CONSTRAINT_NAME}"'
        )
        assert _is_transient(error) is False
