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
import threading
from pathlib import Path
from typing import Any, Dict, Iterator

from hermes_constants import get_hermes_home
from agent import google_oauth

PROVIDER_ID = "google-antigravity"
MARKER_BASE_URL = "cloudcode-pa://antigravity"

# Public OAuth client credentials are intentionally not committed in this
# standalone repo because GitHub push protection treats Google OAuth client
# identifiers/secrets as secrets. Provide them via environment variables:
#
#   HERMES_ANTIGRAVITY_CLIENT_ID
#   HERMES_ANTIGRAVITY_CLIENT_SECRET
#
# Hermes core builds may choose to bundle known public desktop-client values,
# but external plugin distributions should keep them out of git history.
ANTIGRAVITY_CLIENT_ID = ""
ANTIGRAVITY_CLIENT_SECRET = ""
ANTIGRAVITY_SCOPES = (
    "https://www.googleapis.com/auth/cloud-platform "
    "https://www.googleapis.com/auth/userinfo.email "
    "https://www.googleapis.com/auth/userinfo.profile "
    "https://www.googleapis.com/auth/cclog "
    "https://www.googleapis.com/auth/experimentsandconfigs"
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
            google_oauth._DEFAULT_CLIENT_ID = ANTIGRAVITY_CLIENT_ID
            google_oauth._DEFAULT_CLIENT_SECRET = ANTIGRAVITY_CLIENT_SECRET
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


def load_credentials() -> GoogleCredentials | None:
    with _antigravity_profile():
        return google_oauth.load_credentials()


def save_credentials(creds: GoogleCredentials) -> None:
    with _antigravity_profile():
        google_oauth.save_credentials(creds)


def clear_credentials() -> None:
    with _antigravity_profile():
        google_oauth.clear_credentials()


def get_valid_access_token(*, force_refresh: bool = False) -> str:
    with _antigravity_profile():
        return google_oauth.get_valid_access_token(force_refresh=force_refresh)


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
    if not project_id:
        try:
            from agent.google_code_assist import FREE_TIER_ID, load_code_assist

            info = load_code_assist(creds.access_token)
            discovered_project = info.cloudaicompanion_project
            if discovered_project:
                managed_project = discovered_project if info.current_tier_id == FREE_TIER_ID else ""
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
