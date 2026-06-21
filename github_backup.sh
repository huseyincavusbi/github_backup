#!/bin/bash

# A script to back up all of a user's GitHub repositories and mirror
# them to GitLab and Codeberg.

# --- Configuration ---
## No need for GitHub username; script uses authenticated user's token

# Directory to save the backups in.
# YYYY-MM-DD format is used so folder names sort lexicographically
# (enabling reliable pruning of old Drive backups by name sort).
BACKUP_DIR="backups/backup_$(date +%Y-%m-%d)"

# The maximum number of repositories to back up at the same time.
# Start with a low number (e.g., 4) and increase it based on your
# system's performance and network connection.
MAX_JOBS=$(nproc)

# --- Mirror Destinations (all optional) ---
# Set these environment variables to enable live mirroring.
# If a token is absent, that platform's mirroring block is skipped.
#
#   GITLAB_TOKEN   : GitLab PAT with 'api' and 'write_repository' scopes
#   GITLAB_USER    : your gitlab.com username
#   CODEBERG_TOKEN : Codeberg PAT with 'write:repository' scope
#   CODEBERG_USER  : your codeberg.org username
# --- End Configuration ---

# Clean up tokens (GitHub Actions secrets often have trailing newlines from copy-pasting)
GITHUB_TOKEN=$(echo "$GITHUB_TOKEN" | sed 's/[[:space:]]//g')
GITLAB_TOKEN=$(echo "$GITLAB_TOKEN" | sed 's/[[:space:]]//g')
GITLAB_USER=$(echo "$GITLAB_USER" | sed 's/[[:space:]]//g')
CODEBERG_TOKEN=$(echo "$CODEBERG_TOKEN" | sed 's/[[:space:]]//g')
CODEBERG_USER=$(echo "$CODEBERG_USER" | sed 's/[[:space:]]//g')

# Check for dependencies
if ! command -v jq &> /dev/null; then
  echo "Error: jq is not installed. Please install it first."
  exit 1
fi

# Check for Personal Access Token
if [ -z "$GITHUB_TOKEN" ]; then
  echo "Error: GITHUB_TOKEN environment variable is not set."
  echo "Usage: GITHUB_TOKEN=your_token_here ./github_backup.sh"
  exit 1
fi

# Create a temp file to track failed repos/operations.
# mktemp always returns an absolute path, so it is safe to reference
# from parent shell, subshells, and sub-subshells without path offsets.
FAIL_LOG=$(mktemp)

# Create the backup directory if it doesn't exist
mkdir -p "$BACKUP_DIR"
cd "$BACKUP_DIR" || exit

LOG_FILE="backup.log"

# Function to log messages with timestamp to both stdout and the log file
log() {
  echo "[$(date +%H:%M:%S)] $1" | tee -a "$LOG_FILE"
}

# ---------------------------------------------------------------------------
# ensure_gitlab_repo REPO_NAME IS_PRIVATE
#   Creates a GitLab project if it does not already exist.
#   Returns 0 on success (created or already exists), 1 on error.
# ---------------------------------------------------------------------------
ensure_gitlab_repo() {
  local repo_name="$1"
  local is_private="$2"
  local visibility="public"
  if [ "$is_private" = "true" ]; then
    visibility="private"
  fi
  local http_code
  http_code=$(curl -s -o /dev/null -w "%{http_code}" \
    -X POST "https://gitlab.com/api/v4/projects" \
    -H "PRIVATE-TOKEN: $GITLAB_TOKEN" \
    -H "Content-Type: application/json" \
    -d "{\"name\":\"${repo_name}\",\"path\":\"${repo_name}\",\"visibility\":\"${visibility}\",\"initialize_with_readme\":false}")
  # 201 = created, 400 = already exists (name/path taken in this namespace)
  if [ "$http_code" = "201" ] || [ "$http_code" = "400" ]; then
    return 0
  fi
  log "  [GitLab][ERROR] Repo creation failed for '${repo_name}' (HTTP ${http_code})"
  return 1
}

