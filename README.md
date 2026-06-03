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

That's it — no separate credential step. The provider reads your existing `agy`
CLI OAuth token directly at runtime (from
`~/.gemini/antigravity-cli/antigravity-oauth-token`), so as long as you've run
`agy` once and logged in, `hermes chat --provider google-antigravity` just works.
When the token expires it is auto-refreshed via `agy --print`.

> **Note:** `hermes auth add google-antigravity` is **not** required and is
> currently a no-op for this provider — Hermes reports
> `not implemented for auth type oauth yet`. Authentication is handled entirely
> by the runtime credential resolver against the `agy` token, not by the
> `hermes auth` credential store. Skip it.

If you already have `hermes-claude-auth` installed, the `sitecustomize.py` hook
handles both patches side by side. If Claude auth is not installed, those
optional Claude hooks no-op silently.

## Agent Handoff Prompt

Paste this into another Hermes/Codex agent when you want it to install or
refresh this provider on that machine:

```text
Set up the Hermes Google Antigravity provider and automatic post-update repair
on this machine.

Repository: https://github.com/Meapri/hermes-google-antigravity-plugin

Requirements:
- Hermes Agent must already be installed.
- Antigravity CLI (`agy`) must be installed and logged in at least once.
- Do not run `hermes auth add google-antigravity`; this provider uses the
  existing `agy` OAuth token.
- Do not edit Hermes source files directly.

Use a persistent clone at ~/hermes-google-antigravity-plugin:
1. If ~/hermes-google-antigravity-plugin exists, check whether it has local
   changes. If clean, run `git fetch origin && git pull --ff-only origin main`.
   If dirty, do not overwrite local changes; continue with the current checkout
   and report that pull was skipped.
2. If the clone does not exist, run:
   `git clone https://github.com/Meapri/hermes-google-antigravity-plugin.git ~/hermes-google-antigravity-plugin`
3. Run:
   `cd ~/hermes-google-antigravity-plugin && ./scripts/repair.sh`

The repair command must install the provider, install the shared
sitecustomize.py import hook, install the Hermes git post-merge auto-recovery
hook, restart hermes-gateway.service if it is running, and run the full
`./scripts/install.sh --check` integrity/contract check.

After repair, verify and report:
- `./scripts/install.sh --check` passes.
- `~/.hermes/hermes-agent/.git/hooks/post-merge` exists and is executable.
- `~/.hermes/hermes-agent/.git/hooks/post-merge` references
  `hermes-google-antigravity-plugin`.
- the Hermes venv `sitecustomize.py` exists and contains these hooks:
  `hermes_cli.auth`, `hermes_cli.providers`, `hermes_cli.runtime_provider`,
  `hermes_cli.commands`, `cli`, `agent.auxiliary_client`,
  `hermes_cli.model_switch`, and `api.config`.
- `~/.hermes/patches/antigravity_provider_patch.py` does not contain the stale
  line `Run: hermes auth add google-antigravity`; if it does, the installed
  patch is old and must be replaced from the latest repo.
- `./scripts/plan_status.py` reports the expected paid tier. For Google AI
  Plus/Pro/Ultra accounts, `raw_paid_tier_id` or `context_paid_tier_id` should
  be a `g1-*` tier. `credit_attempts` shows whether requests will include
  `GOOGLE_ONE_AI` routing; it is separate from the base plan quota report.
- On macOS, if auth still fails after `agy` login, verify that Keychain has the
  CLI credential at service `gemini`, account `antigravity`, or one of the
  Antigravity safe-storage fallbacks. Do not print or paste token values; only
  inspect service/account names and JSON key names.
- installed files match the repo:
  `~/.hermes/patches/antigravity_provider_patch.py`,
  `~/.hermes/hermes-agent/agent/google_antigravity_adapter.py`,
  `~/.hermes/hermes-agent/agent/google_antigravity_oauth.py`,
  `~/.hermes/hermes-agent/agent/antigravity_quota_report.py`, and
  `~/.hermes/plugins/model-providers/google-antigravity/plugin.yaml`.

If credentials, network, and the `hermes` command are available, run:
`./scripts/repair.sh --smoke`

If the smoke test cannot be run, say exactly what blocked it. Finish with the
current git commit, whether automatic repair is installed, and the check/smoke
results.
```

## Usage

```bash
# One-shot
hermes chat --provider google-antigravity -m gemini-3.5-flash-high -q "Hello"

