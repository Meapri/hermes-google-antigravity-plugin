#!/usr/bin/env bash
set -euo pipefail

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
RESET='\033[0m'

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
HERMES_HOME="${HERMES_HOME:-$HOME/.hermes}"
HERMES_AGENT_DIR="${HERMES_AGENT_DIR:-$HERMES_HOME/hermes-agent}"
PATCHES_DIR="$HERMES_HOME/patches"
MARKER="# hermes-antigravity managed"

if [[ ! -d "$HERMES_AGENT_DIR" ]]; then
    printf "${RED}[✗] hermes-agent not found at %s${RESET}\n" "$HERMES_AGENT_DIR"
    exit 1
fi

# Find venv
if [ -n "${HERMES_VENV:-}" ] && [ -d "$HERMES_VENV" ]; then
    VENV_DIR="$HERMES_VENV"
elif [ -d "$HERMES_AGENT_DIR/venv" ]; then
    VENV_DIR="$HERMES_AGENT_DIR/venv"
elif [ -d "$HERMES_AGENT_DIR/.venv" ]; then
    VENV_DIR="$HERMES_AGENT_DIR/.venv"
else
    printf "${RED}[✗] No virtualenv found${RESET}\n"
    exit 1
fi

SITE_PACKAGES="$("$VENV_DIR/bin/python" -c "import site; print(site.getsitepackages()[0] if site.getsitepackages() else site.getusersitepackages())")"
SITECUSTOMIZE="$SITE_PACKAGES/sitecustomize.py"

# ── Step 1: Copy plugin ────────────────────────────────────────────
mkdir -p "$HERMES_HOME/plugins/model-providers/google-antigravity"
cp "$REPO_ROOT/plugins/model-providers/google-antigravity/__init__.py" \
   "$REPO_ROOT/plugins/model-providers/google-antigravity/plugin.yaml" \
   "$HERMES_HOME/plugins/model-providers/google-antigravity/"
printf "${GREEN}[✓] Installed plugin to %s/plugins/model-providers/google-antigravity/${RESET}\n" "$HERMES_HOME"

# ── Step 2: Copy agent runtime files ───────────────────────────────
for f in google_antigravity_adapter.py google_antigravity_oauth.py \
         antigravity_quota_grpc.py antigravity_stream_grpc.py; do
    cp "$REPO_ROOT/agent/$f" "$HERMES_AGENT_DIR/agent/"
done
printf "${GREEN}[✓] Copied agent runtime files${RESET}\n"

# ── Step 3: Install monkey-patch ───────────────────────────────────
mkdir -p "$PATCHES_DIR"
cp "$REPO_ROOT/patches/antigravity_provider_patch.py" "$PATCHES_DIR/"
printf "${GREEN}[✓] Installed antigravity provider patch${RESET}\n"

# ── Step 4: Install sitecustomize hook ─────────────────────────────
if [ ! -f "$SITECUSTOMIZE" ]; then
    cp "$REPO_ROOT/scripts/sitecustomize_hook.py" "$SITECUSTOMIZE"
elif grep -q "$MARKER" "$SITECUSTOMIZE"; then
    cp "$REPO_ROOT/scripts/sitecustomize_hook.py" "$SITECUSTOMIZE"
else
    BACKUP="$SITECUSTOMIZE.pre-antigravity"
    cp "$SITECUSTOMIZE" "$BACKUP"
    printf "${YELLOW}[!] Backed up existing sitecustomize.py to %s${RESET}\n" "$BACKUP"
    cp "$REPO_ROOT/scripts/sitecustomize_hook.py" "$SITECUSTOMIZE"
fi
chmod 644 "$SITECUSTOMIZE"
printf "${GREEN}[✓] Installed sitecustomize hook${RESET}\n"

# ── Step 5: Restart gateway ────────────────────────────────────────
if systemctl --user is-active hermes-gateway.service >/dev/null 2>&1; then
    systemctl --user restart hermes-gateway.service
    printf "${GREEN}[✓] Restarted hermes-gateway.service${RESET}\n"
else
    printf "${YELLOW}[!] hermes-gateway not running — restart manually when ready${RESET}\n"
fi

echo ""
echo "Installation complete. Next steps:"
echo "  1. Make sure agy CLI is authenticated (run 'agy' once)"
echo "  2. Run: hermes auth add google-antigravity"
echo "  3. Use: hermes chat --provider google-antigravity -m gemini-3.5-flash-high"
