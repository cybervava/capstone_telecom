"""Session fixtures: generate one small dataset and one knowledge pack per test session."""

from __future__ import annotations

from pathlib import Path

import pytest

from skylink_datagen.cli import main
from tests.datagen.helpers import Dataset

SMALL_CUSTOMERS = 6000
SEED = 42


@pytest.fixture(scope="session")
def dataset_dir(tmp_path_factory: pytest.TempPathFactory) -> Path:
    out = tmp_path_factory.mktemp("generated")
    argv = ["generate", "--out", str(out), "--customers", str(SMALL_CUSTOMERS), "--seed", str(SEED)]
    assert main(argv) == 0
    return out


@pytest.fixture(scope="session")
def data(dataset_dir: Path) -> Dataset:
    return Dataset(dataset_dir)


@pytest.fixture(scope="session")
def knowledge_dir(tmp_path_factory: pytest.TempPathFactory) -> Path:
    out = tmp_path_factory.mktemp("knowledge")
    assert main(["knowledge", "--out", str(out), "--seed", str(SEED)]) == 0
    return out
