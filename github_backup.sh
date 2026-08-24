#!/usr/bin/env bash
set -e

# ==============================================================================
# GitHub Backup & Archival Suite - CLI Runner
# ==============================================================================

# Sanitize token variables from environment
export GITHUB_TOKEN=$(echo "${GITHUB_TOKEN:-$PERSONAL_GITHUB_TOKEN}" | sed 's/[[:space:]]//g')
export GITLAB_TOKEN=$(echo "$GITLAB_TOKEN" | sed 's/[[:space:]]//g')
export GITLAB_USER=$(echo "$GITLAB_USER" | sed 's/[[:space:]]//g')
export CODEBERG_TOKEN=$(echo "$CODEBERG_TOKEN" | sed 's/[[:space:]]//g')
export CODEBERG_USER=$(echo "$CODEBERG_USER" | sed 's/[[:space:]]//g')

if [ -z "$GITHUB_TOKEN" ]; then
  echo "Error: GITHUB_TOKEN (or PERSONAL_GITHUB_TOKEN) is not set."
  echo "Usage: GITHUB_TOKEN=ghp_xxx ./github_backup.sh"
  exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

if command -v uv &> /dev/null; then
  echo "[Runner] Running backup engine using uv..."
  uv run python backup_engine.py "$@"
elif [ -f ".venv/bin/activate" ]; then
  echo "[Runner] Running backup engine using active .venv..."
  source .venv/bin/activate
  python backup_engine.py "$@"
else
  echo "[Runner] Running backup engine using python3..."
  python3 backup_engine.py "$@"
fi
