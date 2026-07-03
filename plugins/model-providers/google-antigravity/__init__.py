"""Google Antigravity model-provider (Cloud Code PA v1internal, Google OAuth).

Native Hermes provider — request path handled by AntigravityTransport
(api_mode='antigravity_generate'). Lives in $HERMES_HOME/plugins so it survives
`hermes update`.
"""
from providers import register_provider
from providers.base import ProviderProfile

google_antigravity = ProviderProfile(
    name="google-antigravity",
    aliases=("antigravity", "google-antigravity-oauth"),
    api_mode="chat_completions",
    display_name="Google Antigravity (Cloud Code)",
    description="Google Antigravity via Cloud Code PA (OAuth)",
    signup_url="https://antigravity.google/",
    base_url="https://cloudcode-pa.googleapis.com",
    auth_type="oauth_external",
    supports_vision=True,
    fallback_models=(
        "gemini-3.5-flash-high", "gemini-3.5-flash-medium", "gemini-3.5-flash-low",
        "gemini-3.5-flash-extra-low",
        "gemini-3.1-pro-high", "gemini-3.1-pro-low",
        "gemini-3-flash-high", "gemini-3-flash-low",
        "claude-opus-4-6-thinking", "claude-sonnet-4-6-thinking",
        "gpt-oss-120b",
    ),
)
register_provider(google_antigravity)
