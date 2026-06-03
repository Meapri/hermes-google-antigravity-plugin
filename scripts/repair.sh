#!/usr/bin/env bash
set -euo pipefail

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
RESET='\033[0m'

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
HERMES_HOME="${HERMES_HOME:-$HOME/.hermes}"
HERMES_AGENT_DIR="${HERMES_AGENT_DIR:-$HERMES_HOME/hermes-agent}"

RUN_SMOKE=false
SKIP_PULL=false

for arg in "$@"; do
    case "$arg" in
        --smoke) RUN_SMOKE=true ;;
        --skip-pull) SKIP_PULL=true ;;
        *)
            printf "${RED}[✗] Unknown argument: %s${RESET}\n" "$arg"
            exit 2
            ;;
    esac
done

find_venv() {
    if [ -n "${HERMES_VENV:-}" ] && [ -d "$HERMES_VENV" ]; then
        printf '%s\n' "$HERMES_VENV"
    elif [ -d "$HERMES_AGENT_DIR/venv" ]; then
        printf '%s\n' "$HERMES_AGENT_DIR/venv"
    elif [ -d "$HERMES_AGENT_DIR/.venv" ]; then
        printf '%s\n' "$HERMES_AGENT_DIR/.venv"
    fi
}

printf "${YELLOW}[repair] Hermes Google Antigravity provider recovery${RESET}\n"

if [ ! -d "$HERMES_AGENT_DIR" ]; then
    printf "${RED}[✗] hermes-agent not found at %s${RESET}\n" "$HERMES_AGENT_DIR"
    exit 1
fi

VENV_DIR="$(find_venv || true)"
if [ -z "$VENV_DIR" ]; then
    printf "${RED}[✗] Hermes virtualenv not found${RESET}\n"
    exit 1
fi

if [ -d "$REPO_ROOT/.git" ] && ! $SKIP_PULL; then
    if git -C "$REPO_ROOT" diff --quiet && git -C "$REPO_ROOT" diff --cached --quiet; then
        printf "${YELLOW}[repair] Updating plugin clone...${RESET}\n"
        git -C "$REPO_ROOT" fetch origin
        branch="$(git -C "$REPO_ROOT" branch --show-current)"
        if [ -n "$branch" ]; then
            git -C "$REPO_ROOT" pull --ff-only origin "$branch"
        else
            printf "${YELLOW}[!] Detached HEAD; skipping pull${RESET}\n"
        fi
    else
        printf "${YELLOW}[!] Plugin clone has local changes; skipping git pull${RESET}\n"
    fi
fi

printf "${YELLOW}[repair] Reinstalling runtime files and hooks...${RESET}\n"
bash "$REPO_ROOT/scripts/install.sh"

printf "${YELLOW}[repair] Running final integrity and contract check...${RESET}\n"
bash "$REPO_ROOT/scripts/install.sh" --check

if $RUN_SMOKE; then
    printf "${YELLOW}[repair] Running provider smoke test...${RESET}\n"
    if command -v hermes >/dev/null 2>&1; then
        hermes chat --provider google-antigravity -m gemini-3.5-flash-high -q "OK"
    else
        printf "${RED}[✗] hermes command not found; smoke test skipped${RESET}\n"
        exit 1
    fi
fi

printf "\n${GREEN}[repair] Recovery complete.${RESET}\n"
