"""Policy boundaries and result propagation for module reference CI."""
import base64
import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import yaml

spec = importlib.util.spec_from_file_location("dispatch", Path(__file__).resolve().parents[1] / "actions/module-integration/dispatch.py")
dispatch = importlib.util.module_from_spec(spec)
spec.loader.exec_module(dispatch)
TARGET = {"repository": "NeoTech-vietnam/neocam_csw", "branch": "feat/wifistack", "package": "WifiStack"}
SOURCE = "NeoTech-vietnam/WifiStack"
SHA = "a" * 40


class ModuleIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.posts = []
        self.config = tempfile.TemporaryDirectory()
        self.addCleanup(self.config.cleanup)
        self.path = Path(self.config.name) / "ci_targets.yml"

    def api(self, path, method="GET", payload=None):
        if method == "POST":
            self.posts.append(payload)
            return {"workflow_run_id": len(self.posts), "html_url": "https://github.com/NeoTech-vietnam/neocam_csw/actions/runs/1"}
        if path.endswith("/neocam_csw"):
            return {"default_branch": "main"}
        if "/branches/" in path:
            return {"name": "feat/wifistack"}
        if "/actions/workflows/" in path:
            return {"state": "active"}
        if "/contents/" in path:
            data = {"packages": {"WifiStack": {"repo": f"https://github.com/{SOURCE}.git"}}}
            return {"content": base64.b64encode(yaml.safe_dump(data).encode()).decode()}
        if "/actions/runs/" in path:
            return {"head_branch": "feat/wifistack", "event": "workflow_dispatch", "head_sha": "b" * 40,
                    "status": "completed", "conclusion": "success"}
        self.fail(f"Unexpected API call: {path}")

    def load(self, target):
        self.path.write_text(yaml.safe_dump({"ref_projects": [target]}), encoding="utf-8")
        return dispatch.load_targets(self.path)

    def test_all_source_branches_can_dispatch_to_a_development_target(self):
        self.load(TARGET)
        for branch in ("main", "master", "feat/wifistack"):
            self.posts.clear()
            with self.subTest(branch=branch), patch.dict(dispatch.os.environ, {
                "GITHUB_EVENT_NAME": "push", "GITHUB_REF": f"refs/heads/{branch}",
                "GITHUB_WORKSPACE": self.config.name, "GITHUB_REPOSITORY": SOURCE,
                "GITHUB_SHA": SHA, "DISPATCH_TOKEN": "test-only",
            }, clear=True), patch.object(dispatch, "GitHub", return_value=self.api), \
                    patch.object(dispatch, "write_report"):
                self.assertEqual(dispatch.main(), 0)
                self.assertEqual(self.posts, [{"ref": TARGET["branch"], "inputs": {
                    "package": TARGET["package"], "package_ref": SHA, "source_repository": SOURCE}}])

    def test_non_branch_source_refs_cannot_dispatch(self):
        self.load(TARGET)
        for ref in ("refs/tags/v1.0.0", "refs/pull/1/merge", "refs/heads/", ""):
            with self.subTest(ref=ref), patch.dict(dispatch.os.environ, {
                "GITHUB_EVENT_NAME": "workflow_dispatch", "GITHUB_REF": ref,
                "GITHUB_WORKSPACE": self.config.name,
            }, clear=True), patch.object(dispatch, "GitHub") as api, \
                    patch.object(dispatch, "write_report"):
                self.assertEqual(dispatch.main(), 1)
                api.assert_not_called()
        self.assertEqual(self.posts, [])

    def test_reject_main_aliases_tags_shas_and_invalid_targets(self):
        for branch in ("main", "master", "refs/heads/main", "refs/tags/feat/test", SHA,
                       "feat/../main", "feat//test", "feat/evil.lock", "feat/x\nmain", ""):
            with self.subTest(branch=branch), self.assertRaises(ValueError):
                self.load(dict(TARGET, branch=branch))
        for repo in ("outside/project", "https://github.com/NeoTech-vietnam/neocam_csw", "NeoTech-vietnam/a?ref=main"):
            with self.subTest(repository=repo), self.assertRaises(ValueError):
                self.load(dict(TARGET, repository=repo))

    def test_exact_push_commit_and_target_are_sent(self):
        targets = self.load(TARGET)
        builds = dispatch.dispatch_builds(targets, SOURCE, SHA, self.api)
        self.assertEqual(self.posts, [{"ref": "feat/wifistack", "inputs": {
            "package": "WifiStack", "package_ref": SHA, "source_repository": SOURCE}}])
        self.assertTrue(dispatch.follow_builds(builds, self.api))
        self.assertEqual(builds[0]["source_sha"], SHA)
        self.assertEqual(builds[0]["project_sha"], "b" * 40)

    def test_default_branch_is_rejected_even_if_named_feat(self):
        def api(path, method="GET", payload=None):
            return {"default_branch": "feat/wifistack"} if path.endswith("/neocam_csw") else self.api(path, method, payload)
        with self.assertRaises(ValueError):
            dispatch.dispatch_builds(self.load(TARGET), SOURCE, SHA, api)
        self.assertEqual(self.posts, [])

    def test_all_targets_are_checked_before_dispatch(self):
        targets = [TARGET, dict(TARGET, package="WrongModule")]
        with self.assertRaises(ValueError):
            dispatch.dispatch_builds(targets, SOURCE, SHA, self.api)
        self.assertEqual(self.posts, [])

    def test_package_must_match_pushing_repository(self):
        with self.assertRaises(ValueError):
            dispatch.dispatch_builds([TARGET], "NeoTech-vietnam/CamIo", SHA, self.api)
        self.assertEqual(self.posts, [])

    def test_sha_must_be_an_exact_commit(self):
        for sha in ("main", "feature/wifi", "v1.0.0", "--help", "a" * 39):
            with self.subTest(sha=sha), self.assertRaises(ValueError):
                dispatch.dispatch_builds([TARGET], SOURCE, sha, self.api)
        self.assertEqual(self.posts, [])

    def test_failed_or_cancelled_integration_fails_module_check(self):
        for conclusion in ("failure", "cancelled", "timed_out", "skipped"):
            builds = dispatch.dispatch_builds([TARGET], SOURCE, SHA, self.api)
            def api(path):
                return dict(self.api(path), conclusion=conclusion)
            self.assertFalse(dispatch.follow_builds(builds, api))

    def test_timeout_is_a_failure_instead_of_dispatch_success(self):
        builds = dispatch.dispatch_builds([TARGET], SOURCE, SHA, self.api)
        ticks = iter([0, 0, 2])
        def api(path):
            return dict(self.api(path), status="in_progress", conclusion=None)
        with self.assertRaises(ValueError):
            dispatch.follow_builds(builds, api, seconds=1, sleep=lambda _: None, clock=lambda: next(ticks))

    def test_partial_dispatch_keeps_already_started_run_links(self):
        targets = [TARGET, dict(TARGET, branch="feat/another")]
        def api(path, method="GET", payload=None):
            if method == "POST" and self.posts:
                raise ValueError("connection failed")
            return self.api(path, method, payload)
        builds = []
        with self.assertRaises(ValueError):
            dispatch.dispatch_builds(targets, SOURCE, SHA, api, builds)
        self.assertEqual(len(builds), 1)
        self.assertIn("/actions/runs/", builds[0]["url"])

    def test_unexpected_run_branch_is_rejected(self):
        builds = dispatch.dispatch_builds([TARGET], SOURCE, SHA, self.api)
        with self.assertRaises(ValueError):
            dispatch.follow_builds(builds, lambda path: dict(self.api(path), head_branch="main"))

    def test_newly_dispatched_run_can_be_temporarily_invisible(self):
        builds = dispatch.dispatch_builds([TARGET], SOURCE, SHA, self.api)
        calls = []
        def api(path):
            calls.append(path)
            if len(calls) == 1:
                raise dispatch.GitHubError(404, "GET")
            return self.api(path)
        self.assertTrue(dispatch.follow_builds(builds, api, sleep=lambda _: None))
        self.assertEqual(len(calls), 2)

    def test_missing_run_retry_is_bounded_and_permission_errors_fail_immediately(self):
        for status in (404, 403):
            builds = dispatch.dispatch_builds([TARGET], SOURCE, SHA, self.api)
            elapsed = [0]
            calls = []
            def api(path):
                calls.append(path)
                raise dispatch.GitHubError(status, "GET")
            def sleep(_):
                elapsed[0] += 61
            with self.subTest(status=status), self.assertRaises(dispatch.GitHubError):
                dispatch.follow_builds(builds, api, sleep=sleep, clock=lambda: elapsed[0])
            self.assertEqual(len(calls), 2 if status == 404 else 1)


if __name__ == "__main__":
    unittest.main()
