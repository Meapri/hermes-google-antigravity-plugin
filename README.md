# Hermes Google Antigravity OAuth Provider

Unofficial Google Antigravity OAuth provider for [Hermes Agent](https://github.com/NousResearch/hermes-agent).

**One-click install.** Log into `agy` CLI once, then run `install.sh`. No API keys, no OAuth client registration, no manual patching. Works the same way as [hermes-claude-auth](https://github.com/kristianvast/hermes-claude-auth) — uses your existing Antigravity CLI OAuth token to access Gemini models through Hermes.

## How it works

```
                        agy CLI login (once)
                              ↓
          ~/.gemini/antigravity-cli/antigravity-oauth-token
                              ↓
              sitecustomize.py → import hook fires
                              ↓
         antigravity_provider_patch.py → injects provider
                              ↓
    Hermes google-antigravity provider → Cloud Code PA API
                              ↓
              Gemini / Claude / GPT-OSS models
```

### Anti-detection (Google blocking countermeasure)

Similar to how `hermes-claude-auth` spoofs Claude Code headers to bypass Anthropic's third-party block:

| What | Without patch | With patch |
|------|--------------|------------|
| `User-Agent` | `hermes-agent (gemini-cli-compat)` | `Antigravity/2.0.1 Chrome/138... Electron/37...` |
| `X-Goog-Api-Client` | `gl-python/hermes` | `antigravity-cli/2.0.1` |
| OAuth token | Requires separate registration | Extracted from `agy` binary at runtime |
| **Google sees** | "Third-party tool Hermes" :x: | "Official Antigravity desktop app" :white_check_mark: |

The version number is fetched live from Antigravity's auto-update endpoint, so it stays current without manual updates.

### Token refresh

When the OAuth token expires, the normal refresh flow may fail (client secrets are embedded in `agy`'s binary). The plugin falls back to `agy --print "OK"` which refreshes the token using `agy`'s own credential management, then re-reads the token file.

## Prerequisites

- [Hermes Agent](https://github.com/NousResearch/hermes-agent) installed
- [Antigravity CLI](https://antigravity.google/cli/install) (`agy`) installed and logged in at least once
- Linux or macOS (Windows untested)
- Python 3.11+

## Install

```bash
git clone https://github.com/Meapri/hermes-google-antigravity-plugin.git
cd hermes-google-antigravity-plugin
./scripts/install.sh
```

Then add the credential (auto-detects your existing `agy` CLI token):

```bash
hermes auth add google-antigravity
```

That's it. If you already have `hermes-claude-auth` installed, the `sitecustomize.py` hook handles both patches side by side.

## Usage

```bash
# One-shot
hermes chat --provider google-antigravity -m gemini-3.5-flash-high -q "Hello"

# Set as default
hermes config set model.provider google-antigravity
hermes config set model.default gemini-3.5-flash-high
```

Provider aliases: `google-antigravity`, `antigravity`, `antigravity-oauth`

### TUI Model Picker (`hermes model`)

As of v1.1, google-antigravity appears in the `hermes model` interactive provider list.
Select it from the menu and pick a model — no manual config editing needed.

If the TUI integration breaks after a Hermes update, the provider still works
via `hermes config set`. See [After Hermes Update](#after-hermes-update) below.

## After Hermes Update

When you run `hermes update` (which does `git pull` + `pip install`), the
`sitecustomize.py` inside the venv may be overwritten. Everything else survives.

**Check what's broken:**
```bash
cd hermes-google-antigravity-plugin
./scripts/install.sh --check
```

**Recover (only restores what's needed):**
```bash
cd hermes-google-antigravity-plugin
git pull && ./scripts/install.sh --post-update
```

**Full recovery (if anything went badly wrong):**
```bash
cd hermes-google-antigravity-plugin
git pull && ./scripts/install.sh
```

### What survives `hermes update`

| File | Location | Survives? |
|------|----------|:---:|
| `antigravity_provider_patch.py` | `~/.hermes/patches/` | ✅ Outside repo |
| `google_antigravity_*.py` | `hermes-agent/agent/` | ✅ Untracked files |
| Plugin metadata | `~/.hermes/plugins/` | ✅ Outside repo |
| **`sitecustomize.py`** | venv `site-packages/` | ❌ Overwritten |
| Auth token | `~/.gemini/...` | ✅ Managed by agy |

Only `sitecustomize.py` needs recovery. `--post-update` does exactly that.

### Patch safety (for developers)

Every monkey-patch function verifies Hermes API compatibility via
`inspect.signature` before applying and returns `False` on mismatch
instead of crashing. If Hermes internals change, the affected patch
declines gracefully and the rest keep working. `apply()` reports
results like `6/6 patches applied` or `5/6 (failed: model_picker)`.

## Supported Models

### Gemini Flash

| Model ID | Tier | Backend ID |
|----------|------|------------|
| `gemini-3.5-flash-high` | High (best) | `gemini-3-flash-agent` |
| `gemini-3.5-flash` | High | `gemini-3-flash-agent` |
| `gemini-3.5-flash-medium` | Medium | `gemini-3-flash` |
| `gemini-3.5-flash-low` | Low (fastest) | `gemini-3-flash` |
| `gemini-3-flash-high` | High | `gemini-3-flash` |
| `gemini-3-flash-medium` | Medium | `gemini-3-flash` |
| `gemini-3-flash-low` | Low | `gemini-3-flash` |

### Gemini Pro

| Model ID | Tier | Backend ID |
|----------|------|------------|
| `gemini-3.1-pro-high` | High (best) | `gemini-3.1-pro-low` |
| `gemini-3.1-pro-medium` | Medium | `gemini-3.1-pro-low` |
| `gemini-3.1-pro` | Default | `gemini-3.1-pro-low` |

### Claude (via Antigravity)

| Model ID | Thinking | Backend ID |
|----------|----------|------------|
| `claude-sonnet-4-6` | Off | `claude-sonnet-4-6` |
| `claude-sonnet-4-6-thinking` | On :thought_balloon: | `claude-sonnet-4-6` |
| `claude-opus-4-6` | Off | `claude-opus-4-6-thinking` |
| `claude-opus-4-6-thinking` | On :thought_balloon: | `claude-opus-4-6-thinking` |

### GPT (via Antigravity)

| Model ID | Backend ID |
|----------|------------|
| `gpt-oss-120b` | `gpt-oss-120b-medium` |
| `gpt-oss-120b-medium` | `gpt-oss-120b-medium` |

### Provider-prefixed aliases

Standard provider prefixes are accepted and mapped automatically:

| Aliased Model ID | Resolves to |
|------------------|-------------|
| `google/gemini-3.1-pro-high` | `gemini-3.1-pro-high` |
| `anthropic/claude-sonnet-4-6-thinking` | `claude-sonnet-4-6-thinking` |
| `openai/gpt-oss-120b` | `gpt-oss-120b` |

Provider aliases: `google-antigravity`, `antigravity`, `antigravity-oauth`

## How tier/thinking works

Gemini Flash/Pro use `thinkingConfig.thinkingLevel` (high/medium/low) injected into the request body. The model ID sent to the backend is the canonical internal ID — tiers are controlled purely by the thinking level parameter.

Claude thinking is controlled by `include_thoughts: true` in the request. Adding `-thinking` to the model name triggers interleaved thinking mode automatically.

## What gets installed

| Path | Purpose |
|------|---------|
| `~/.hermes/plugins/model-providers/google-antigravity/` | Provider plugin metadata |
| `~/.hermes/hermes-agent/agent/google_antigravity_adapter.py` | API adapter (Cloud Code PA → OpenAI-compatible) |
| `~/.hermes/hermes-agent/agent/google_antigravity_oauth.py` | OAuth handling + `agy` binary credential extraction |
| `~/.hermes/hermes-agent/agent/antigravity_quota_grpc.py` | Quota probing via gRPC |
| `~/.hermes/hermes-agent/agent/antigravity_stream_grpc.py` | Optional context compression |
| `~/.hermes/patches/antigravity_provider_patch.py` | Runtime monkey-patch — injects provider into Hermes |
| `<venv>/site-packages/sitecustomize.py` | Import hook — auto-loads on Python startup |

**No Hermes source files are modified.** All provider registration happens through the `sitecustomize.py` MetaPathFinder hook. This is the same pattern used by `hermes-claude-auth`.

## How anti-detection works

When Google eventually starts blocking third-party tools (like Anthropic did in April 2026), this plugin is already prepared:

1. **Header spoofing** — `X-Goog-Api-Client` and `User-Agent` headers match the official Antigravity desktop app
2. **Version auto-sync** — fetches the latest Antigravity version from their update endpoint, so headers never go stale
3. **Identical endpoint** — uses the same `cloudcode-pa.googleapis.com` endpoint as `agy`
4. **Same OAuth flow** — uses the same token file as `agy`, so Google sees identical auth state

If Google adds additional checks (session fingerprints, gRPC metadata, etc.), the `get_antigravity_headers()` function is the single place to add new headers — similar to how `anthropic_billing_bypass.py` handles Anthropic's evolving validation.

## Uninstall

```bash
# Remove sitecustomize hook (also disables claude-auth if installed)
rm "$(hermes config venv-path 2>/dev/null || echo ~/.hermes/hermes-agent/venv)/lib/python*/site-packages/sitecustomize.py"

# Remove plugin files
rm -rf ~/.hermes/plugins/model-providers/google-antigravity
rm -f ~/.hermes/patches/antigravity_provider_patch.py
rm -f ~/.hermes/hermes-agent/agent/google_antigravity_*.py
rm -f ~/.hermes/hermes-agent/agent/antigravity_*.py
rm -f ~/.hermes/auth/google_antigravity.json*

# Remove auto-recovery hook
rm -f ~/.hermes/hermes-agent/.git/hooks/post-merge

# Remove credential from pool
hermes auth remove google-antigravity 1

# Restart gateway
systemctl --user restart hermes-gateway
```

## Troubleshooting

**"Unknown provider: google-antigravity"** — the `sitecustomize.py` hook didn't load. Restart the gateway: `systemctl --user restart hermes-gateway`. If that doesn't help, run `./scripts/install.sh --post-update`.

**google-antigravity not in `hermes model` list** — the TUI picker patch may have been declined due to Hermes API changes. Run `hermes config set model.provider google-antigravity` as a workaround.

**Token refresh fails** — make sure `agy` is on PATH and logged in. Run `agy --print "OK"` manually to verify.

**"invalid_client" on fresh login** — the OAuth client credentials extracted from your `agy` binary may be outdated. Update `agy` to the latest version and reinstall the plugin.

**Reinstall** — just run `./scripts/install.sh` again. It overwrites all installed files and restarts the gateway.

**After `hermes update`** — run `./scripts/install.sh --check` to see what's broken, then `./scripts/install.sh --post-update` to fix it.

## Compatibility

- Hermes Agent (any recent version — no source files are modified)
- Linux / macOS
- Python 3.11+
- Coexists with `hermes-claude-auth` (same `sitecustomize.py` handles both)

## Credits

- [NoeFabris/opencode-antigravity-auth](https://github.com/NoeFabris/opencode-antigravity-auth) — original TypeScript implementation for opencode
- [kristianvast/hermes-claude-auth](https://github.com/kristianvast/hermes-claude-auth) — same pattern for Claude Code
- [NousResearch/hermes-agent](https://github.com/NousResearch/hermes-agent) — the AI agent this extends

## License

MIT
