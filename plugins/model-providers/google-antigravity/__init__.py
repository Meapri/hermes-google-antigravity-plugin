"""Native Google Antigravity provider; no core patch or HTTP proxy."""
import time

from providers import register_provider
from providers.base import ProviderProfile
from .auth import auth_handler, refresh_credential

# Gemini ids this provider is live-validated on, in picker order. Used as the curated floor when the
# live catalog is unavailable (not signed in, expired token, network error).
_GEMINI_MODELS = (
    'gemini-3.8-flash-tiered', 'gemini-3.8-flash-high', 'gemini-3.8-flash-medium', 'gemini-3.8-flash-low',
    'gemini-4-argon-high', 'gemini-4-argon-medium', 'gemini-4-argon-low',
    'gemini-3-flash', 'gemini-3.5-flash-lite', 'gemini-3.1-flash-lite',
    'gemini-2.5-pro', 'gemini-2.5-flash',
)


def _pooled_token():
    """A usable access token from Hermes' own credential pool, or ''.

    Local addition: `hermes model`, the `/model` picker and the Desktop model dropdown build their
    catalog outside the inference path, so no key is ever handed to `fetch_models`. Reading the
    pooled row here lets those surfaces query the live Cloud Code catalog instead of showing a
    static list. Never refreshes or writes; an expired row simply yields ''.
    """
    try:
        from agent.credential_pool import load_pool
    except Exception:
        return ''
    now_ms = int(time.time() * 1000)
    try:
        entries = load_pool('google-antigravity').entries()
    except Exception:
        return ''
    for entry in entries:
        token = str(getattr(entry, 'access_token', '') or '').strip()
        expires = getattr(entry, 'expires_at_ms', None)
        if token and (not expires or int(expires) > now_ms):
            return token
    return ''


class AntigravityProfile(ProviderProfile):
    def build_extra_body(self, *, session_id=None, **context):
        from agent.transports.chat_completions import _build_gemini_thinking_config
        config = _build_gemini_thinking_config(context.get('model') or '', context.get('reasoning_config'))
        return {'thinking_config': config} if config else {}

    def fetch_catalog(self, *, api_key=None, timeout=8.0, transport=None):
        import httpx
        from .transport import cli_user_agent
        if not api_key:
            return {}
        with httpx.Client(transport=transport, timeout=timeout) as client:
            response = client.post(self.base_url + '/v1internal:fetchAvailableModels',
                                   headers={'Authorization': f'Bearer {api_key}', 'User-Agent': cli_user_agent()},
                                   json={'project': 'aicode-consumers'})
            response.raise_for_status()
            models = response.json().get('models', {})
        for name, metadata in models.items():
            window = metadata.get('maxInputTokens') or metadata.get('maxTokens')
            if isinstance(window, int) and window > 0:
                self.model_capabilities.setdefault(name, {})['context_window'] = window
        return models

    def fetch_models(self, *, api_key=None, base_url=None, timeout=8.0):
        # No key on the catalog paths: fall back to the pooled OAuth row so the catalog is live.
        _ensure_picker_catalog()
        return list(self.fetch_catalog(api_key=api_key or _pooled_token(), timeout=timeout)) or None

    def listed(self) -> bool:
        # Runs on every picker build: the only hook guaranteed to fire after hermes_cli.models has
        # finished importing, so the live-catalog fetcher gets installed even when the plugin was
        # first loaded during that module's own import (circular, retried here).
        _ensure_picker_catalog()
        return super().listed()

    def create_client(self, **kwargs):
        from .transport import AntigravityClient
        return AntigravityClient(**kwargs)


google_antigravity = AntigravityProfile(
    name='google-antigravity', aliases=('google-antigravity-oauth', 'antigravity-native'),
    api_mode='chat_completions', display_name='Google Antigravity (native Cloud Code)',
    description='Google OAuth, native Gemini wire, no proxy',
    base_url='https://daily-cloudcode-pa.googleapis.com', auth_type='oauth_external',
    supports_vision=True, supports_health_check=False,
    # Curated floor for the pickers. `_profile_live_catalog` serves `fallback_models` alone for a
    # non-api_key provider, so an empty tuple renders an empty model list (#picker).
    fallback_models=_GEMINI_MODELS,
    model_capabilities={
        model: {'supports_reasoning': True, 'supports_vision': True, 'supports_tools': True,
                'context_window': 1048576, 'model_family': 'gemini'}
        for model in _GEMINI_MODELS
    },
    auth_handler=auth_handler, refresh_credential=refresh_credential,
)
register_provider(google_antigravity)


def _picker_catalog(normalized='google-antigravity', force_refresh=False):
    """Live model ids for the provider pickers, the way the Nous Portal provider does it.

    Hermes v0.22.x builds a non-api_key profile's picker rows from `fallback_models` only: it never
    calls `fetch_models`, so a static list is all a plugin can offer by itself. This fetcher is
    registered into `hermes_cli.models._PROVIDER_CATALOG_FETCHERS` (the per-provider live-catalog
    table the pickers consult first) so the list mirrors the account's actual catalog: models the
    user cannot select drop out, new ones appear without a plugin release, and per-model context
    windows land in `model_capabilities` via `fetch_catalog`.

    Returns None on any miss so the caller degrades to the curated floor instead of an empty list.
    """
    token = _pooled_token()
    if not token:
        return None
    try:
        catalog = google_antigravity.fetch_catalog(api_key=token, timeout=6.0)
    except Exception:
        return None
    # Placeholder rows (`chat_20706`, `tab_flash_lite_preview`, ...) are internal router slots, not
    # selectable models, and they are the only rows whose public name is not a model id. Every real
    # row carries `model: MODEL_PLACEHOLDER_M###` internally, so match the NAME shape, not metadata.
    ids = [name for name, meta in catalog.items()
           if isinstance(meta, dict) and meta and not name.startswith(('chat_', 'tab_'))]
    if not ids:
        return None
    preferred = [model for model in _GEMINI_MODELS if model in ids]
    return preferred + sorted(model for model in ids if model not in preferred)


_picker_registered = False


def _ensure_picker_catalog():
    """Install the live-catalog fetcher once, tolerating an import-time circular miss.

    The plugin is often first imported from inside `hermes_cli.models` itself, where that module is
    still initialising and its catalog table does not exist yet. Retried from `listed()` (every
    picker build) and `fetch_models` until it lands.
    """
    global _picker_registered
    if _picker_registered:
        return True
    try:
        from hermes_cli.models import _PROVIDER_CATALOG_FETCHERS
    except Exception:
        return False
    _PROVIDER_CATALOG_FETCHERS.setdefault('google-antigravity', _picker_catalog)
    _picker_registered = True
    return True


_ensure_picker_catalog()
