#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
HERMES_HOME="${HERMES_HOME:-$HOME/.hermes}"
HERMES_AGENT_DIR="${HERMES_AGENT_DIR:-$HERMES_HOME/hermes-agent}"

if [[ ! -d "$HERMES_AGENT_DIR" ]]; then
  echo "Hermes source tree not found: $HERMES_AGENT_DIR" >&2
  echo "Set HERMES_AGENT_DIR=/path/to/hermes-agent and retry." >&2
  exit 1
fi

ENV_FILE="$HERMES_HOME/.env"

_has_env_key() {
  local key="$1"
  [[ -n "${!key:-}" ]] && return 0
  [[ -f "$ENV_FILE" ]] && grep -qE "^${key}=" "$ENV_FILE"
}

_set_env_key() {
  local key="$1"
  local value="$2"
  mkdir -p "$HERMES_HOME"
  touch "$ENV_FILE"
  chmod 600 "$ENV_FILE" 2>/dev/null || true
  if grep -qE "^${key}=" "$ENV_FILE"; then
    python3 - "$ENV_FILE" "$key" "$value" <<'PY'
from pathlib import Path
import sys
path = Path(sys.argv[1])
key = sys.argv[2]
value = sys.argv[3]
lines = path.read_text().splitlines()
for idx, line in enumerate(lines):
    if line.startswith(key + "="):
        lines[idx] = f"{key}={value}"
        break
path.write_text("\n".join(lines) + "\n")
PY
  else
    printf '%s=%s\n' "$key" "$value" >> "$ENV_FILE"
  fi
}

_prompt_oauth_credentials() {
  if _has_env_key HERMES_ANTIGRAVITY_CLIENT_ID && _has_env_key HERMES_ANTIGRAVITY_CLIENT_SECRET; then
    return 0
  fi

  if [[ ! -t 0 ]]; then
    cat >&2 <<'MSG'
Antigravity OAuth client credentials are not configured.
Set HERMES_ANTIGRAVITY_CLIENT_ID and HERMES_ANTIGRAVITY_CLIENT_SECRET in your environment or ~/.hermes/.env, then restart Hermes.
MSG
    return 0
  fi

  echo "Antigravity OAuth client credentials are required for 'hermes model' login."
  echo "They will be saved to: $ENV_FILE"
  if ! _has_env_key HERMES_ANTIGRAVITY_CLIENT_ID; then
    read -r -p "HERMES_ANTIGRAVITY_CLIENT_ID: " client_id
    [[ -n "$client_id" ]] && _set_env_key HERMES_ANTIGRAVITY_CLIENT_ID "$client_id"
  fi
  if ! _has_env_key HERMES_ANTIGRAVITY_CLIENT_SECRET; then
    read -r -s -p "HERMES_ANTIGRAVITY_CLIENT_SECRET: " client_secret
    echo
    [[ -n "$client_secret" ]] && _set_env_key HERMES_ANTIGRAVITY_CLIENT_SECRET "$client_secret"
  fi
}

_core_integration_present() {
  grep -q '"google-antigravity"' "$HERMES_AGENT_DIR/hermes_cli/providers.py" \
    && grep -q '"google-antigravity"' "$HERMES_AGENT_DIR/hermes_cli/auth_commands.py" \
    && grep -q 'resolve_antigravity_runtime_credentials' "$HERMES_AGENT_DIR/hermes_cli/auth.py" \
    && grep -q 'GoogleAntigravityClient' "$HERMES_AGENT_DIR/agent/google_antigravity_adapter.py"
}

_apply_core_patch_without_copied_runtime() {
  local patch_file="$1"
  local check_log="$2"
  local excludes=(
    "--exclude=agent/google_antigravity_adapter.py"
    "--exclude=agent/google_antigravity_oauth.py"
    "--exclude=tests/agent/test_google_antigravity_adapter.py"
  )

  if git -C "$HERMES_AGENT_DIR" apply "${excludes[@]}" --check "$patch_file" 2>"$check_log"; then
    git -C "$HERMES_AGENT_DIR" apply "${excludes[@]}" "$patch_file"
    echo "Applied Hermes core integration patch, keeping copied Antigravity runtime files from this repo."
    return 0
  fi
  return 1
}

mkdir -p "$HERMES_HOME/plugins/model-providers/google-antigravity"
cp "$REPO_ROOT/plugins/model-providers/google-antigravity/__init__.py" \
   "$REPO_ROOT/plugins/model-providers/google-antigravity/plugin.yaml" \
   "$HERMES_HOME/plugins/model-providers/google-antigravity/"

cp "$REPO_ROOT/agent/google_antigravity_adapter.py" "$HERMES_AGENT_DIR/agent/"
cp "$REPO_ROOT/agent/google_antigravity_oauth.py" "$HERMES_AGENT_DIR/agent/"
cp "$REPO_ROOT/agent/antigravity_quota_grpc.py" "$HERMES_AGENT_DIR/agent/"
cp "$REPO_ROOT/agent/antigravity_stream_grpc.py" "$HERMES_AGENT_DIR/agent/"
_prompt_oauth_credentials

if [[ -s "$REPO_ROOT/patches/hermes-agent-antigravity-core.patch" ]]; then
  patch_check_log="$(mktemp)"
  patch_reverse_log="$(mktemp)"
  if git -C "$HERMES_AGENT_DIR" apply --check "$REPO_ROOT/patches/hermes-agent-antigravity-core.patch" 2>"$patch_check_log"; then
    git -C "$HERMES_AGENT_DIR" apply "$REPO_ROOT/patches/hermes-agent-antigravity-core.patch"
    echo "Applied Hermes core integration patch."
  elif git -C "$HERMES_AGENT_DIR" apply --reverse --check "$REPO_ROOT/patches/hermes-agent-antigravity-core.patch" 2>"$patch_reverse_log"; then
    echo "Hermes core integration patch already applied."
  elif _apply_core_patch_without_copied_runtime "$REPO_ROOT/patches/hermes-agent-antigravity-core.patch" "$patch_check_log"; then
    true
  elif _core_integration_present; then
    echo "Hermes core integration already present."
  else
    echo "Core patch did not apply cleanly. Your Hermes tree may already include these changes or may have drifted." >&2
    echo "Review: $REPO_ROOT/patches/hermes-agent-antigravity-core.patch" >&2
  fi
  rm -f "$patch_check_log" "$patch_reverse_log"
fi

echo "Installed google-antigravity provider plugin to: $HERMES_HOME/plugins/model-providers/google-antigravity"
echo "Restart Hermes, then run: hermes auth add google-antigravity"
