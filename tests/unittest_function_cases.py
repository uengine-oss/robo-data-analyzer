"""Register zero-argument module test functions with the canonical unittest runner."""
from __future__ import annotations

import asyncio
import inspect
import unittest
from collections.abc import Mapping


def load_function_cases(
    standard_tests: unittest.TestSuite,
    namespace: Mapping[str, object],
) -> unittest.TestSuite:
    """Add each top-level ``test_*`` function as a real unittest case."""
    functions = [
        (name, value)
        for name, value in sorted(namespace.items())
        if name.startswith("test_") and inspect.isfunction(value)
    ]
    if not functions:
        raise RuntimeError("module declares load_function_cases without test functions")
    for name, function in functions:
        if inspect.signature(function).parameters:
            raise TypeError(
                f"{function.__module__}.{name} requires fixture arguments; "
                "convert it to unittest setup before registration"
            )
        if inspect.iscoroutinefunction(function):
            def case(function=function):
                asyncio.run(function())
        else:
            case = function
        standard_tests.addTest(
            unittest.FunctionTestCase(case, description=name)
        )
    return standard_tests
