"""Settings fail closed and each process must be given the secrets it needs."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from skylink_care.config import MissingSecretError, OssKeys, Settings


def test_environment_defaults_to_production_so_dev_conveniences_are_off() -> None:
    settings = Settings(db_password="x")

    assert settings.env == "prod"


def test_workers_start_without_a_signing_secret_but_the_api_cannot() -> None:
    settings = Settings(db_password="x")

    assert settings.jwt_secret is None
    with pytest.raises(MissingSecretError, match="CARE_JWT_SECRET"):
        settings.require_jwt_secret()


def test_secrets_do_not_appear_in_the_settings_repr() -> None:
    settings = Settings(db_password="hunter2-not-real", jwt_secret="s3cret-not-real")

    assert "hunter2" not in repr(settings)
    assert "s3cret" not in repr(settings)


def test_a_key_that_was_not_given_to_the_process_cannot_be_used() -> None:
    keys = OssKeys(triage="key-for-triage")

    assert keys.require("triage") == "key-for-triage"
    with pytest.raises(MissingSecretError, match="OSS_KEY_EXECUTOR"):
        keys.require("executor")


def test_gateway_keys_are_never_read_from_a_dotenv_file(
    tmp_path: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / ".env").write_text("OSS_KEY_EXECUTOR=from-a-file\n")  # type: ignore[operator]
    monkeypatch.chdir(tmp_path)  # type: ignore[arg-type]
    monkeypatch.delenv("OSS_KEY_EXECUTOR", raising=False)

    assert OssKeys().executor is None


def test_cluster_window_shorter_than_the_hold_is_refused() -> None:
    """A line held for the whole hold must still count towards its node at the end of it."""
    with pytest.raises(ValidationError, match="CARE_LINE_TEST_CLUSTER_WINDOW_SECONDS"):
        Settings(
            db_password="x",
            line_test_cluster_window_seconds=300,
            dispatch_hold_seconds=600,
        )


def test_settings_that_would_switch_the_failing_lines_check_off_are_refused() -> None:
    """Three customers, all with a failing line and nobody's passing, must always be evidence
    against the node. With a fault rate set so high that this could be coincidence, the check
    would be switched off without anyone having said so."""
    with pytest.raises(ValidationError, match="CARE_LINE_OWN_FAULT_RATE"):
        Settings(db_password="x", line_own_fault_rate=0.2)


def test_the_default_settings_keep_three_failing_lines_of_three_suspect() -> None:
    settings = Settings(db_password="x")

    by_coincidence = settings.line_own_fault_rate**settings.line_test_cluster_threshold
    assert by_coincidence < settings.line_test_cluster_chance
