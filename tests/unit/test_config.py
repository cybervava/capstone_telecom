"""Settings fail closed and each process must be given the secrets it needs."""

from __future__ import annotations

import pytest

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