# Set as default
hermes config set model.provider google-antigravity
hermes config set model.default gemini-3.5-flash-high
```

Provider aliases: `google-antigravity`, `antigravity`, `antigravity-oauth`

### Google AI Plus/Pro/Ultra Plan And Quota Routing

This provider reuses the OAuth token produced by the official `agy` CLI. On
each client startup it probes `loadCodeAssist` to discover the assigned project
and account tier. Google can report Google One paid plans in the raw
`paidTier` field even when Hermes' older `google_code_assist.py` parser only
exposes `current_tier_id=standard-tier`; the adapter reads that raw `paidTier`
so Plus/Pro/Ultra plan entitlement is detected correctly.

Paid plan entitlement, base request quota, and AI credit/overage routing are
not the same signal:

- `paidTier` confirms the Google AI Plus/Pro/Ultra plan attached to the `agy`
  account/session.
- `retrieveUserQuota` reports the base Code Assist request buckets that Google
  exposes through the REST quota API.
- `enabledCreditTypes=["GOOGLE_ONE_AI"]` asks Google to route eligible requests
  through Google One AI credit/overage handling. Google does not expose actual
  AI credit consumption through this quota API, so the plugin reports this as
  routing state, not as proven credit spend.

Default routing mode is `auto`:

- paid Google One tier detected (`g1-plus`, `g1-pro`, `g1-ultra`, or matching
  plan name): request bodies request `enabledCreditTypes=["GOOGLE_ONE_AI"]`
- no paid tier detected: request bodies use the base Code Assist bucket

Override with `HERMES_ANTIGRAVITY_GOOGLE_ONE_AI_CREDITS`:

| Value | Behavior |
|-------|----------|
| `auto` / unset | Request Google One AI routing only when a paid tier is detected |
| `always` / `1` / `true` | Always request Google One AI credit routing |
| `fallback` | Try base quota first, then Google One AI credits on capacity errors |
| `never` / `0` / `false` | Never request Google One AI credit routing |

Check the current account without printing tokens:

```bash
cd ~/hermes-google-antigravity-plugin
./scripts/plan_status.py
```

For an Ultra account, expect output like:

```text
raw_paid_tier_id: g1-ultra-tier
context_paid_tier_id: g1-ultra-tier
context_has_google_one_ai_credits: True
credit_mode: auto
credit_attempts: [True]
```

Inside an interactive Hermes CLI session, run `/agyquota` to see the same
account tier plus live quota status:

```text
/agyquota
Google Antigravity quota/status
  currentTier: standard-tier
  paidTier: g1-ultra-tier
  paidTierName: Gemini Code Assist in Google One AI Ultra
  creditRoutingMode: auto
  creditAttempts: [True]
