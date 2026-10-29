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

From this repository root, install the four model-provider files into the
profile you intend to use:

```sh
HOME_DIR="${HERMES_HOME:-$HOME/.hermes}"
mkdir -p "$HOME_DIR/plugins/model-providers/google-antigravity"
cp plugins/model-providers/google-antigravity/{__init__.py,auth.py,transport.py,plugin.yaml} \
  "$HOME_DIR/plugins/model-providers/google-antigravity/"
hermes auth add google-antigravity
hermes chat --provider google-antigravity --model gemini-3.8-flash-tiered -q 'Hello'
```

The plugin ships default OAuth application credentials (client ID and secret)
taken from the upstream CLIProxyAPI Antigravity integration, where both values
are already public. This is a desktop-type public OAuth client, which Google's
desktop-app guidance does not treat as confidential, so login works without any
prerequisite setup. To use your own registration instead, set
`GOOGLE_ANTIGRAVITY_CLIENT_ID` / `GOOGLE_ANTIGRAVITY_CLIENT_SECRET` or supply
`client_id` / `client_secret` in `$HERMES_HOME/auth/google_antigravity_client.json`
(restrict the file to 0600). Environment variables take precedence over the
file, and both override the built-in defaults. User access and refresh tokens
remain Hermes-managed secrets.
The OAuth client must permit `http://127.0.0.1:51121/auth/callback`. The provider
uses Hermes' browser-based PKCE flow; login success depends on your OAuth
registration and account. Hermes owns pooled tokens, expiry,
refresh, status and logout; this plugin never imports or modifies agy's credential
file automatically. Do not share OAuth credentials, callback URLs or tokens.

On a headless remote host, start `hermes auth add google-antigravity` there and
forward `127.0.0.1:51121` from your local machine to the same port on the host.
Open the URL printed by Hermes in your local browser and complete authorization.
The callback listener has a limited lifetime; restart the login if it expires.

```sh
hermes auth status google-antigravity
hermes auth refresh google-antigravity
```

Installation commands above are instructions, not actions already performed by
this change. Do not apply `patches/hermes-agent-antigravity-core.patch` or copy
`agent/antigravity_*.py` into Hermes. Those files, the image plugin, and
`plugins/model-providers/google-antigravity/login.py` are retained as **legacy**
implementations, not used by this native model provider. In particular, do not
use the legacy `login.py` in place of `hermes auth add`: it writes a different
credential file rather than registering a credential in the Hermes pool.

## Google Search grounding web provider

`plugins/web/google_grounding` is a Hermes `web_search` backend built on the
native provider above. It reuses the pooled `google-antigravity` OAuth credential
(Hermes refreshes it) and the provider's `create_client`, calls a Gemini model
with `tools=[{"google_search": {}}]`, and maps grounding chunks/supports to the
standard rows `{title, url, description, position}`. Google redirect links are
resolved to the cited page URL. The model's grounded answer and the search
queries Google ran are added as `data.answer` and `data.search_queries`.
Search-only: extraction stays on your configured extract backend.

```sh
HOME_DIR="${HERMES_HOME:-$HOME/.hermes}"
mkdir -p "$HOME_DIR/plugins/web/google_grounding"
cp plugins/web/google_grounding/{__init__.py,plugin.yaml} "$HOME_DIR/plugins/web/google_grounding/"
hermes plugins enable web/google_grounding
hermes config set web.search_backend google_grounding   # optional: make it the search backend
```

Optional `config.yaml`:

```yaml
web:
  google_grounding:
    mode: auto                    # auto | fast (3.5-flash-lite x2) | standard (3.8-flash-high) | deep (4-argon-low)
    timeout: 60
    resolve_urls: true
```

Requires the `google-antigravity` model provider plugin installed and logged in.
Verified on Hermes v0.22.5 through `tools.web_tools.web_search_tool`.

## Search benchmark

`bench/` holds a 28-query golden set (freshness, navigational, stable facts, Korean,
tech news, long-tail, analysis) graded on expected values and official domains.
2026-10-26 run (`bench/results_tiers.jsonl`): fast 3.8 s median / 38 of 39 checks,
standard 8.4 s / 39 of 39, deep 14.9 s / 39 of 39, auto 6.6 s (p90 21.1 s) / 37 of 39.

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

### Context and compaction

The Cloud Code catalog's context value is metadata, not proof that an input of
that size will be accepted through this OAuth route. The plugin calls Cloud Code
directly and does not use the `agy` CLI conversation manager, but Google may apply
its own limits. Hermes also manages its own context and can compress a conversation
before the advertised limit. Check the active profile's resolved model window and
`compression` settings (including any per-model or absolute threshold) when
diagnosing an early compaction. See the [Hermes context compression guide](https://hermes-agent.nousresearch.com/docs/developer-guide/context-compression-and-caching).
A larger configured window or threshold does not increase the upstream limit.
Verify large inputs for the specific model and account before relying on them;
such tests consume quota.

This provider is distinct from the `agy`-backed DirectSDK provider. It registers
`google-antigravity` and the aliases `antigravity-native` and
`google-antigravity-oauth`; it does not claim the bare `antigravity` alias.

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

The native-provider tests use `unittest`. The grounding tests use `pytest` and
require it to be available alongside the installed Hermes dependencies. Set
`HERMES_AGENT_SOURCE` to the path of your installed Hermes source tree; the
default location below may differ in your installation.

```sh
HERMES_AGENT_SOURCE="$HOME/.hermes/hermes-agent" python -m unittest discover -s tests -p 'test_native_provider.py' -v
HERMES_AGENT_SOURCE="$HOME/.hermes/hermes-agent" python -m pytest tests/test_google_grounding_rows.py
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
