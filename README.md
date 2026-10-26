# Native Google Antigravity model provider for Hermes

This provider sends **native Gemini JSON over Google Cloud Code PA**, using Google
OAuth. There is no OpenAI-compatible HTTP endpoint, proxy, or server. Hermes'
installed `agent.gemini_native_adapter.GeminiNativeClient` owns message/tool
conversion, thought signatures, streaming, usage accounting, continuation tokens,
and response objects. Completion-shaped Python objects are internal Hermes ABI,
not an OpenAI wire protocol.

## Requirements and installation

Requires a recent Hermes with `ProviderProfile.create_client`, provider-owned
`auth_handler` / `refresh_credential`, `hermes_cli.auth_oauth_pkce_plugin`, and
`GeminiNativeClient`. These interfaces are described in the
[official provider plugin documentation](https://hermes-agent.nousresearch.com/docs/developer-guide/model-provider-plugin).
No Hermes core edits are required.

From this repository root, install **only** the model-provider directory into the
profile you intend to use:

```sh
HOME_DIR="${HERMES_HOME:-$HOME/.hermes}"
mkdir -p "$HOME_DIR/plugins/model-providers/google-antigravity"
cp plugins/model-providers/google-antigravity/{__init__.py,auth.py,transport.py,plugin.yaml} \
  "$HOME_DIR/plugins/model-providers/google-antigravity/"
hermes auth add google-antigravity
hermes chat --provider google-antigravity --model gemini-3.8-flash-tiered -q 'Hello'
```

Set `GOOGLE_ANTIGRAVITY_CLIENT_ID` and, if required by the OAuth registration,
`GOOGLE_ANTIGRAVITY_CLIENT_SECRET` in your secure environment. Alternatively supply
`$HERMES_HOME/auth/google_antigravity_client.json` with `client_id` and
`client_secret` (restrict permissions to 0600). Client credentials are not shipped.
The OAuth client must permit `http://127.0.0.1:51121/auth/callback`; this generic
Hermes PKCE callback differs from the legacy localhost callback. Browser login
with your OAuth registration was not exercised here. Hermes owns pooled tokens,
expiry, refresh, status and logout; this plugin never imports or modifies agy's
credential file automatically.

```sh
hermes auth status google-antigravity
hermes auth refresh google-antigravity
```

Installation commands above are instructions, not actions already performed by
this change. Do not apply `patches/hermes-agent-antigravity-core.patch` or copy
`agent/antigravity_*.py` into Hermes. Those files and the existing image/search
plugins are retained as **legacy** implementations, not used by this native model
provider. Their installation and compatibility are outside this update.

## Transport and catalog

Default endpoint: `https://daily-cloudcode-pa.googleapis.com`.
RPCs: `/v1internal:generateContent`,
`/v1internal:streamGenerateContent?alt=sse`, and
`/v1internal:fetchAvailableModels`. Requests carry `project=aicode-consumers`,
model, unique requestId, userAgent=antigravity, requestType=agent, and the untouched
Gemini `request`. Responses and SSE events are unwrapped before the installed
Gemini adapter processes them; gzip is decoded before unwrapping.

`fetch_models` queries the authenticated live catalog, rather than maintaining
stale model aliases. `fetch_catalog` also populates profile context windows from
`maxInputTokens` or the catalog's actual `maxTokens` field. This is the **model
context**, not agy's trajectory/checkpoint budget. Metadata exists in-process
after a successful catalog fetch; no persistent/global context override is made.
An absent credential or catalog failure does not invent a model list.

Cloud Code gates the catalog on the `User-Agent` header. Both catalog and
generation requests send the agy CLI format
(`antigravity/cli/<version> (aidev_client; os_type=…; arch=…; cl=…; auth_method=consumer)`).
A bare `antigravity/<version>` agent receives a reduced catalog without Gemini 4
Argon, and generation for those hidden models fails with 404 NOT_FOUND.

Observed in bounded live testing: `gemini-3.8-flash-tiered`,
`gemini-4-argon-medium` and `gemini-4-argon-high` each report a catalog context
of 1,048,576 and completed inference and a tool roundtrip. A full 1M-token input
was not exercised. `gemini-3.5-flash-extra-low` returned a retirement notice
instead of performing the task. Catalog membership alone does not guarantee service.
No silent model fallback is performed. Non-Gemini routed models, images, long
continuation chains and concurrent sessions have not been live-validated.

## Tests

Use the Python interpreter from your installed Hermes environment (discover the
runtime with `hermes --print-runtime-command`), with its dependencies available:

```sh
HERMES_AGENT_SOURCE="$HOME/.hermes/hermes-agent" python -m unittest discover -s tests -v
# Explicit opt-in: bills/uses subscription quota; reads agy token, never refreshes it.
python tests/live_smoke.py
```

The optional smoke accepts `ANTIGRAVITY_SMOKE_TOKEN_FILE` and
`ANTIGRAVITY_SMOKE_MODEL`. It checks live catalog context, a native function call,
a native function-result turn and final streaming output, then checks the source
token file is byte-identical. If that token is expired, renew it separately via
your approved login workflow; the smoke will not write it.

Unit tests cover native envelopes, bearer auth, native streaming thought/tool
signatures, usage, gzip, HTTP status mapping (401/403/429/503), OAuth expiry and
refresh outcomes, live-catalog context fields and reasoning configuration.
