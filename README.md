# GitHub Backup & Mirror

A backup engine and GitHub Actions workflow to back up all your GitHub repositories, code metadata, discussions, Actions logs, and account-wide contributions, save them to Google Drive, and automatically mirror them to GitLab.

## Features

- **True Mirrors & Wikis:** Uses `git clone --mirror` to capture all branches, tags, commit history, and wikis.
- **Issues, PRs & Discussions:** Backs up all issues, comments, pull requests (with diffs & reviews), and discussions (GraphQL).
- **Actions Logs & Activity:** Archives workflow run execution step logs (ZIP) and your account's external contributions across GitHub.
- **Smart Sync:** Automatically creates missing repositories on GitLab/Codeberg matching your GitHub visibility (Public/Private).
- **Drive Retention:** Uploads a daily snapshot to Google Drive and automatically prunes backups older than 7 days.
- **Runner Diagnostics:** Displays CI runner geolocation and network info via `ipinfo.io`.

---

## Setup & Automation

The easiest way to use this is via the included GitHub Actions workflow, which runs completely free in the background every night.

### 1. Add Required Secret
Go to your repository **Settings → Secrets and variables → Actions** and add:
- `PERSONAL_GITHUB_TOKEN`: A GitHub Personal Access Token with `repo`, `read:discussion`, `actions:read`, `read:user`, and `gist` scopes.

### 2. Enable Cloud & Mirroring (Optional)
To enable the advanced features, simply add these additional secrets:

| Feature | Secret Name | What it does |
| :--- | :--- | :--- |
| **Google Drive** | `RCLONE_CONFIG` | Paste your full `rclone.conf` contents here to upload backups to Drive. |
| **GitLab Mirror** | `GITLAB_TOKEN`<br>`GITLAB_USER` | Your GitLab PAT (`api`, `write_repository` scopes) and username. |
| **Codeberg Mirror** | `CODEBERG_TOKEN`<br>`CODEBERG_USER` | Your Codeberg PAT (`write:repository`, `write:user` scopes) and username. |

---

## Running Locally

Run locally using **[`uv`](https://github.com/astral-sh/uv)** (Python 3.13) or the shell runner:

```bash
# 1. Sync dependencies
uv sync

# 2. Run backup
GITHUB_TOKEN=ghp_xxx uv run python backup_engine.py

# Or via shell wrapper:
chmod +x github_backup.sh
GITHUB_TOKEN=ghp_xxx ./github_backup.sh
```

---

## Restoring a Backup

To restore any repository from the bare mirror:

```bash
git clone /path/to/backups/backup_YYYY-MM-DD/repositories/owner__repo/repo.git
```