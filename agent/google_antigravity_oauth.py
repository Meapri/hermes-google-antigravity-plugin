"""Antigravity-flavoured Google OAuth for Hermes.

This module intentionally reuses the generic machinery in ``agent.google_oauth``
instead of copying the PKCE, callback-server, credential-file, refresh-dedup,
and secure I/O logic.  It does not use the Gemini CLI provider path; the
Antigravity profile supplies its own OAuth client, scopes, redirect path/port,
credential filename, and project-id env vars.

The Antigravity OAuth client and headers are public constants extracted from
NoeFabris/opencode-antigravity-auth. This is an unofficial integration; users
should understand the account/ToS risk before logging in.
"""

from __future__ import annotations

import contextlib
import json
import os
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterator, Optional

from hermes_constants import get_hermes_home
from agent import google_oauth
from utils import atomic_replace

PROVIDER_ID = "google-antigravity"
MARKER_BASE_URL = "cloudcode-pa://antigravity"

# OAuth client credentials are extracted from the agy CLI binary at runtime
# (same approach as hermes-claude-auth — no secrets committed to git).
# Override via environment variables if needed:
#   HERMES_ANTIGRAVITY_CLIENT_ID
#   HERMES_ANTIGRAVITY_CLIENT_SECRET
ANTIGRAVITY_CLIENT_ID = ""
ANTIGRAVITY_CLIENT_SECRET=""

def _extract_from_agy_binary():
    """Extract OAuth client credentials from the agy CLI binary at runtime."""
    global ANTIGRAVITY_CLIENT_ID, ANTIGRAVITY_CLIENT_SECRET
    if ANTIGRAVITY_CLIENT_ID and ANTIGRAVITY_CLIENT_SECRET:
        return
    import subprocess, re
    try:
        agy_path = subprocess.check_output(["which", "agy"], text=True, timeout=5).strip()
    except (subprocess.CalledProcessError, FileNotFoundError, subprocess.TimeoutExpired):
        return
    try:
        data = subprocess.check_output(["strings", agy_path], timeout=30)
        text = data.decode(errors='replace')
    except (subprocess.CalledProcessError, FileNotFoundError, subprocess.TimeoutExpired):
        return
    ids = re.findall(r'(\d+-[\w]+\.apps\.googleusercontent\.com)', text)
    secrets = re.findall(r'(GOCSPX-[\w]+)', text)
    if ids and secrets:
        # Prefer the consumer client (884354919052-...) over the NoeFabris one
        for cid in ids:
            if cid.startswith("884354919052"):
                ANTIGRAVITY_CLIENT_ID = cid
                break
        if not ANTIGRAVITY_CLIENT_ID:
            ANTIGRAVITY_CLIENT_ID = ids[0]
        ANTIGRAVITY_CLIENT_SECRET = secrets[0]

def _get_client_id():
    _extract_from_agy_binary()
    if not ANTIGRAVITY_CLIENT_ID:
        import os
        return os.getenv("HERMES_ANTIGRAVITY_CLIENT_ID", "").strip()
    return ANTIGRAVITY_CLIENT_ID


def _get_client_secret():
    _extract_from_agy_binary()
    if not ANTIGRAVITY_CLIENT_SECRET:
        import os
        return os.getenv("HERMES_ANTIGRAVITY_CLIENT_SECRET", "").strip()
    return ANTIGRAVITY_CLIENT_SECRET
ANTIGRAVITY_SCOPES = (
    "https://www.googleapis.com/auth/cloud-platform "
    "https://www.googleapis.com/auth/userinfo.email "
    "https://www.googleapis.com/auth/userinfo.profile "
    "https://www.googleapis.com/auth/cclog "
    "https://www.googleapis.com/auth/experimentsandconfigs "
    "https://www.googleapis.com/auth/aicode"
)
ANTIGRAVITY_REDIRECT_PORT = 51121
ANTIGRAVITY_CALLBACK_PATH = "/oauth-callback"
# Do not hard-code a fallback project: Antigravity assigns account-specific
# Cloud Code projects. We discover and persist the account's project after
# OAuth via loadCodeAssist instead of reusing a stale project from another login.
ANTIGRAVITY_DEFAULT_PROJECT_ID = ""

_profile_lock = threading.RLock()


