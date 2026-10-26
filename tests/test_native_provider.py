import importlib.util
import json
import pathlib
import sys
import unittest

import httpx

ROOT = pathlib.Path(__file__).resolve().parents[1]
import os
sys.path.insert(0, os.environ.get('HERMES_AGENT_SOURCE', str(pathlib.Path.home() / '.hermes/hermes-agent')))
SPEC = importlib.util.spec_from_file_location('antigravity_test_plugin', ROOT / 'plugins/model-providers/google-antigravity/__init__.py', submodule_search_locations=[str(ROOT / 'plugins/model-providers/google-antigravity')])
plugin = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = plugin
SPEC.loader.exec_module(plugin)


class NativeTests(unittest.TestCase):
    def test_profile_client_sends_native_envelope(self):
        seen = []
        def backend(request):
            seen.append(request)
            return httpx.Response(200, json={'response': {'candidates': [{'content': {'parts': [{'text': 'OK'}]}, 'finishReason': 'STOP'}], 'usageMetadata': {'promptTokenCount': 2, 'candidatesTokenCount': 1, 'totalTokenCount': 3}}})
        client = plugin.google_antigravity.create_client(api_key='test-token', transport=httpx.MockTransport(backend))
        self.assertIsNotNone(client, 'profile must own its native client')
        with client:
            answer = client.chat.completions.create(model='gemini-4-argon-medium', messages=[{'role': 'user', 'content': 'hello'}])
        self.assertEqual(answer.choices[0].message.content, 'OK')
        self.assertEqual(answer.usage.total_tokens, 3)
        request = seen[0]
        self.assertEqual(str(request.url), 'https://daily-cloudcode-pa.googleapis.com/v1internal:generateContent')
        body = json.loads(request.content)
        self.assertEqual(body['model'], 'gemini-4-argon-medium')
        self.assertEqual(body['project'], 'aicode-consumers')
        self.assertEqual(body['userAgent'], 'antigravity')
        self.assertEqual(body['requestType'], 'agent')
        self.assertTrue(body['requestId'])
        self.assertIn('contents', body['request'])
        self.assertNotIn('messages', body['request'])
        self.assertEqual(request.headers['authorization'], 'Bearer test-token')
        self.assertNotIn('x-goog-api-key', request.headers)

    def test_stream_tools_thoughts_and_usage(self):
        seen = []
        def backend(request):
            seen.append(json.loads(request.content))
            event = {'response': {'candidates': [{'content': {'parts': [{'text': 'private', 'thought': True}, {'functionCall': {'name': 'lookup', 'args': {'key': 'x'}}, 'thoughtSignature': 'opaque-signature'}]}, 'finishReason': 'STOP'}], 'usageMetadata': {'promptTokenCount': 8, 'candidatesTokenCount': 2, 'totalTokenCount': 10}}}
            return httpx.Response(200, content=('data: ' + json.dumps(event) + '\n\n').encode())
        with plugin.google_antigravity.create_client(api_key='test', transport=httpx.MockTransport(backend)) as client:
            chunks = list(client.chat.completions.create(model='gemini-4-argon-medium', messages=[{'role': 'user', 'content': 'lookup'}], stream=True))
        self.assertEqual(chunks[-1].usage.total_tokens, 10)
        calls = [c.choices[0].delta.tool_calls[0] for c in chunks if getattr(c.choices[0].delta, 'tool_calls', None)]
        self.assertEqual(calls[0].extra_content['google']['thought_signature'], 'opaque-signature')
        self.assertTrue(any(getattr(c.choices[0].delta, 'reasoning_content', None) == 'private' for c in chunks))

    def test_catalog_has_live_context(self):
        self.assertTrue(callable(getattr(plugin.google_antigravity, 'fetch_catalog', None)), 'native catalog hook required')
        def backend(request):
            self.assertEqual(request.url.path, '/v1internal:fetchAvailableModels')
            return httpx.Response(200, json={'models': {'gemini-4-argon-medium': {'maxInputTokens': 1048576, 'maxOutputTokens': 65536}}})
        catalog = plugin.google_antigravity.fetch_catalog(api_key='test', transport=httpx.MockTransport(backend))
        self.assertEqual(catalog['gemini-4-argon-medium']['maxInputTokens'], 1048576)
        self.assertEqual(plugin.google_antigravity.model_capabilities['gemini-4-argon-medium']['context_window'], 1048576)

    def test_auth_hooks_and_expiry(self):
        self.assertTrue(callable(plugin.google_antigravity.auth_handler))
        self.assertTrue(callable(plugin.google_antigravity.refresh_credential))
        from hermes_cli.auth_oauth_pkce_plugin import _is_usable
        self.assertFalse(_is_usable('test', 1000, 2000))
        self.assertTrue(_is_usable('test', 3000, 2000))

    def test_requests_use_cli_user_agent_for_full_catalog(self):
        # Cloud Code hides Gemini 4 Argon unless the User-Agent matches the agy CLI format.
        agents = []
        def backend(request):
            agents.append(request.headers['user-agent'])
            if request.url.path.endswith('fetchAvailableModels'):
                return httpx.Response(200, json={'models': {}})
            return httpx.Response(200, json={'response': {'candidates': [{'content': {'parts': [{'text': 'OK'}]}, 'finishReason': 'STOP'}]}})
        plugin.google_antigravity.fetch_catalog(api_key='test', transport=httpx.MockTransport(backend))
        with plugin.google_antigravity.create_client(api_key='test', transport=httpx.MockTransport(backend)) as client:
            client.chat.completions.create(model='gemini-4-argon-medium', messages=[{'role': 'user', 'content': 'hi'}])
        self.assertEqual(len(agents), 2)
        for agent in agents:
            self.assertTrue(agent.startswith('antigravity/cli/'), agent)
            self.assertIn('auth_method=consumer', agent)

    def test_foreign_base_url_falls_back_to_cloud_code(self):
        # Hermes may pass the config's model.base_url from another provider (e.g. process://...).
        seen = []
        def backend(request):
            seen.append(str(request.url))
            return httpx.Response(200, json={'response': {'candidates': [{'content': {'parts': [{'text': 'OK'}]}, 'finishReason': 'STOP'}]}})
        for foreign in ('process://claude-subscription-directsdk-experimental', 'https://openrouter.ai/api/v1'):
            with self.subTest(base_url=foreign):
                with plugin.google_antigravity.create_client(api_key='t', base_url=foreign, transport=httpx.MockTransport(backend)) as client:
                    client.chat.completions.create(model='gemini-4-argon-medium', messages=[{'role': 'user', 'content': 'hi'}])
                self.assertTrue(seen[-1].startswith('https://daily-cloudcode-pa.googleapis.com/v1internal:'), seen[-1])

    def test_aliases_do_not_shadow_agy_directsdk_provider(self):
        # antigravity-subscription-directsdk already owns the bare 'antigravity' alias.
        self.assertNotIn('antigravity', plugin.google_antigravity.aliases)
        self.assertNotIn('agy', plugin.google_antigravity.aliases)

    def test_catalog_native_max_tokens(self):
        def backend(request):
            return httpx.Response(200, json={'models': {'gemini-live': {'maxTokens': 1048576}}})
        plugin.google_antigravity.fetch_catalog(api_key='test', transport=httpx.MockTransport(backend))
        self.assertEqual(plugin.google_antigravity.model_capabilities['gemini-live']['context_window'], 1048576)

    def test_http_errors_keep_status(self):
        from agent.gemini_native_adapter import GeminiAPIError
        for status in (401, 403, 429, 503):
            with self.subTest(status=status):
                def backend(request):
                    return httpx.Response(status, json={'error': {'status': 'UNAUTHENTICATED', 'message': 'test failure'}}, headers={'retry-after': '2'})
                with plugin.google_antigravity.create_client(api_key='test', transport=httpx.MockTransport(backend)) as client:
                    with self.assertRaises(GeminiAPIError) as caught:
                        list(client.chat.completions.create(model='gemini-test', messages=[], stream=True))
                self.assertEqual(caught.exception.status_code, status)
                self.assertNotIn('Standard key', str(caught.exception))

    def test_gzip_native_response(self):
        import gzip
        def backend(request):
            body = json.dumps({'response': {'candidates': [{'content': {'parts': [{'text': 'decoded'}]}, 'finishReason': 'STOP'}]}}).encode()
            return httpx.Response(200, headers={'content-encoding': 'gzip'}, stream=httpx.ByteStream(gzip.compress(body)))
        with plugin.google_antigravity.create_client(api_key='test', transport=httpx.MockTransport(backend)) as c:
            self.assertEqual(c.chat.completions.create(model='gemini-test', messages=[]).choices[0].message.content, 'decoded')

    def test_reasoning_hook_uses_native_thinking_config(self):
        body = plugin.google_antigravity.build_extra_body(model='gemini-4-argon-medium', reasoning_config={'effort': 'high', 'enabled': True})
        self.assertIn('thinking_config', body)

    def test_refresh_rotates_pool_fields_without_external_file(self):
        from types import SimpleNamespace
        from unittest.mock import patch
        entry = SimpleNamespace(provider='google-antigravity', id='test', refresh_token='old')
        from hermes_cli.auth_constants import AuthError
        with patch.dict('os.environ', {'GOOGLE_ANTIGRAVITY_CLIENT_ID': 'test-client'}):
            with patch('hermes_cli.auth.read_credential_pool', return_value=[]), patch('hermes_cli.auth_constants.httpx.post', return_value=httpx.Response(200, json={'access_token': 'new', 'expires_in': 3600})):
                fields = plugin.google_antigravity.refresh_credential(entry)
                self.assertEqual(fields['access_token'], 'new')
                self.assertEqual(fields['refresh_token'], 'old')
                self.assertGreater(fields['expires_at_ms'], 0)
            with patch('hermes_cli.auth.read_credential_pool', return_value=[]), patch('hermes_cli.auth_constants.httpx.post', return_value=httpx.Response(400, json={'error': 'invalid_grant'})):
                with self.assertRaises(AuthError) as caught:
                    plugin.google_antigravity.refresh_credential(entry)
                self.assertEqual(caught.exception.code, 'invalid_grant')

if __name__ == '__main__':
    unittest.main()
