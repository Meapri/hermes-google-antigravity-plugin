# Hermes Google Antigravity OAuth Provider

Unofficial Google Antigravity OAuth provider for [Hermes Agent](https://github.com/NousResearch/hermes-agent).

**One-click install.** Log into `agy` CLI once, then run `install.sh`. No API keys, no separate OAuth client registration needed. Works the same way as [hermes-claude-auth](https://github.com/kristianvast/hermes-claude-auth) — uses your existing Antigravity CLI OAuth token to access Gemini models through Hermes.

## How it works

```
agy CLI login (once) → ~/.gemini/antigravity-cli/antigravity-oauth-token
                                   ↓
              sitecustomize.py import hook (runtime monkey-patch)
                                   ↓
              Hermes google-antigravity provider → Cloud Code PA API
                                   ↓
              Gemini 3.5 Flash / Pro, Claude Sonnet / Opus, GPT-OSS
```

- **No Hermes source files are modified.** All provider registration happens through a `sitecustomize.py` MetaPathFinder hook.
- **Token refresh via `agy --print`** — when the OAuth token expires, the plugin runs `agy --print "OK"` in the background to refresh it (agy handles its own client secrets).
- **Shares auth state with Antigravity CLI** — Hermes and `agy` read/write the same token file, so they stay in sync.

## Install

```bash
git clone https://github.com/Meapri/hermes-google-antigravity-plugin.git
cd hermes-google-antigravity-plugin
./scripts/install.sh
```

Then add the credential (auto-detects agy CLI token):

```bash
hermes auth add google-antigravity
```

That's it. No env vars needed — OAuth client credentials are extracted from the agy CLI binary.

## Usage

```bash
# One-shot
hermes chat --provider google-antigravity -m gemini-3.5-flash-high -q "Hello"

# Set as default
hermes config set model.provider google-antigravity
hermes config set model.default gemini-3.5-flash-high
```

## Supported Models

| Model ID | Backend |
|----------|---------|
| `gemini-3.5-flash-high` | Gemini 3.5 Flash (High) |
| `gemini-3.5-flash-low` | Gemini 3.5 Flash (Low) |
| `gemini-3.1-pro-high` | Gemini 3.1 Pro (High) |
| `gemini-3.1-pro-low` | Gemini 3.1 Pro (Low) |
| `claude-sonnet-4-6` | Claude Sonnet 4.6 |
| `claude-sonnet-4-6-thinking` | Claude Sonnet 4.6 (Thinking) |
| `claude-opus-4-6` | Claude Opus 4.6 |
| `claude-opus-4-6-thinking` | Claude Opus 4.6 (Thinking) |
| `gpt-oss-120b-medium` | GPT-OSS 120B |

Provider aliases: `google-antigravity`, `antigravity`, `antigravity-oauth`

## What gets installed

| Path | Purpose |
|------|---------|
| `~/.hermes/plugins/model-providers/google-antigravity/` | Provider plugin |
| `~/.hermes/hermes-agent/agent/google_antigravity_*.py` | Runtime adapters |
| `~/.hermes/patches/antigravity_provider_patch.py` | Monkey-patch (injects provider at runtime) |
| `<venv>/site-packages/sitecustomize.py` | Import hook (auto-loads on Python startup) |

**No Hermes source files are modified.**

## Uninstall

```bash
# Remove sitecustomize hook
rm "$(hermes config venv-path 2>/dev/null || echo ~/.hermes/hermes-agent/venv)/lib/python*/site-packages/sitecustomize.py"

# Remove plugin files
rm -rf ~/.hermes/plugins/model-providers/google-antigravity
rm -f ~/.hermes/patches/antigravity_provider_patch.py
rm -f ~/.hermes/hermes-agent/agent/google_antigravity_*.py
rm -f ~/.hermes/hermes-agent/agent/antigravity_*.py

# Restart
systemctl --user restart hermes-gateway
```

## Compatibility

- Hermes Agent (any recent version — no source patching needed)
- Linux / macOS
- Python 3.11+
- Antigravity CLI (`agy`) authenticated

## Credits

- [NoeFabris/opencode-antigravity-auth](https://github.com/NoeFabris/opencode-antigravity-auth) — original TypeScript implementation for opencode
- [kristianvast/hermes-claude-auth](https://github.com/kristianvast/hermes-claude-auth) — same pattern for Claude Code
- [NousResearch/hermes-agent](https://github.com/NousResearch/hermes-agent) — the AI agent this extends

## License

MIT
