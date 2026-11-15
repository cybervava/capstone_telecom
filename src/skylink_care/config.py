"""Runtime settings, read from the environment (and from ``.env`` in development).

Secrets are ``SecretStr`` so they cannot leak through ``repr`` or logs. Each process validates at
start-up that the secrets *it* needs are present, and reads no others.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal, Self

from psycopg.conninfo import make_conninfo
from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

AgentIdentity = Literal[
    "triage", "diagnostics", "resolution", "dispatch", "notification", "ingest", "executor"
]


class MissingSecretError(RuntimeError):
    """A secret this process needs is absent. Raised at start-up, never mid-request."""


class Settings(BaseSettings):
    # Environment only. Locally, `uv run --env-file …` supplies it; nothing is read implicitly
    # from disk, so a process holds exactly the settings it was started with.
    model_config = SettingsConfigDict(env_prefix="CARE_", extra="ignore", frozen=True)

    # Fails closed: development conveniences exist only when this is set to "dev" on purpose.
    env: Literal["dev", "test", "prod"] = "prod"

    db_host: str = "127.0.0.1"
    db_port: int = 55432
    db_name: str = "care"
    db_user: str = "care"
    db_password: SecretStr
    db_pool_max: int = Field(default=10, ge=1, le=50)

    # Only the API process signs and checks tokens; workers run without it.
    jwt_secret: SecretStr | None = None
    api_host: str = "127.0.0.1"
    api_port: int = 8080

    oss_base_url: str = "http://127.0.0.1:8081"

    credit_policy_path: Path = Path("data/policies/credit_policy.v0.yaml")
    # The knowledge pack; its router guides are indexed with `care-admin index-guides`.
    knowledge_dir: Path = Path("data/knowledge")
    credit_review_all: bool = True
    # A credit nobody approves or rejects within this time ends unapplied and goes to a person.
    approval_ttl_seconds: int = Field(default=72 * 3600, gt=0)

    status_max_age_seconds: int = Field(default=60, gt=0)
    feed_max_age_seconds: int = Field(default=90, gt=0)
    # This many lines failing their test on one node is treated as a node fault,
    # counting each line's latest test within this window.
    line_test_cluster_threshold: int = Field(default=3, ge=2)
    line_test_cluster_window_seconds: int = Field(default=900, gt=0)
    # A line is not tested again sooner than this: a test exercises real equipment.
    line_retest_seconds: int = Field(default=120, ge=0)
    # Dispatch waits this long after the first failed line test: the NOC detection window.
    dispatch_hold_seconds: int = Field(default=600, ge=0)
    # A technician visit is offered with this many of the earliest free slots.
    visit_slots_offered: int = Field(default=3, ge=1, le=6)
    # A visit rests on a failing line test no older than this when it is proposed (seconds),
    # and no older than the second value when the executor books it. Past that nothing is
    # booked: a visit is never arranged on evidence that has gone stale.
    visit_line_test_max_age_seconds: int = Field(default=300, gt=0)
    visit_evidence_max_age_seconds: int = Field(default=1800, gt=0)

    # A contact whose network status could not be verified is looked at again this often, and
    # handed to a person after this many looks without an answer.
    network_recheck_seconds: int = Field(default=60, gt=0)
    network_recheck_limit: int = Field(default=5, ge=1)
    # A customer told about a declared outage normally hears of its end through the event feed.
    # The live status is looked at this often as well, so a lost feed event cannot strand them.
    outage_recheck_seconds: int = Field(default=900, gt=0)
    # A suspected node fault gets this long to be declared or to clear before a person takes the
    # contact. It is longer than the NOC normally needs to declare an outage.
    suspected_wait_seconds: int = Field(default=900, gt=0)
    # After "service restored", a contact the customer does not answer is closed after this long.
    close_quiet_seconds: int = Field(default=86_400, gt=0)
    # "An outage has started" is not sent for a feed event older than this (the consumer was
    # down or is catching up): the news would arrive stale, possibly after the outage ended.
    notify_max_event_age_seconds: int = Field(default=900, gt=0)

    llm_provider: Literal["offline", "anthropic"] = "offline"
    llm_model: str = "claude-opus-5-5"

    @model_validator(mode="after")
    def _windows_fit_together(self) -> Self:
        # A line held for the whole hold must still be inside the window in which failing lines
        # on its node are counted, or a real cluster could go unseen.
        if self.line_test_cluster_window_seconds < self.dispatch_hold_seconds:
            raise ValueError(
                "CARE_LINE_TEST_CLUSTER_WINDOW_SECONDS must not be shorter than"
                " CARE_DISPATCH_HOLD_SECONDS"
            )
        if self.visit_evidence_max_age_seconds < self.visit_line_test_max_age_seconds:
            raise ValueError(
                "CARE_VISIT_EVIDENCE_MAX_AGE_SECONDS must not be shorter than"
                " CARE_VISIT_LINE_TEST_MAX_AGE_SECONDS"
            )
        return self

    def require_jwt_secret(self) -> str:
        if self.jwt_secret is None or not self.jwt_secret.get_secret_value():
            raise MissingSecretError("CARE_JWT_SECRET is not set")
        return self.jwt_secret.get_secret_value()

    def database_conninfo(self, *, dbname: str | None = None) -> str:
        """libpq connection string; values are quoted, so any password character is safe."""
        return make_conninfo(
            host=self.db_host,
            port=self.db_port,
            dbname=dbname or self.db_name,
            user=self.db_user,
            password=self.db_password.get_secret_value(),
            application_name="skylink-care",
        )


class OssKeys(BaseSettings):
    """API keys for the OSS/BSS gateway, one per agent identity.

    Every field is optional so that a process only needs the keys of the identities it runs.
    """

    # Read from the process environment only, never from a file: which keys a process holds is
    # decided by whoever starts it, and that is what keeps the write key out of the orchestrator.
    model_config = SettingsConfigDict(env_prefix="OSS_KEY_", extra="ignore", frozen=True)

    triage: SecretStr | None = None
    diagnostics: SecretStr | None = None
    resolution: SecretStr | None = None
    dispatch: SecretStr | None = None
    notification: SecretStr | None = None
    ingest: SecretStr | None = None
    executor: SecretStr | None = None

    def require(self, identity: AgentIdentity) -> str:
        """Return the key for an identity, or fail fast with the variable name to set."""
        secret: SecretStr | None = getattr(self, identity)
        if secret is None or not secret.get_secret_value():
            raise MissingSecretError(f"OSS_KEY_{identity.upper()} is not set")
        return secret.get_secret_value()


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()


@lru_cache(maxsize=1)
def get_oss_keys() -> OssKeys:
    return OssKeys()
