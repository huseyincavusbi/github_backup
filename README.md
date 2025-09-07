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

### 2. Google Drive Upload Setup (For Automated Workflow)

The GitHub Actions workflow can automatically upload backups to Google Drive using rclone:

1. Install rclone locally: `sudo apt-get install rclone` (or equivalent for your OS).
2. Configure rclone for Google Drive: `rclone config` and create a remote named `gdrivegit`.
3. Get the configuration: `rclone config show gdrivegit`
4. Add the entire configuration output as a GitHub secret named `RCLONE_CONFIG` in your repository settings.
5. Ensure the Google Drive account has a folder named `GitHub_Backups`.

**Note:** This setup is only required if you want to use the automated GitHub Actions workflow. Manual script execution doesn't require Google Drive setup.

### 3. Configuration

The script uses environment variables for configuration:

```bash
# Your GitHub username (set via environment variable)
export GITHUB_USER="YOUR_USERNAME"

# Directory to save the backups in.
BACKUP_DIR="backups/backup_$(date +%d.%m.%Y)"

# The maximum number of repositories to back up at the same time.
# Defaults to the number of CPU cores (nproc) for optimal performance.
MAX_JOBS=$(nproc)
```

The script will warn you if `GITHUB_USER` is not set and will default to "your_username".

### 4. Get a Personal Access Token (PAT)

This script requires a GitHub PAT with the **repo** scope selected.

- Follow the [GitHub guide to create a classic PAT](https://docs.github.com/en/authentication/keeping-your-account-and-data-secure/managing-your-personal-access-tokens#creating-a-personal-access-token-classic).
- Copy the token immediately. You will not see it again.

**For GitHub Actions workflow:** Save your token as a repository secret named `PERSONAL_GITHUB_TOKEN`.

### 5. Run the Script

Before running the script, you need to make it executable:

```bash
chmod +x github_backup.sh
```

Set your GitHub username and run the script from your terminal, providing the token as an environment variable:

```bash
export GITHUB_USER=your_username
GITHUB_TOKEN=ghp_YourSecretTokenGoesHere ./github_backup.sh
```

After the backup completes, check the `backup.log` file in the backup directory for detailed logs.

## GitHub Actions Workflow (Automated Backups)

This repository includes a GitHub Actions workflow that automatically backs up your repositories daily and uploads them to Google Drive.

### Workflow Features

- **Automated Daily Backups:** Runs every day at midnight UTC
- **Manual Trigger:** Can be triggered manually via GitHub Actions
- **Google Drive Upload:** Automatically uploads backups to Google Drive
- **Cleanup:** Removes backups older than 2 days from Google Drive
- **Parallel Processing:** Uses optimized settings for faster uploads

### Setting up the Workflow

1. **Set up repository secrets** in your GitHub repository settings:
   - `PERSONAL_GITHUB_TOKEN`: Your GitHub Personal Access Token with repo scope
   - `RCLONE_CONFIG`: Your rclone configuration for Google Drive (see below)
   - `GITHUB_USER`: Your GitHub username

2. **Configure rclone for Google Drive:**
   ```bash
   # Install rclone locally
   sudo apt-get install rclone  # or equivalent for your OS
   
   # Configure rclone for Google Drive
   rclone config
   # Create a remote named 'gdrivegit'
   # Follow the prompts to authenticate with Google Drive
   
   # Export the config
   rclone config show gdrivegit
   # Copy the entire output and save it as the RCLONE_CONFIG secret
   ```

3. **Create Google Drive folder:** Ensure your Google Drive has a folder named `GitHub_Backups`

4. **Enable the workflow:** The workflow will automatically run daily or can be triggered manually from the Actions tab.

The workflow will create timestamped backup folders in your Google Drive and automatically clean up old backups to save space.

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