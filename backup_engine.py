#!/usr/bin/env python3
"""
GitHub Backup & Archival Engine
Comprehensive backup tool for GitHub:
- Git Repositories & Wikis (True Git mirrors)
- Issues & Issue Comments
- Pull Requests, Code Reviews, Review Comments, and Diffs
- GitHub Discussions (via GraphQL API)
- GitHub Actions Workflow Runs & Execution Logs (ZIP archives)
- Releases & Changelog metadata
- Account-Wide Activity in External Repositories (Authored Issues/PRs, Comments, Commits)
- User Profile, Event Timeline feeds, Starred Repositories, and Gists (Git mirrors)
- Live Mirroring to GitLab and Codeberg
"""

import argparse
import concurrent.futures
import datetime
import json
import os
import re
import shutil
import subprocess
import sys
import time
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlparse
import requests


# --- Logger Utility ---
def log(msg: str, level: str = "INFO") -> None:
    now = datetime.datetime.now().strftime("%H:%M:%S")
    prefix = f"[{now}] [{level}]"
    print(f"{prefix} {msg}", flush=True)


def sanitize_token(token: Optional[str]) -> str:
    if not token:
        return ""
    return re.sub(r"\s+", "", token.strip())


# --- GitHub API Client ---
class GitHubClient:
    def __init__(self, token: str, max_retries: int = 3):
        self.token = token
        self.base_url = "https://api.github.com"
        self.graphql_url = "https://api.github.com/graphql"
        self.max_retries = max_retries
        self.session = requests.Session()
        self.session.headers.update({
            "Authorization": f"Bearer {self.token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "GitHub-Backup-Engine/2.0",
        })

    def _handle_rate_limit(self, response: requests.Response) -> None:
        remaining = response.headers.get("x-ratelimit-remaining")
        reset_time = response.headers.get("x-ratelimit-reset")

        if remaining is not None and int(remaining) == 0 and reset_time is not None:
            sleep_duration = max(0, int(reset_time) - int(time.time())) + 2
            log(f"Primary rate limit reached. Sleeping for {sleep_duration}s until reset...", level="WARN")
            time.sleep(sleep_duration)
        elif response.status_code == 403 and "rate limit" in response.text.lower():
            retry_after = response.headers.get("retry-after")
            sleep_duration = int(retry_after) if retry_after else 60
            log(f"Secondary rate limit hit. Pausing for {sleep_duration}s...", level="WARN")
            time.sleep(sleep_duration)

    def request(self, method: str, url: str, **kwargs) -> requests.Response:
        if not url.startswith("http"):
            url = f"{self.base_url.rstrip('/')}/{url.lstrip('/')}"

        for attempt in range(1, self.max_retries + 1):
            try:
                res = self.session.request(method, url, **kwargs)
                self._handle_rate_limit(res)

                if res.status_code in (429, 500, 502, 503, 504) and attempt < self.max_retries:
                    backoff = 2 ** attempt
                    log(f"Received HTTP {res.status_code} on {url}. Retrying in {backoff}s...", level="WARN")
                    time.sleep(backoff)
                    continue

                return res
            except requests.RequestException as e:
                if attempt == self.max_retries:
                    raise
                backoff = 2 ** attempt
                log(f"Request failed: {e}. Retrying in {backoff}s (attempt {attempt}/{self.max_retries})...", level="WARN")
                time.sleep(backoff)
        raise RuntimeError(f"Failed request after {self.max_retries} attempts: {url}")

    def get_json(self, endpoint: str, params: Optional[Dict[str, Any]] = None) -> Optional[Any]:
        res = self.request("GET", endpoint, params=params)
        if res.status_code == 200:
            return res.json()
        elif res.status_code in (404, 410):
            return None
        else:
            log(f"GET {endpoint} returned status {res.status_code}: {res.text[:200]}", level="WARN")
            return None

    def paginate_rest(self, endpoint: str, params: Optional[Dict[str, Any]] = None, max_pages: Optional[int] = None) -> List[Any]:
        items: List[Any] = []
        page = 1
        query_params = dict(params or {})
        query_params.setdefault("per_page", 100)

        while True:
            query_params["page"] = page
            res = self.request("GET", endpoint, params=query_params)
            if res.status_code != 200:
                if res.status_code not in (404, 410):
                    log(f"Pagination stopped for {endpoint} (HTTP {res.status_code})", level="WARN")
                break

            data = res.json()
            if isinstance(data, list):
                if not data:
                    break
                items.extend(data)
                if len(data) < query_params["per_page"]:
                    break
            elif isinstance(data, dict):
                # Search API returns {"total_count": ..., "items": [...]}
                if "items" in data and isinstance(data["items"], list):
                    page_items = data["items"]
                    if not page_items:
                        break
                    items.extend(page_items)
                    if len(page_items) < query_params["per_page"]:
                        break
                else:
                    items.append(data)
                    break
            else:
                break

            page += 1
            if max_pages and page > max_pages:
                break

        return items

    def execute_graphql(self, query: str, variables: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, Any]]:
        for attempt in range(1, self.max_retries + 1):
            try:
                res = self.session.post(self.graphql_url, json={"query": query, "variables": variables or {}})
                self._handle_rate_limit(res)
                if res.status_code == 200:
                    data = res.json()
                    if "errors" in data and not data.get("data"):
                        log(f"GraphQL returned errors: {data['errors']}", level="WARN")
                        return None
                    return data.get("data")
                elif attempt < self.max_retries:
                    time.sleep(2 ** attempt)
                    continue
                return None
            except Exception as e:
                if attempt == self.max_retries:
                    log(f"GraphQL execution error: {e}", level="WARN")
                    return None
                time.sleep(2 ** attempt)
        return None

    def download_file(self, url: str, destination_path: str) -> bool:
        try:
            os.makedirs(os.path.dirname(destination_path), exist_ok=True)
            res = self.session.get(url, stream=True, allow_redirects=True)
            self._handle_rate_limit(res)
            if res.status_code == 200:
                with open(destination_path, "wb") as f:
                    for chunk in res.iter_content(chunk_size=65536):
                        if chunk:
                            f.write(chunk)
                return True
            elif res.status_code in (404, 410):
                # Log expired or unavailable
                return False
            else:
                log(f"Download file returned {res.status_code} for {url}", level="WARN")
                return False
        except Exception as e:
            log(f"Failed to download {url}: {e}", level="WARN")
            return False


