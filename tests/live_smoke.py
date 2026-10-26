"""Opt-in bounded live smoke. Reads agy credentials without modifying them."""
import json
import os
import pathlib
import runpy

root = pathlib.Path(__file__).resolve().parents[1]
plugin = runpy.run_path(str(root / 'tests/test_native_provider.py'))['plugin']
path = pathlib.Path(os.environ.get('ANTIGRAVITY_SMOKE_TOKEN_FILE', pathlib.Path.home() / '.gemini/antigravity-cli/antigravity-oauth-token'))
before = path.read_bytes()
token = json.loads(before)['token']['access_token']
p = plugin.google_antigravity
catalog = p.fetch_catalog(api_key=token)
model = os.environ.get('ANTIGRAVITY_SMOKE_MODEL', 'gemini-3.8-flash-tiered')
print('catalog context:', model, p.model_capabilities.get(model))
messages = [{'role': 'user', 'content': 'Call echo with value native-test, then report its result.'}]
tools = [{'type': 'function', 'function': {'name': 'echo', 'description': 'Return value unchanged', 'parameters': {'type': 'object', 'properties': {'value': {'type': 'string'}}, 'required': ['value']}}}]
with p.create_client(api_key=token) as c:
    first = c.chat.completions.create(model=model, messages=messages, tools=tools, tool_choice='required', max_tokens=2048)
    message = first.choices[0].message
    calls = message.tool_calls or []
    if not calls:
        raise RuntimeError('Backend returned no tool call')
    serialized = []
    for call in calls:
        item = {'id': call.id, 'type': 'function', 'function': {'name': call.function.name, 'arguments': call.function.arguments}}
        if getattr(call, 'extra_content', None):
            item['extra_content'] = call.extra_content
        serialized.append(item)
    messages.append({'role': 'assistant', 'content': message.content, 'tool_calls': serialized})
    for call in calls:
        messages.append({'role': 'tool', 'tool_call_id': call.id, 'content': json.dumps({'value': json.loads(call.function.arguments)['value']})})
    chunks = list(c.chat.completions.create(model=model, messages=messages, tools=tools, stream=True, max_tokens=2048))
    text = ''.join(getattr(chunk.choices[0].delta, 'content', '') or '' for chunk in chunks)
    assert 'native-test' in text, 'Tool result missing from final response'
    print('tool roundtrip: PASS; final:', text)
assert path.read_bytes() == before, 'Original token file changed'
print('original credential file unchanged')
