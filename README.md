# GitHub Repository Backup Script

A simple and efficient Bash script that backs up all your public and private GitHub repositories in parallel.

## Features

- **Complete Backups:** Creates a full, mirror copy of each repository, including all commits, branches, and tags.
- **Parallel Processing:** Backs up multiple repositories simultaneously to significantly reduce the time it takes.
- **Secure:** Uses a GitHub Personal Access Token (PAT) passed as an environment variable, not stored in the code.
- **Simple Configuration:** Just set your username and backup location at the top of the script.
- **Pagination Support:** Automatically fetches all repositories, even if more than 100.
- **Logging:** Logs all operations to a file in the backup directory.
- **Dependency Checks:** Ensures required tools are installed before running.

## Setup and Usage

### 1. Prerequisites

You need to have `git` and `jq` installed. `jq` is a command-line JSON processor used to parse the repository list from GitHub's API. The script will check for these dependencies automatically.

- **On Debian/Ubuntu:** `sudo apt-get install git jq`
- **On macOS (using Homebrew):** `brew install git jq`

### 2. Google Drive Upload Setup (Optional)

To enable automatic upload of backup zip files to Google Drive:

1. Install rclone: `sudo apt-get install rclone` (or equivalent for your OS).
2. Configure rclone for Google Drive: `rclone config` and create a remote named `gdrive`.
3. Export the config: `rclone config dump | jq -r .gdrive > rclone_gdrive.conf`
4. Add the contents of `rclone_gdrive.conf` as a GitHub secret named `RCLONE_CONFIG` in your repository settings.
5. Ensure the Google Drive account has a folder named `GitHub_Backups` (or update the path in the workflow).

### 3. Configuration

Open the `github_backup.sh` script and edit the configuration section at the top:

```bash
# Your GitHub username
GITHUB_USER="YOUR_USERNAME"

# Directory to save the backups in.
BACKUP_DIR="backups/backup_$(date +%Y_%m_%d)"

# The maximum number of repositories to back up at the same time.
# Start with a low number (e.g., 4) and increase it based on your
# system's performance and network connection.
MAX_JOBS=4
```

### 4. Get a Personal Access Token (PAT)

This script requires a GitHub PAT with the **repo** scope selected.

- Follow the [GitHub guide to create a classic PAT](https://docs.github.com/en/authentication/keeping-your-account-and-data-secure/managing-your-personal-access-tokens#creating-a-personal-access-token-classic).
- Copy the token immediately. You will not see it again.

### 5. Run the Script
Before running the script, you need to make it executable:

```bash
chmod +x github_backup.sh
```
Run the script from your terminal, providing the token as an environment variable.

```bash
GITHUB_TOKEN=ghp_YourSecretTokenGoesHere ./github_backup.sh
```

After the backup completes, check the `backup.log` file in the backup directory for detailed logs.

## How to See Your Files (Restoring from a Backup)

This script creates **bare repositories**. A bare repository is a complete copy of the Git database (all your commits and files), but it does not have a "working directory" with the files visible. This is the standard and safest way to store backups.

To see and work with your files again, you simply clone from your local backup folder as if it were GitHub.

1. Navigate to a new directory where you want to restore the project.
2. Use the `git clone` command on your backup file:

```bash
# Example:
git clone /path/to/your/backups/MyProject.git MyProject-restored
```

This will create a new folder `MyProject-restored` containing all your familiar scripts, notebooks, and files, with their complete Git history intact.

## License

This project is licensed under the MIT License.