```

`/agyquota` also prints base REST quota buckets and attempts the Antigravity
gRPC quota endpoint for extended/base buckets. If Google rejects the gRPC token
scope, the command reports that explicitly instead of pretending extended
quota is known.

### TUI Model Picker (`hermes model`)

As of v1.1, google-antigravity appears in the `hermes model` interactive provider list.
Select it from the menu and pick a model — no manual config editing needed.

If the TUI integration breaks after a Hermes update, the provider still works
via `hermes config set`. See [After Hermes Update](#after-hermes-update) below.

## One-Command Repair

Use this whenever Hermes updates frequently, provider behavior looks wrong, or
you want to force the installed files/hooks back to the repo state:

```bash
cd ~/hermes-google-antigravity-plugin
./scripts/repair.sh
```

`repair.sh` does the full recovery path:

1. Fast-forwards the plugin clone when the working tree is clean.
2. Reinstalls Antigravity runtime files, patch files, plugin metadata, and the
   shared `sitecustomize.py` hook.
3. Reinstalls the Hermes `post-merge` auto-recovery hook.
4. Restarts `hermes-gateway.service` when it is running.
5. Runs `./scripts/install.sh --check`, including file drift checks,
   `sitecustomize.py` hook checks, and Hermes patch contract checks.

For an end-to-end provider call after repair:

```bash
./scripts/repair.sh --smoke
```

Use `./scripts/repair.sh --skip-pull` if you intentionally have local changes
in the plugin clone and only want to reinstall/check the current checkout.

## After Hermes Update

When you run `hermes update` (which does `git pull` + `pip install`), the
`sitecustomize.py` inside the venv may be overwritten. Everything else survives.

**Automatic recovery (default).** `install.sh` installs a git `post-merge`
hook into `~/.hermes/hermes-agent/.git/hooks/`, so the moment `hermes update`
runs its `git pull`, the hook detects the missing `sitecustomize.py` hooks and
re-runs recovery for both this plugin and `hermes-claude-auth` automatically
with no manual steps. It restores the coexistence `sitecustomize.py` (9 Antigravity
import hooks plus 2 Claude hooks) and restarts the gateway.

> **Keep the clone in a persistent path** (e.g. `~/hermes-google-antigravity-plugin`),
> **not `/tmp`**. The post-merge hook looks for the installer at
> `$HOME/hermes-google-antigravity-plugin/scripts/install.sh` first. If your
> only clone lives in `/tmp`, a reboot wipes it and auto-recovery silently
> can't run.

**Manual check** (if you disabled the hook or want to verify):
```bash
cd ~/hermes-google-antigravity-plugin
./scripts/install.sh --check
```

**Manual repair:**
```bash
cd ~/hermes-google-antigravity-plugin
./scripts/repair.sh
```

**Full recovery (if anything went badly wrong):**
```bash
cd ~/hermes-google-antigravity-plugin
./scripts/repair.sh --smoke
```

### What survives `hermes update`

| File | Location | Survives? |
|------|----------|:---:|
| `antigravity_provider_patch.py` | `~/.hermes/patches/` | ✅ Outside repo |
| `google_antigravity_*.py` | `hermes-agent/agent/` | ✅ Untracked files |
| Plugin metadata | `~/.hermes/plugins/` | ✅ Outside repo |
| **`sitecustomize.py`** | venv `site-packages/` | ❌ Overwritten |
| Auth token | `~/.gemini/...` | ✅ Managed by agy |

`sitecustomize.py` is the file most likely to be overwritten, but `repair.sh`
also refreshes the copied runtime files and verifies every installed file
against the repo so drift is caught immediately.

### Patch safety (for developers)

Every monkey-patch function verifies Hermes API compatibility via
`inspect.signature` before applying and returns `False` on mismatch
instead of crashing. If Hermes internals change, the affected patch
declines gracefully and the rest keep working. `apply()` reports
results like `11/11 patches applied` or `10/11 (failed: model_picker)`.

The 11 patches are: `providers`, `auth_registry`, `commands`,
`runtime_provider`, `cli_agyquota`, `agent_runtime`, `models_module`,
`model_picker`, `model_switch_picker`, `auxiliary_client`, `webui_config`.

### Maintainer notes (import-order traps & verification)

These are hard-won internals worth knowing before you touch the hooks:

- **The `sitecustomize.py` needs all 9 Antigravity import hooks** (plus the
  2 Claude hooks = 11 total when coexisting). They fire on import of, in order:
  - `agent.error_classifier` — Claude auth error classification
  - `agent.anthropic_adapter` — Claude bypass (hermes-claude-auth)
  - `hermes_cli.auth` — Antigravity **early** apply:
    `_patch_auth_registry` + `_patch_providers` + `_patch_auxiliary_client`
  - `hermes_cli.providers` — full provider apply
  - `hermes_cli.commands` — `/agyquota` command metadata
  - `cli` — `/agyquota` command handler
  - `agent.auxiliary_client` — auxiliary-client provider resolver
  - `hermes_cli.runtime_provider` — runtime credential resolver after module load
  - `hermes_cli.main` — TUI model picker
  - `hermes_cli.model_switch` — gateway `/model` picker row
  - `api.config` — hermes-webui `/api/models` row
- The 2 Claude hooks are optional compatibility hooks. If
  `anthropic_billing_bypass.py` is not installed, they must no-op silently so
  Antigravity-only installs do not print traceback noise during `hermes model`.
- **Why the `hermes_cli.auth` hook is load-bearing (import-order trap):** if it
  is missing, `resolve_provider` runs its validation *before* `hermes_cli.providers`
  is ever imported, so the provider isn't registered yet and you get
  **`Unknown provider: google-antigravity`**. The nasty part: a direct
  `python -c "import ..."` test can still PASS (it imports `providers` directly),
  while `hermes chat` FAILS — because the real startup path validates in a
  different order. Don't trust a bare-import smoke test here.
- **Always verify with a real E2E call, not a direct import.** The canonical
  check is `hermes chat --provider google-antigravity -m gemini-3.5-flash-high
  -q "OK"`, not `python -c "import agent.google_antigravity_adapter"`.
- **⚠ Don't run `hermes-claude-auth`'s `install.sh` raw when both are installed.**
  Its older/`fix`-branch installer can overwrite `sitecustomize.py` with a
  **Claude-only** hook (no `--check`), silently dropping the 9 Antigravity hooks
  and bringing back `Unknown provider`. Recover by re-running **this** plugin's
  `./scripts/install.sh`, which restores the coexistence hook (all 11). The
  current claude-auth installer is coexistence-aware (it restores the shared
  multi-hook file first), but verify after any claude-auth reinstall.

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
| `~/.hermes/hermes-agent/agent/antigravity_quota_report.py` | `/agyquota` plan/quota report builder |
| `~/.hermes/hermes-agent/agent/antigravity_stream_grpc.py` | Optional context compression |
| `~/.hermes/patches/antigravity_provider_patch.py` | Runtime monkey-patch — injects provider into Hermes |
| `<venv>/site-packages/sitecustomize.py` | Import hook — auto-loads on Python startup (11 hooks: 2 Claude + 9 Antigravity) |
| `~/.hermes/hermes-agent/.git/hooks/post-merge` | Auto-recovery hook — restores sitecustomize.py after `hermes update` |

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
rm -f ~/.hermes/auth/google_antigravity*.json*

# Remove auto-recovery hook
rm -f ~/.hermes/hermes-agent/.git/hooks/post-merge

# Restart gateway
systemctl --user restart hermes-gateway
```

