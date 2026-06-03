import json
from types import SimpleNamespace
import time
from datetime import datetime, timedelta, timezone


def test_antigravity_load_imports_cli_token_when_hermes_token_missing(tmp_path, monkeypatch):
    from hermes_constants import reset_hermes_home_override, set_hermes_home_override
    from agent import google_antigravity_oauth as oauth

    hermes_home = tmp_path / "hermes"
    home = tmp_path / "home"
    cli_path = home / ".gemini" / "antigravity-cli" / "antigravity-oauth-token"
    cli_path.parent.mkdir(parents=True)
    expiry = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat().replace("+00:00", "Z")
    cli_path.write_text(
        json.dumps(
            {
                "access_token": "cli-access",
                "refresh_token": "cli-refresh",
                "token_type": "Bearer",
                "expiry": expiry,
            }
        ),
        encoding="utf-8",
    )

    token = set_hermes_home_override(hermes_home)
    monkeypatch.setenv("HOME", str(home))
    try:
        creds = oauth.load_credentials()
    finally:
        reset_hermes_home_override(token)

    assert creds is not None
    assert creds.access_token == "cli-access"
    assert creds.refresh_token == "cli-refresh"
    assert creds.expires_ms > int(time.time() * 1000)


def test_antigravity_valid_token_prefers_existing_agy_cli_token(tmp_path, monkeypatch):
    from hermes_constants import reset_hermes_home_override, set_hermes_home_override
    from agent import google_antigravity_oauth as oauth

    hermes_home = tmp_path / "hermes"
    home = tmp_path / "home"
    cli_path = home / ".gemini" / "antigravity-cli" / "antigravity-oauth-token"
    cli_path.parent.mkdir(parents=True)
    expiry = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat().replace("+00:00", "Z")
    cli_path.write_text(
        json.dumps(
            {
                "token": {
                    "access_token": "cli-access",
                    "refresh_token": "cli-refresh",
                    "token_type": "Bearer",
                    "expiry": expiry,
                },
                "auth_method": "consumer",
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("HOME", str(home))
    token = set_hermes_home_override(hermes_home)
    try:
        access_token = oauth.get_valid_access_token()
    finally:
        reset_hermes_home_override(token)

    assert access_token == "cli-access"


def test_antigravity_save_mirrors_hermes_token_to_cli_shape(tmp_path, monkeypatch):
    from hermes_constants import reset_hermes_home_override, set_hermes_home_override
    from agent import google_antigravity_oauth as oauth

    hermes_home = tmp_path / "hermes"
    home = tmp_path / "home"
    monkeypatch.setenv("HOME", str(home))
    token = set_hermes_home_override(hermes_home)
    try:
        oauth.save_credentials(
            oauth.GoogleCredentials(
                access_token="hermes-access",
                refresh_token="hermes-refresh",
                expires_ms=int((time.time() + 3600) * 1000),
                email="user@example.com",
            )
        )
    finally:
        reset_hermes_home_override(token)

    cli_path = home / ".gemini" / "antigravity-cli" / "antigravity-oauth-token"
    data = json.loads(cli_path.read_text(encoding="utf-8"))
    assert data["auth_method"] == "consumer"
    assert data["token"]["access_token"] == "hermes-access"
    assert data["token"]["refresh_token"] == "hermes-refresh"
    assert data["token"]["token_type"] == "Bearer"
    assert data["token"]["expiry"].endswith("Z")


def test_antigravity_loads_alternate_cli_token_path(tmp_path, monkeypatch):
    from hermes_constants import reset_hermes_home_override, set_hermes_home_override
    from agent import google_antigravity_oauth as oauth

    hermes_home = tmp_path / "hermes"
    home = tmp_path / "home"
    cli_path = home / ".gemini" / "antigravity" / "oauth-token"
    cli_path.parent.mkdir(parents=True)
    cli_path.write_text(
        json.dumps(
            {
                "token": {
                    "accessToken": "alt-access",
                    "refreshToken": "alt-refresh",
                    "expiresAt": int((time.time() + 3600) * 1000),
                },
                "email": "user@example.com",
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("HOME", str(home))
    token = set_hermes_home_override(hermes_home)
    try:
        creds = oauth.load_credentials()
    finally:
        reset_hermes_home_override(token)

    assert creds is not None
    assert creds.access_token == "alt-access"
    assert creds.refresh_token == "alt-refresh"
    assert creds.email == "user@example.com"


def test_antigravity_loads_macos_keychain_token(monkeypatch, tmp_path):
    from hermes_constants import reset_hermes_home_override, set_hermes_home_override
    from agent import google_antigravity_oauth as oauth

    hermes_home = tmp_path / "hermes"
    home = tmp_path / "home"
    payload = json.dumps(
        {
            "token": {
                "access_token": "keychain-access",
                "refresh_token": "keychain-refresh",
                "expiry": (datetime.now(timezone.utc) + timedelta(hours=1))
                .isoformat()
                .replace("+00:00", "Z"),
            },
            "auth_method": "consumer",
        }
    )

    def fake_run(cmd, **kwargs):
        if cmd[:6] == [
            "security",
            "find-generic-password",
            "-s",
            "gemini",
            "-a",
            "antigravity",
        ]:
            return SimpleNamespace(returncode=0, stdout=payload, stderr="")
        return SimpleNamespace(returncode=1, stdout="", stderr="")

    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr(oauth.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(oauth.subprocess, "run", fake_run)
    token = set_hermes_home_override(hermes_home)
    try:
        creds = oauth.load_credentials()
    finally:
        reset_hermes_home_override(token)

    assert creds is not None
    assert creds.access_token == "keychain-access"
    assert creds.refresh_token == "keychain-refresh"


def test_antigravity_loads_macos_keychain_refresh_only_token(monkeypatch, tmp_path):
    from hermes_constants import reset_hermes_home_override, set_hermes_home_override
    from agent import google_antigravity_oauth as oauth

    hermes_home = tmp_path / "hermes"
    home = tmp_path / "home"
    payload = json.dumps(
        {
            "token": {
                "refresh_token": "keychain-refresh",
            },
            "auth_method": "consumer",
        }
    )

    def fake_run(cmd, **kwargs):
        if cmd[:6] == [
            "security",
            "find-generic-password",
            "-s",
            "gemini",
            "-a",
            "antigravity",
        ]:
            return SimpleNamespace(returncode=0, stdout=payload, stderr="")
        return SimpleNamespace(returncode=1, stdout="", stderr="")

    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr(oauth.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(oauth.subprocess, "run", fake_run)
    token = set_hermes_home_override(hermes_home)
    try:
        creds = oauth.load_credentials()
    finally:
        reset_hermes_home_override(token)

    assert creds is not None
    assert creds.access_token == ""
    assert creds.refresh_token == "keychain-refresh"
    assert creds.expires_ms == 0
