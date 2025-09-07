## GitHub Backup Script

This script backs up all repositories for the authenticated GitHub user using their personal access token.

Just set your `GITHUB_TOKEN` environment variable and run the script.

Backups are saved locally in a dated folder.

## Google Drive Upload (Optional)

You can automatically upload your backup files to Google Drive using rclone:

1. Install rclone:
	```bash
	sudo apt-get install rclone
	```
2. Configure rclone for Google Drive:
	```bash
	rclone config
	```
	Follow the prompts to create a remote 
3. The workflow will use rclone to upload backups if configured. Make sure your rclone config is accessible for Actions in your repo.

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
