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
import secrets
import subprocess
import threading
import time
import urllib.parse
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
# Startup must not scan the 100MB+ agy binary on every invocation.  Prefer
# explicit env vars, then a private cache populated by install/update or first
# fallback extraction, and only fall back to `strings agy` when neither exists.
# Override via environment variables if needed:
#   HERMES_ANTIGRAVITY_CLIENT_ID
#   HERMES_ANTIGRAVITY_CLIENT_SECRET
ANTIGRAVITY_CLIENT_ID = ""
ANTIGRAVITY_CLIENT_SECRET = ""
_CLIENT_CACHE_EXTRACTOR_VERSION = 4


def _client_cache_path() -> Path:
    return get_hermes_home() / "auth" / "google_antigravity_client.json"


def _load_client_from_env_or_cache() -> bool:
    """Load OAuth client credentials without touching the large agy binary."""
    global ANTIGRAVITY_CLIENT_ID, ANTIGRAVITY_CLIENT_SECRET
    if ANTIGRAVITY_CLIENT_ID and ANTIGRAVITY_CLIENT_SECRET:
        return True

    env_id = os.getenv("HERMES_ANTIGRAVITY_CLIENT_ID", "").strip()
    env_secret = os.getenv("HERMES_ANTIGRAVITY_CLIENT_SECRET", "").strip()
    if env_id and env_secret:
        ANTIGRAVITY_CLIENT_ID = env_id
        ANTIGRAVITY_CLIENT_SECRET = env_secret
        return True

    try:
        data = json.loads(_client_cache_path().read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    cache_id = str(data.get("client_id", "") or "").strip()
    cache_secret = str(data.get("client_secret", "") or "").strip()
    extractor_version = int(data.get("extractor_version", 0) or 0)
    if extractor_version < _CLIENT_CACHE_EXTRACTOR_VERSION:
        return False
    if cache_id and cache_secret:
        ANTIGRAVITY_CLIENT_ID = cache_id
        ANTIGRAVITY_CLIENT_SECRET = cache_secret
        return True
    return False


def _save_client_cache() -> None:
    if not (ANTIGRAVITY_CLIENT_ID and ANTIGRAVITY_CLIENT_SECRET):
        return
    path = _client_cache_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(
        {
            "client_id": ANTIGRAVITY_CLIENT_ID,
            "client_secret": ANTIGRAVITY_CLIENT_SECRET,
            "extractor_version": _CLIENT_CACHE_EXTRACTOR_VERSION,
            "source": "agy strings cache",
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


def _extract_client_from_agy_strings(text: str) -> tuple[str, str]:
    import re

    id_matches = list(re.finditer(r"(\d+-[\w]+\.apps\.googleusercontent\.com)", text))
    # Google OAuth client secrets have a fixed "GOCSPX-" prefix plus 28
    # URL-safe characters. Go binaries can concatenate adjacent string data, so
    # an unbounded character class over-captures into unrelated bytes.
    secret_matches = list(re.finditer(r"(GOCSPX-[A-Za-z0-9_-]{28})", text))
    if not id_matches or not secret_matches:
        return "", ""

    target = next(
        (match for match in id_matches if match.group(1).startswith("1071006060591")),
        id_matches[0],
    )
    client_id = target.group(1)
    secret_clusters: list[list[Any]] = []
    for match in secret_matches:
        if (
            not secret_clusters
            or match.start() - secret_clusters[-1][-1].start()
            > len(secret_clusters[-1][-1].group(1))
        ):
            secret_clusters.append([match])
        else:
            secret_clusters[-1].append(match)
    nearest_cluster = min(
        secret_clusters,
        key=lambda cluster: min(abs(match.start() - target.start()) for match in cluster),
    )
    client_secret = nearest_cluster[0].group(1)
    return client_id, client_secret


def _extract_from_agy_binary():
    """Extract OAuth client credentials from the agy CLI binary at runtime."""
    global ANTIGRAVITY_CLIENT_ID, ANTIGRAVITY_CLIENT_SECRET
    if _load_client_from_env_or_cache():
        return
    import subprocess
    try:
        agy_path = subprocess.check_output(["which", "agy"], text=True, timeout=5).strip()
    except (subprocess.CalledProcessError, FileNotFoundError, subprocess.TimeoutExpired):
        return
    try:
        data = subprocess.check_output(["strings", agy_path], timeout=30)
        text = data.decode(errors="replace")
    except (subprocess.CalledProcessError, FileNotFoundError, subprocess.TimeoutExpired):
        return
    client_id, client_secret = _extract_client_from_agy_strings(text)
    if client_id and client_secret:
        ANTIGRAVITY_CLIENT_ID = client_id
        ANTIGRAVITY_CLIENT_SECRET = client_secret
        _save_client_cache()


def _get_client_id():
    if not _load_client_from_env_or_cache():
        _extract_from_agy_binary()
    return ANTIGRAVITY_CLIENT_ID


def _get_client_secret():
    # Match agy's hosted callback flow: the token endpoint rejects the
    # authorization-code exchange for this client when client_secret is absent.
    if not _load_client_from_env_or_cache():
        _extract_from_agy_binary()
    return ANTIGRAVITY_CLIENT_SECRET
ANTIGRAVITY_SCOPES = (
    "https://www.googleapis.com/auth/cloud-platform "
    "https://www.googleapis.com/auth/userinfo.email "
    "https://www.googleapis.com/auth/userinfo.profile "
    "https://www.googleapis.com/auth/cclog "
    "https://www.googleapis.com/auth/experimentsandconfigs "
    "openid"
)
ANTIGRAVITY_AUTH_ENDPOINT = "https://accounts.google.com/o/oauth2/auth"
ANTIGRAVITY_REDIRECT_URI = "https://antigravity.google/oauth-callback"
ANTIGRAVITY_AUTH_WAIT_SECONDS = 30.0
ANTIGRAVITY_REDIRECT_PORT = 51121
ANTIGRAVITY_CALLBACK_PATH = "/auth/callback"
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


def _candidate_cli_credentials_paths() -> list[Path]:
    primary = _cli_credentials_path()
    candidates = [
        primary,
        Path.home() / ".gemini" / "antigravity-cli" / "oauth-token",
        Path.home() / ".gemini" / "antigravity" / "antigravity-oauth-token",
        Path.home() / ".gemini" / "antigravity" / "oauth-token",
    ]
    seen: set[str] = set()
    result: list[Path] = []
    for path in candidates:
        key = str(path.expanduser())
        if key not in seen:
            seen.add(key)
            result.append(path.expanduser())
    return result


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
            "AUTH_ENDPOINT": getattr(google_oauth, "AUTH_ENDPOINT", None),
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
            if hasattr(google_oauth, "AUTH_ENDPOINT"):
                google_oauth.AUTH_ENDPOINT = ANTIGRAVITY_AUTH_ENDPOINT
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


def _credentials_from_mapping(data: Dict[str, Any]) -> Optional[GoogleCredentials]:
    if not isinstance(data, dict):
        return None
    # Support both agy CLI's nested format: {"token": {"access_token": ..., "refresh_token": ..., "expiry": ...}}
    # and the flat format: {"access_token": ..., "refresh_token": ..., "expiry": ...}.
    # Some CLI entries only store a refresh token; keep those as expired
    # credentials so the normal Google refresh path can mint access.
    token_data = data.get("token") if isinstance(data.get("token"), dict) else data
    access = str(
        token_data.get("access_token")
        or token_data.get("accessToken")
        or token_data.get("access")
        or ""
    )
    refresh = str(
        token_data.get("refresh_token")
        or token_data.get("refreshToken")
        or token_data.get("refresh")
        or ""
    )
    if "refresh" in token_data and refresh:
        refresh = RefreshParts.parse(refresh).refresh_token
    if not refresh:
        return None
    return GoogleCredentials(
        access_token=access,
        refresh_token=refresh,
        expires_ms=(
            _parse_cli_expiry_ms(
                token_data.get("expiry")
                or token_data.get("expires_at")
                or token_data.get("expiresAt")
                or token_data.get("expires")
            )
            if access
            else 0
        ),
        email=str(data.get("email") or token_data.get("email") or ""),
    )


def _credentials_from_text(text: str) -> Optional[GoogleCredentials]:
    stripped = text.strip()
    if not stripped:
        return None
    try:
        data = json.loads(stripped)
    except json.JSONDecodeError:
        # Raw Google refresh tokens often start with "1//". Avoid treating
        # arbitrary keychain secrets as OAuth credentials.
        if stripped.startswith("1//"):
            return GoogleCredentials(access_token="", refresh_token=stripped, expires_ms=0)
        return None
    return _credentials_from_json_data(data)


def _credentials_from_json_data(data: Any) -> Optional[GoogleCredentials]:
    if isinstance(data, list):
        for item in data:
            if isinstance(item, dict):
                creds = _credentials_from_mapping(item)
                if creds is not None:
                    return creds
        return None
    if not isinstance(data, dict):
        return None
    creds = _credentials_from_mapping(data)
    if creds is not None:
        return creds
    for value in data.values():
        if isinstance(value, dict):
            creds = _credentials_from_mapping(value)
            if creds is not None:
                return creds
        elif isinstance(value, list):
            for item in value:
                if isinstance(item, dict):
                    creds = _credentials_from_mapping(item)
                    if creds is not None:
                        return creds
    return None


def _load_credentials_from_json_text(text: str) -> Optional[GoogleCredentials]:
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return None
    return _credentials_from_json_data(data)


def _load_cli_file_credentials() -> Optional[GoogleCredentials]:
    for path in _candidate_cli_credentials_paths():
        if not path.exists():
            continue
        try:
            creds = _credentials_from_text(path.read_text(encoding="utf-8"))
        except OSError:
            continue
        if creds is not None:
            return creds
    return None


def _load_cli_credentials() -> Optional[GoogleCredentials]:
    return _load_cli_file_credentials()


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


def _refresh_token_via_agy_cli() -> bool:
    """Use agy CLI to refresh the OAuth token using its own credential management.

    The client_secret extracted from the agy binary may not match the one
    Google expects for token refresh (binary can be stale or secrets rotated).
    agy manages its own secrets internally, so running ``agy --print "OK"``
    forces agy to refresh the token with its correct credentials, which we
    then re-read from the token file.

    Returns True if the agy CLI ran successfully.
    """
    try:
        subprocess.run(
            ["agy", "--prompt", "OK", "--print-timeout", "30s"],
            capture_output=True, text=True, timeout=60,
        )
        return True
    except Exception:
        return False


def _build_antigravity_auth_url(
    *,
    client_id: str,
    code_challenge: str,
    state: str,
) -> str:
    """Build the browser login URL to match the agy CLI flow."""
    params = [
        ("access_type", "offline"),
        ("client_id", client_id),
        ("code_challenge", code_challenge),
        ("code_challenge_method", "S256"),
        ("prompt", "consent"),
        ("redirect_uri", ANTIGRAVITY_REDIRECT_URI),
        ("response_type", "code"),
        ("scope", ANTIGRAVITY_SCOPES),
        ("state", state),
    ]
    return ANTIGRAVITY_AUTH_ENDPOINT + "?" + urllib.parse.urlencode(params)


def _extract_authorization_code(raw: str, *, expected_state: str) -> str:
    text = raw.strip()
    if not text:
        return ""

    params: dict[str, list[str]] = {}
    if text.startswith("http://") or text.startswith("https://"):
        parsed = urllib.parse.urlparse(text)
        params = urllib.parse.parse_qs(parsed.query)
        fragment_params = urllib.parse.parse_qs(parsed.fragment)
        for key, value in fragment_params.items():
            params.setdefault(key, value)
    elif text.startswith("?"):
        params = urllib.parse.parse_qs(text[1:])
    elif "code=" in text or "error=" in text:
        params = urllib.parse.parse_qs(text.lstrip("?"))

    if params:
        error = (params.get("error") or [""])[0]
        if error:
            raise GoogleOAuthError(
                f"Authorization failed: {error}",
                code="google_oauth_authorization_failed",
            )
        state = (params.get("state") or [""])[0]
        if state and state != expected_state:
            raise GoogleOAuthError(
                "Authorization failed: state mismatch.",
                code="google_oauth_state_mismatch",
            )
        return (params.get("code") or [""])[0].strip()

    return text


def _read_authorization_code(timeout_seconds: float) -> str:
    prompt = "Authorization code or callback URL: "
    try:
        import select
        import sys

        if sys.stdin.isatty():
            print(prompt, end="", flush=True)
            ready, _, _ = select.select([sys.stdin], [], [], max(0.0, timeout_seconds))
            if not ready:
                print()
                raise GoogleOAuthError(
                    "Authentication timed out.",
                    code="google_oauth_timeout",
                )
            return sys.stdin.readline().strip()
    except GoogleOAuthError:
        raise
    except Exception:
        pass
    return input(prompt)


def get_valid_access_token(*, force_refresh: bool = False) -> str:
    with _antigravity_profile():
        creds = load_credentials()
        if creds is not None and not force_refresh and not creds.access_token_expired():
            token = creds.access_token
        else:
            token = ""
        try:
            if not token:
                token = google_oauth.get_valid_access_token(force_refresh=force_refresh)
                creds = google_oauth.load_credentials()
        except GoogleOAuthError:
            # Standard OAuth refresh failed — likely client_secret mismatch
            # with the secret extracted from the agy binary.  Fall back to
            # agy CLI which manages its own secrets internally.
            if not _refresh_token_via_agy_cli():
                raise GoogleOAuthError(
                    "No Antigravity OAuth credentials found. Run `hermes auth add google-antigravity`.",
                    code="antigravity_oauth_not_logged_in",
                )
            # Re-read the token that agy just refreshed
            creds = _load_cli_credentials()
            if creds is None:
                raise GoogleOAuthError(
                    "No Antigravity OAuth credentials found after `agy` refresh. Run `hermes auth add google-antigravity`.",
                    code="antigravity_oauth_not_logged_in",
                )
            # Persist to Hermes credential store so future refreshes
            # pick up the agy-refreshed token first
            google_oauth.save_credentials(creds)
            token = creds.access_token
            if not token or creds.access_token_expired(skew_seconds=0):
                token = google_oauth.get_valid_access_token(force_refresh=True)
                creds = google_oauth.load_credentials()
    if creds is not None:
        _mirror_credentials_to_cli(creds)
    return token


def start_oauth_flow(
    *,
    force_relogin: bool = False,
    open_browser: bool = True,
    callback_wait_seconds: float = ANTIGRAVITY_AUTH_WAIT_SECONDS,
    project_id: str = "",
) -> GoogleCredentials:
    if not project_id:
        project_id = resolve_project_id_from_env()
    print()
    print("⚠️  Google Antigravity OAuth is unofficial. It may violate Google/Antigravity terms")
    print("   or cause account/API access issues. Continue only if you accept that risk.")
    with _antigravity_profile():
        if not force_relogin:
            existing = google_oauth.load_credentials()
            if existing and existing.access_token:
                return existing

        client_id = google_oauth._require_client_id()
        client_secret = _get_client_secret()
        verifier, challenge = google_oauth._generate_pkce_pair()
        state = secrets.token_urlsafe(16)
        auth_url = _build_antigravity_auth_url(
            client_id=client_id,
            code_challenge=challenge,
            state=state,
        )

        print()
        print("Authentication required. Please visit the URL to log in:")
        print(f"  {auth_url}")
        print()
        print(f"Waiting for authentication (timeout {int(callback_wait_seconds)}s)...")
        print("Or, paste the authorization code here and press Enter:")
        print()

        if open_browser:
            try:
                import webbrowser

                webbrowser.open(auth_url, new=1, autoraise=True)
            except Exception:
                pass

        raw_code = _read_authorization_code(callback_wait_seconds)
        code = _extract_authorization_code(raw_code, expected_state=state)
        if not code:
            raise GoogleOAuthError(
                "No authorization code provided.",
                code="google_oauth_no_code",
            )

        token_resp = google_oauth.exchange_code(
            code,
            verifier,
            ANTIGRAVITY_REDIRECT_URI,
            client_id=client_id,
            client_secret=client_secret,
        )
        creds = google_oauth._persist_token_response(token_resp, project_id=project_id)
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