def _credentials_path() -> Path:
    return get_hermes_home() / "auth" / "google_antigravity.json"


def _cli_credentials_path() -> Path:
    override = os.getenv("HERMES_ANTIGRAVITY_CLI_TOKEN_PATH", "").strip()
    if override:
        return Path(override).expanduser()
    return Path.home() / ".gemini" / "antigravity-cli" / "antigravity-oauth-token"


def _lock_path() -> Path:
    return _credentials_path().with_suffix(".json.lock")


@contextlib.contextmanager
def _antigravity_profile() -> Iterator[None]:
    """Temporarily point google_oauth's generic machinery at Antigravity.

    ``google_oauth`` resolves its globals at call time, so an RLock-protected
    profile swap lets us avoid maintaining a fork of ~1k lines of OAuth code.
    """
    with _profile_lock:
        saved = {
            "ENV_CLIENT_ID": google_oauth.ENV_CLIENT_ID,
            "ENV_CLIENT_SECRET": google_oauth.ENV_CLIENT_SECRET,
            "_DEFAULT_CLIENT_ID": google_oauth._DEFAULT_CLIENT_ID,
            "_DEFAULT_CLIENT_SECRET": google_oauth._DEFAULT_CLIENT_SECRET,
            "OAUTH_SCOPES": google_oauth.OAUTH_SCOPES,
            "DEFAULT_REDIRECT_PORT": google_oauth.DEFAULT_REDIRECT_PORT,
            "REDIRECT_HOST": google_oauth.REDIRECT_HOST,
            "CALLBACK_PATH": google_oauth.CALLBACK_PATH,
            "_credentials_path": google_oauth._credentials_path,
            "_lock_path": google_oauth._lock_path,
        }
        try:
            google_oauth.ENV_CLIENT_ID = "HERMES_ANTIGRAVITY_CLIENT_ID"
            google_oauth.ENV_CLIENT_SECRET = "HERMES_ANTIGRAVITY_CLIENT_SECRET"
            google_oauth._DEFAULT_CLIENT_ID = _get_client_id()
            google_oauth._DEFAULT_CLIENT_SECRET = _get_client_secret()
            google_oauth.OAUTH_SCOPES = ANTIGRAVITY_SCOPES
            google_oauth.DEFAULT_REDIRECT_PORT = ANTIGRAVITY_REDIRECT_PORT
            google_oauth.REDIRECT_HOST = "localhost"
            google_oauth.CALLBACK_PATH = ANTIGRAVITY_CALLBACK_PATH
            google_oauth._credentials_path = _credentials_path
            google_oauth._lock_path = _lock_path
            yield
        finally:
            for name, value in saved.items():
                setattr(google_oauth, name, value)


GoogleOAuthError = google_oauth.GoogleOAuthError
GoogleCredentials = google_oauth.GoogleCredentials
RefreshParts = google_oauth.RefreshParts


def _parse_cli_expiry_ms(value: Any) -> int:
    if isinstance(value, (int, float)) and value > 0:
        return int(value * 1000) if value < 10_000_000_000 else int(value)
    if not isinstance(value, str) or not value.strip():
        return 0
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        return int(datetime.fromisoformat(text).timestamp() * 1000)
    except ValueError:
        return 0


def _format_cli_expiry(expires_ms: int) -> str:
    if expires_ms <= 0:
        expires_ms = int((time.time() + 3600) * 1000)
    return datetime.fromtimestamp(expires_ms / 1000, tz=timezone.utc).isoformat().replace("+00:00", "Z")


def _load_cli_credentials() -> Optional[GoogleCredentials]:
    path = _cli_credentials_path()
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    # Support both agy CLI's nested format: {"token": {"access_token": ..., "refresh_token": ..., "expiry": ...}}
    # and the flat format: {"access_token": ..., "refresh_token": ..., "expiry": ...}
    token_data = data.get("token") if "token" in data and isinstance(data.get("token"), dict) else data
    access = str(token_data.get("access_token", "") or "")
    refresh = str(token_data.get("refresh_token", "") or "")
    if not access or not refresh:
        return None
    return GoogleCredentials(
        access_token=access,
        refresh_token=refresh,
        expires_ms=_parse_cli_expiry_ms(token_data.get("expiry")),
        email="",
    )


