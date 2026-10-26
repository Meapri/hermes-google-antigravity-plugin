"""Native Google Antigravity provider; no core patch or HTTP proxy."""
from providers import register_provider
from providers.base import ProviderProfile
from .auth import auth_handler, refresh_credential


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
        return list(self.fetch_catalog(api_key=api_key, timeout=timeout)) or None

    def create_client(self, **kwargs):
        from .transport import AntigravityClient
        return AntigravityClient(**kwargs)


google_antigravity = AntigravityProfile(
    name='google-antigravity', aliases=('google-antigravity-oauth', 'antigravity-native'),
    api_mode='chat_completions', display_name='Google Antigravity (native Cloud Code)',
    description='Google OAuth, native Gemini wire, no proxy',
    base_url='https://daily-cloudcode-pa.googleapis.com', auth_type='oauth_external',
    supports_vision=True, supports_health_check=False,
    auth_handler=auth_handler, refresh_credential=refresh_credential,
)
register_provider(google_antigravity)