# ---------------------------------------------------------------------------
# ensure_codeberg_repo REPO_NAME IS_PRIVATE
#   Creates a Codeberg (Forgejo) repository if it does not exist.
#   Returns 0 on success (created or already exists), 1 on error.
# ---------------------------------------------------------------------------
ensure_codeberg_repo() {
  local repo_name="$1"
  local is_private="$2"

  # Check if the repo already exists first (GET) — avoids relying on
  # POST returning 409 which some Forgejo versions break (HTTP 500).
  local exists_code
  exists_code=$(curl -s -o /dev/null -w "%{http_code}" \
    "https://codeberg.org/api/v1/repos/${CODEBERG_USER}/${repo_name}" \
    -H "Authorization: token $CODEBERG_TOKEN")

  if [ "$exists_code" = "200" ]; then
    return 0
  fi

  # Repo doesn't exist yet — attempt to create via POST
  local http_code
  http_code=$(curl -s -o /dev/null -w "%{http_code}" \
    -X POST "https://codeberg.org/api/v1/user/repos" \
    -H "Authorization: token $CODEBERG_TOKEN" \
    -H "Content-Type: application/json" \
    -d "{\"name\":\"${repo_name}\",\"private\":${is_private},\"auto_init\":false}")
  # 201 = created, 409 = already exists (race condition with parallel jobs)
  if [ "$http_code" = "201" ] || [ "$http_code" = "409" ]; then
    return 0
  fi
  log "  [Codeberg][ERROR] Repo creation failed for '${repo_name}' (HTTP ${http_code})"
  return 1
}

log "Fetching repository list for authenticated user..."

# Fetch the list of repositories with pagination
REPO_ITEMS=""
PAGE=1
while true; do
  RESPONSE=$(curl -s -H "Authorization: token $GITHUB_TOKEN" "https://api.github.com/user/repos?per_page=100&page=$PAGE")
  if [ -z "$RESPONSE" ] || [ "$RESPONSE" = "[]" ]; then
    break
  fi
  PAGE_ITEMS=$(echo "$RESPONSE" | jq -r '.[] | "\(.clone_url)|\(.private)"')
  if [ -z "$PAGE_ITEMS" ]; then
    break
  fi
  REPO_ITEMS="$REPO_ITEMS $PAGE_ITEMS"
  PAGE=$((PAGE + 1))
done

if [ -z "$REPO_ITEMS" ]; then
  echo "Could not fetch repositories. Check your token."
  rm -f "$FAIL_LOG"
  exit 1
fi

log "Found repositories. Starting parallel backup with up to $MAX_JOBS jobs..."

