import json
import time
import urllib.parse
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


def test_antigravity_loads_refresh_only_cli_token(tmp_path, monkeypatch):
    from hermes_constants import reset_hermes_home_override, set_hermes_home_override
    from agent import google_antigravity_oauth as oauth

    hermes_home = tmp_path / "hermes"
    home = tmp_path / "home"
    cli_path = home / ".gemini" / "antigravity-cli" / "antigravity-oauth-token"
    cli_path.parent.mkdir(parents=True)
    cli_path.write_text(
        json.dumps(
            {
                "token": {
                    "refresh_token": "cli-refresh",
                },
                "auth_method": "consumer",
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
    assert creds.access_token == ""
    assert creds.refresh_token == "cli-refresh"
    assert creds.expires_ms == 0


def test_antigravity_loads_raw_refresh_cli_token(tmp_path, monkeypatch):
    from hermes_constants import reset_hermes_home_override, set_hermes_home_override
    from agent import google_antigravity_oauth as oauth

    hermes_home = tmp_path / "hermes"
    home = tmp_path / "home"
    cli_path = home / ".gemini" / "antigravity-cli" / "antigravity-oauth-token"
    cli_path.parent.mkdir(parents=True)
    cli_path.write_text("1//raw-refresh-token", encoding="utf-8")

    monkeypatch.setenv("HOME", str(home))
    token = set_hermes_home_override(hermes_home)
    try:
        creds = oauth.load_credentials()
    finally:
        reset_hermes_home_override(token)

    assert creds is not None
    assert creds.access_token == ""
    assert creds.refresh_token == "1//raw-refresh-token"
    assert creds.expires_ms == 0


def test_antigravity_loads_alternate_cli_token_path_with_access_token(monkeypatch, tmp_path):
    from hermes_constants import reset_hermes_home_override, set_hermes_home_override
    from agent import google_antigravity_oauth as oauth

    hermes_home = tmp_path / "hermes"
    home = tmp_path / "home"
    cli_path = home / ".gemini" / "antigravity" / "antigravity-oauth-token"
    cli_path.parent.mkdir(parents=True)
    cli_path.write_text(
        json.dumps(
        {
            "token": {
                "access_token": "alt-access",
                "refresh_token": "alt-refresh",
                "expiry": (datetime.now(timezone.utc) + timedelta(hours=1))
                .isoformat()
                .replace("+00:00", "Z"),
            },
            "auth_method": "consumer",
        }),
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


def test_antigravity_extracts_hyphenated_agy_oauth_secret():
    from agent import google_antigravity_oauth as oauth

    text = "\n".join(
        [
            "884354919052-other.apps.googleusercontent.com",
            "1071006060591-tmhssin2h21lcre235vtolojh4g403ep.apps.googleusercontent.com",
            "GOCSPX-K58FW-part-two_part-three",
        ]
    )

    client_id, client_secret = oauth._extract_client_from_agy_strings(text)

    assert client_id == "1071006060591-tmhssin2h21lcre235vtolojh4g403ep.apps.googleusercontent.com"
    assert client_secret == "GOCSPX-K58FW-part-two_part-three"


def test_antigravity_rejects_old_client_cache(tmp_path, monkeypatch):
    from hermes_constants import reset_hermes_home_override, set_hermes_home_override
    from agent import google_antigravity_oauth as oauth

    hermes_home = tmp_path / "hermes"
    cache = hermes_home / "auth" / "google_antigravity_client.json"
    cache.parent.mkdir(parents=True)
    cache.write_text(
        json.dumps(
            {
                "client_id": "1071006060591-client.apps.googleusercontent.com",
                "client_secret": "GOCSPX-truncated",
            }
        ),
        encoding="utf-8",
    )

    monkeypatch.setattr(oauth, "ANTIGRAVITY_CLIENT_ID", "")
    monkeypatch.setattr(oauth, "ANTIGRAVITY_CLIENT_SECRET", "")
    token = set_hermes_home_override(hermes_home)
    try:
        assert not oauth._load_client_from_env_or_cache()
    finally:
        reset_hermes_home_override(token)


def test_antigravity_does_not_send_client_secret_by_default(tmp_path, monkeypatch):
    from hermes_constants import reset_hermes_home_override, set_hermes_home_override
    from agent import google_antigravity_oauth as oauth

    hermes_home = tmp_path / "hermes"
    cache = hermes_home / "auth" / "google_antigravity_client.json"
    cache.parent.mkdir(parents=True)
    cache.write_text(
        json.dumps(
            {
                "client_id": "1071006060591-client.apps.googleusercontent.com",
                "client_secret": "GOCSPX-full-secret",
                "extractor_version": 2,
            }
        ),
        encoding="utf-8",
    )

    monkeypatch.delenv("HERMES_ANTIGRAVITY_USE_CLIENT_SECRET", raising=False)
    monkeypatch.setattr(oauth, "ANTIGRAVITY_CLIENT_ID", "")
    monkeypatch.setattr(oauth, "ANTIGRAVITY_CLIENT_SECRET", "")
    token = set_hermes_home_override(hermes_home)
    try:
        assert oauth._get_client_id() == "1071006060591-client.apps.googleusercontent.com"
        assert oauth._get_client_secret() == ""
    finally:
        reset_hermes_home_override(token)


def test_antigravity_can_opt_in_to_client_secret(tmp_path, monkeypatch):
    from hermes_constants import reset_hermes_home_override, set_hermes_home_override
    from agent import google_antigravity_oauth as oauth

    hermes_home = tmp_path / "hermes"
    cache = hermes_home / "auth" / "google_antigravity_client.json"
    cache.parent.mkdir(parents=True)
    cache.write_text(
        json.dumps(
            {
                "client_id": "1071006060591-client.apps.googleusercontent.com",
                "client_secret": "GOCSPX-full-secret",
                "extractor_version": 2,
            }
        ),
        encoding="utf-8",
    )

    monkeypatch.setenv("HERMES_ANTIGRAVITY_USE_CLIENT_SECRET", "1")
    monkeypatch.setattr(oauth, "ANTIGRAVITY_CLIENT_ID", "")
    monkeypatch.setattr(oauth, "ANTIGRAVITY_CLIENT_SECRET", "")
    token = set_hermes_home_override(hermes_home)
    try:
        assert oauth._get_client_secret() == "GOCSPX-full-secret"
    finally:
        reset_hermes_home_override(token)


def test_antigravity_profile_matches_official_auth_endpoint_and_omits_secret(monkeypatch):
    from agent import google_antigravity_oauth as oauth
    from agent import google_oauth

    original_endpoint = getattr(google_oauth, "AUTH_ENDPOINT", "")
    original_callback_path = google_oauth.CALLBACK_PATH
    monkeypatch.delenv("HERMES_ANTIGRAVITY_USE_CLIENT_SECRET", raising=False)
    monkeypatch.setattr(oauth, "ANTIGRAVITY_CLIENT_ID", "1071006060591-client.apps.googleusercontent.com")
    monkeypatch.setattr(oauth, "ANTIGRAVITY_CLIENT_SECRET", "GOCSPX-full-secret")

    with oauth._antigravity_profile():
        assert google_oauth.AUTH_ENDPOINT == "https://accounts.google.com/o/oauth2/auth"
        assert google_oauth.CALLBACK_PATH == "/auth/callback"
        assert google_oauth._DEFAULT_CLIENT_SECRET == ""

    assert google_oauth.AUTH_ENDPOINT == original_endpoint
    assert google_oauth.CALLBACK_PATH == original_callback_path


def test_antigravity_auth_url_matches_agy_login_flow():
    from agent import google_antigravity_oauth as oauth

    url = oauth._build_antigravity_auth_url(
        client_id="1071006060591-client.apps.googleusercontent.com",
        code_challenge="challenge",
        state="state",
    )
    parsed = urllib.parse.urlparse(url)
    params = urllib.parse.parse_qs(parsed.query)

    assert f"{parsed.scheme}://{parsed.netloc}{parsed.path}" == (
        "https://accounts.google.com/o/oauth2/auth"
    )
    assert params["access_type"] == ["offline"]
    assert params["client_id"] == ["1071006060591-client.apps.googleusercontent.com"]
    assert params["code_challenge"] == ["challenge"]
    assert params["code_challenge_method"] == ["S256"]
    assert params["prompt"] == ["consent"]
    assert params["redirect_uri"] == ["https://antigravity.google/oauth-callback"]
    assert params["response_type"] == ["code"]
    assert params["scope"] == [
        (
            "https://www.googleapis.com/auth/cloud-platform "
            "https://www.googleapis.com/auth/userinfo.email "
            "https://www.googleapis.com/auth/userinfo.profile "
            "https://www.googleapis.com/auth/cclog "
            "https://www.googleapis.com/auth/experimentsandconfigs "
            "openid"
        )
    ]
    assert params["state"] == ["state"]
    assert parsed.fragment == ""


def test_antigravity_authorization_code_parser_validates_state():
    from agent import google_antigravity_oauth as oauth

    callback = (
        "https://antigravity.google/oauth-callback?"
        "code=auth-code&state=expected-state"
    )

    assert oauth._extract_authorization_code(
        callback,
        expected_state="expected-state",
    ) == "auth-code"
    assert oauth._extract_authorization_code(
        "code=query-code&state=expected-state",
        expected_state="expected-state",
    ) == "query-code"

    try:
        oauth._extract_authorization_code(callback, expected_state="other-state")
    except oauth.GoogleOAuthError as exc:
        assert exc.code == "google_oauth_state_mismatch"
    else:
        raise AssertionError("state mismatch should fail")


def test_antigravity_agy_refresh_uses_prompt_argument(monkeypatch):
    from subprocess import CompletedProcess
    from agent import google_antigravity_oauth as oauth

    calls = []

    def fake_run(cmd, **kwargs):
        calls.append((cmd, kwargs))
        return CompletedProcess(cmd, 0, stdout="OK\n", stderr="")

    monkeypatch.setattr(oauth.subprocess, "run", fake_run)

    assert oauth._refresh_token_via_agy_cli()
    assert calls == [
        (
            ["agy", "--prompt", "OK", "--print-timeout", "30s"],
            {"capture_output": True, "text": True, "timeout": 60},
        )
    ]
