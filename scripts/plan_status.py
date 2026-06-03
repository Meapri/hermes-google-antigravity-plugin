#!/usr/bin/env python3
"""Print Antigravity plan and Google One AI credit-routing status."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


def _find_hermes_agent() -> Path:
    hermes_home = Path(os.environ.get("HERMES_HOME", "~/.hermes")).expanduser()
    return Path(os.environ.get("HERMES_AGENT_DIR", hermes_home / "hermes-agent")).expanduser()


def _find_venv_python(hermes_agent: Path) -> Path | None:
    env_venv = os.environ.get("HERMES_VENV")
    candidates = []
    if env_venv:
        candidates.append(Path(env_venv).expanduser() / "bin/python")
    candidates.extend([
        hermes_agent / "venv/bin/python",
        hermes_agent / ".venv/bin/python",
    ])
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return None


def _ensure_hermes_python() -> None:
    if os.environ.get("HERMES_ANTIGRAVITY_PLAN_STATUS_REEXEC") == "1":
        return
    hermes_agent = _find_hermes_agent()
    venv_python = _find_venv_python(hermes_agent)
    if not venv_python:
        return
    current = Path(sys.executable)
    target = venv_python
    if current == target:
        return
    env = dict(os.environ)
    env["HERMES_ANTIGRAVITY_PLAN_STATUS_REEXEC"] = "1"
    raise SystemExit(subprocess.call([str(target), *sys.argv], env=env))


def _bootstrap_paths() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    hermes_agent = _find_hermes_agent()
    for path in (hermes_agent, repo_root):
        text = str(path)
        if text not in sys.path:
            sys.path.insert(0, text)


def _mask(value: object) -> str:
    text = str(value or "")
    if not text:
        return ""
    if len(text) <= 10:
        return f"{text[:2]}***"
    return f"{text[:6]}...{text[-4:]}"


def main() -> int:
    _ensure_hermes_python()
    _bootstrap_paths()
    from agent import google_antigravity_oauth
    from agent.google_code_assist import load_code_assist
    from agent.google_antigravity_adapter import (
        GoogleAntigravityClient,
        _antigravity_credit_attempts,
        _antigravity_google_one_ai_credits_mode,
    )

    creds = google_antigravity_oauth.load_credentials()
    if not creds or not getattr(creds, "access_token", ""):
        print("oauth_token_present: False")
        print("status: agy token not found; run agy once and sign in")
        return 1

    model = os.environ.get("HERMES_ANTIGRAVITY_PLAN_STATUS_MODEL", "gemini-3.5-flash-high")
    project_id = getattr(creds, "project_id", "") or ""
    info = load_code_assist(
        creds.access_token,
        project_id=project_id,
        user_agent_model=model,
    )
    raw = getattr(info, "raw", {}) or {}
    raw_paid_tier = raw.get("paidTier") if isinstance(raw, dict) else {}
    raw_paid_tier = raw_paid_tier if isinstance(raw_paid_tier, dict) else {}

    client = GoogleAntigravityClient(api_key=creds.access_token, model=model)
    ctx = client._ensure_project_context(creds.access_token, model)
    attempts = _antigravity_credit_attempts(ctx)

    print("oauth_token_present: True")
    print(f"project_id: {_mask(getattr(info, 'cloudaicompanion_project', '') or project_id)}")
    print(f"current_tier_id: {getattr(info, 'current_tier_id', '')}")
    print(f"raw_paid_tier_id: {raw_paid_tier.get('id', '')}")
    print(f"raw_paid_tier_name: {raw_paid_tier.get('name', '')}")
    print(f"context_tier_id: {ctx.tier_id}")
    print(f"context_paid_tier_id: {ctx.paid_tier_id}")
    print(f"context_paid_tier_name: {ctx.paid_tier_name}")
    print(f"context_has_google_one_ai_credits: {ctx.has_google_one_ai_credits}")
    print(f"context_google_one_ai_credit_amount: {ctx.google_one_ai_credit_amount}")
    print(f"credit_mode: {_antigravity_google_one_ai_credits_mode()}")
    print(f"credit_attempts: {attempts}")
    if attempts == [True]:
        print("status: Google One AI paid-tier routing enabled")
    elif attempts == [False, True]:
        print("status: base quota first, then Google One AI fallback")
    else:
        print("status: Google One AI paid-tier routing not enabled")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
