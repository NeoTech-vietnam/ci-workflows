#!/usr/bin/env python3
"""Dispatch reference builds from module YAML and report their actual results."""
import base64
import json
import os
from pathlib import Path
import re
import sys
import time
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

import yaml

WORKFLOW = "module-integration.yml"
BRANCH = re.compile(r"(?:feat|feature|fix|dev)/[A-Za-z0-9][A-Za-z0-9._/-]*")
REPOSITORY = re.compile(r"NeoTech-vietnam/[A-Za-z0-9_.-]+")


def require(condition, message):
    if not condition:
        raise ValueError(message)


def branch_allowed(branch):
    return (isinstance(branch, str) and BRANCH.fullmatch(branch) is not None
            and not any(part.startswith(".") or part.endswith((".", ".lock"))
                        for part in branch.split("/"))
            and ".." not in branch and "//" not in branch and not branch.endswith("/"))


class GitHubError(ValueError):
    def __init__(self, status, method):
        super().__init__(f"GitHub API {method} returned HTTP {status}; check target, branch and token permissions")
        self.status = status


class GitHub:
    def __init__(self, token):
        require(bool(token), "Add repository Actions secret INTEGRATION_DISPATCH_TOKEN; see module CI setup")
        self.token = token

    def __call__(self, path, method="GET", payload=None):
        request = Request("https://api.github.com/" + path, method=method,
                          data=json.dumps(payload).encode() if payload is not None else None,
                          headers={"Authorization": f"Bearer {self.token}",
                                   "Accept": "application/vnd.github+json", "Content-Type": "application/json",
                                   "X-GitHub-Api-Version": "2026-03-10", "User-Agent": "neocam-module-ci"})
        try:
            with urlopen(request, timeout=30) as response:
                content = response.read()
                return json.loads(content) if content else None
        except HTTPError as error:
            raise GitHubError(error.code, method) from None
        except URLError:
            raise ValueError("GitHub API connection failed; check runner network/proxy") from None