> The provider is never added to the `hermes auth` credential pool (it
> authenticates directly against the `agy` token), so there is **no**
> `hermes auth remove` step — deleting the files above is sufficient.

## Troubleshooting

**"Unknown provider: google-antigravity"** — the `sitecustomize.py` hook didn't load. Restart the gateway: `systemctl --user restart hermes-gateway`. If that doesn't help, run `./scripts/repair.sh`.

**google-antigravity not in `hermes model` list** — the TUI picker patch may have been declined due to Hermes API changes. Run `hermes config set model.provider google-antigravity` as a workaround.

**Token refresh fails** — make sure `agy` is on PATH and logged in. Run `agy --print "OK"` manually to verify.

**Auth check says `No Antigravity OAuth credentials found after agy refresh`** —
the plugin could run `agy`, but could not read the refreshed credential. On
Linux it reads `~/.gemini/antigravity-cli/antigravity-oauth-token`; on macOS it
checks Keychain in this order: service `gemini` account `antigravity`, then
`Antigravity Safe Storage` accounts `Antigravity` and `Antigravity Key`, then
`Antigravity IDE Safe Storage` account `Antigravity IDE`, then legacy
service-only fallbacks. The token payload may contain both `access_token` and
`refresh_token`, or only a `refresh_token`; refresh-only credentials are treated
as expired and refreshed through Hermes' Google OAuth path. If your `agy` uses a
custom token file, set `HERMES_ANTIGRAVITY_CLI_TOKEN_PATH=/path/to/token` and
run `./scripts/repair.sh --skip-pull`.

On macOS, this is a safe shape check for the current CLI Keychain token. It
does not print token values:

```bash
python3 - <<'PY'
import json, subprocess
raw = subprocess.check_output(
    ["security", "find-generic-password", "-s", "gemini", "-a", "antigravity", "-w"],
    text=True,
)
data = json.loads(raw)
print("top keys", sorted(data.keys()) if isinstance(data, dict) else type(data).__name__)
token = data.get("token") if isinstance(data, dict) and isinstance(data.get("token"), dict) else data
print("token keys", sorted(token.keys()) if isinstance(token, dict) else type(token).__name__)
PY
```

**Auth check says `No Google OAuth credentials found` or suggests
`hermes auth add google-antigravity`** — that is a stale Hermes Google OAuth
path, not the intended Antigravity flow. Run `agy` once and sign in, then run
`./scripts/repair.sh --skip-pull`. This provider should reuse the `agy` token;
`hermes auth add google-antigravity` is not required.

**Google AI Plus/Pro/Ultra is not being used** — run `./scripts/plan_status.py`
and, inside Hermes CLI, `/agyquota`. If `raw_paid_tier_id` is empty, Google did
not report a paid tier for the `agy` account/session. If `raw_paid_tier_id` is
`g1-*` but `credit_attempts` does not include `True`, run
`./scripts/repair.sh` to reinstall the latest adapter. Remember that paid plan
entitlement and AI credit/overage consumption are separate; `/agyquota` can
show routing state, but Google's quota API does not prove actual AI credit
spend.

**"invalid_client" on fresh login** — the OAuth client credentials extracted from your `agy` binary may be outdated. Update `agy` to the latest version and reinstall the plugin.

**Reinstall** — run `./scripts/repair.sh`. It refreshes the clone when safe, overwrites installed files, restarts the gateway when running, and verifies contracts.

**After `hermes update`** — run `./scripts/repair.sh`. For a live provider call too, run `./scripts/repair.sh --smoke`.

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