for ITEM in $REPO_ITEMS; do
  # Extract URL and privacy status
  REPO_URL="${ITEM%|*}"
  IS_PRIVATE="${ITEM#*|}"
  IS_PRIVATE=$(echo "$IS_PRIVATE" | sed 's/[[:space:]]//g')

  # Each repo runs as a background subshell.
  # Subshells inherit: CWD, all variables, all functions.
  (
    REPO_NAME=$(basename "$REPO_URL" .git)
    log "--- [Starting] Processing: $REPO_NAME ---"

    # Embed the token in the URL for authentication (standard for CI environments)
    AUTH_REPO_URL=$(echo "$REPO_URL" | sed "s|://|://$GITHUB_TOKEN@|")

    # Track whether the local backup step succeeded.
    # Only mirror to GitLab/Codeberg if local backup is healthy.
    BACKUP_OK=1

    # --- Local Backup (clone --mirror or incremental update) ---
    if [ -d "$REPO_NAME.git" ]; then
      # Directory exists: fetch all ref updates incrementally
      if (cd "$REPO_NAME.git" && git remote update) >> "$LOG_FILE" 2>&1; then
        log "--- [OK] Updated: $REPO_NAME ---"
      else
        log "--- [ERROR] Failed to update: $REPO_NAME ---"
        echo "$REPO_NAME (local update)" >> "$FAIL_LOG"
        BACKUP_OK=0
      fi
    else
      # First run: full mirror clone (captures all refs: branches, tags, notes)
      if git clone --mirror "$AUTH_REPO_URL" "$REPO_NAME.git" >> "$LOG_FILE" 2>&1; then
        log "--- [OK] Cloned: $REPO_NAME ---"
      else
        log "--- [ERROR] Failed to clone: $REPO_NAME ---"
        echo "$REPO_NAME (local clone)" >> "$FAIL_LOG"
        BACKUP_OK=0
      fi
    fi

    # Skip mirroring if local backup step failed
    if [ "$BACKUP_OK" = "0" ]; then
      log "--- [SKIP] Skipping mirrors for $REPO_NAME (local backup failed) ---"
      exit 0
    fi

    # --- Mirror to GitLab ---
    if [ -n "$GITLAB_TOKEN" ] && [ -n "$GITLAB_USER" ]; then
      log "  [GitLab] Ensuring repo exists: ${REPO_NAME} (private=${IS_PRIVATE})"
      if ensure_gitlab_repo "$REPO_NAME" "$IS_PRIVATE"; then
        GITLAB_URL="https://oauth2:${GITLAB_TOKEN}@gitlab.com/${GITLAB_USER}/${REPO_NAME}.git"
        log "  [GitLab] Pushing mirror: ${REPO_NAME}"
        if (cd "${REPO_NAME}.git" && git push --mirror "$GITLAB_URL") >> "$LOG_FILE" 2>&1; then
          log "  [GitLab] Done: ${REPO_NAME}"
        else
          log "  [GitLab][ERROR] Push failed: ${REPO_NAME}"
          echo "$REPO_NAME (GitLab push)" >> "$FAIL_LOG"
        fi
      fi
    fi

    # --- Mirror to Codeberg ---
    if [ -n "$CODEBERG_TOKEN" ] && [ -n "$CODEBERG_USER" ]; then
      log "  [Codeberg] Ensuring repo exists: ${REPO_NAME} (private=${IS_PRIVATE})"
      if ensure_codeberg_repo "$REPO_NAME" "$IS_PRIVATE"; then
        CODEBERG_URL="https://${CODEBERG_USER}:${CODEBERG_TOKEN}@codeberg.org/${CODEBERG_USER}/${REPO_NAME}.git"
        log "  [Codeberg] Pushing mirror: ${REPO_NAME}"
        if (cd "${REPO_NAME}.git" && git push --mirror "$CODEBERG_URL") >> "$LOG_FILE" 2>&1; then
          log "  [Codeberg] Done: ${REPO_NAME}"
        else
          log "  [Codeberg][ERROR] Push failed: ${REPO_NAME}"
          echo "$REPO_NAME (Codeberg push)" >> "$FAIL_LOG"
        fi
      fi
    fi

  ) &

  # --- Job Management ---
  # Throttle to MAX_JOBS parallel background jobs.
  while [ "$(jobs -p | wc -l)" -ge "$MAX_JOBS" ]; do
    sleep 1
  done

done

# Wait for all remaining background jobs to finish
log "Waiting for all backup jobs to finish..."
wait

# --- Final report: surface failures and set exit code ---
if [ -s "$FAIL_LOG" ]; then
  FAIL_COUNT=$(wc -l < "$FAIL_LOG")
  log "=========================================="
  log "WARNING: $FAIL_COUNT operation(s) failed (likely due to platform size limits):"
  while IFS= read -r failed_item; do
    log "  ✗ $failed_item"
  done < "$FAIL_LOG"
  log "=========================================="
  rm -f "$FAIL_LOG"
  log "GitHub backup completed WITH WARNINGS. (Workflow will continue)"
else
  rm -f "$FAIL_LOG"
  log "GitHub backup complete! All operations succeeded."
fi
