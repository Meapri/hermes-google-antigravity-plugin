from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def _load_env_file() -> None:
    env_path = Path(os.getenv("ANTIGRAVITY_PROXY_ENV_FILE", ".env")).expanduser()
    if not env_path.exists():
        return
    for line in env_path.read_text(encoding="utf-8", errors="replace").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


@dataclass(frozen=True)
class Settings:
    model: str
    image_model: str
    antigravity_auth_file: Path
    antigravity_client_file: Path
    antigravity_cli_path: Path
    antigravity_cli_token_file: Path
    antigravity_project_id: str = ""

    @classmethod
    def from_env(cls) -> "Settings":
        _load_env_file()
        return cls(
            model=os.getenv("ANTIGRAVITY_PROXY_MODEL", "gemini-3.5-flash-high").strip(),
            image_model=os.getenv("ANTIGRAVITY_PROXY_IMAGE_MODEL", "gemini-3.1-flash-image").strip(),
            antigravity_auth_file=Path(
                os.getenv("ANTIGRAVITY_AUTH_FILE", "~/.hermes/auth/google_antigravity.json")
            ).expanduser(),
            antigravity_client_file=Path(
                os.getenv("ANTIGRAVITY_CLIENT_FILE", "~/.hermes/auth/google_antigravity_client.json")
            ).expanduser(),
            antigravity_cli_path=Path(os.getenv("ANTIGRAVITY_CLI_PATH", "~/.local/bin/agy")).expanduser(),
            antigravity_cli_token_file=Path(
                os.getenv("ANTIGRAVITY_CLI_TOKEN_FILE", "~/.gemini/antigravity-cli/antigravity-oauth-token")
            ).expanduser(),
            antigravity_project_id=os.getenv("ANTIGRAVITY_PROJECT_ID", "").strip(),
        )
