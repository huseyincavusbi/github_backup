# GitHub Backup & Mirror

A script and GitHub Actions workflow to back up all your GitHub repositories, save them to Google Drive, and automatically mirror them to GitLab and Codeberg.

## Features

- **True Mirrors:** Uses `git clone --mirror` to capture all branches, tags, and commits.
- **Smart Sync:** Automatically creates missing repositories on GitLab/Codeberg matching your GitHub visibility (Public/Private).
- **Parallel Processing:** Throttles concurrent backups to your CPU cores for maximum speed.
- **Drive Retention:** Uploads a daily snapshot to Google Drive and automatically prunes backups older than 7 days.
- **Failsafe:** Tracks errors and fails the GitHub Action workflow if any repository fails to sync.

---

## Setup & Automation

The easiest way to use this is via the included GitHub Actions workflow, which runs completely free in the background every night.

### 1. Add Required Secret
Go to your repository **Settings → Secrets and variables → Actions** and add:
- `PERSONAL_GITHUB_TOKEN`: A GitHub Personal Access Token with the `repo` scope.

### 2. Enable Cloud & Mirroring (Optional)
To enable the advanced features, simply add these additional secrets:

| Feature | Secret Name | What it does |
| :--- | :--- | :--- |
| **Google Drive** | `RCLONE_CONFIG` | Paste your full `rclone.conf` contents here to upload backups to Drive. |
| **GitLab Mirror** | `GITLAB_TOKEN`<br>`GITLAB_USER` | Your GitLab PAT (`api`, `write_repository` scopes) and username. |
| **Codeberg Mirror** | `CODEBERG_TOKEN`<br>`CODEBERG_USER` | Your Codeberg PAT (`write:repository` scope) and username. |

> **Note:** You must generate the respective Personal Access Tokens on GitLab and/or Codeberg with the required scopes listed above, then add them as GitHub Actions Secrets.

---

## Running Locally

You can also run the script manually on your own machine.

```bash
chmod +x github_backup.sh

# Basic GitHub Backup
GITHUB_TOKEN=ghp_xxx ./github_backup.sh

# Full Backup + Mirroring
GITHUB_TOKEN=ghp_xxx \
GITLAB_TOKEN=glpat_xxx GITLAB_USER=username \
CODEBERG_TOKEN=xxx CODEBERG_USER=username \
./github_backup.sh
```

---

## Restoring a Backup

Because this script creates **bare repositories**, there is no visible working directory. To restore your code and see your files again, simply clone the backup directory to a new folder:

```bash
git clone /path/to/backups/backup_2026-04-11/MyProject.git 
```