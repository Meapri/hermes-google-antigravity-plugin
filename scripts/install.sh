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

mkdir -p "$HERMES_HOME/plugins/model-providers/google-antigravity"
cp "$REPO_ROOT/plugins/model-providers/google-antigravity/__init__.py" \
   "$REPO_ROOT/plugins/model-providers/google-antigravity/plugin.yaml" \
   "$HERMES_HOME/plugins/model-providers/google-antigravity/"

cp "$REPO_ROOT/agent/google_antigravity_adapter.py" "$HERMES_AGENT_DIR/agent/"
cp "$REPO_ROOT/agent/google_antigravity_oauth.py" "$HERMES_AGENT_DIR/agent/"

if [[ -s "$REPO_ROOT/patches/hermes-agent-antigravity-core.patch" ]]; then
  if git -C "$HERMES_AGENT_DIR" apply --check "$REPO_ROOT/patches/hermes-agent-antigravity-core.patch"; then
    git -C "$HERMES_AGENT_DIR" apply "$REPO_ROOT/patches/hermes-agent-antigravity-core.patch"
    echo "Applied Hermes core integration patch."
  else
    echo "Core patch did not apply cleanly. Your Hermes tree may already include these changes." >&2
    echo "Review: $REPO_ROOT/patches/hermes-agent-antigravity-core.patch" >&2
  fi
fi

echo "Installed google-antigravity provider plugin to: $HERMES_HOME/plugins/model-providers/google-antigravity"
echo "Restart Hermes, then run: hermes login --provider google-antigravity"
