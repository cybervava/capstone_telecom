"""Traces of the workflow keep their contents on the platform unless a setting asks for them."""

from __future__ import annotations

import inspect
import os

import pytest

from skylink_care.observability.tracing import keep_trace_contents_private
from skylink_care.orchestrator import graph


def test_inputs_and_outputs_are_hidden_when_nothing_is_set() -> None:
    env: dict[str, str] = {"LANGSMITH_TRACING": "true"}

    keep_trace_contents_private(env)

    assert env["LANGSMITH_HIDE_INPUTS"] == "true"
    assert env["LANGSMITH_HIDE_OUTPUTS"] == "true"


@pytest.mark.parametrize("prefix", ["LANGSMITH", "LANGCHAIN"])
def test_a_value_set_on_purpose_is_left_as_it_is(prefix: str) -> None:
    env = {f"{prefix}_HIDE_INPUTS": "false", f"{prefix}_HIDE_OUTPUTS": "false"}

    keep_trace_contents_private(env)

    assert env == {f"{prefix}_HIDE_INPUTS": "false", f"{prefix}_HIDE_OUTPUTS": "false"}


def test_a_blank_value_counts_as_unset_as_langsmith_reads_it() -> None:
    env = {"LANGSMITH_HIDE_INPUTS": " ", "LANGSMITH_HIDE_OUTPUTS": ""}

    keep_trace_contents_private(env)

    assert (env["LANGSMITH_HIDE_INPUTS"], env["LANGSMITH_HIDE_OUTPUTS"]) == ("true", "true")


def test_the_process_environment_is_what_it_changes_by_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Set blank (which counts as unset) rather than deleted, so that monkeypatch puts back
    # whatever was there before when the test ends.
    for name in ("HIDE_INPUTS", "HIDE_OUTPUTS"):
        monkeypatch.setenv(f"LANGSMITH_{name}", "")
        monkeypatch.setenv(f"LANGCHAIN_{name}", "")

    keep_trace_contents_private()

    assert os.environ["LANGSMITH_HIDE_INPUTS"] == os.environ["LANGSMITH_HIDE_OUTPUTS"] == "true"


def test_the_graph_is_built_with_the_default_in_place() -> None:
    """Whatever builds the graph, the worker, a test or an evaluation, gets the default before
    anything can be traced."""
    source = inspect.getsource(graph.build_graph)

    assert "keep_trace_contents_private()" in source.split("nodes = Nodes(runtime)")[0]
