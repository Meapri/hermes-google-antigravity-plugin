"""Cloud Code envelopes only; Hermes owns Gemini conversion and streaming."""
from __future__ import annotations

import json
import uuid
from typing import Any

import httpx
from agent.gemini_native_adapter import GeminiNativeClient, gemini_http_error

BASE_URL = 'https://daily-cloudcode-pa.googleapis.com'
CLI_VERSION = '1.4.0'
CLI_CHANGELIST = '1000494485'


def cli_user_agent() -> str:
    """User-Agent in the agy CLI format.

    Cloud Code gates the model catalog on this header: a bare
    ``antigravity/<version>`` agent gets a reduced catalog without Gemini 4
    Argon, and generation for hidden models fails with 404 NOT_FOUND.
    """
    import platform
    system = platform.system().lower() or 'linux'
    machine = platform.machine().lower()
    arch = {'aarch64': 'arm64', 'x86_64': 'amd64', 'amd64': 'amd64', 'arm64': 'arm64'}.get(machine, machine or 'amd64')
    return (f'antigravity/cli/{CLI_VERSION} (aidev_client; os_type={system}; arch={arch}; '
            f'cl={CLI_CHANGELIST}; auth_method=consumer)')


def _cloud_code_base_url(base_url) -> str:
    """Keep only Cloud Code PA endpoints.

    Hermes can hand a provider the config's ``model.base_url`` even when that
    value belongs to another provider (for example
    ``process://claude-subscription-directsdk-experimental``). Any URL that is
    not an HTTPS ``*cloudcode-pa.googleapis.com`` host falls back to BASE_URL.
    """
    try:
        url = httpx.URL(str(base_url or ''))
    except Exception:
        return BASE_URL
    host = url.host or ''
    if url.scheme == 'https' and (host == 'cloudcode-pa.googleapis.com' or host.endswith('-cloudcode-pa.googleapis.com')):
        return f'https://{host}'
    return BASE_URL


class _UnwrapStream(httpx.SyncByteStream):
    def __init__(self, response, sse):
        self.response, self.sse = response, sse
        self.stream = response.iter_bytes()

    def __iter__(self):
        if not self.sse:
            data = json.loads(b''.join(self.stream))
            yield json.dumps(data.get('response', data)).encode()
            return
        pending = b''
        for chunk in self.stream:
            pending += chunk
            while b'\n' in pending:
                line, pending = pending.split(b'\n', 1)
                yield self._line(line) + b'\n'
        if pending:
            yield self._line(pending) + b'\n\n'

    @staticmethod
    def _line(line):
        if line.startswith(b'data:'):
            raw = line[5:].strip()
            if raw and raw != b'[DONE]':
                data = json.loads(raw)
                return b'data: ' + json.dumps(data.get('response', data)).encode()
        return line

    def close(self):
        self.response.close()


class CloudCodeTransport(httpx.BaseTransport):
    def __init__(self, inner=None, project='aicode-consumers'):
        self.inner = inner or httpx.HTTPTransport()
        self.project = project

    def handle_request(self, request):
        model, method = request.url.path.split('/models/', 1)[1].split(':', 1)
        envelope = {'project': self.project, 'model': model, 'requestId': 'agent-' + str(uuid.uuid4()),
                    'userAgent': 'antigravity', 'requestType': 'agent', 'request': json.loads(request.content)}
        url = request.url.copy_with(path='/v1internal:' + method)
        headers = dict(request.headers)
        headers.pop('content-length', None)
        forwarded = httpx.Request('POST', url, headers=headers, json=envelope, extensions=request.extensions)
        response = self.inner.handle_request(forwarded)
        if response.status_code == 200:
            headers = dict(response.headers)
            headers.pop('content-length', None)
            headers.pop('content-encoding', None)
            return httpx.Response(response.status_code, headers=headers,
                                  stream=_UnwrapStream(response, method == 'streamGenerateContent'))
        return response

    def close(self):
        self.inner.close()


class AntigravityClient(GeminiNativeClient):
    MISSING_KEY_ERROR = 'Sign in with `hermes auth add google-antigravity`.'

    def __init__(self, *, api_key: str, base_url=None, transport=None, project='aicode-consumers', timeout=None, default_headers=None, **_: Any):
        # Keep the native Gemini builder/continuation/stream machinery intact.
        super().__init__(api_key=api_key, base_url=_cloud_code_base_url(base_url),
                         default_headers=default_headers, timeout=timeout,
                         http_client=httpx.Client(transport=CloudCodeTransport(transport, project),
                                                 timeout=timeout or 180.0))

    def _auth_headers(self):
        return {'Authorization': f'Bearer {self.api_key}'}

    def _headers(self):
        return {**super()._headers(), 'User-Agent': cli_user_agent()}

    def _http_error(self, response, body_text=None):
        return gemini_http_error(response, body_text=body_text, base_url=self.base_url, key_guidance=False)
