"""Google Antigravity OAuth provider profile for Hermes Agent.

This plugin registers the `google-antigravity` model provider profile. Runtime
OAuth and transport support is supplied by the companion Hermes core patch in
this repository until Hermes exposes plugin hooks for custom OAuth resolvers and
model clients.
"""

from __future__ import annotations

from typing import Any

from providers import register_provider
from providers.base import ProviderProfile


class GoogleAntigravityProfile(ProviderProfile):
    """Google Antigravity OAuth profile.

    Antigravity uses the Cloud Code PA transport shape, but with Antigravity
    OAuth credentials, headers, model IDs, and UI thinking tier semantics.
    """

    def build_extra_body(
        self, *, session_id: str | None = None, **context: Any
    ) -> dict[str, Any]:
        extra: dict[str, Any] = {}
        if session_id:
            extra["session_id"] = session_id
        model = str(context.get("model") or "")
        normalized = model.strip().lower()
        vendor, sep, bare = normalized.partition("/")
        if sep and vendor in {"google", "gemini"}:
            normalized = bare.strip() or normalized
        if normalized.startswith("gemini-") and "pro" in normalized:
            if normalized.endswith("-high"):
                extra["thinking_config"] = {"thinkingLevel": "high"}
            if normalized.endswith("-low"):
                extra["thinking_config"] = {"thinkingLevel": "low"}
        return extra


google_antigravity = GoogleAntigravityProfile(
    name="google-antigravity",
    aliases=("antigravity", "antigravity-oauth"),
    display_name="Google Antigravity (OAuth)",
    description="Google Antigravity via OAuth + Code Assist; no Gemini CLI dependency.",
    api_mode="chat_completions",
    env_vars=(),
    base_url="cloudcode-pa://antigravity",
    auth_type="oauth_external",
    supports_health_check=False,
    fallback_models=(
        "gemini-3.5-flash-high",
        "gemini-3.5-flash-medium",
        "gemini-3.1-pro-high",
        "gemini-3.1-pro-low",
        "claude-sonnet-4-6-thinking",
        "claude-opus-4-6-thinking",
        "gpt-oss-120b-medium",
        "gemini-3-flash",
        "claude-sonnet-4-6",
    ),
)

register_provider(google_antigravity)
