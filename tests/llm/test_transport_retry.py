from __future__ import annotations

import unittest

from llm.runners.transport_retry import is_transient_transport_error


class RemoteProtocolError(Exception):
    pass


class LengthFinishReasonError(Exception):
    pass


class RepetitionLoopAborted(Exception):
    pass


class StatusError(Exception):
    def __init__(self, status_code: int) -> None:
        self.status_code = status_code


class TransportRetryPolicyTest(unittest.TestCase):
    def test_transient_network_and_server_failures_retry(self):
        self.assertTrue(is_transient_transport_error(RemoteProtocolError()))
        self.assertTrue(is_transient_transport_error(StatusError(429)))
        self.assertTrue(is_transient_transport_error(StatusError(503)))

    def test_semantic_and_non_transient_failures_do_not_retry(self):
        self.assertFalse(is_transient_transport_error(LengthFinishReasonError()))
        self.assertFalse(is_transient_transport_error(RepetitionLoopAborted()))
        self.assertFalse(is_transient_transport_error(ValueError("schema")))
        self.assertFalse(is_transient_transport_error(StatusError(401)))


if __name__ == "__main__":
    unittest.main()
