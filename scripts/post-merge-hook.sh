#!/usr/bin/env bash
# post-merge hook — auto-recover provider patches after hermes update
# ====================================================================
# Runs automatically after `hermes update` (git pull).
# Restores sitecustomize.py and patch files if they were overwritten.
#
# Installed by hermes-google-antigravity-plugin and hermes-claude-auth.
# Do not edit manually — re-run install.sh to update.

set -euo pipefail

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RESET='\033[0m'

quiet() { "$@" 2>/dev/null; }

# ── Check if hermes-antigravity plugin is installed ────────────────
ANTIGRAVITY_PATCH="$HOME/.hermes/patches/antigravity_provider_patch.py"
ANTIGRAVITY_INSTALL=""
for candidate in \
    "$HOME/hermes-google-antigravity-plugin/scripts/install.sh" \
    "/tmp/hermes-google-antigravity-plugin/scripts/install.sh"; do
    if [ -x "$candidate" ]; then
        ANTIGRAVITY_INSTALL="$candidate"
        break
    fi
done

# ── Check if hermes-claude-auth is installed ────────────────────────
CLAUDE_PATCH="$HOME/.hermes/patches/anthropic_billing_bypass.py"
CLAUDE_INSTALL=""
for candidate in \
    "$HOME/hermes-claude-auth/install.sh" \
    "/tmp/hermes-claude-auth/install.sh"; do
    if [ -x "$candidate" ]; then
        CLAUDE_INSTALL="$candidate"
        break
    fi
done

# ── Check sitecustomize integrity ───────────────────────────────────
VENV_DIR=""
for d in "$HOME/.hermes/hermes-agent/venv" "$HOME/.hermes/hermes-agent/.venv"; do
    [ -d "$d" ] && { VENV_DIR="$d"; break; }
done

if [ -z "$VENV_DIR" ]; then
    exit 0
fi

SITE_PACKAGES="$("$VENV_DIR/bin/python" -c "import site; print(site.getsitepackages()[0] if site.getsitepackages() else site.getusersitepackages())" 2>/dev/null)" || exit 0
SITECUSTOMIZE="$SITE_PACKAGES/sitecustomize.py"

needs_recovery=false

# Check antigravity hook presence
if [ -f "$ANTIGRAVITY_PATCH" ]; then
    if [ ! -f "$SITECUSTOMIZE" ] || ! grep -q "hermes-antigravity" "$SITECUSTOMIZE" 2>/dev/null; then
        needs_recovery=true
    fi
fi

# Check claude hook presence
if [ -f "$CLAUDE_PATCH" ]; then
    if [ ! -f "$SITECUSTOMIZE" ] || ! grep -q "hermes-claude-auth" "$SITECUSTOMIZE" 2>/dev/null; then
        needs_recovery=true
    fi
fi

if ! $needs_recovery; then
    exit 0
fi

echo ""
printf "${YELLOW}[hermes-post-update] Detected missing sitecustomize hooks. Recovering...${RESET}\n"

recovered=false

# Recover antigravity first (its sitecustomize includes both hooks)
if [ -f "$ANTIGRAVITY_PATCH" ] && [ -n "$ANTIGRAVITY_INSTALL" ]; then
    echo ""
    printf "${GREEN}→ Recovering Google Antigravity provider...${RESET}\n"
    bash "$ANTIGRAVITY_INSTALL" --repair || true
    recovered=true
fi

# Recover claude (will skip sitecustomize if antigravity already restored it)
if [ -f "$CLAUDE_PATCH" ] && [ -n "$CLAUDE_INSTALL" ]; then
    echo ""
    printf "${GREEN}→ Recovering Claude Code bypass...${RESET}\n"
    bash "$CLAUDE_INSTALL" --post-update || true
    recovered=true
fi

# Restart gateway
if systemctl --user is-active hermes-gateway.service >/dev/null 2>&1; then
    systemctl --user restart hermes-gateway.service 2>/dev/null || true
fi

if $recovered; then
    printf "\n${GREEN}[hermes-post-update] Recovery complete.${RESET}\n"
fi
