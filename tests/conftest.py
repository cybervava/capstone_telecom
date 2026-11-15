"""Shared test configuration: every test is marked by the directory it lives in."""

from __future__ import annotations

from pathlib import Path

import pytest

pytest_plugins = ["tests.support.postgres", "tests.support.stack", "tests.support.platform"]

_TESTS_ROOT = Path(__file__).resolve().parent
_MARK_BY_DIRECTORY = {
    "unit": "unit",
    "datagen": "unit",
    "mock_oss": "unit",
    "integration": "integration",
    "contract": "contract",
    "e2e": "e2e",
}


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    for item in items:
        try:
            top = Path(str(item.fspath)).resolve().relative_to(_TESTS_ROOT).parts[0]
        except ValueError:
            continue
        marker = _MARK_BY_DIRECTORY.get(top)
        if marker is not None:
            item.add_marker(getattr(pytest.mark, marker))
