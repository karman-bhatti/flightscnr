"""GitHub release/commit check and portal-triggered updates."""

from __future__ import annotations

import json
import logging
import os
import subprocess
import time
from datetime import datetime, timezone

import requests

logger = logging.getLogger("flightscnr.updater")

GITHUB_REPO = os.environ.get("FLIGHTSCNR_GITHUB_REPO", "yashmulgaonkar/FlightScnr_Pi")
GITHUB_BRANCH = os.environ.get("FLIGHTSCNR_GITHUB_BRANCH", "main")
GITHUB_API = "https://api.github.com"
DATA_DIR = os.environ.get("FLIGHTSCNR_DATA_DIR", "/var/lib/flightscnr")
STATUS_PATH = os.path.join(DATA_DIR, "update-status.json")
LOCK_PATH = os.path.join(DATA_DIR, "update.lock")
UPDATE_LOG_PATH = os.path.join(DATA_DIR, "update.log")
GITHUB_TIMEOUT_S = 12
_REMOTE_CACHE_PATH = os.path.join(DATA_DIR, "github-remote-cache.json")
_REMOTE_CACHE_TTL_S = 30 * 60
_REMOTE_CACHE_STALE_S = 24 * 3600


def repo_root() -> str:
    return os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))


def update_script_path() -> str:
    return os.path.join(repo_root(), "flightscnr", "setup", "portal-update.sh")


def _run_git(args: list[str]) -> str | None:
    root = repo_root()
    if not os.path.isdir(os.path.join(root, ".git")):
        return None
    try:
        result = subprocess.run(
            [
                "git",
                "-c",
                f"safe.directory={root}",
                "-C",
                root,
                *args,
            ],
            capture_output=True,
            text=True,
            timeout=10,
            check=True,
        )
        return (result.stdout or "").strip()
    except (OSError, subprocess.SubprocessError) as exc:
        logger.warning("git %s failed: %s", " ".join(args), exc)
        return None


def local_version_info() -> dict:
    from version import APP_VERSION, read_version

    root = repo_root()
    commit = _run_git(["rev-parse", "HEAD"]) or ""
    short = _run_git(["rev-parse", "--short", "HEAD"]) or (commit[:7] if commit else "")
    describe = _run_git(["describe", "--tags", "--always", "--dirty"]) or short
    branch = _run_git(["rev-parse", "--abbrev-ref", "HEAD"]) or ""
    release = read_version() or APP_VERSION
    return {
        "release": release,
        "commit": commit,
        "commit_short": short,
        "describe": describe,
        "branch": branch,
        "repo_root": root,
        "is_git_repo": bool(commit),
    }


def _github_get(path: str) -> dict | None:
    url = f"{GITHUB_API}{path}"
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "FlightScnr-Pi-Updater",
    }
    token = os.environ.get("GITHUB_TOKEN", "").strip()
    if token:
        headers["Authorization"] = f"Bearer {token}"
    try:
        response = requests.get(url, headers=headers, timeout=GITHUB_TIMEOUT_S)
        if response.status_code == 404:
            return None
        response.raise_for_status()
        return response.json()
    except requests.RequestException as exc:
        logger.warning("GitHub API request failed (%s): %s", path, exc)
        return None


def _remote_commit_via_git() -> dict:
    ref = f"refs/heads/{GITHUB_BRANCH}"
    output = _run_git(["ls-remote", "origin", ref])
    if not output:
        output = _run_git(["ls-remote", "origin", "HEAD"])
    if not output:
        return {}
    commit = output.split()[0].strip()
    if not commit:
        return {}
    return {
        "commit": commit,
        "commit_short": commit[:7],
        "branch": GITHUB_BRANCH,
        "source": "git",
    }


def _remote_latest_tag_via_git() -> dict:
    """Latest semver tag from origin (works when GitHub API is rate-limited)."""
    from version import ReleaseVersion, normalize_version

    output = _run_git(["ls-remote", "--tags", "origin"])
    if not output:
        return {}

    peeled: dict[str, str] = {}
    tag_names: list[str] = []
    for line in output.splitlines():
        parts = line.split()
        if len(parts) < 2:
            continue
        commit, ref = parts[0], parts[1]
        if not ref.startswith("refs/tags/"):
            continue
        tag = ref[len("refs/tags/") :]
        if tag.endswith("^{}"):
            peeled[tag[:-3]] = commit
        elif tag not in peeled:
            tag_names.append(tag)

    best: ReleaseVersion | None = None
    best_tag = ""
    for tag in tag_names:
        parsed = ReleaseVersion.parse(tag)
        if parsed is None:
            continue
        if best is None or parsed > best:
            best = parsed
            best_tag = normalize_version(tag)

    if not best_tag:
        return {}

    commit = peeled.get(best_tag, "")
    return {
        "release_tag": best_tag,
        "commit": commit,
        "commit_short": commit[:7] if commit else "",
        "branch": GITHUB_BRANCH,
        "source": "git_tags",
    }


