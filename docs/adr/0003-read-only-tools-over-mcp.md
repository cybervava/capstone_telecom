# ADR 0003: The platform's read-only tools over the Model Context Protocol

Status: accepted, 25 November 2026, after the project review. The gateway behind it is the stand-in of assumption A1.

## Context

The review asked for MCP in the project. The Model Context Protocol is how an assistant is given tools by a server it did not write: the server declares each tool with a name, a description and a typed input, and any client that speaks the protocol can list them and call them.

The platform has such tools already, in its own code. The diagnostics agent decides what is known about a node, the troubleshooting agent finds the steps of a router's guide, the credit engine prices an outage. They are reached through typed adapters with one read-only key for each agent, and what matters most about them is what they refuse: a status that cannot be read is never an all-clear, steps are quoted and not invented, an amount is computed and not guessed. Two things follow for where MCP belongs.

- The language model in the customer's path has no tool, and that is a control. What it reads cannot make anything happen (threat model, LLM06). Putting MCP between that model and the enterprise systems would give it the one thing the design withholds.
- Other people's assistants have no such discipline. A contact-centre supervisor asking a desktop assistant "is there an outage on that node, and what would the credit be?" gets an answer made up from nothing unless the assistant can ask the systems that know. That is the case MCP exists for.

## Decision

The platform serves its read-only tools to MCP clients from a sixth process, `care-mcp`. The customer's path is unchanged and does not go through it.

1. **Four tools, each the platform's own rule.** `check_network_status` is the three-state decision of the diagnostics agent for one node, on a status read at that moment and the NOC feed. `search_router_guide` is the retrieval of the troubleshooting agent. `quote_service_credit` is the credit engine on the policy file. `platform_status` reads the feed's health, the queue and the guide index. No rule is written a second time for the protocol.
2. **What an answer may be used for travels with it.** `UNKNOWN` comes with the sentence that it is not an all-clear; a quotation says that nothing is applied; the server's instructions say that steps are to be given word for word with their source.
3. **Nothing of a customer, and nothing that acts.** No tool takes or returns a customer's id, name or line. The process holds no key that reads a customer or writes to an enterprise system: its one gateway key (`OSS_KEY_MCP`) has the scope `network:read`, and the gateway refuses it a line test. The executor remains the only writer.
4. **Limits are in the declarations and in code.** Every argument is typed and bounded where the tool is declared, so one that is out of bounds is refused before the tool runs. All clients together may make 60 calls a minute, readings of the two documents included. Every call that reaches a tool is in the audit trail with what was asked and how it ended, also one that failed or that the client abandoned; the words of a problem description are not kept, only their length. Of the calls refused for the limit, the first of a minute is in the trail and the rest are in the log, so that a client that keeps asking cannot make each refusal cost a write. An answer whose record cannot be written is not given.
5. **Closed unless a key is configured.** Over HTTP a client presents `CARE_MCP_KEY` as a bearer token, compared in constant time; with no key set the endpoint answers nobody. The guard names what is open (the health check) and asks the key of everything else, so a path it was never told about is closed too. A request that has the key is still turned away under a host name other than the loopback names. Over stdio there is no key: whoever starts the process is the client.
6. **It cannot stop the stack.** Both of its keys are optional where the stack is started. The AWS host's configuration binds it to the host's loopback, and its settings hold no key for it: there it is closed.

## What was decided against

| Alternative | Why not |
|---|---|
| Put the agents' calls to the enterprise systems through an MCP server | A hop and a second place to fail in the path that must answer during a surge, to give deterministic code a protocol made for models choosing tools. The adapters are already typed, scoped by key and rested by one circuit breaker |
| Give the language model in the customer's path its tools over MCP | It turns a reader into an actor. Every guarantee about what an injected message can achieve rests on that model having nothing to call |
| Expose write tools (apply a credit, book a visit) with a confirmation | A second writer beside the executor, outside the action ledger, the approval and the checks made again before a write. An assistant that wants something done can tell a person |
| Expose customer look-ups to staff assistants | Personal data would leave for whatever model the assistant runs on. The staff desk shows a case to a person who signed in; an assistant's provider is not that person |
| OAuth for the HTTP endpoint | The right answer for a deployment with real staff sign-in, which this demonstration does not have (staff sign in with one shared key; real sign-in is listed as not built). The SDK supports it; the server's own guard is a few lines that a token verifier replaces |

## What it costs

- A sixth process to run and a second key to keep. One key is shared by every client, so the trail names them all `mcp:client`.
- The tools answer for a node, a router model and figures the caller gives. An assistant that is asked about "my customer" has to be told the node and the router by a person who looked them up.
- `quote_service_credit` assumes an active service on an affected node and no earlier credit for the outage. The platform's own credit is worked out from Billing's record, which may say otherwise.
- The limit of 60 calls a minute is counted in the one process, like the API's limits.
- Like every process of the platform it connects to the database as the one role that owns the schema (threat model, "Out of scope"). It holds no key that reads a customer at the gateway; at the database nothing but its own code keeps it to the tables it reads.
- The status read behind `check_network_status` is recorded by the diagnostics agent's code under that agent's name (`agent:diagnostics`, `tool:network.node_status`), beside the client's own entry and joined to it by a correlation id that begins `mcp-`.

## Not built

- Sign-in for each person or assistant, and scopes for each tool.
- A route to it on the AWS host, and a key in that host's settings. Until then it is closed there.
- Prompts and subscriptions of the protocol; two resources are served (the policy file and the router models that have a guide).
- The customer-facing workflow does not call it, by decision.

## How it is shown

| Claim | Evidence |
|---|---|
| Each tool answers by the platform's rule: clear, outage, and unknown said not to be an all-clear | `tests/integration/test_mcp_server.py`, asked through the protocol by the SDK's own client |
| Arguments out of bounds never reach a tool; calls beyond the limit are refused, and a client that keeps asking cannot fill the trail; a call that reaches a tool is in the trail, without the words that were asked, also when it fails or is abandoned | the same tests |
| Its gateway key reads a node's status and is refused a line test | `tests/contract/test_adapters_against_mock.py`; every client against every route in `tests/mock_oss/test_auth.py` |
| Without the key nobody is answered, on any path but the health check and under any prefix; with it, only under a loopback name | `tests/unit/test_mcp_http.py` |
| It answers over HTTP on the running stack | `scripts/smoke.py`, journey 9 |
