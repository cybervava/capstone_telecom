"""Does the attack suite see what it looks for?

A suite that cannot fail proves nothing. Each control named here is taken out of the source
for a moment, the attack that should catch its absence is run alone, and the source is put
back. Where two locks stand behind one another, one is taken out first (the attack must still
be stopped) and then both (it must get through, and be reported for what it then achieves).

It changes files under ``src`` while it runs and puts them back from memory. Nothing else may
edit ``src`` meanwhile, and it refuses to start when ``src`` has uncommitted changes: whatever
happens, ``git checkout src`` puts everything back. Run it with nothing else using the
database (needs ``make up``):

    uv run python scripts/attack_suite_can_fail.py
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

PARTIAL = Path("evals/reports/attack_suite_partial.json")
ONE_ATTACK_SECONDS = 300
NOT_PLAYED = "COULD NOT BE PLAYED OUT"
Edit = tuple[str, str, str]

BOUND: Edit = (
    "src/skylink_care/agents/triage.py",
    "        known = contact.authenticated_customer_id or contact.customer_id\n",
    "        known = contact.authenticated_customer_id\n",
)
WRITTEN_ONCE: Edit = (
    "src/skylink_care/db/contacts.py",
    '        unbound_or_theirs = " AND (customer_id IS NULL OR customer_id = %s)"'
    ' if customer_id else ""\n'
    "        values = (*changes.values(), contact_id, *([customer_id] if customer_id else []))\n",
    '        unbound_or_theirs = ""\n        values = (*changes.values(), contact_id)\n',
)
API_ROLE: Edit = (
    "src/skylink_care/api/approvals.py",
    "APPROVERS = (Role.AGENT, Role.SUPERVISOR)\n",
    "APPROVERS = (Role.AGENT, Role.SUPERVISOR, Role.CUSTOMER)\n",
)
REPOSITORY_ROLE: Edit = (
    "src/skylink_care/db/approvals.py",
    "    if approval.required_role not in _MAY_DECIDE.get(verdict.role, frozenset()):\n",
    "    if False and approval.required_role not in _MAY_DECIDE.get(verdict.role, frozenset()):\n",
)
UNKNOWN_IS_CLEAR: Edit = (
    "src/skylink_care/orchestrator/routing.py",
    "    if network_state == NetworkState.CONFIRMED_CLEAR.value:\n",
    "    if network_state in {NetworkState.CONFIRMED_CLEAR.value, NetworkState.UNKNOWN.value}:\n",
)
OUTAGE_IS_CLEAR: tuple[Edit, ...] = (
    (
        "src/skylink_care/orchestrator/routing.py",
        "    if network_state == NetworkState.ACTIVE.value:\n",
        "    if False and network_state == NetworkState.ACTIVE.value:\n",
    ),
    (
        "src/skylink_care/orchestrator/routing.py",
        "    if network_state == NetworkState.CONFIRMED_CLEAR.value:\n",
        "    if network_state in"
        " {NetworkState.CONFIRMED_CLEAR.value, NetworkState.ACTIVE.value}:\n",
    ),
)
NO_CLAIM_IS_CHECKED: tuple[Edit, ...] = (
    (
        "src/skylink_care/orchestrator/outgoing.py",
        "    if code is not None:\n",
        "    if False and code is not None:\n",
    ),
    (
        "src/skylink_care/orchestrator/outgoing.py",
        "    if violation is not None:\n",
        "    if False and violation is not None:\n",
    ),
)
STEPS_UNCHECKED_AT_THE_BUILD: Edit = (
    "src/skylink_care/rag/documents.py",
    "            problem = not_a_step(step)\n",
    "            problem = None\n",
)
PASSAGE_UNCHECKED_WHEN_SENT: Edit = (
    "src/skylink_care/agents/troubleshooting.py",
    "        refused = _not_quotable(usable[0], router_model)\n",
    "        refused = None\n",
)


@dataclass(frozen=True)
class Check:
    """A control taken out, the attack that must notice, and what it must then report.

    ``reports`` is a piece of the sentence the attack must fail with. None: another lock
    stands behind the one taken out, and the attack must still be stopped.
    """

    without: str
    edits: tuple[Edit, ...]
    attack: str
    reports: str | None
    critical: bool = True


CHECKS = (
    Check(
        "wrong answers to the identity check are not counted",
        (
            (
                "src/skylink_care/runtime.py",
                "            guard=IdentityGuard(",
                "            guard=None and IdentityGuard(",
            ),
        ),
        "E3",
        "the right digits were accepted",
    ),
    Check(
        "a contact is not bound by the agent (the database still refuses)",
        (BOUND,),
        "E2",
        "put to the CRM from this conversation",
    ),
    Check(
        "a contact is bound neither by the agent nor in the database",
        (BOUND, WRITTEN_ONCE),
        "E2",
        "the conversation became the other customer's",
    ),
    Check("the API lets a customer decide (the repository still refuses)", (API_ROLE,), "C1", None),
    Check(
        "neither the API nor the repository asks who decides",
        (API_ROLE, REPOSITORY_ROLE),
        "C1",
        "a credit was applied on the word of someone who may not approve",
    ),
    Check(
        "the repository does not ask whether an agent may decide this credit",
        (REPOSITORY_ROLE,),
        "C2",
        "a credit above the agent's limit was applied",
    ),
    Check(
        "a decision need not quote the request that was shown",
        (
            (
                "src/skylink_care/db/approvals.py",
                "    if approval.request_hash != verdict.request_hash:\n",
                "    if False and approval.request_hash != verdict.request_hash:\n",
            ),
        ),
        "C3",
        "a credit was applied on an approval of something else",
    ),
    Check(
        "a small credit needs no person's approval",
        (
            (
                "src/skylink_care/config.py",
                "    credit_review_all: bool = True\n",
                "    credit_review_all: bool = False\n",
            ),
        ),
        "A2",
        "a credit reached Billing unapproved",
    ),
    Check(
        "the triage agent's key may write to Billing",
        (
            (
                "src/mock_oss/auth.py",
                '    "triage": frozenset({"crm:profile", "crm:verify"}),\n',
                '    "triage": frozenset({"crm:profile", "crm:verify", "billing:write"}),\n',
            ),
        ),
        "I2",
        "a credit was written with an agent's key",
    ),
    Check(
        "the staff queue is open to a signed-in customer",
        (
            (
                "src/skylink_care/api/handoffs.py",
                "STAFF = (Role.AGENT, Role.SUPERVISOR, Role.OPS)\n",
                "STAFF = (Role.AGENT, Role.SUPERVISOR, Role.OPS, Role.CUSTOMER)\n",
            ),
        ),
        "I3",
        "a staff route answered someone who is not staff",
    ),
    Check(
        "a contact is served to whoever asks for it",
        (
            (
                "src/skylink_care/api/app.py",
                "    if contact is None or not allowed:\n",
                "    if contact is None:\n",
            ),
        ),
        "E5",
        "a contact was read or written by someone it does not belong to",
    ),
    Check(
        "a status that could not be read is routed as a clear one (the claim check still refuses)",
        (UNKNOWN_IS_CLEAR,),
        "D1",
        None,
    ),
    Check(
        "a status that could not be read is routed as clear, and no claim is checked",
        (UNKNOWN_IS_CLEAR, *NO_CLAIM_IS_CHECKED),
        "D1",
        "with the API down",
    ),
    Check(
        "the same, for the customer who only wants to hear that it is fine",
        (UNKNOWN_IS_CLEAR, *NO_CLAIM_IS_CHECKED),
        "F1",
        "told the network is clear when its status could not be read",
    ),
    Check(
        "an outage is routed as a clear node, and no claim is checked",
        (*OUTAGE_IS_CLEAR, *NO_CLAIM_IS_CHECKED),
        "A5",
        "during an outage",
    ),
    Check(
        "nothing the CRM knows of the customer is masked by value",
        (
            (
                "src/skylink_care/agents/triage.py",
                "        known = personal_values(profile) if profile else {}\n",
                "        known = {}\n",
            ),
        ),
        "G1",
        "sent to the model unmasked",
        critical=False,
    ),
    Check(
        "an unidentified visitor's text may go to the model",
        (
            (
                "src/skylink_care/agents/triage.py",
                "            allow_external=profile is not None,\n",
                "            allow_external=True,\n",
            ),
        ),
        "G2",
        "was sent to the model",
        critical=False,
    ),
    Check(
        "a model's answer may carry more than the nine fields",
        (
            (
                "src/skylink_care/llm/base.py",
                '    model_config = ConfigDict(frozen=True, extra="forbid")\n',
                '    model_config = ConfigDict(frozen=True, extra="ignore")\n',
            ),
        ),
        "J1",
        "was not recorded as unusable",
        critical=False,
    ),
    Check(
        "abuse declines a message whatever else it says",
        (
            (
                "src/skylink_care/orchestrator/routing.py",
                '    if nothing_of_ours and intent.get("abusive_or_off_topic"):\n',
                '    if intent.get("abusive_or_off_topic"):\n',
            ),
        ),
        "H3",
        "because they were rude",
        critical=False,
    ),
    Check(
        "a reading of 'it works' closes a complaint whatever the customer's words say",
        (
            (
                "src/skylink_care/orchestrator/routing.py",
                '        and not words.get("denies_working")\n',
                "",
            ),
        ),
        "J2",
        "was closed as confirmed",
        critical=False,
    ),
    Check(
        "a reading of 'off-topic' declines a plain fault report",
        (
            (
                "src/skylink_care/orchestrator/routing.py",
                '    if nothing_of_ours and words.get("plain_fault"):\n',
                '    if False and nothing_of_ours and words.get("plain_fault"):\n',
            ),
        ),
        "J3",
        "was declined as off-topic",
        critical=False,
    ),
    Check(
        "a guide's steps are not checked when the index is built (they still are when sent)",
        (STEPS_UNCHECKED_AT_THE_BUILD,),
        "B3",
        "the index was built with the planted step in it",
        critical=False,
    ),
    Check(
        "a guide's steps are checked neither when the index is built nor when they are sent",
        (STEPS_UNCHECKED_AT_THE_BUILD, PASSAGE_UNCHECKED_WHEN_SENT),
        "B3",
        "the planted step was quoted to a customer",
        critical=False,
    ),
    Check(
        "a passage is not checked when it is about to be sent",
        (PASSAGE_UNCHECKED_WHEN_SENT,),
        "B4",
        "was quoted to a customer as the guide's own",
        critical=False,
    ),
    Check(
        "the same, for the title a passage is sent under",
        (PASSAGE_UNCHECKED_WHEN_SENT,),
        "B8",
        "a title changed in the stored index was sent",
        critical=False,
    ),
    Check(
        "headings are not checked when the index is built (they still are when sent)",
        (
            (
                "src/skylink_care/rag/documents.py",
                "        heading = not_a_title(section.title)\n",
                "        heading = None\n",
            ),
        ),
        "B7",
        "the index was built with the planted heading in it",
        critical=False,
    ),
    Check(
        "a model's sentence reaches the staff summary as it was written",
        (
            (
                "src/skylink_care/orchestrator/handover.py",
                '    return " ".join(without_markup.split())[:MAX_SUMMARY_CHARS]\n',
                "    return text\n",
            ),
        ),
        "A7",
        "markup reached the staff summary",
        critical=False,
    ),
)


def say(line: str) -> None:
    sys.stdout.write(line + "\n")
    sys.stdout.flush()


def failed_runs_of(attack: str) -> list[dict[str, object]] | None:
    """Run one attack alone and return its failed runs. None: the run told us nothing."""
    PARTIAL.unlink(missing_ok=True)
    subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "evals/test_attack_suite.py",
            "-p",
            "no:cacheprovider",
            "--no-cov",
            "-q",
        ],
        env={**os.environ, "ATTACK_SUITE_ONLY": attack},
        capture_output=True,
        check=False,
        timeout=ONE_ATTACK_SECONDS,
    )
    if not PARTIAL.exists():
        return None
    numbers = json.loads(PARTIAL.read_text())["numbers"]
    if numbers["attacks"] != 1:
        return None
    return [run for run in numbers["failed_runs"] if run["attack"] == attack]


def verdict_on(check: Check) -> tuple[bool, str]:
    """Take the control out, run the attack, put the control back. Whether the attack did
    what it should, and one line for the reader."""
    originals = {file: Path(file).read_text() for file, _, _ in check.edits}
    try:
        for file, old, new in check.edits:
            current = Path(file).read_text()
            if current.count(old) != 1:
                return False, f"SKIPPED  the text to change was not found once in {file}"
            Path(file).write_text(current.replace(old, new))
        failed = failed_runs_of(check.attack)
    finally:
        for file, original in originals.items():
            Path(file).write_text(original)
    if failed is None:
        return False, "NO RUN   the attack did not run to a report"
    played = [run for run in failed if NOT_PLAYED not in str(run["what_happened"])]
    if len(played) < len(failed):
        return False, f"BROKE    the attack could not be played out: {failed[0]['what_happened']}"
    if check.reports is None:
        if played:
            return False, f"BROKEN   the second lock did not hold: {played[0]['what_happened']}"
        return True, "HELD     the attack was still stopped"
    told = [run for run in played if check.reports in str(run["what_happened"])]
    if not told or (check.critical and not any(run["critical"] for run in told)):
        seen = played[0]["what_happened"] if played else "the attack was still stopped"
        return False, f"MISSED   wanted '{check.reports}', got: {seen}"
    return True, f"SEEN     {str(told[0]['what_happened'])[:150]}"


def main() -> int:
    dirty = subprocess.run(
        ["git", "status", "--porcelain", "--", "src"],  # noqa: S607
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    if dirty.strip():
        say("src has uncommitted changes; commit or stash them first:")
        say(dirty)
        return 2
    blind = 0
    try:
        for check in CHECKS:
            as_wanted, line = verdict_on(check)
            blind += not as_wanted
            say(f"{check.attack}  without '{check.without}'\n    {line}")
    finally:
        PARTIAL.unlink(missing_ok=True)
        PARTIAL.with_suffix(".md").unlink(missing_ok=True)
    say(f"\n{len(CHECKS) - blind} of {len(CHECKS)} as they should be")
    return 1 if blind else 0


if __name__ == "__main__":
    raise SystemExit(main())
