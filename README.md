# Hermes Google Antigravity OAuth Provider

Unofficial Google Antigravity OAuth provider for [Hermes Agent](https://github.com/NousResearch/hermes-agent).

This package turns the Antigravity work into a clean Hermes plugin bundle:

- `plugins/model-providers/google-antigravity/` registers the `google-antigravity` provider profile.
- `agent/google_antigravity_oauth.py` reuses Hermes' generic Google OAuth machinery with Antigravity's OAuth client, scopes, callback port, credential file, and project ID handling.
- `agent/google_antigravity_adapter.py` adapts Antigravity's Cloud Code PA endpoint to Hermes' OpenAI-compatible chat-completions client interface.
- `agent/antigravity_quota_grpc.py` probes Antigravity's `FetchQuotaStatus` gRPC quota API and falls back safely when the service returns no buckets.
- `agent/antigravity_stream_grpc.py` optionally injects Antigravity context-window compression settings without enabling them by default.
- `patches/hermes-agent-antigravity-core.patch` wires Hermes' current runtime, auth, model picker, and quota paths to the plugin until Hermes exposes first-class plugin hooks for custom model clients and OAuth resolvers.

## Status

Experimental / unofficial. Use at your own risk. Antigravity OAuth endpoints and model IDs may change upstream.

This integration intentionally does not depend on Gemini CLI. It can import or mirror the Antigravity app/CLI OAuth token file only when present, so Hermes and the Antigravity app can share the same login state without committing credentials.

## Supported provider and aliases

- Provider: `google-antigravity`
- Aliases: `antigravity`, `antigravity-oauth`
- Base URL marker: `cloudcode-pa://antigravity`

Curated model IDs (matching the Antigravity UI model picker):

- `gemini-3.5-flash-high` — Gemini 3.5 Flash (High)
- `gemini-3.5-flash-low` — Gemini 3.5 Flash (Medium)
- `gemini-3.1-pro-high` — Gemini 3.1 Pro (High)
- `gemini-3.1-pro-low` — Gemini 3.1 Pro (Low)
- `claude-sonnet-4-6-thinking` — Claude Sonnet 4.6 (Thinking)
- `claude-sonnet-4-6` — Claude Sonnet 4.6
- `claude-opus-4-6-thinking` — Claude Opus 4.6 (Thinking)
- `claude-opus-4-6` — Claude Opus 4.6
- `gpt-oss-120b-medium` — GPT-OSS 120B (Medium)

Display-friendly IDs like `gemini-3.5-flash-high` or `gemini-3.1-pro-high` are mapped internally to the real Antigravity backend IDs (e.g. `gemini-3-flash-agent`, `gemini-3.1-pro-low`) and get the appropriate `thinkingConfig.thinkingLevel` (high/medium/low) injected automatically. Claude thinking is likewise controlled by the model name: names containing `thinking` get `include_thoughts: true` injected into the request. Hermes session IDs are hashed before they are sent as Antigravity `sessionId` values, so platform/user identifiers are not exposed verbatim.

Provider-prefixed model IDs copied from other tools are accepted for the supported families, for example `google/gemini-3.1-pro-high`, `anthropic/claude-sonnet-4.6-thinking`, and `openai/gpt-oss-120b`.

## Install

```bash
git clone https://github.com/Meapri/hermes-google-antigravity-plugin.git
cd hermes-google-antigravity-plugin
./scripts/install.sh
```

The installer copies the plugin/runtime files and, when run interactively, prompts for `HERMES_ANTIGRAVITY_CLIENT_ID` and `HERMES_ANTIGRAVITY_CLIENT_SECRET` if they are not already set. It saves them to `$HERMES_HOME/.env` with mode `600`, so after restarting Hermes the normal `hermes model` flow can open the Antigravity login directly.

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

If you want a new Hermes install to remember these values locally without committing them to git, append them to that machine's Hermes env file instead:

```bash
mkdir -p "$HOME/.hermes"
chmod 700 "$HOME/.hermes"
cat >> "$HOME/.hermes/.env" <<'EOF'
HERMES_ANTIGRAVITY_CLIENT_ID=your-antigravity-oauth-client-id
HERMES_ANTIGRAVITY_CLIENT_SECRET=your-antigravity-oauth-client-secret
EOF
chmod 600 "$HOME/.hermes/.env"
```

After that, `./scripts/install.sh` and `hermes auth add google-antigravity` will pick them up automatically on that machine. The login itself then creates the per-account token file at `$HERMES_HOME/auth/google_antigravity.json`; do not copy or commit that token file.

After installing, restart Hermes and run:

```bash
hermes auth add google-antigravity
```

or select Google Antigravity from:

```bash
hermes model
```

Credentials are stored under Hermes home:

```text
$HERMES_HOME/auth/google_antigravity.json
```

For compatibility with the Antigravity app/CLI, the runtime also reads and mirrors the same OAuth token shape at:

```text
$HOME/.gemini/antigravity-cli/antigravity-oauth-token
```

Override that location only for local testing or isolated deployments:

```bash
export HERMES_ANTIGRAVITY_CLI_TOKEN_PATH=/secure/path/antigravity-oauth-token
```

Both files contain bearer/refresh tokens and must be treated as secrets. The writer uses mode `600` for mirrored token files, but you should still avoid putting `$HOME/.gemini`, `$HERMES_HOME/auth`, or terminal transcripts under version control.

## Configure manually

```bash
hermes config set model.provider google-antigravity
hermes config set model.base_url cloudcode-pa://antigravity
hermes config set model.default gemini-3.5-flash-high
```

Project handling:

The plugin does not hard-code a default project. Antigravity/Code Assist projects are account-specific, so the runtime discovers the account's `cloudaicompanionProject` with `loadCodeAssist` after OAuth and persists it in `$HERMES_HOME/auth/google_antigravity.json`.

If discovery fails or you need to force a paid/workspace project, override it explicitly:

```bash
export HERMES_ANTIGRAVITY_PROJECT_ID=your-google-cloud-project-id
```

Optional Antigravity app version override:

```bash
export HERMES_ANTIGRAVITY_VERSION=2.0.2
```

Optional Antigravity-side context compression:

```bash
# Default: off. Hermes keeps normal model-specific context handling.
export HERMES_ANTIGRAVITY_CONTEXT_COMPRESSION=0

# Opt in when testing Antigravity's own sliding-window compression.
export HERMES_ANTIGRAVITY_CONTEXT_COMPRESSION=1
```

When enabled, Hermes sends a conservative `contextWindowCompression` hint and drops that hint during minimal INVALID_ARGUMENT recovery retries so a bad compression setting does not trap the session.

Quota, tier, and credit behavior:

The adapter reads `loadCodeAssist.paidTier` and treats it as the effective tier when present. This is required because Google One AI subscribers can still report `currentTier: free-tier`; Plus/Pro/Ultra entitlement lives in `paidTier` with `availableCredits`.

By default the adapter auto-detects Google AI Plus / Pro / Ultra and only sends `enabledCreditTypes: ["GOOGLE_ONE_AI"]` when usable `GOOGLE_ONE_AI` credits are available. Override only for diagnostics or if you intentionally want a different burn order:

```bash
# Default: detect paid Google AI plan + usable credits before opting in
export HERMES_ANTIGRAVITY_GOOGLE_ONE_AI_CREDITS=auto

# Force Google One AI entitlement/credit routing
export HERMES_ANTIGRAVITY_GOOGLE_ONE_AI_CREDITS=always

# Try raw Code Assist first, then retry with Google One AI entitlement on capacity errors
export HERMES_ANTIGRAVITY_GOOGLE_ONE_AI_CREDITS=fallback

# Disable Google One AI entitlement entirely
export HERMES_ANTIGRAVITY_GOOGLE_ONE_AI_CREDITS=off
```

`/gquota` displays the effective plan name from `paidTier.name`, the live Google One AI credit balance, and marks base-quota buckets at 0% as `→ using credits` when the credit path is available. It does not hard-code Plus/Pro/Ultra numeric quota limits; live API data is the source of truth.

Claude Opus/Sonnet and GPT-OSS can also hit a short rolling capacity guard even when `/gquota` shows the 5-hour/daily bucket as available. The adapter locally paces those expensive models and retries short `RESOURCE_EXHAUSTED` responses before surfacing an error. Tune or disable that guard with:

```bash
# Default is 8 seconds for claude-* and gpt-oss* models
export HERMES_ANTIGRAVITY_CAPACITY_PACING_SECONDS=8

# Disable local pacing/retry delay
export HERMES_ANTIGRAVITY_CAPACITY_PACING_SECONDS=0
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

Do not commit `google_antigravity.json`, `antigravity-oauth-token`, OAuth tokens, browser cookies, `.env`, SQLite state databases, exported chat transcripts, screenshots, or terminal logs.

OAuth client IDs/secrets, even public desktop-client values, are intentionally not committed here. Configure them with `HERMES_ANTIGRAVITY_CLIENT_ID` and `HERMES_ANTIGRAVITY_CLIENT_SECRET` in your local environment or `$HERMES_HOME/.env`.

Before publishing patches or bug reports, redact:

- `Authorization` headers and bearer tokens
- `refresh_token`, `access_token`, `client_secret`, and OAuth callback URLs
- Google account email addresses and project IDs if they identify a personal/work account
- Hermes session IDs, Telegram/Discord IDs, and raw chat logs that may contain user content

This repo uses fake token strings in tests only. If GitHub push protection blocks a push, assume a real secret may have slipped into the diff and rotate that credential before retrying.

## License

MIT
