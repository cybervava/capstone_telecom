"""Evaluations: scored runs of the real workflow against the in-process stand-in systems.

They use the same fixtures as the end-to-end tests (PostgreSQL, the mock gateway, the platform
with its workers) and are run on their own with ``make eval``, not with the test suite.
"""

from __future__ import annotations

import pytest

pytest_plugins = ["tests.support.postgres", "tests.support.stack", "tests.support.platform"]


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    for item in items:
        item.add_marker(pytest.mark.eval)
