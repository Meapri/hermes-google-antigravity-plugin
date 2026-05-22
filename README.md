# Hermes Google Antigravity OAuth Provider

Unofficial Google Antigravity OAuth provider for [Hermes Agent](https://github.com/NousResearch/hermes-agent).

This package turns the Antigravity work into a clean Hermes plugin bundle:

- `plugins/model-providers/google-antigravity/` registers the `google-antigravity` provider profile.
- `agent/google_antigravity_oauth.py` reuses Hermes' generic Google OAuth machinery with Antigravity's OAuth client, scopes, callback port, credential file, and project ID handling.
- `agent/google_antigravity_adapter.py` adapts Antigravity's Cloud Code PA endpoint to Hermes' OpenAI-compatible chat-completions client interface.
- `patches/hermes-agent-antigravity-core.patch` wires Hermes' current runtime, auth, model picker, and quota paths to the plugin until Hermes exposes first-class plugin hooks for custom model clients and OAuth resolvers.

## Status

Experimental / unofficial. Use at your own risk. Antigravity OAuth endpoints and model IDs may change upstream.

This integration intentionally does not depend on Gemini CLI.

## Supported provider and aliases

- Provider: `google-antigravity`
- Aliases: `antigravity`, `antigravity-oauth`
- Base URL marker: `cloudcode-pa://antigravity`

Curated model IDs:

- `gemini-3.5-flash-high`
- `gemini-3.5-flash-medium`
- `gemini-3.1-pro-high`
- `gemini-3.1-pro-low`
- `claude-sonnet-4-6-thinking`
- `claude-opus-4-6-thinking`
- `gpt-oss-120b-medium`
- `gemini-3-flash`
- `claude-sonnet-4-6`

The adapter preserves Antigravity UI tier semantics for Gemini Pro suffixes such as `-high` and `-low` by translating them into Gemini `thinkingLevel` settings when needed.

## Install

```bash
git clone https://github.com/Meapri/hermes-google-antigravity-plugin.git
cd hermes-google-antigravity-plugin
./scripts/install.sh
```

By default the installer assumes Hermes is installed at:

```text
$HOME/.hermes/hermes-agent
```

Override if needed:

```bash
HERMES_AGENT_DIR=/path/to/hermes-agent ./scripts/install.sh
```

The installer copies the provider plugin to:

```text
$HERMES_HOME/plugins/model-providers/google-antigravity
```

and copies the companion runtime files into the Hermes source tree.

## Login

This standalone repo does not commit Antigravity's OAuth client credentials because GitHub push protection classifies Google OAuth client IDs/secrets as secrets. Set them from your local environment before logging in:

```bash
export HERMES_ANTIGRAVITY_CLIENT_ID='your-antigravity-oauth-client-id'
export HERMES_ANTIGRAVITY_CLIENT_SECRET='your-antigravity-oauth-client-secret'
```

After installing, restart Hermes and run:

```bash
hermes login --provider google-antigravity
```

or select Google Antigravity from:

```bash
hermes model
```

Credentials are stored under Hermes home, separate from Gemini CLI credentials:

```text
$HERMES_HOME/auth/google_antigravity.json
```

## Configure manually

```bash
hermes config set model.provider google-antigravity
hermes config set model.base_url cloudcode-pa://antigravity
hermes config set model.default gemini-3.1-pro-high
```

Optional project override:

```bash
export HERMES_ANTIGRAVITY_PROJECT_ID=your-google-cloud-project-id
```

Optional Antigravity app version override:

```bash
export HERMES_ANTIGRAVITY_VERSION=2.0.2
```

## Development

Run the focused tests from the Hermes source tree after installing/copying files:

```bash
cd ~/.hermes/hermes-agent
venv/bin/python -m pytest tests/agent/test_google_antigravity_adapter.py -q
```

For a broader Antigravity/Gemini check:

```bash
venv/bin/python -m pytest \
  tests/agent/test_google_antigravity_adapter.py \
  tests/agent/test_gemini_cloudcode.py \
  tests/agent/test_gemini_schema.py \
  tests/hermes_cli/test_runtime_provider_resolution.py \
  -q
```

## Notes for Hermes maintainers

Hermes model-provider plugins currently register declarative `ProviderProfile` objects, but runtime client construction and OAuth resolver dispatch still live in Hermes core. That is why this repo includes both:

1. a proper user-installable provider plugin directory, and
2. a small core integration patch.

Once Hermes adds plugin hooks for custom OAuth providers and model clients, the core patch can be removed and this repo can become a pure drop-in provider plugin.

## Security

Do not commit `google_antigravity.json`, OAuth tokens, browser cookies, `.env`, or terminal logs.

The OAuth client ID/secret in the source are public client credentials used by the Antigravity desktop flow, not user credentials.

## License

MIT