# --- Helper Functions ---
def save_json(data: Any, path: str) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def run_cmd(cmd: List[str], cwd: Optional[str] = None) -> Tuple[int, str, str]:
    proc = subprocess.run(cmd, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    return proc.returncode, proc.stdout, proc.stderr


# --- GraphQL Queries ---
DISCUSSIONS_QUERY = """
query($owner: String!, $name: String!, $cursor: String) {
  repository(owner: $owner, name: $name) {
    discussions(first: 50, after: $cursor) {
      pageInfo {
        hasNextPage
        endCursor
      }
      totalCount
      nodes {
        id
        number
        title
        body
        createdAt
        url
        author {
          login
        }
        category {
          id
          name
          description
          emoji
        }
        comments(first: 50) {
          totalCount
          nodes {
            id
            body
            createdAt
            author {
              login
            }
            replies(first: 20) {
              totalCount
              nodes {
                id
                body
                createdAt
                author {
                  login
                }
              }
            }
          }
        }
      }
    }
  }
}
"""


# --- Mirror Providers ---
class RemoteMirrorManager:
    def __init__(self, gitlab_token: str, gitlab_user: str, codeberg_token: str, codeberg_user: str):
        self.gitlab_token = gitlab_token
        self.gitlab_user = gitlab_user
        self.codeberg_token = codeberg_token
        self.codeberg_user = codeberg_user

    def mirror_to_gitlab(self, repo_name: str, is_private: bool, repo_git_dir: str) -> Tuple[bool, str]:
        if not self.gitlab_token or not self.gitlab_user:
            return True, "Skipped (no GitLab credentials)"

        visibility = "private" if is_private else "public"
        headers = {"PRIVATE-TOKEN": self.gitlab_token, "Content-Type": "application/json"}
        payload = {"name": repo_name, "path": repo_name, "visibility": visibility, "initialize_with_readme": False}

        try:
            res = requests.post("https://gitlab.com/api/v4/projects", headers=headers, json=payload, timeout=15)
            # 201: Created, 400: Project already exists
            if res.status_code not in (201, 400):
                return False, f"GitLab project creation failed (HTTP {res.status_code})"

            gitlab_url = f"https://oauth2:{self.gitlab_token}@gitlab.com/{self.gitlab_user}/{repo_name}.git"
            rc, stdout, stderr = run_cmd(["git", "push", "--mirror", gitlab_url], cwd=repo_git_dir)
            if rc != 0:
                return False, f"GitLab push failed: {stderr.strip()[:150]}"
            return True, "Mirrored to GitLab successfully"
        except Exception as e:
            return False, f"GitLab error: {e}"

    def mirror_to_codeberg(self, repo_name: str, is_private: bool, repo_git_dir: str) -> Tuple[bool, str]:
        if not self.codeberg_token or not self.codeberg_user:
            return True, "Skipped (no Codeberg credentials)"

        headers = {"Authorization": f"token {self.codeberg_token}", "Content-Type": "application/json"}
        try:
            # Check existence first
            check_res = requests.get(f"https://codeberg.org/api/v1/repos/{self.codeberg_user}/{repo_name}", headers=headers, timeout=15)
            if check_res.status_code != 200:
                create_res = requests.post(
                    "https://codeberg.org/api/v1/user/repos",
                    headers=headers,
                    json={"name": repo_name, "private": is_private, "auto_init": False},
                    timeout=15,
                )
                if create_res.status_code not in (201, 409):
                    return False, f"Codeberg repo creation failed (HTTP {create_res.status_code})"

            codeberg_url = f"https://{self.codeberg_user}:{self.codeberg_token}@codeberg.org/{self.codeberg_user}/{repo_name}.git"
            rc, stdout, stderr = run_cmd(["git", "push", "--mirror", codeberg_url], cwd=repo_git_dir)
            if rc != 0:
                return False, f"Codeberg push failed: {stderr.strip()[:150]}"
            return True, "Mirrored to Codeberg successfully"
        except Exception as e:
            return False, f"Codeberg error: {e}"


# --- Main Backup Engine ---
class GitHubBackupEngine:
    def __init__(
        self,
        token: str,
        output_dir: str,
        max_workers: int = 4,
        skip_git: bool = False,
        skip_metadata: bool = False,
        skip_actions_logs: bool = False,
        skip_external_activity: bool = False,
        gitlab_token: str = "",
        gitlab_user: str = "",
        codeberg_token: str = "",
        codeberg_user: str = "",
    ):
        self.token = token
        self.output_dir = output_dir
        self.max_workers = max_workers
        self.skip_git = skip_git
        self.skip_metadata = skip_metadata
        self.skip_actions_logs = skip_actions_logs
        self.skip_external_activity = skip_external_activity

        self.client = GitHubClient(token)
        self.mirror_manager = RemoteMirrorManager(
            gitlab_token=gitlab_token,
            gitlab_user=gitlab_user,
            codeberg_token=codeberg_token,
            codeberg_user=codeberg_user,
        )

        self.user_info: Dict[str, Any] = {}
        self.summary: Dict[str, Any] = {
            "started_at": datetime.datetime.now().isoformat(),
            "completed_at": None,
            "repositories_backed_up": 0,
            "issues_count": 0,
            "pull_requests_count": 0,
            "discussions_count": 0,
            "actions_logs_downloaded": 0,
            "gists_backed_up": 0,
            "warnings_and_errors": [],
        }

    def authenticate_and_load_user(self) -> None:
        log("Authenticating with GitHub API...")
        user = self.client.get_json("/user")
        if not user or "login" not in user:
            raise RuntimeError("Failed to authenticate with GitHub. Please check your GITHUB_TOKEN.")
        self.user_info = user
        log(f"Authenticated as @{user['login']} ({user.get('name', 'N/A')})")

    def backup_account_activity(self) -> None:
        if self.skip_external_activity:
            log("Skipping account-wide external activity backup (--skip-external-activity)")
            return

        username = self.user_info["login"]
        log(f"Backing up account-wide activity for @{username}...")
        account_dir = os.path.join(self.output_dir, "account_activity")

        # 1. Profile & Organizations
        save_json(self.user_info, os.path.join(account_dir, "profile.json"))
        orgs = self.client.paginate_rest("/user/orgs")
        save_json(orgs, os.path.join(account_dir, "organizations.json"))

        # 2. Public & Private Events feed
        events = self.client.paginate_rest(f"/users/{username}/events")
        save_json(events, os.path.join(account_dir, "events_feed.json"))
        public_events = self.client.paginate_rest(f"/users/{username}/events/public")
        save_json(public_events, os.path.join(account_dir, "public_events.json"))

        # 3. Starred Repositories
        starred = self.client.paginate_rest("/user/starred")
        save_json(starred, os.path.join(account_dir, "starred_repos.json"))

        # 4. Authored Issues & Pull Requests across all GitHub repos
        log("Searching for issues & PRs authored across GitHub...")
        authored_issues = self.client.paginate_rest(f"/search/issues?q=author:{username}+type:issue", max_pages=10)
        authored_prs = self.client.paginate_rest(f"/search/issues?q=author:{username}+type:pr", max_pages=10)
        save_json(
            {"authored_issues": authored_issues, "authored_pull_requests": authored_prs},
            os.path.join(account_dir, "authored_issues_and_prs.json"),
        )

        # 5. Commented Issues & PRs across GitHub
        commented = self.client.paginate_rest(f"/search/issues?q=commenter:{username}", max_pages=10)
        save_json(commented, os.path.join(account_dir, "commented_issues_and_prs.json"))

        # 6. Gists & Gist Git Mirrors
        log("Fetching user Gists...")
        gists = self.client.paginate_rest("/gists")
        gists_dir = os.path.join(account_dir, "gists")
        save_json(gists, os.path.join(gists_dir, "gists_metadata.json"))

        for gist in gists:
            gist_id = gist.get("id")
            if not gist_id:
                continue
            gist_git_url = f"https://{self.token}@gist.github.com/{gist_id}.git"
            gist_dest = os.path.join(gists_dir, f"{gist_id}.git")
            if os.path.exists(gist_dest):
                rc, _, err = run_cmd(["git", "remote", "update"], cwd=gist_dest)
            else:
                rc, _, err = run_cmd(["git", "clone", "--mirror", gist_git_url, gist_dest])
            if rc == 0:
                self.summary["gists_backed_up"] += 1
            else:
                self.summary["warnings_and_errors"].append(f"Gist {gist_id} mirror failed: {err.strip()[:100]}")

    def fetch_discussions(self, owner: str, name: str) -> List[Dict[str, Any]]:
        discussions: List[Dict[str, Any]] = []
        cursor: Optional[str] = None

        while True:
            data = self.client.execute_graphql(DISCUSSIONS_QUERY, {"owner": owner, "name": name, "cursor": cursor})
            if not data or "repository" not in data or not data["repository"]:
                break
            disc_conn = data["repository"].get("discussions")
            if not disc_conn:
                break

            nodes = disc_conn.get("nodes", [])
            discussions.extend(nodes)

            page_info = disc_conn.get("pageInfo", {})
            if page_info.get("hasNextPage"):
                cursor = page_info.get("endCursor")
            else:
                break

        return discussions

    def backup_single_repository(self, repo: Dict[str, Any]) -> None:
        full_name = repo["full_name"]
        owner, repo_name = full_name.split("/", 1)
        is_private = repo.get("private", False)
        clone_url = repo.get("clone_url", "")
        auth_clone_url = clone_url.replace("https://", f"https://{self.token}@")

        log(f"--- [Starting] {full_name} ---")
        repo_dir = os.path.join(self.output_dir, "repositories", f"{owner}__{repo_name}")
        os.makedirs(repo_dir, exist_ok=True)
        meta_dir = os.path.join(repo_dir, "metadata")
        os.makedirs(meta_dir, exist_ok=True)

        # 1. True Git Mirror
        git_dir = os.path.join(repo_dir, "repo.git")
        if not self.skip_git:
            if os.path.exists(git_dir):
                rc, _, err = run_cmd(["git", "remote", "update"], cwd=git_dir)
            else:
                rc, _, err = run_cmd(["git", "clone", "--mirror", auth_clone_url, git_dir])

            if rc != 0:
                msg = f"Git mirror failed for {full_name}: {err.strip()[:150]}"
                log(msg, level="ERROR")
                self.summary["warnings_and_errors"].append(msg)
            else:
                log(f"Git mirror OK: {full_name}")

        # 2. Wiki Mirror (if present)
        if not self.skip_git and repo.get("has_wiki", False):
            wiki_url = clone_url.replace(".git", ".wiki.git").replace("https://", f"https://{self.token}@")
            wiki_dir = os.path.join(repo_dir, "wiki.git")
            if os.path.exists(wiki_dir):
                run_cmd(["git", "remote", "update"], cwd=wiki_dir)
            else:
                rc_wiki, _, _ = run_cmd(["git", "clone", "--mirror", wiki_url, wiki_dir])
                if rc_wiki == 0:
                    log(f"Wiki mirror OK: {full_name}")

        # 3. Metadata Backup (Issues, PRs, Comments, Reviews, Diffs, Releases, Discussions)
        if not self.skip_metadata:
            # Repo Details
            save_json(repo, os.path.join(meta_dir, "repo_details.json"))

            # Issues & Issue Comments
            issues = self.client.paginate_rest(f"/repos/{full_name}/issues", params={"state": "all", "filter": "all"})
            save_json(issues, os.path.join(meta_dir, "issues", "all_issues.json"))
            self.summary["issues_count"] += len(issues)

            issue_comments = self.client.paginate_rest(f"/repos/{full_name}/issues/comments")
            save_json(issue_comments, os.path.join(meta_dir, "issues", "issue_comments.json"))

            # Pull Requests, Reviews & Diffs
            pulls = self.client.paginate_rest(f"/repos/{full_name}/pulls", params={"state": "all"})
            save_json(pulls, os.path.join(meta_dir, "pull_requests", "all_pulls.json"))
            self.summary["pull_requests_count"] += len(pulls)

            pr_review_comments = self.client.paginate_rest(f"/repos/{full_name}/pulls/comments")
            save_json(pr_review_comments, os.path.join(meta_dir, "pull_requests", "pr_review_comments.json"))

            # Fetch reviews and diffs for each PR
            pr_reviews_map = {}
            diffs_dir = os.path.join(meta_dir, "pull_requests", "pr_diffs")
            os.makedirs(diffs_dir, exist_ok=True)

            for pr in pulls:
                pr_num = pr.get("number")
                if not pr_num:
                    continue
                reviews = self.client.get_json(f"/repos/{full_name}/pulls/{pr_num}/reviews")
                if reviews:
                    pr_reviews_map[str(pr_num)] = reviews

                diff_res = self.client.session.get(
                    f"{self.client.base_url}/repos/{full_name}/pulls/{pr_num}",
                    headers={"Accept": "application/vnd.github.v3.diff"},
                )
                if diff_res.status_code == 200:
                    with open(os.path.join(diffs_dir, f"pr_{pr_num}.diff"), "w", encoding="utf-8") as f:
                        f.write(diff_res.text)

            save_json(pr_reviews_map, os.path.join(meta_dir, "pull_requests", "pr_reviews.json"))

            # Releases
            releases = self.client.paginate_rest(f"/repos/{full_name}/releases")
            save_json(releases, os.path.join(meta_dir, "releases", "all_releases.json"))

            # Discussions (GraphQL)
            if repo.get("has_discussions", True):
                try:
                    discussions = self.fetch_discussions(owner, repo_name)
                    if discussions:
                        save_json(discussions, os.path.join(meta_dir, "discussions", "all_discussions.json"))
                        self.summary["discussions_count"] += len(discussions)
                except Exception as e:
                    log(f"Discussions query for {full_name} returned notice: {e}", level="WARN")

        # 4. GitHub Actions Workflow Runs & Step Logs
        if not self.skip_actions_logs:
            actions_dir = os.path.join(meta_dir, "actions")
            runs = self.client.paginate_rest(f"/repos/{full_name}/actions/runs", max_pages=5)
            save_json(runs, os.path.join(actions_dir, "workflow_runs.json"))

            logs_dir = os.path.join(actions_dir, "logs")
            for run in runs:
                run_id = run.get("id")
                if not run_id:
                    continue
                log_zip_path = os.path.join(logs_dir, f"run_{run_id}.zip")
                if not os.path.exists(log_zip_path):
                    download_url = f"{self.client.base_url}/repos/{full_name}/actions/runs/{run_id}/logs"
                    if self.client.download_file(download_url, log_zip_path):
                        self.summary["actions_logs_downloaded"] += 1

        # 5. Remote Mirrors (GitLab / Codeberg)
        if not self.skip_git and os.path.exists(git_dir):
            gl_ok, gl_msg = self.mirror_manager.mirror_to_gitlab(repo_name, is_private, git_dir)
            if not gl_ok:
                self.summary["warnings_and_errors"].append(f"[{full_name}] {gl_msg}")

            cb_ok, cb_msg = self.mirror_manager.mirror_to_codeberg(repo_name, is_private, git_dir)
            if not cb_ok:
                self.summary["warnings_and_errors"].append(f"[{full_name}] {cb_msg}")

        self.summary["repositories_backed_up"] += 1
        log(f"--- [Completed] {full_name} ---")

    def run(self) -> None:
        start_time = time.time()
        self.authenticate_and_load_user()

        os.makedirs(self.output_dir, exist_ok=True)

        # Step 1: Account-wide activity
        self.backup_account_activity()

        # Step 2: Repositories
        log("Fetching repository list for user...")
        repos = self.client.paginate_rest("/user/repos", params={"affiliation": "owner,collaborator,organization_member"})
        log(f"Discovered {len(repos)} repositories to process.")

        with concurrent.futures.ThreadPoolExecutor(max_workers=self.max_workers) as executor:
            future_to_repo = {executor.submit(self.backup_single_repository, r): r for r in repos}
            for future in concurrent.futures.as_completed(future_to_repo):
                repo = future_to_repo[future]
                try:
                    future.result()
                except Exception as e:
                    msg = f"Unhandled exception backing up {repo.get('full_name', 'unknown')}: {e}"
                    log(msg, level="ERROR")
                    self.summary["warnings_and_errors"].append(msg)

        self.summary["completed_at"] = datetime.datetime.now().isoformat()
        self.summary["duration_seconds"] = round(time.time() - start_time, 2)

        summary_path = os.path.join(self.output_dir, "backup_summary.json")
        save_json(self.summary, summary_path)

        log("==========================================")
        log("BACKUP SUMMARY:")
        log(f"  Repositories:   {self.summary['repositories_backed_up']}")
        log(f"  Issues:         {self.summary['issues_count']}")
        log(f"  Pull Requests:  {self.summary['pull_requests_count']}")
        log(f"  Discussions:    {self.summary['discussions_count']}")
        log(f"  Action Logs:    {self.summary['actions_logs_downloaded']}")
        log(f"  Gists:          {self.summary['gists_backed_up']}")
        log(f"  Duration:       {self.summary['duration_seconds']}s")
        if self.summary["warnings_and_errors"]:
            log(f"  Warnings/Errors ({len(self.summary['warnings_and_errors'])}):", level="WARN")
            for we in self.summary["warnings_and_errors"][:10]:
                log(f"    - {we}", level="WARN")
        log(f"Summary written to: {summary_path}")
        log("==========================================")


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Comprehensive GitHub Backup & Archival Engine")
    today = datetime.datetime.now().strftime("%Y-%m-%d")
    parser.add_argument(
        "--output-dir",
        default=os.environ.get("BACKUP_DIR", f"backups/backup_{today}"),
        help="Target backup directory (default: backups/backup_YYYY-MM-DD)",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=int(os.environ.get("MAX_JOBS", os.cpu_count() or 4)),
        help="Number of concurrent worker threads",
    )
    parser.add_argument("--skip-git", action="store_true", help="Skip Git mirror cloning")
    parser.add_argument("--skip-metadata", action="store_true", help="Skip Issues, PRs, Discussions metadata")
    parser.add_argument("--skip-actions-logs", action="store_true", help="Skip GitHub Actions logs download")
    parser.add_argument("--skip-external-activity", action="store_true", help="Skip account-wide activity search")
    return parser.parse_args()


def main() -> None:
    args = parse_arguments()

    github_token = sanitize_token(os.environ.get("GITHUB_TOKEN"))
    if not github_token:
        log("Error: GITHUB_TOKEN environment variable is not set.", level="ERROR")
        sys.exit(1)

    gitlab_token = sanitize_token(os.environ.get("GITLAB_TOKEN"))
    gitlab_user = sanitize_token(os.environ.get("GITLAB_USER"))
    codeberg_token = sanitize_token(os.environ.get("CODEBERG_TOKEN"))
    codeberg_user = sanitize_token(os.environ.get("CODEBERG_USER"))

    engine = GitHubBackupEngine(
        token=github_token,
        output_dir=args.output_dir,
        max_workers=args.workers,
        skip_git=args.skip_git,
        skip_metadata=args.skip_metadata,
        skip_actions_logs=args.skip_actions_logs,
        skip_external_activity=args.skip_external_activity,
        gitlab_token=gitlab_token,
        gitlab_user=gitlab_user,
        codeberg_token=codeberg_token,
        codeberg_user=codeberg_user,
    )
    engine.run()


if __name__ == "__main__":
    main()
