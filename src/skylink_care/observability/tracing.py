"""Traces of the workflow, which LangGraph sends to LangSmith when ``LANGSMITH_TRACING`` is set.

LangGraph sends them by itself, from the environment; no code here sends anything. What this
module adds is the default that keeps their contents on the platform: each step's inputs and
outputs (the conversation's state, what the customer wrote, the profile) are hidden unless the
environment says otherwise on purpose. The run tree, timings, errors and metadata still go.
"""

from __future__ import annotations

import os
from collections.abc import MutableMapping

# What LangSmith hides when set to "true". It reads each under either prefix, LANGSMITH_ first.
_HIDDEN = ("HIDE_INPUTS", "HIDE_OUTPUTS")
_PREFIXES = ("LANGSMITH", "LANGCHAIN")


def keep_trace_contents_private(environ: MutableMapping[str, str] | None = None) -> None:
    """Hide the inputs and outputs of every traced step, unless a setting already decides.

    LangSmith reads these when it makes its client, at the first trace, so this must run before
    a graph is invoked: ``build_graph`` calls it. A value set on purpose, ``false`` included, is
    left as it is.
    """
    env = os.environ if environ is None else environ
    for name in _HIDDEN:
        if not any(env.get(f"{prefix}_{name}", "").strip() for prefix in _PREFIXES):
            env[f"LANGSMITH_{name}"] = "true"
