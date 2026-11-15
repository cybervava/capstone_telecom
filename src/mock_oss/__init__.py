"""Stand-in OSS/BSS gateway (mock REST APIs with fault injection).

This package simulates the client systems. It must never be imported by ``skylink_care``:
the agent system reaches it only over HTTP through typed adapters.
"""