def _remote_via_raw_github() -> dict:
    """Read VERSION from raw.githubusercontent.com (no REST API rate limit)."""
    from version import normalize_version

    owner, _, name = GITHUB_REPO.partition("/")
    url = f"https://raw.githubusercontent.com/{owner}/{name}/{GITHUB_BRANCH}/VERSION"
    try:
        response = requests.get(
            url,
            timeout=GITHUB_TIMEOUT_S,
            headers={"User-Agent": "FlightScnr-Pi-Updater"},
        )
        response.raise_for_status()
        release = normalize_version(response.text)
        if not release:
            return {}
        return {"release_tag": release, "source": "raw_github"}
    except requests.RequestException as exc:
        logger.warning("Raw GitHub VERSION fetch failed: %s", exc)
        return {}


def _read_remote_cache() -> tuple[dict, float]:
    try:
        with open(_REMOTE_CACHE_PATH, encoding="utf-8") as fh:
            data = json.load(fh)
        if not isinstance(data, dict):
            return {}, 0.0
        cached = data.get("remote")
        ts = float(data.get("ts") or 0.0)
        return (cached if isinstance(cached, dict) else {}), ts
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        return {}, 0.0


def _write_remote_cache(remote: dict) -> None:
    try:
        os.makedirs(DATA_DIR, exist_ok=True)
        tmp = _REMOTE_CACHE_PATH + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump({"ts": time.time(), "remote": remote}, fh, indent=2)
        os.replace(tmp, _REMOTE_CACHE_PATH)
    except OSError as exc:
        logger.warning("Could not write remote update cache: %s", exc)


def _merge_remote(*parts: dict) -> dict:
    """Merge remote version hints, picking the highest release tag across sources."""
    from version import ReleaseVersion, normalize_version

    merged = {
        "commit": "",
        "commit_short": "",
        "branch": GITHUB_BRANCH,
        "release_tag": "",
        "release_name": "",
        "release_published": "",
        "commit_date": "",
        "repo": GITHUB_REPO,
        "source": "",
    }

    best: ReleaseVersion | None = None
    best_tag = ""
    best_part: dict = {}

    for part in parts:
        if not part:
            continue
        tag = normalize_version(part.get("release_tag") or "")
        if not tag:
            continue
        parsed = ReleaseVersion.parse(tag)
        if parsed is None:
            continue
        if best is None or parsed > best:
            best = parsed
            best_tag = tag
            best_part = part

    if best_tag:
        merged["release_tag"] = best_tag
        merged["release_name"] = str(best_part.get("release_name") or best_tag)
        merged["release_published"] = str(best_part.get("release_published") or "")
        if best_part.get("source"):
            merged["source"] = str(best_part["source"])

    commit = ""
    commit_date = ""
    if best_part.get("commit"):
        commit = str(best_part["commit"])
        commit_date = str(best_part.get("commit_date") or "")
    else:
        for part in parts:
            if part and part.get("source") == "git" and part.get("commit"):
                commit = str(part["commit"])
                break
        if not commit:
            for part in parts:
                if part and part.get("source") == "github_api" and part.get("commit"):
                    commit = str(part["commit"])
                    commit_date = str(part.get("commit_date") or "")
                    break

    if commit:
        merged["commit"] = commit
        merged["commit_short"] = commit[:7]
        if commit_date:
            merged["commit_date"] = commit_date

    if not merged["source"]:
        for part in parts:
            if part and part.get("source"):
                merged["source"] = str(part["source"])
                break

    return merged


def remote_version_info(*, force: bool = False) -> dict:
    cached, cached_ts = _read_remote_cache()
    age = time.time() - cached_ts
    if not force and cached and age < _REMOTE_CACHE_TTL_S:
        return dict(cached)

    owner, _, name = GITHUB_REPO.partition("/")
    release = _github_get(f"/repos/{owner}/{name}/releases/latest")
    branch_commit = _github_get(f"/repos/{owner}/{name}/commits/{GITHUB_BRANCH}")

    api_remote: dict = {}
    if branch_commit:
        remote_commit = str(branch_commit.get("sha") or "")
        commit_meta = branch_commit.get("commit") or {}
        api_remote = {
            "commit": remote_commit,
            "commit_short": remote_commit[:7],
            "commit_date": str(commit_meta.get("committer", {}).get("date") or ""),
            "source": "github_api",
        }
    if release:
        release_tag = str(release.get("tag_name") or "")
        api_remote.update(
            {
                "release_tag": release_tag,
                "release_name": str(release.get("name") or release_tag),
                "release_published": str(release.get("published_at") or ""),
            }
        )
        if not api_remote.get("source"):
            api_remote["source"] = "github_api"

    git_branch = _remote_commit_via_git()
    git_tags = _remote_latest_tag_via_git()
    raw_version = _remote_via_raw_github()

    remote = _merge_remote(api_remote, git_tags, git_branch, raw_version)
    remote["branch"] = GITHUB_BRANCH
    remote["repo"] = GITHUB_REPO

    if remote.get("release_tag") or remote.get("commit"):
        _write_remote_cache(remote)
    elif cached and age < _REMOTE_CACHE_STALE_S:
        logger.info("Using stale GitHub remote cache (API unreachable)")
        return dict(cached)

    return remote


