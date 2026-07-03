# hermes-google-antigravity-plugin

Native **Google Antigravity (Cloud Code PA)** provider for the
[Hermes Agent](https://github.com/) — a first-class `oauth_external` provider you
sign into with your **Google account** (subscription-style), exactly like the
other OAuth providers (`hermes auth add`, `hermes model`, `hermes chat`). No API
key, no dummy token: the access token lives in an auto-refreshed OAuth file.

Models are served through the `cloudcode-pa.googleapis.com/v1internal:generateContent`
API and exposed to Hermes as OpenAI-style `chat.completions` via a thin shim, so
Gemini / Claude / GPT-OSS models routed through Antigravity behave like any other
Hermes model (tool calls, thinking, multi-turn).

## Layout

```
agent/
  antigravity_native_adapter.py     # OpenAI chat.completions shim over the cloudcode-pa client
  antigravity_oauth.py              # OAuth login (PKCE) + runtime token resolution (auto-refresh)
  antigravity_cloudcode.py          # vendored Cloud Code PA client (generateContent, refresh, model aliases)
  antigravity_cloudcode_config.py   # settings/paths for the vendored client
patches/
  hermes-agent-antigravity-core.patch   # integration patch (9 Hermes core files)
plugins/model-providers/google-antigravity/
  __init__.py                       # ProviderProfile(name="google-antigravity", auth_type="oauth_external")
  plugin.yaml                       # plugin manifest
  login.py                          # standalone login helper
```

## How it integrates (the core patch)

`patches/hermes-agent-antigravity-core.patch` touches 9 files:

| File | What it adds |
|------|--------------|
| `hermes_cli/runtime_provider.py` | `google-antigravity` short-circuit (Vertex-style): resolves the OAuth token → `cloudcode-pa` base URL, never falls through to OpenRouter |
| `agent/auxiliary_client.py` | matching branch in `resolve_provider_client` for auxiliary tasks |
| `agent/agent_runtime_helpers.py` | `create_openai_client` hook — `is_antigravity_base_url` → instantiates the native shim |
| `hermes_cli/auth.py` | `PROVIDER_REGISTRY["google-antigravity"]` = `oauth_external` |
| `hermes_cli/providers.py` | `HERMES_OVERLAYS` entry (`oauth_external`, cloudcode-pa) |
| `hermes_cli/auth_commands.py` | `hermes auth add google-antigravity` → Google login + pooled credential |
| `hermes_cli/main.py` | `hermes model` dispatch to the antigravity flow |
| `hermes_cli/model_setup_flows.py` | `_model_flow_google_antigravity` (login + model picker) |
| `hermes_cli/models.py` | provider entry + curated model list |

The shim reads/refreshes the OAuth file itself, so the `api_key` Hermes routes is
only used to satisfy routing — the real auth is always the live Google token.

## Install

### Recommended: via the recovery system

Deploy through [hermes-patch-manager](https://github.com/Meapri/hermes-patch-manager)
as a mod (`source-patch` + `new-file` components). It re-applies the patch and
re-copies the modules automatically after `hermes update` (which does a
`git reset` + venv rebuild), with an LLM-assisted merge tier for upstream drift.

### Manual

```bash
AGENT=~/.hermes/hermes-agent
# 1) new modules
cp agent/antigravity_*.py "$AGENT/agent/"
# 2) core integration patch
git -C "$AGENT" apply patches/hermes-agent-antigravity-core.patch
# 3) plugin (survives hermes update — lives outside the repo)
mkdir -p ~/.hermes/plugins/model-providers/google-antigravity
cp plugins/model-providers/google-antigravity/* ~/.hermes/plugins/model-providers/google-antigravity/
# 4) restart the gateway so the re-applied source is loaded
```

## Usage

```bash
hermes auth add google-antigravity     # Google account OAuth login
hermes model                           # pick "Google Antigravity" + a model
hermes chat --provider google-antigravity --model gemini-3.5-flash-high
```

Available models (curated): `gemini-3.1-pro-high/low`, `gemini-3.5-flash-high/medium/low`,
`gemini-3-flash-high/low`, `claude-opus-4-6-thinking`, `claude-sonnet-4-6-thinking`,
`gpt-oss-120b`.

## Image generation

Also ships an `image_gen` backend plugin
(`plugins/image_gen/google-antigravity/`) exposing Antigravity's Gemini image
models (Nano Banana family) over the same OAuth session. It delegates to the
vendored client's `generate_image()` and lists models **live** from the backend
(`fetch_available_image_models`), with a curated fallback when the live catalog
is unavailable.

Enable + activate in `~/.hermes/config.yaml`:

```yaml
plugins:
  enabled:
    - image_gen/google-antigravity
image_gen:
  provider: google-antigravity
  model: gemini-3.1-flash-image
```

Models: `gemini-3-pro-image` (Nano Banana Pro), `gemini-3.1-flash-image`
(Nano Banana), `gemini-2.5-flash-image`.

## Web search (Google grounding)

Also ships a `web_search` backend plugin (`plugins/web/google_grounding/`) that
answers queries with **Google Search grounding** through Antigravity's
`gemini-3.5-flash-high` — cited, current sources, no API key. It calls the
vendored client's `grounded_search()` (which adds `tools=[{"google_search": {}}]`
to the generateContent request) and maps the response's `groundingChunks` to
Hermes search results.

Enable + activate in `~/.hermes/config.yaml`:

```yaml
plugins:
  enabled:
    - web/google_grounding
web:
  search_backend: google_grounding
```

Search-only (cited source URLs + snippets); page-content extraction stays on
your configured `web.extract_backend`.

## Login notes

`run_antigravity_login()` uses the PKCE authorization-code flow (the device-code
flow is rejected by the Antigravity client as `invalid_client`). It runs a local
callback server on `127.0.0.1:51121` **and** reads a pasted callback URL/code from
stdin concurrently — whichever arrives first wins:

- **Local machine / SSH tunnel** (`ssh -L 51121:localhost:51121 <host>`): the
  browser redirect is captured automatically.
- **Headless remote, no tunnel**: the redirect page fails to load — just copy the
  full redirected URL (or the `code=` value) and paste it into the terminal.

Token expiry is stored in **milliseconds** to match the vendored client, so a
fresh login is not seen as instantly expired.

## Note

This replaces an earlier gRPC/quota-oriented implementation. That approach was
incompatible with current Hermes; its files remain in git history. The vendored
Cloud Code client is adapted from Meapri/Antigravity-Proxy.
