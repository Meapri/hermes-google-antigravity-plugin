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

prime_antigravity_client_cache() {
    # Extracting from the agy binary is intentionally an install/update-time
    # operation, not a normal `hermes` startup operation.  The binary can be
    # 100MB+; cache the extracted OAuth client pair privately and refresh only
    # when the binary metadata changes or explicit env overrides are supplied.
    HERMES_HOME="$HERMES_HOME" "$VENV_DIR/bin/python" <<'PY'
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

home = Path(os.environ.get("HERMES_HOME") or Path.home() / ".hermes")
cache = home / "auth" / "google_antigravity_client.json"
cache.parent.mkdir(parents=True, exist_ok=True)

env_id = os.environ.get("HERMES_ANTIGRAVITY_CLIENT_ID", "").strip()
env_secret = os.environ.get("HERMES_ANTIGRAVITY_CLIENT_SECRET", "").strip()
agy_path = shutil.which("agy")


def write_cache(payload):
    tmp = cache.with_suffix(f".tmp.{os.getpid()}")
    fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, indent=2, sort_keys=True)
            fh.write("\n")
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, cache)
    finally:
        try:
            if tmp.exists():
                tmp.unlink()
        except OSError:
            pass


if env_id and env_secret:
    write_cache({
        "client_id": env_id,
        "client_secret": env_secret,
        "source": "env",
    })
    print(f"[✓] Primed Antigravity OAuth client cache from env: {cache}")
    sys.exit(0)

if not agy_path:
    print("[!] agy not found on PATH — skipping Antigravity OAuth client cache priming")
    sys.exit(0)

try:
    st = os.stat(agy_path)
    agy_meta = {
        "source_agy_path": agy_path,
        "source_agy_size": st.st_size,
        "source_agy_mtime_ns": st.st_mtime_ns,
    }
except OSError:
    agy_meta = {"source_agy_path": agy_path}

try:
    existing = json.loads(cache.read_text(encoding="utf-8"))
except (OSError, json.JSONDecodeError):
    existing = {}

if (
    existing.get("client_id")
    and existing.get("client_secret")
    and existing.get("source_agy_path") == agy_meta.get("source_agy_path")
    and existing.get("source_agy_size") == agy_meta.get("source_agy_size")
    and existing.get("source_agy_mtime_ns") == agy_meta.get("source_agy_mtime_ns")
):
    print(f"[✓] Antigravity OAuth client cache already fresh: {cache}")
    sys.exit(0)

try:
    data = subprocess.check_output(["strings", agy_path], timeout=30)
except (subprocess.CalledProcessError, FileNotFoundError, subprocess.TimeoutExpired) as exc:
    print(f"[!] Failed to extract Antigravity OAuth client from agy: {exc}")
    sys.exit(0)

text = data.decode(errors="replace")
ids = re.findall(r"(\d+-[\w]+\.apps\.googleusercontent\.com)", text)
secrets = re.findall(r"(GOCSPX-[\w]+)", text)
if not ids or not secrets:
    print("[!] No Google OAuth client id/secret found in agy — cache not updated")
    sys.exit(0)

chosen_index = 0
client_id = ""
for i, cid in enumerate(ids):
    if cid.startswith("1071006060591"):
        client_id = cid
        chosen_index = i
        break
if not client_id:
    client_id = ids[0]

if chosen_index < len(secrets):
    client_secret = secrets[chosen_index]
elif len(secrets) == 1:
    client_secret = secrets[0]
else:
    client_secret = secrets[0]

write_cache({
    "client_id": client_id,
    "client_secret": client_secret,
    "source": "install strings agy",
    **agy_meta,
})
print(
    f"[✓] Primed Antigravity OAuth client cache: {cache} "
    f"(id_prefix={client_id[:12]}, secret_len={len(client_secret)})"
)
PY
}

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

    # ── Content drift check: installed copy must match the repo ─────────
    # File-existence alone does not catch the case where a runtime hotfix
    # was applied to the installed copy but never synced back to the repo
    # (or vice-versa). Compare byte-for-byte and warn on any drift.
    declare -A DRIFT_PAIRS=(
        ["$PATCHES_DIR/antigravity_provider_patch.py"]="$REPO_ROOT/patches/antigravity_provider_patch.py"
        ["$HERMES_AGENT_DIR/agent/google_antigravity_adapter.py"]="$REPO_ROOT/agent/google_antigravity_adapter.py"
        ["$HERMES_AGENT_DIR/agent/google_antigravity_oauth.py"]="$REPO_ROOT/agent/google_antigravity_oauth.py"
        ["$HERMES_AGENT_DIR/agent/antigravity_quota_grpc.py"]="$REPO_ROOT/agent/antigravity_quota_grpc.py"
        ["$HERMES_AGENT_DIR/agent/antigravity_stream_grpc.py"]="$REPO_ROOT/agent/antigravity_stream_grpc.py"
    )
    for installed in "${!DRIFT_PAIRS[@]}"; do
        repo="${DRIFT_PAIRS[$installed]}"
        if [[ -f "$installed" && -f "$repo" ]]; then
            if ! cmp -s "$installed" "$repo"; then
                printf "${YELLOW}[!] DRIFT: %s differs from repo (%s)${RESET}\n" \
                    "$(basename "$installed")" "$repo"
                ALL_OK=false
            fi
        fi
    done

    if $ALL_OK; then
        printf "\n${GREEN}All patches intact.${RESET}\n"
        exit 0
    else
        printf "\n${YELLOW}Patches missing or drifted. To restore from repo: ./scripts/install.sh --post-update${RESET}\n"
        printf "${YELLOW}If the installed copy is the newer one, sync it back to the repo and commit instead.${RESET}\n"
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

# ── Step 3: Prime OAuth client cache ────────────────────────────────
prime_antigravity_client_cache

# ── Step 4: Install monkey-patch ────────────────────────────────────
mkdir -p "$PATCHES_DIR"
cp "$REPO_ROOT/patches/antigravity_provider_patch.py" "$PATCHES_DIR/"
printf "${GREEN}[✓] Installed antigravity provider patch (safe mode)${RESET}\n"

# ── Step 5: Install sitecustomize hook ──────────────────────────────
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
printf "${GREEN}[✓] Installed sitecustomize hook (7 Antigravity + 2 Claude import hooks)${RESET}\n"

# ── Step 6: Verify patches ──────────────────────────────────────────
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

# ── Step 7: Install auto-recovery git hook ──────────────────────────
GIT_HOOKS_DIR="$HERMES_AGENT_DIR/.git/hooks"
POST_MERGE_HOOK="$GIT_HOOKS_DIR/post-merge"
if [ -d "$GIT_HOOKS_DIR" ]; then
    cp "$REPO_ROOT/scripts/post-merge-hook.sh" "$POST_MERGE_HOOK"
    chmod +x "$POST_MERGE_HOOK"
    printf "${GREEN}[✓] Installed auto-recovery hook (post-merge)${RESET}\n"
else
    printf "${YELLOW}[!] No .git/hooks dir — skipping auto-recovery hook${RESET}\n"
fi

# ── Step 8: Restart gateway ─────────────────────────────────────────
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
