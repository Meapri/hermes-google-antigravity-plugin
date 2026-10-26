"""Use Hermes' pooled PKCE implementation; never modify the agy token store."""
import json
import os
from pathlib import Path

from hermes_cli.auth_oauth_pkce_plugin import OAuthPKCEConfig, pkce_auth_handler, pkce_refresh_credential


def _config():
    from hermes_constants import get_hermes_home
    path = Path(get_hermes_home()) / 'auth/google_antigravity_client.json'
    data = json.loads(path.read_text()) if path.exists() else {}
    client_id = os.getenv('GOOGLE_ANTIGRAVITY_CLIENT_ID') or data.get('client_id', '')
    secret = os.getenv('GOOGLE_ANTIGRAVITY_CLIENT_SECRET') or data.get('client_secret', '')
    return OAuthPKCEConfig(
        client_id=client_id, authorize_url='https://accounts.google.com/o/oauth2/auth',
        token_url='https://oauth2.googleapis.com/token', allowed_hosts=('accounts.google.com', 'oauth2.googleapis.com'),
        scopes=('https://www.googleapis.com/auth/cloud-platform', 'https://www.googleapis.com/auth/userinfo.email',
                'https://www.googleapis.com/auth/userinfo.profile', 'https://www.googleapis.com/auth/cclog',
                'https://www.googleapis.com/auth/experimentsandconfigs', 'openid'),
        redirect_port=51121, redirect_path='/auth/callback', label='Google Antigravity',
        extra_authorize_params={'access_type': 'offline', 'prompt': 'consent'},
        extra_token_params={'client_secret': secret} if secret else {},
    )


def auth_handler(action, args):
    return pkce_auth_handler(_config())(action, args)


def refresh_credential(entry):
    return pkce_refresh_credential(_config())(entry)
