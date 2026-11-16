"""What the platform reads of a customer, and what it does not.

The CRM hands over a customer's profile: name, phone, email, segment, status, consent and
services. Decisions (what a customer is told, whether a person takes over, whether a visit is
offered, whether a credit is owed) rest on the service, the network and the policy. This test
reads the source to hold that: the segment is read nowhere, and the name and the email address
are read in one place, to be masked.

It is a test of the code as written, not of behaviour: ``evals/test_segment_parity.py`` shows
the same by outcome.
"""

from __future__ import annotations

import ast
from pathlib import Path

import skylink_care
from skylink_care.adapters.models import CustomerProfile

PACKAGE = Path(skylink_care.__file__).parent
# Attributes a decision must not rest on. The first is the one the profile holds; the rest
# are named so that adding one to the profile fails this test before it is used.
PROTECTED = (
    "segment",
    "gender",
    "age",
    "date_of_birth",
    "religion",
    "caste",
    "language",
    "income",
    "nationality",
    "marital_status",
)


def read_in(attribute: str) -> set[str]:
    """The modules in which ``something.<attribute>`` is read or ``[...]["<attribute>"]``."""
    found = set()
    for path in sorted(PACKAGE.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            by_attribute = isinstance(node, ast.Attribute) and node.attr == attribute
            by_key = (
                isinstance(node, ast.Subscript)
                and isinstance(node.slice, ast.Constant)
                and node.slice.value == attribute
            )
            if by_attribute or by_key:
                found.add(path.relative_to(PACKAGE).as_posix())
    return found


def test_the_profile_holds_one_protected_attribute_and_no_other() -> None:
    held = set(CustomerProfile.model_fields)

    assert held == {
        "customer_id",
        "full_name",
        "phone",
        "email",
        "segment",
        "status",
        "consent",
        "services",
    }
    assert held & set(PROTECTED) == {"segment"}


def test_no_protected_attribute_is_read_anywhere() -> None:
    assert {attribute: read_in(attribute) for attribute in PROTECTED} == {
        attribute: set() for attribute in PROTECTED
    }


def test_the_name_and_the_email_address_are_read_only_to_be_masked() -> None:
    """``agents/triage.py`` turns them into the values the masking looks for before a model
    reads a message. Nothing else looks at them."""
    assert read_in("full_name") == {"agents/triage.py"}
    assert read_in("email") <= {"agents/triage.py"}