def _read_status() -> dict:
    try:
        with open(STATUS_PATH, encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _write_status(state: str, message: str = "", **extra) -> dict:
    payload = {
        "state": state,
        "message": message,
        "updated_at": datetime.now(timezone.utc).isoformat(),
        **extra,
    }
    try:
        os.makedirs(DATA_DIR, exist_ok=True)
        tmp = STATUS_PATH + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, indent=2)
        os.replace(tmp, STATUS_PATH)
    except OSError as exc:
        logger.warning("Could not write update status: %s", exc)
    return payload


def update_running() -> bool:
    if os.path.isfile(LOCK_PATH):
        try:
            with open(LOCK_PATH, encoding="utf-8") as fh:
                pid = int((fh.read() or "").strip() or "0")
        except (OSError, ValueError):
            pid = 0
        if pid > 0:
            try:
                os.kill(pid, 0)
                return True
            except OSError:
                pass
        try:
            os.remove(LOCK_PATH)
        except OSError:
            pass
    status = _read_status()
    return status.get("state") == "running"


def check_for_update(*, force: bool = False) -> dict:
    from version import compare_versions, normalize_version

    local = local_version_info()
    remote = remote_version_info(force=force)
    status = _read_status()

    local_release = normalize_version(local.get("release") or "")
    remote_release = normalize_version(remote.get("release_tag") or "")

    update_available = False
    if local_release and remote_release:
        # Release tags are authoritative — matching versions are up to date.
        update_available = compare_versions(local_release, remote_release) < 0
    elif local.get("commit") and remote.get("commit"):
        update_available = local["commit"] != remote["commit"]

    message = "Up to date."
    if not local.get("is_git_repo"):
        if remote_release:
            message = "Up to date (install is not a git checkout — use install-pi.sh to update)."
        else:
            message = "This install is not a git checkout — use install-pi.sh manually."
    elif not remote.get("commit") and not remote_release:
        message = "Could not reach GitHub to check for updates."
    elif update_available:
        if remote_release and local_release:
            message = f"Update available: {local_release} → {remote_release}"
        else:
            message = "A newer version is available."

    running = update_running()
    if running:
        message = "Update in progress…"

    return {
        "ok": True,
        "update_available": update_available and not running,
        "update_running": running,
        "message": message,
        "local": local,
        "remote": remote,
        "status": status,
    }


def mark_update_running() -> None:
    _write_status("running", "Update started.")


def mark_update_finished(success: bool, message: str) -> None:
    _write_status("success" if success else "failed", message)


def start_update() -> dict:
    if update_running():
        return {"ok": False, "message": "An update is already running."}

    local = local_version_info()
    if not local.get("is_git_repo"):
        return {"ok": False, "message": "This install is not a git repository."}

    script = update_script_path()
    if not os.path.isfile(script):
        return {"ok": False, "message": f"Update script not found: {script}"}

    os.makedirs(DATA_DIR, exist_ok=True)
    mark_update_running()

    log_fh = open(UPDATE_LOG_PATH, "a", encoding="utf-8")
    log_fh.write(f"\n--- update started {datetime.now(timezone.utc).isoformat()} ---\n")
    log_fh.flush()

    if os.geteuid() == 0:
        cmd = ["/bin/bash", script]
    else:
        cmd = ["sudo", "-n", "/bin/bash", script]

    try:
        subprocess.Popen(
            cmd,
            stdout=log_fh,
            stderr=subprocess.STDOUT,
            start_new_session=True,
            close_fds=True,
        )
    except OSError as exc:
        log_fh.close()
        mark_update_finished(False, f"Could not start update: {exc}")
        return {"ok": False, "message": f"Could not start update: {exc}"}

    return {
        "ok": True,
        "message": "Update started. The display will restart shortly.",
    }


def update_status() -> dict:
    status = _read_status()
    running = update_running()
    tail = ""
    try:
        if os.path.isfile(UPDATE_LOG_PATH):
            with open(UPDATE_LOG_PATH, encoding="utf-8", errors="replace") as fh:
                tail = "".join(fh.readlines()[-40:])
    except OSError:
        pass
    return {
        "ok": True,
        "update_running": running,
        "status": status,
        "log_tail": tail,
    }