def _mirror_credentials_to_cli(creds: GoogleCredentials) -> None:
    if not creds.access_token or not creds.refresh_token:
        return
    path = _cli_credentials_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    # Write in agy CLI's nested format: {"token": {...}, "auth_method": "consumer"}
    payload = json.dumps(
        {
            "token": {
                "access_token": creds.access_token,
                "refresh_token": creds.refresh_token,
                "token_type": "Bearer",
                "expiry": _format_cli_expiry(creds.expires_ms),
            },
            "auth_method": "consumer",
        },
        indent=2,
        sort_keys=True,
    ) + "\n"
    tmp_path = path.with_suffix(f".tmp.{os.getpid()}")
    try:
        fd = os.open(str(tmp_path), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(payload)
            fh.flush()
            os.fsync(fh.fileno())
        atomic_replace(tmp_path, path)
    finally:
        try:
            if tmp_path.exists():
                tmp_path.unlink()
        except OSError:
            pass


def load_credentials() -> GoogleCredentials | None:
    with _antigravity_profile():
        creds = google_oauth.load_credentials()
        if creds is not None:
            return creds
        cli_creds = _load_cli_credentials()
        if cli_creds is not None:
            google_oauth.save_credentials(cli_creds)
        return cli_creds


def save_credentials(creds: GoogleCredentials) -> None:
    with _antigravity_profile():
        google_oauth.save_credentials(creds)
    _mirror_credentials_to_cli(creds)


def clear_credentials() -> None:
    with _antigravity_profile():
        google_oauth.clear_credentials()


def get_valid_access_token(*, force_refresh: bool = False) -> str:
    with _antigravity_profile():
        token = google_oauth.get_valid_access_token(force_refresh=force_refresh)
        creds = google_oauth.load_credentials()
    if creds is not None:
        _mirror_credentials_to_cli(creds)
    return token


def start_oauth_flow(
    *,
    force_relogin: bool = False,
    open_browser: bool = True,
    callback_wait_seconds: float = google_oauth.CALLBACK_WAIT_SECONDS,
    project_id: str = "",
) -> GoogleCredentials:
    if not project_id:
        project_id = resolve_project_id_from_env()
    print()
    print("⚠️  Google Antigravity OAuth is unofficial. It may violate Google/Antigravity terms")
    print("   or cause account/API access issues. Continue only if you accept that risk.")
    with _antigravity_profile():
        creds = google_oauth.start_oauth_flow(
            force_relogin=force_relogin,
            open_browser=open_browser,
            callback_wait_seconds=callback_wait_seconds,
            project_id=project_id,
        )
    _mirror_credentials_to_cli(creds)
    if not project_id:
        try:
            from agent.google_code_assist import FREE_TIER_ID, load_code_assist

            info = load_code_assist(creds.access_token, client_profile="antigravity")
            discovered_project = info.cloudaicompanion_project
            if discovered_project:
                effective_tier = info.effective_tier_id or info.current_tier_id
                managed_project = discovered_project if effective_tier == FREE_TIER_ID else ""
                update_project_ids(project_id=discovered_project, managed_project_id=managed_project)
                creds.project_id = discovered_project
                creds.managed_project_id = managed_project
        except Exception:
            # Login should still succeed even if project discovery is temporarily unavailable.
            pass
    return creds


def update_project_ids(project_id: str = "", managed_project_id: str = "") -> None:
    with _antigravity_profile():
        google_oauth.update_project_ids(project_id=project_id, managed_project_id=managed_project_id)


def run_antigravity_oauth_login_pure() -> Dict[str, Any]:
    creds = start_oauth_flow(force_relogin=True)
    return {
        "access_token": creds.access_token,
        "refresh_token": creds.refresh_token,
        "expires_at_ms": creds.expires_ms,
        "email": creds.email,
        "project_id": creds.project_id,
    }


def resolve_project_id_from_env() -> str:
    for var in (
        "HERMES_ANTIGRAVITY_PROJECT_ID",
        "GOOGLE_CLOUD_PROJECT",
        "GOOGLE_CLOUD_PROJECT_ID",
    ):
        val = __import__("os").getenv(var, "").strip()
        if val:
            return val
    return ANTIGRAVITY_DEFAULT_PROJECT_ID
