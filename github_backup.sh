#!/bin/bash

# A script to back up all of a user's GitHub repositories.

# --- Configuration ---
## No need for GitHub username; script uses authenticated user's token

# Directory to save the backups in.
BACKUP_DIR="backups/backup_$(date +%d.%m.%Y)"

# The maximum number of repositories to back up at the same time.
# Start with a low number (e.g., 4) and increase it based on your
# system's performance and network connection.
MAX_JOBS=$(nproc)
# --- End Configuration ---

## No username check needed

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

# Create the backup directory if it doesn't exist
mkdir -p "$BACKUP_DIR"
cd "$BACKUP_DIR" || exit

LOG_FILE="backup.log"

# Function to log messages
log() {
  echo "$1" | tee -a "$LOG_FILE"
}

log "Fetching repository list for authenticated user..."

# Fetch the list of repositories with pagination
REPO_URLS=""
PAGE=1
while true; do
  RESPONSE=$(curl -s -H "Authorization: token $GITHUB_TOKEN" "https://api.github.com/user/repos?per_page=100&page=$PAGE")
  if [ -z "$RESPONSE" ] || [ "$RESPONSE" = "[]" ]; then
    break
  fi
  PAGE_URLS=$(echo "$RESPONSE" | jq -r '.[].clone_url')
  if [ -z "$PAGE_URLS" ]; then
    break
  fi
  REPO_URLS="$REPO_URLS $PAGE_URLS"
  PAGE=$((PAGE + 1))
done

if [ -z "$REPO_URLS" ]; then
  echo "Could not fetch repositories. Check your token."
  exit 1
fi

log "Found repositories. Starting parallel backup with up to $MAX_JOBS jobs..."

for REPO_URL in $REPO_URLS; do
  # This block is what gets run in parallel.
  # We wrap it in parentheses and add '&' at the end to run it as a background job.
  (
    REPO_NAME=$(basename "$REPO_URL" .git)
    echo "--- [Starting] Processing: $REPO_NAME ---"

    # Add the token to the URL for authentication
    AUTH_REPO_URL=$(echo "$REPO_URL" | sed "s|://|://$GITHUB_TOKEN@|")

    if [ -d "$REPO_NAME.git" ]; then
      # If the directory exists, update it
      (cd "$REPO_NAME.git" && git remote update)
      echo "--- [Finished] Updating: $REPO_NAME ---"
    else
      # Otherwise, clone it as a bare repository
      git clone --mirror "$AUTH_REPO_URL" "$REPO_NAME.git"
      echo "--- [Finished] Cloning: $REPO_NAME ---"
    fi
  ) &

  # --- Job Management ---
  # Check the number of running background jobs
  # and wait if it reaches the maximum limit.
  # 'jobs -p' lists the Process IDs (PIDs) of background jobs.
  # 'wc -l' counts the number of lines, giving us the job count.
  while [ "$(jobs -p | wc -l)" -ge "$MAX_JOBS" ]; do
    sleep 1
  done

done

# Wait for all remaining background jobs to complete before exiting
log "Waiting for all backup jobs to finish..."
wait
log "GitHub backup complete!"