def load_targets(path):
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    require(isinstance(data, dict) and set(data) == {"ref_projects"}, "Expected a ref_projects mapping")
    targets = data["ref_projects"]
    require(isinstance(targets, list) and 1 <= len(targets) <= 8, "Configure between one and eight reference projects")
    seen = set()
    for target in targets:
        require(isinstance(target, dict) and set(target) == {"repository", "branch", "package"},
                "Each reference project requires repository, branch and package")
        require(all(isinstance(value, str) for value in target.values()), "Target fields must be strings")
        require(REPOSITORY.fullmatch(target["repository"]), "Only NeoTech-vietnam repositories are supported")
        require(branch_allowed(target["branch"]), "Target must be a feat/, feature/, fix/ or dev/ branch; main is forbidden")
        require(re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*", target["package"]), "Invalid package key")
        identity = tuple(target[key] for key in ("repository", "branch", "package"))
        require(identity not in seen, "Duplicate reference project")
        seen.add(identity)
    return targets


def preflight(targets, source_repo, source_sha, api):
    require(REPOSITORY.fullmatch(source_repo), "Invalid source module repository")
    require(re.fullmatch(r"[0-9a-f]{40}", source_sha), "Use the exact pushed module commit SHA")
    for target in targets:
        base = f"repos/{target['repository']}"
        repository = api(base)
        require(target["branch"] != repository["default_branch"], "The project's default branch is forbidden")
        api(f"{base}/branches/{quote(target['branch'], safe='')}")
        workflow = api(f"{base}/actions/workflows/{WORKFLOW}")
        require(workflow["state"] == "active", "Target module-integration workflow is not active")
        # Check every target before dispatching any builds.
        for filename in (f".github/workflows/{WORKFLOW}", "packages.yml"):
            content = api(f"{base}/contents/{filename}?ref={quote(target['branch'], safe='')}")
            decoded = yaml.safe_load(base64.b64decode(content["content"]).decode("utf-8"))
            if filename == "packages.yml":
                package = decoded.get("packages", {}).get(target["package"], {})
                require(package.get("repo", "").removesuffix(".git").casefold()
                        == f"https://github.com/{source_repo}".casefold(),
                        "Package repository does not match the pushing module")


def dispatch_builds(targets, source_repo, source_sha, api, builds=None):
    preflight(targets, source_repo, source_sha, api)
    if builds is None:
        builds = []
    for target in targets:
        response = api(f"repos/{target['repository']}/actions/workflows/{WORKFLOW}/dispatches", "POST", {
            "ref": target["branch"], "inputs": {"package": target["package"], "package_ref": source_sha,
                                                  "source_repository": source_repo}})
        require(isinstance(response, dict) and isinstance(response.get("workflow_run_id"), int),
                "Dispatch returned no run ID; inspect project Actions before retrying")
        builds.append({**target, "run_id": response["workflow_run_id"], "url": response["html_url"],
                       "source_repository": source_repo, "source_sha": source_sha, "conclusion": None})
    return builds


def follow_builds(builds, api, seconds=2100, sleep=time.sleep, clock=time.monotonic):
    started = clock()
    deadline = started + seconds
    while any(build["conclusion"] is None for build in builds):
        require(clock() < deadline, "Timed out waiting for reference builds; see their run links")
        for build in builds:
            if build["conclusion"] is not None:
                continue
            try:
                run = api(f"repos/{build['repository']}/actions/runs/{build['run_id']}")
            except GitHubError as error:
                # A dispatch ID can become readable shortly after the POST returns.
                if error.status == 404 and "project_sha" not in build and clock() < started + 60:
                    continue
                raise
            require(run["head_branch"] == build["branch"] and run["event"] == "workflow_dispatch",
                    "Dispatched run does not match the selected development branch")
            build["project_sha"] = run["head_sha"]
            if run["status"] == "completed":
                build["conclusion"] = run["conclusion"] or "unknown"
                print(f"{build['repository']}:{build['branch']}: {build['conclusion']} - {build['url']}", flush=True)
        if any(build["conclusion"] is None for build in builds):
            sleep(10)
    return all(build["conclusion"] == "success" for build in builds)


def write_report(builds, error=None):
    output = Path("_out/module-integration-results.json")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps({"builds": builds, "error": error}, indent=2) + "\n", encoding="utf-8")
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as stream:
            stream.write("## Module reference builds\n\n")
            for build in builds:
                stream.write(f"- [{build['repository']} / {build['branch']}]({build['url']}): "
                             f"{build['conclusion'] or 'pending'}; `{build['source_sha']}`\n")
            if error:
                stream.write("\nReference build orchestration failed; see the job log and JSON report.\n")


def main():
    builds = []
    error = None
    try:
        require(os.environ.get("GITHUB_EVENT_NAME") in ("push", "workflow_dispatch"), "Unsupported module trigger")
        source_ref = os.environ.get("GITHUB_REF", "")
        require(source_ref.startswith("refs/heads/") and source_ref not in ("refs/heads/main", "refs/heads/master"),
                "Run module reference CI from a development branch, not main/master or a tag")
        root = Path(os.environ["GITHUB_WORKSPACE"]).resolve()
        path = (root / os.environ.get("CONFIG_PATH", "ci_targets.yml")).resolve()
        require(path.is_relative_to(root), "Config path escapes the module repository")
        targets = load_targets(path)
        api = GitHub(os.environ.get("DISPATCH_TOKEN", ""))
        dispatch_builds(targets, os.environ["GITHUB_REPOSITORY"], os.environ["GITHUB_SHA"], api, builds)
        write_report(builds)
        for build in builds:
            print(f"Reference build: {build['url']}", flush=True)
        require(follow_builds(builds, api), "One or more reference integration builds failed")
    except (ValueError, OSError, KeyError, TypeError, yaml.YAMLError) as failure:
        error = str(failure)
        print(f"Module CI: {error}", file=sys.stderr)
    finally:
        write_report(builds, error)
    return 1 if error else 0


if __name__ == "__main__":
    sys.exit(main())
