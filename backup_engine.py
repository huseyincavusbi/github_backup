#!/usr/bin/env python3
"""
GitHub Backup & Archival Engine (Complete & Fully Optimized)
Comprehensive backup suite for GitHub:
- Git Repositories & Wikis (True Git mirrors)
- Issues, Issue Comments, Labels, Milestones
- Pull Requests, Code Reviews, Review Comments, and Diffs
- GitHub Discussions (via GraphQL API)
- GitHub Actions Workflow Runs & Execution Logs (ZIP archives)
- Releases, Changelogs, and Release Asset downloads
- Branch Protection Rules & Repository Rulesets
- Webhooks & Deploy Keys
- Security Alerts (Dependabot & Secret Scanning)
- Projects v2 (Repository & User Kanban boards via GraphQL)
- Account-Wide Activity: Authored Issues/PRs across GitHub, Comments, Commits
- User Profile, Emails, SSH/GPG Public Keys, Followers, Following, Event Timelines, Starred Repos, Gists
- Live Multi-Platform Mirroring to GitLab and Codeberg
- Smart Delta Caching: Skips unchanged repositories based on pushed_at/updated_at
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
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry


# --- Logger Utility ---
def log(msg: str, level: str = "INFO") -> None:
    now = datetime.datetime.now().strftime("%H:%M:%S")
    prefix = f"[{now}] [{level}]"
    print(f"{prefix} {msg}", flush=True)


def sanitize_token(token: Optional[str]) -> str:
    if not token:
        return ""
    return re.sub(r"\s+", "", token.strip())


# --- GitHub API Client with Connection Pooling & Rate-Limiting ---
class GitHubClient:
    def __init__(self, token: str, max_retries: int = 3, pool_size: int = 36):
        self.token = token
        self.base_url = "https://api.github.com"
        self.graphql_url = "https://api.github.com/graphql"
        self.max_retries = max_retries
        self.session = requests.Session()

        # Connection pooling & automatic socket recycling
        adapter = HTTPAdapter(
            pool_connections=pool_size,
            pool_maxsize=pool_size,
            max_retries=Retry(total=2, backoff_factor=0.5, status_forcelist=[500, 502, 503, 504]),
        )
        self.session.mount("https://", adapter)
        self.session.mount("http://", adapter)

        self.session.headers.update({
            "Authorization": f"Bearer {self.token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "GitHub-Backup-Engine/3.0",
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
                    time.sleep(2 ** attempt)
                    continue
                return res
            except requests.RequestException:
                if attempt == self.max_retries:
                    raise
                time.sleep(2 ** attempt)
        raise RuntimeError(f"Failed request after {self.max_retries} attempts: {url}")

    def get_json(self, endpoint: str, params: Optional[Dict[str, Any]] = None) -> Optional[Any]:
        res = self.request("GET", endpoint, params=params)
        if res.status_code == 200:
            return res.json()
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
                break

            data = res.json()
            if isinstance(data, list):
                if not data:
                    break
                items.extend(data)
                if len(data) < query_params["per_page"]:
                    break
            elif isinstance(data, dict):
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
                        return None
                    return data.get("data")
                elif attempt < self.max_retries:
                    time.sleep(2 ** attempt)
                    continue
                return None
            except Exception:
                if attempt == self.max_retries:
                    return None
                time.sleep(2 ** attempt)
        return None

    def download_file(self, url: str, destination_path: str, max_size_bytes: int = 150 * 1024 * 1024) -> bool:
        try:
            os.makedirs(os.path.dirname(destination_path), exist_ok=True)
            res = self.session.get(url, stream=True, allow_redirects=True)
            self._handle_rate_limit(res)
            if res.status_code == 200:
                content_len = res.headers.get("content-length")
                if content_len and int(content_len) > max_size_bytes:
                    return False

                with open(destination_path, "wb") as f:
                    for chunk in res.iter_content(chunk_size=131072):
                        if chunk:
                            f.write(chunk)
                return True
            return False
        except Exception:
            return False


# --- Helper Functions ---
def save_json(data: Any, path: str) -> None:
    if data is None or (isinstance(data, list) and len(data) == 0):
        return
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def load_json(path: str) -> Optional[Any]:
    if not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def run_cmd(cmd: List[str], cwd: Optional[str] = None) -> Tuple[int, str, str]:
    proc = subprocess.run(cmd, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    return proc.returncode, proc.stdout, proc.stderr


# --- GraphQL Queries ---
DISCUSSIONS_QUERY = """
query($owner: String!, $name: String!, $cursor: String) {
  repository(owner: $owner, name: $name) {
    discussions(first: 50, after: $cursor) {
      pageInfo { hasNextPage endCursor }
      totalCount
      nodes {
        id number title body createdAt url
        author { login }
        category { id name description emoji }
        comments(first: 50) {
          totalCount
          nodes {
            id body createdAt
            author { login }
            replies(first: 20) {
              totalCount
              nodes { id body createdAt author { login } }
            }
          }
        }
      }
    }
  }
}
"""

PROJECTS_V2_QUERY = """
query($owner: String!, $name: String!) {
  repository(owner: $owner, name: $name) {
    projectsV2(first: 20) {
      nodes {
        id title number closed createdAt updatedAt url
        items(first: 100) {
          nodes {
            id type createdAt updatedAt
            content {
              ... on Issue { number title body }
              ... on PullRequest { number title body }
              ... on DraftIssue { title body }
            }
          }
        }
      }
    }
  }
}
"""

USER_PROJECTS_V2_QUERY = """
query($login: String!) {
  user(login: $login) {
    projectsV2(first: 20) {
      nodes {
        id title number closed createdAt updatedAt url
        items(first: 100) {
          nodes {
            id type createdAt updatedAt
            content {
              ... on Issue { number title body }
              ... on PullRequest { number title body }
              ... on DraftIssue { title body }
            }
          }
        }
      }
    }
  }
}
"""


# --- Remote Mirrors (GitLab & Codeberg) ---
class RemoteMirrorManager:
    def __init__(self, gitlab_token: str, gitlab_user: str, codeberg_token: str, codeberg_user: str):
        self.gitlab_token = gitlab_token
        self.gitlab_user = gitlab_user
        self.codeberg_token = codeberg_token
        self.codeberg_user = codeberg_user

    def mirror_to_gitlab(self, repo_name: str, is_private: bool, repo_git_dir: str) -> Tuple[bool, str]:
        if not self.gitlab_token or not self.gitlab_user:
            return True, "Skipped"

        visibility = "private" if is_private else "public"
        headers = {"PRIVATE-TOKEN": self.gitlab_token, "Content-Type": "application/json"}
        payload = {"name": repo_name, "path": repo_name, "visibility": visibility, "initialize_with_readme": False}

        try:
            res = requests.post("https://gitlab.com/api/v4/projects", headers=headers, json=payload, timeout=12)
            if res.status_code not in (201, 400):
                return False, f"GitLab repo creation failed (HTTP {res.status_code})"

            gitlab_url = f"https://oauth2:{self.gitlab_token}@gitlab.com/{self.gitlab_user}/{repo_name}.git"
            rc, _, stderr = run_cmd(["git", "push", "--mirror", gitlab_url], cwd=repo_git_dir)
            if rc != 0:
                return False, f"GitLab push failed: {stderr.strip()[:120]}"
            return True, "Mirrored to GitLab"
        except Exception as e:
            return False, f"GitLab error: {e}"

    def mirror_to_codeberg(self, repo_name: str, is_private: bool, repo_git_dir: str) -> Tuple[bool, str]:
        if not self.codeberg_token or not self.codeberg_user:
            return True, "Skipped"

        headers = {"Authorization": f"token {self.codeberg_token}", "Content-Type": "application/json"}
        try:
            check_res = requests.get(f"https://codeberg.org/api/v1/repos/{self.codeberg_user}/{repo_name}", headers=headers, timeout=12)
            if check_res.status_code != 200:
                create_res = requests.post(
                    "https://codeberg.org/api/v1/user/repos",
                    headers=headers,
                    json={"name": repo_name, "private": is_private, "auto_init": False},
                    timeout=12,
                )
                if create_res.status_code not in (201, 409):
                    return False, f"Codeberg creation failed (HTTP {create_res.status_code})"

            codeberg_url = f"https://{self.codeberg_user}:{self.codeberg_token}@codeberg.org/{self.codeberg_user}/{repo_name}.git"
            rc, _, stderr = run_cmd(["git", "push", "--mirror", codeberg_url], cwd=repo_git_dir)
            if rc != 0:
                return False, f"Codeberg push failed: {stderr.strip()[:120]}"
            return True, "Mirrored to Codeberg"
        except Exception as e:
            return False, f"Codeberg error: {e}"


# --- Main Backup Engine ---
class GitHubBackupEngine:
    def __init__(
        self,
        token: str,
        output_dir: str,
        max_workers: int = 12,
        skip_git: bool = False,
        skip_metadata: bool = False,
        skip_actions_logs: bool = False,
        skip_external_activity: bool = False,
        download_release_assets: bool = False,
        force_refresh: bool = False,
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
        self.download_release_assets = download_release_assets
        self.force_refresh = force_refresh

        self.client = GitHubClient(token, pool_size=max_workers * 3)
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
            "repositories_delta_skipped": 0,
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
            return

        username = self.user_info["login"]
        log(f"Backing up complete account activity & social assets for @{username}...")
        account_dir = os.path.join(self.output_dir, "account_activity")
        os.makedirs(account_dir, exist_ok=True)

        # 1. Profile, Emails & Organizations
        save_json(self.user_info, os.path.join(account_dir, "profile.json"))
        emails = self.client.get_json("/user/emails")
        save_json(emails, os.path.join(account_dir, "emails.json"))
        orgs = self.client.paginate_rest("/user/orgs")
        save_json(orgs, os.path.join(account_dir, "organizations.json"))

        # 2. Public Keys (SSH & GPG)
        ssh_keys = self.client.paginate_rest("/user/keys")
        save_json(ssh_keys, os.path.join(account_dir, "ssh_keys.json"))
        gpg_keys = self.client.paginate_rest("/user/gpg_keys")
        save_json(gpg_keys, os.path.join(account_dir, "gpg_keys.json"))

        # 3. Social & Network Lists
        followers = self.client.paginate_rest("/user/followers")
        save_json(followers, os.path.join(account_dir, "followers.json"))
        following = self.client.paginate_rest("/user/following")
        save_json(following, os.path.join(account_dir, "following.json"))

        # 4. User Event Feeds
        events = self.client.paginate_rest(f"/users/{username}/events")
        save_json(events, os.path.join(account_dir, "events_feed.json"))
        public_events = self.client.paginate_rest(f"/users/{username}/events/public")
        save_json(public_events, os.path.join(account_dir, "public_events.json"))

        # 5. Starred Repositories
        starred = self.client.paginate_rest("/user/starred")
        save_json(starred, os.path.join(account_dir, "starred_repos.json"))

        # 6. Authored & Commented Issues / PRs / Commits across GitHub
        authored_issues = self.client.paginate_rest(f"/search/issues?q=author:{username}+type:issue", max_pages=10)
        authored_prs = self.client.paginate_rest(f"/search/issues?q=author:{username}+type:pr", max_pages=10)
        save_json(
            {"authored_issues": authored_issues, "authored_pull_requests": authored_prs},
            os.path.join(account_dir, "authored_issues_and_prs.json"),
        )
        commented = self.client.paginate_rest(f"/search/issues?q=commenter:{username}", max_pages=10)
        save_json(commented, os.path.join(account_dir, "commented_issues_and_prs.json"))

        # 7. User-Level Projects v2
        user_projects = self.client.execute_graphql(USER_PROJECTS_V2_QUERY, {"login": username})
        if user_projects:
            save_json(user_projects, os.path.join(account_dir, "user_projects_v2.json"))

        # 8. Gists & Gist Git Mirrors
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
                rc, _, _ = run_cmd(["git", "remote", "update"], cwd=gist_dest)
            else:
                rc, _, _ = run_cmd(["git", "clone", "--mirror", gist_git_url, gist_dest])
            if rc == 0:
                self.summary["gists_backed_up"] += 1

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
        is_fork = repo.get("fork", False)
        clone_url = repo.get("clone_url", "")
        auth_clone_url = clone_url.replace("https://", f"https://{self.token}@")

        repo_dir = os.path.join(self.output_dir, "repositories", f"{owner}__{repo_name}")
        os.makedirs(repo_dir, exist_ok=True)
        meta_dir = os.path.join(repo_dir, "metadata")
        os.makedirs(meta_dir, exist_ok=True)

        git_dir = os.path.join(repo_dir, "repo.git")

        # --- Delta Check: Skip static metadata if repo has not changed ---
        prev_details = load_json(os.path.join(meta_dir, "repo_details.json"))
        curr_pushed_at = repo.get("pushed_at")
        curr_updated_at = repo.get("updated_at")

        is_unchanged = (
            not self.force_refresh
            and prev_details
            and os.path.exists(git_dir)
            and prev_details.get("pushed_at") == curr_pushed_at
            and prev_details.get("updated_at") == curr_updated_at
        )

        # 1. True Git Mirror
        if not self.skip_git:
            if os.path.exists(git_dir):
                rc, _, err = run_cmd(["git", "remote", "update"], cwd=git_dir)
            else:
                rc, _, err = run_cmd(["git", "clone", "--mirror", auth_clone_url, git_dir])

            if rc != 0:
                self.summary["warnings_and_errors"].append(f"Git mirror failed for {full_name}: {err.strip()[:100]}")

        # 2. Wiki Mirror (only if enabled)
        if not self.skip_git and repo.get("has_wiki", False):
            wiki_url = clone_url.replace(".git", ".wiki.git").replace("https://", f"https://{self.token}@")
            wiki_dir = os.path.join(repo_dir, "wiki.git")
            if os.path.exists(wiki_dir):
                run_cmd(["git", "remote", "update"], cwd=wiki_dir)
            else:
                run_cmd(["git", "clone", "--mirror", wiki_url, wiki_dir])

        # If repo is completely unchanged since last backup, skip re-fetching static API metadata
        if is_unchanged and not self.skip_metadata:
            self.summary["repositories_delta_skipped"] += 1
            self.summary["repositories_backed_up"] += 1
            return

        # 3. Comprehensive Metadata Backup
        if not self.skip_metadata:
            save_json(repo, os.path.join(meta_dir, "repo_details.json"))

            # Labels & Milestones
            labels = self.client.paginate_rest(f"/repos/{full_name}/labels")
            save_json(labels, os.path.join(meta_dir, "labels.json"))

            milestones = self.client.paginate_rest(f"/repos/{full_name}/milestones", params={"state": "all"})
            save_json(milestones, os.path.join(meta_dir, "milestones.json"))

            # Branch Protection & Rulesets
            branches = self.client.paginate_rest(f"/repos/{full_name}/branches")
            save_json(branches, os.path.join(meta_dir, "branches.json"))
            rulesets = self.client.get_json(f"/repos/{full_name}/rulesets")
            save_json(rulesets, os.path.join(meta_dir, "rulesets.json"))

            # Webhooks & Deploy Keys
            hooks = self.client.get_json(f"/repos/{full_name}/hooks")
            save_json(hooks, os.path.join(meta_dir, "webhooks.json"))
            deploy_keys = self.client.get_json(f"/repos/{full_name}/keys")
            save_json(deploy_keys, os.path.join(meta_dir, "deploy_keys.json"))

            # Security Alerts
            dependabot_alerts = self.client.get_json(f"/repos/{full_name}/dependabot/alerts")
            save_json(dependabot_alerts, os.path.join(meta_dir, "dependabot_alerts.json"))
            secret_alerts = self.client.get_json(f"/repos/{full_name}/secret-scanning/alerts")
            save_json(secret_alerts, os.path.join(meta_dir, "secret_scanning_alerts.json"))

            # Issues & Issue Comments (only if issues enabled)
            if repo.get("has_issues", True):
                issues = self.client.paginate_rest(f"/repos/{full_name}/issues", params={"state": "all", "filter": "all"})
                save_json(issues, os.path.join(meta_dir, "issues", "all_issues.json"))
                self.summary["issues_count"] += len(issues)

                if issues:
                    issue_comments = self.client.paginate_rest(f"/repos/{full_name}/issues/comments")
                    save_json(issue_comments, os.path.join(meta_dir, "issues", "issue_comments.json"))

            # Pull Requests, Reviews & Diffs
            pulls = self.client.paginate_rest(f"/repos/{full_name}/pulls", params={"state": "all"})
            save_json(pulls, os.path.join(meta_dir, "pull_requests", "all_pulls.json"))
            self.summary["pull_requests_count"] += len(pulls)

            if pulls:
                pr_review_comments = self.client.paginate_rest(f"/repos/{full_name}/pulls/comments")
                save_json(pr_review_comments, os.path.join(meta_dir, "pull_requests", "pr_review_comments.json"))

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

            # Releases & Optional Assets
            releases = self.client.paginate_rest(f"/repos/{full_name}/releases")
            save_json(releases, os.path.join(meta_dir, "releases", "all_releases.json"))

            if releases and self.download_release_assets:
                assets_dir = os.path.join(meta_dir, "releases", "assets")
                for rel in releases:
                    for asset in rel.get("assets", []):
                        asset_url = asset.get("browser_download_url")
                        asset_name = asset.get("name")
                        if asset_url and asset_name:
                            self.client.download_file(asset_url, os.path.join(assets_dir, asset_name))

            # Discussions (GraphQL)
            if repo.get("has_discussions", False) or not is_fork:
                try:
                    discussions = self.fetch_discussions(owner, repo_name)
                    if discussions:
                        save_json(discussions, os.path.join(meta_dir, "discussions", "all_discussions.json"))
                        self.summary["discussions_count"] += len(discussions)
                except Exception:
                    pass

            # Repository Projects v2 (Kanban)
            try:
                repo_projects = self.client.execute_graphql(PROJECTS_V2_QUERY, {"owner": owner, "name": repo_name})
                if repo_projects:
                    save_json(repo_projects, os.path.join(meta_dir, "projects_v2.json"))
            except Exception:
                pass

        # 4. GitHub Actions Workflow Runs & Step Logs
        if not self.skip_actions_logs and not is_fork:
            actions_dir = os.path.join(meta_dir, "actions")
            runs = self.client.paginate_rest(f"/repos/{full_name}/actions/runs", max_pages=5)
            save_json(runs, os.path.join(actions_dir, "workflow_runs.json"))

            if runs:
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
            if not gl_ok and "Skipped" not in gl_msg:
                self.summary["warnings_and_errors"].append(f"[{full_name}] {gl_msg}")

            cb_ok, cb_msg = self.mirror_manager.mirror_to_codeberg(repo_name, is_private, git_dir)
            if not cb_ok and "Skipped" not in cb_msg:
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
        log(f"Discovered {len(repos)} repositories to process with {self.max_workers} concurrent workers.")

        with concurrent.futures.ThreadPoolExecutor(max_workers=self.max_workers) as executor:
            future_to_repo = {executor.submit(self.backup_single_repository, r): r for r in repos}
            for future in concurrent.futures.as_completed(future_to_repo):
                repo = future_to_repo[future]
                try:
                    future.result()
                except Exception as e:
                    msg = f"Error backing up {repo.get('full_name', 'unknown')}: {e}"
                    self.summary["warnings_and_errors"].append(msg)

        self.summary["completed_at"] = datetime.datetime.now().isoformat()
        self.summary["duration_seconds"] = round(time.time() - start_time, 2)

        summary_path = os.path.join(self.output_dir, "backup_summary.json")
        save_json(self.summary, summary_path)

        log("==========================================")
        log("BACKUP SUMMARY:")
        log(f"  Repositories Processed: {self.summary['repositories_backed_up']}")
        log(f"  Delta Unchanged Skips:  {self.summary['repositories_delta_skipped']}")
        log(f"  Issues:                 {self.summary['issues_count']}")
        log(f"  Pull Requests:          {self.summary['pull_requests_count']}")
        log(f"  Discussions:            {self.summary['discussions_count']}")
        log(f"  Action Logs:            {self.summary['actions_logs_downloaded']}")
        log(f"  Gists:                  {self.summary['gists_backed_up']}")
        log(f"  Duration:               {self.summary['duration_seconds']}s")
        log(f"Summary written to: {summary_path}")
        log("==========================================")


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Complete GitHub Backup & Archival Suite")
    today = datetime.datetime.now().strftime("%Y-%m-%d")
    parser.add_argument(
        "--output-dir",
        default=os.environ.get("BACKUP_DIR", f"backups/backup_{today}"),
        help="Target backup directory (default: backups/backup_YYYY-MM-DD)",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=int(os.environ.get("MAX_JOBS", 12)),
        help="Number of concurrent worker threads (default: 12)",
    )
    parser.add_argument("--skip-git", action="store_true", help="Skip Git mirror cloning")
    parser.add_argument("--skip-metadata", action="store_true", help="Skip Issues, PRs, Discussions metadata")
    parser.add_argument("--skip-actions-logs", action="store_true", help="Skip GitHub Actions logs download")
    parser.add_argument("--skip-external-activity", action="store_true", help="Skip account-wide activity search")
    parser.add_argument("--download-release-assets", action="store_true", help="Download binary release assets")
    parser.add_argument("--force-refresh", action="store_true", help="Force re-fetch all metadata ignoring delta cache")
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
        download_release_assets=args.download_release_assets,
        force_refresh=args.force_refresh,
        gitlab_token=gitlab_token,
        gitlab_user=gitlab_user,
        codeberg_token=codeberg_token,
        codeberg_user=codeberg_user,
    )
    engine.run()


if __name__ == "__main__":
    main()
