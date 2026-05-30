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

# ── Parse flags ─────────────────────────────────────────────────────
POST_UPDATE=false
CHECK_ONLY=false

for arg in "$@"; do
    case "$arg" in
        --post-update) POST_UPDATE=true ;;
        --check)       CHECK_ONLY=true ;;
        *) ;;
    esac
done

# ── Pre-flight checks ───────────────────────────────────────────────
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

# ── --check mode: verify patch integrity ────────────────────────────
if $CHECK_ONLY; then
    ALL_OK=true
    for f in \
        "$PATCHES_DIR/antigravity_provider_patch.py" \
        "$HERMES_AGENT_DIR/agent/google_antigravity_adapter.py" \
        "$HERMES_AGENT_DIR/agent/google_antigravity_oauth.py"; do
        if [[ -f "$f" ]]; then
            printf "${GREEN}[✓] %s${RESET}\n" "$f"
        else
            printf "${RED}[✗] MISSING: %s${RESET}\n" "$f"
            ALL_OK=false
        fi
    done

    if [[ -f "$SITECUSTOMIZE" ]] && grep -q "$MARKER" "$SITECUSTOMIZE"; then
        printf "${GREEN}[✓] sitecustomize hook present${RESET}\n"
    else
        printf "${RED}[✗] sitecustomize hook MISSING or outdated${RESET}\n"
        ALL_OK=false
    fi

    if $ALL_OK; then
        printf "\n${GREEN}All patches intact.${RESET}\n"
        exit 0
    else
        printf "\n${YELLOW}Some patches missing. Run: ./scripts/install.sh --post-update${RESET}\n"
        exit 1
    fi
fi

# ── Full install or post-update recovery ────────────────────────────
if $POST_UPDATE; then
    printf "${YELLOW}[post-update] Restoring patches after hermes update...${RESET}\n"
else
    printf "${YELLOW}[install] Installing Google Antigravity provider...${RESET}\n"
fi

# ── Step 1: Copy plugin (skip in post-update — plugin metadata rarely changes) ──
if ! $POST_UPDATE; then
    mkdir -p "$HERMES_HOME/plugins/model-providers/google-antigravity"
    cp "$REPO_ROOT/plugins/model-providers/google-antigravity/__init__.py" \
       "$REPO_ROOT/plugins/model-providers/google-antigravity/plugin.yaml" \
       "$HERMES_HOME/plugins/model-providers/google-antigravity/"
    printf "${GREEN}[✓] Installed plugin to %s/plugins/model-providers/google-antigravity/${RESET}\n" "$HERMES_HOME"
fi

# ── Step 2: Copy agent runtime files ────────────────────────────────
for f in google_antigravity_adapter.py google_antigravity_oauth.py \
         antigravity_quota_grpc.py antigravity_stream_grpc.py; do
    cp "$REPO_ROOT/agent/$f" "$HERMES_AGENT_DIR/agent/"
done
printf "${GREEN}[✓] Copied agent runtime files${RESET}\n"

# ── Step 3: Install monkey-patch ────────────────────────────────────
mkdir -p "$PATCHES_DIR"
cp "$REPO_ROOT/patches/antigravity_provider_patch.py" "$PATCHES_DIR/"
printf "${GREEN}[✓] Installed antigravity provider patch (safe mode)${RESET}\n"

# ── Step 4: Install sitecustomize hook ──────────────────────────────
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
printf "${GREEN}[✓] Installed sitecustomize hook (3 import hooks)${RESET}\n"

# ── Step 5: Verify patches ──────────────────────────────────────────
PATCH_CHECK=$("$VENV_DIR/bin/python" -c "
import sys, os
sys.path.insert(0, os.path.expanduser('$PATCHES_DIR'))
try:
    import antigravity_provider_patch
    ok = antigravity_provider_patch._patch_models_module()
    print('models:OK' if ok else 'models:FAIL')
except Exception as e:
    print(f'import:FAIL ({e})'
)" 2>/dev/null || echo "verify:FAIL")
printf "${GREEN}[✓] Patch integrity: %s${RESET}\n" "$PATCH_CHECK"

# ── Step 6: Install auto-recovery git hook ──────────────────────────
GIT_HOOKS_DIR="$HERMES_AGENT_DIR/.git/hooks"
POST_MERGE_HOOK="$GIT_HOOKS_DIR/post-merge"
if [ -d "$GIT_HOOKS_DIR" ]; then
    cp "$REPO_ROOT/scripts/post-merge-hook.sh" "$POST_MERGE_HOOK"
    chmod +x "$POST_MERGE_HOOK"
    printf "${GREEN}[✓] Installed auto-recovery hook (post-merge)${RESET}\n"
else
    printf "${YELLOW}[!] No .git/hooks dir — skipping auto-recovery hook${RESET}\n"
fi

# ── Step 7: Restart gateway ─────────────────────────────────────────
if systemctl --user is-active hermes-gateway.service >/dev/null 2>&1; then
    systemctl --user restart hermes-gateway.service
    printf "${GREEN}[✓] Restarted hermes-gateway.service${RESET}\n"
else
    printf "${YELLOW}[!] hermes-gateway not running — restart manually when ready${RESET}\n"
fi

echo ""
if $POST_UPDATE; then
    echo "Post-update recovery complete."
    echo "Run 'hermes model' to verify Google Antigravity appears in the provider list."
else
    echo "Installation complete. Next steps:"
    echo "  1. Make sure agy CLI is authenticated (run 'agy' once)"
    echo "  2. Run: hermes auth add google-antigravity"
    echo "  3. Use: hermes chat --provider google-antigravity -m gemini-3.5-flash-high"
fi
