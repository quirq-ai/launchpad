"""Behavioral CLI tests: exact requests, auth selection, secret handling and lifecycle flows."""
import contextlib
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from xo_launchpad import cli
from xo_launchpad.client import CliError

APP_ID = "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee"
PREFIX = f"/launchpad/apps/{APP_ID}"
APP = {"id": APP_ID, "name": "demo", "slug": "demo-xo-abcdefg", "source": "user-image",
       "image": "registry.xo.builders/demo-xo-abcdefg/app", "image_tag": None,
       "url": "https://demo.apps.example.com", "public_url": "https://demo.apps.example.com",
       "status": "running", "push_robot": {"username": "xo-demo+push", "expires_at": -1}}
REGISTRY = {"server": "registry.xo.builders", "repository": APP["image"], "username": "xo-demo+push", "password": "registry-secret"}
RELEASE = {"id": "release-1", "rollout_status": "ready"}


class FakeClient:
    instances = []
    def __init__(self, url, key, **kwargs):
        self.url, self.key, self.secrets = url, key, [key]
        self.calls = []
        self.instances.append(self)

    def request(self, method, path, body=None, query=None):
        self.calls.append((method, path, body, query))
        if path == "/launchpad/apps":
            return {"apps": [APP]} if method == "GET" else {"app": APP, "registry": REGISTRY, "notes": "Push your image"}
        if path == PREFIX + "/robots/push/rotate":
            return {"registry": REGISTRY}
        if path == "/launchpad/templates":
            return {"templates": [{"name": "blank", "displayName": "Blank", "source": "user-image", "version": 1, "parameters": []}]}
        if path.endswith("/status"):
            return {"app": APP, "release": RELEASE, "status": {"state": "ready"}}
        if path.endswith("/env"):
            return {"vars": [{"key": "TOKEN", "value": None, "sensitive": True}, {"key": "PLAIN", "value": "keep", "sensitive": False}]}
        if path.endswith("/logs"):
            return {"logs": "hello from app\n"}
        if path.endswith(("/deploy", "/rollback")):
            return {"release": RELEASE}
        return {"app": APP}


class CliTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.env = patch.dict(os.environ, {"XO_API_KEY": "ak_platform_secret", "XO_LAUNCHPAD_CONFIG": str(Path(self.tmp.name) / "config.json"), "XDG_CONFIG_HOME": self.tmp.name}, clear=True)
        self.env.start()
        self.addCleanup(self.env.stop)
        FakeClient.instances.clear()
        self.client = patch.object(cli, "Client", FakeClient)
        self.client.start()
        self.addCleanup(self.client.stop)

    def run_cli(self, *args):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = cli.main(list(args))
        return code, out.getvalue(), err.getvalue()

    def shared_login(self, key="ak_shared_key"):
        path = Path(self.tmp.name) / "xo" / "credentials"
        path.parent.mkdir(exist_ok=True)
        path.write_text(json.dumps({"url": "https://xo.quirq.dev", "key": key}), encoding="utf-8")
        path.chmod(0o600)
        return path

    def test_no_command_shows_full_help_without_authentication(self):
        with patch.dict(os.environ, {}, clear=True), patch.object(cli.auth, "run_xo") as run_xo:
            code, out, err = self.run_cli()
        self.assertEqual(code, 0, err)
        self.assertEqual(out, cli.build_parser().format_help())
        self.assertEqual(err, "")
        self.assertFalse(FakeClient.instances)
        run_xo.assert_not_called()
        for section in ("Quick start:", "App commands:", "Authentication:", "More help:", "global options"):
            self.assertIn(section, out)

    def test_explicit_help_matches_no_command(self):
        _, expected, _ = self.run_cli()
        out = io.StringIO()
        with contextlib.redirect_stdout(out), self.assertRaises(SystemExit) as exit_result:
            cli.main(["--help"])
        self.assertEqual(exit_result.exception.code, 0)
        self.assertEqual(out.getvalue(), expected)
        self.assertFalse(FakeClient.instances)

    def test_global_options_without_command_show_help(self):
        code, out, err = self.run_cli("--json", "--url", "https://example.com")
        self.assertEqual(code, 0, err)
        self.assertEqual(out, cli.build_parser().format_help())
        self.assertFalse(FakeClient.instances)

    def test_abbreviated_dry_run_never_executes_mutations(self):
        for args in (
            ["--dry", "app", "create", "demo"],
            ["app", "create", "demo", "--dry"],
            ["--dry-r", "app", "delete", APP_ID, "--yes"],
        ):
            with self.subTest(args=args):
                code, out, err = self.run_cli(*args)
                self.assertEqual(code, 1)
                self.assertIn("unrecognized arguments", err)
                self.assertEqual(out, "")
                self.assertFalse(FakeClient.instances)

    def test_full_dry_run_previews_mutations_without_a_client(self):
        for args in (
            ["--dry-run", "app", "create", "demo"],
            ["app", "delete", APP_ID, "--dry-run", "--yes"],
        ):
            with self.subTest(args=args):
                code, out, err = self.run_cli(*args)
                self.assertEqual(code, 0, err)
                self.assertIn(json.loads(out)["method"], ("POST", "DELETE"))
                self.assertFalse(FakeClient.instances)

    def test_create_prints_usable_commands_and_one_time_registry_password(self):
        code, out, err = self.run_cli("app", "create", "demo", "--port", "3000", "--shell", "powershell")
        self.assertEqual(code, 0, err)
        self.assertIn("docker login registry.xo.builders -u xo-demo+push", out)
        self.assertIn("docker build --platform linux/amd64", out)
        self.assertIn("docker push", out)
        self.assertIn("registry-secret", out)
        self.assertNotIn("ak_platform_secret", out + err)
        self.assertEqual(FakeClient.instances[-1].calls[-1][:3], ("POST", "/launchpad/apps", {"name": "demo", "template": "blank", "params": {"port": 3000}}))

    def test_all_canonical_user_routes_have_exact_command_requests(self):
        cases = [
            (["templates"], "GET", "/launchpad/templates", None),
            (["app", "list"], "GET", "/launchpad/apps", None),
            (["app", "get", APP_ID], "GET", PREFIX, None),
            (["app", "delete", APP_ID, "--yes"], "DELETE", PREFIX, None),
            (["app", "status", APP_ID], "GET", PREFIX + "/status", None),
            (["app", "logs", APP_ID], "GET", PREFIX + "/logs", None),
            (["app", "events", APP_ID], "GET", PREFIX + "/events", None),
            (["app", "artifacts", APP_ID], "GET", PREFIX + "/artifacts", None),
            (["app", "releases", APP_ID], "GET", PREFIX + "/releases", None),
            (["app", "deploy", APP_ID, "--tag", "v2"], "POST", PREFIX + "/deploy", {"tag": "v2"}),
            (["app", "rollback", APP_ID, "r1"], "POST", PREFIX + "/rollback", {"release_id": "r1"}),
            *[(["app", action, APP_ID], "POST", PREFIX + "/" + action, None) for action in ("start", "stop", "restart")],
            (["app", "domains", "list", APP_ID], "GET", PREFIX + "/domains", None),
            (["app", "domains", "add", APP_ID, "shop.example.com"], "POST", PREFIX + "/domains", {"hostname": "shop.example.com"}),
            (["app", "domains", "verify", APP_ID, "1"], "POST", PREFIX + "/domains/1/verify", None),
            (["app", "domains", "remove", APP_ID, "1", "--yes"], "DELETE", PREFIX + "/domains/1", None),
            (["app", "credentials", "rotate", APP_ID, "--yes"], "POST", PREFIX + "/robots/push/rotate", {}),
            (["app", "env", "list", APP_ID], "GET", PREFIX + "/env", None),
            (["app", "env", "replace", APP_ID, "--data", '{"env": {}, "sensitive": []}', "--yes"], "PUT", PREFIX + "/env", {"env": {}, "sensitive": []}),
            (["app", "storage", "resize", APP_ID, "8"], "PUT", PREFIX + "/storage", {"size_gi": 8}),
            (["app", "port", "set", APP_ID, "8080"], "PUT", PREFIX + "/port", {"port": 8080}),
            (["app", "health", "set", APP_ID, "--path", "/healthz"], "PUT", PREFIX + "/health", {"path": "/healthz"}),
        ]
        covered = {("POST", "/launchpad/apps")}
        for args, method, path, body in cases:
            with self.subTest(command=args):
                code, _, err = self.run_cli(*args)
                self.assertEqual(code, 0, err)
                self.assertEqual(FakeClient.instances[-1].calls[-1][:3], (method, path, body))
                normalized = path.replace(APP_ID, "{app_id}").replace("/domains/1", "/domains/{domain_id}")
                covered.add((method, normalized))
        expected = {(o["method"], o["path"]) for o in cli.operations()["operations"] if o["audience"] == "user" and o["path"].startswith("/launchpad/")}
        self.assertEqual(covered, expected)

    def test_template_parameters_keep_types_and_read_secret_environment(self):
        os.environ["GH_TEST_TOKEN"] = "github-secret"
        code, out, err = self.run_cli("app", "create", "GitHub-Demo", "--template", "github", "--param", "repoUrl=https://github.com/acme/demo", "--param", 'workflows=["one","two"]', "--param-env", "githubToken=GH_TEST_TOKEN", "--json")
        self.assertEqual(code, 0, err)
        body = FakeClient.instances[-1].calls[-1][2]
        self.assertEqual(body["name"], "github-demo")
        self.assertEqual(body["params"]["workflows"], ["one", "two"])
        self.assertEqual(body["params"]["githubToken"], "github-secret")
        self.assertNotIn("github-secret", out)

    def test_env_set_preserves_write_only_values_and_other_variables(self):
        code, _, err = self.run_cli("app", "env", "set", APP_ID, "NEW=value", "--sensitive", "NEW")
        self.assertEqual(code, 0, err)
        body = FakeClient.instances[-1].calls[-1][2]
        self.assertEqual(body, {"env": {"TOKEN": None, "PLAIN": "keep", "NEW": "value"}, "sensitive": ["NEW", "TOKEN"]})

    def test_env_unset_preserves_every_other_variable(self):
        code, _, err = self.run_cli("app", "env", "unset", APP_ID, "PLAIN", "--yes")
        self.assertEqual(code, 0, err)
        self.assertEqual(FakeClient.instances[-1].calls[-1][2], {"env": {"TOKEN": None}, "sensitive": ["TOKEN"]})

    def test_name_resolution_and_queries(self):
        code, _, err = self.run_cli("app", "logs", "demo", "--tail", "25", "--previous", "--json")
        self.assertEqual(code, 0, err)
        self.assertEqual(FakeClient.instances[-1].calls, [("GET", "/launchpad/apps", None, None), ("GET", PREFIX + "/logs", None, {"tail": 25, "previous": True})])

    def test_delete_without_confirmation_sends_nothing(self):
        with patch.object(cli.sys.stdin, "isatty", return_value=False):
            code, _, err = self.run_cli("app", "delete", APP_ID)
        self.assertEqual(code, 1)
        self.assertIn("--yes", err)
        self.assertEqual(FakeClient.instances[-1].calls, [])

    def test_dry_run_works_without_key_and_hides_secrets(self):
        os.environ.pop("XO_API_KEY")
        code, out, err = self.run_cli("app", "create", "demo", "--param", "secret=private", "--dry-run")
        self.assertEqual(code, 0, err)
        self.assertNotIn("private", out)
        self.assertEqual(json.loads(out)["body"]["params"], "[hidden]")
        self.assertEqual(FakeClient.instances, [])

    def test_dry_run_names_never_trigger_resolution_requests(self):
        code, _, err = self.run_cli("app", "get", "demo", "--dry-run")
        self.assertEqual(code, 1)
        self.assertIn("UUID", err)
        self.assertEqual(FakeClient.instances, [])

    def test_local_docker_commands_do_not_rotate_credentials(self):
        code, out, err = self.run_cli("app", "docker", APP_ID, "--shell", "posix")
        self.assertEqual(code, 0, err)
        self.assertIn("xo-demo+push", out)
        self.assertNotIn("registry-secret", out)
        self.assertTrue(all(call[0] == "GET" for call in FakeClient.instances[-1].calls))

    def test_invalid_input_does_not_create_anything(self):
        for args in (("app", "create", "bad/name"), ("app", "create", "demo", "--storage-size", "2"),
                     ("app", "create", "demo", "--port", "0"), ("app", "create", "demo", "--storage-size", "21")):
            with self.subTest(args=args):
                FakeClient.instances.clear()
                code, _, _ = self.run_cli(*args)
                self.assertEqual(code, 1)
                self.assertTrue(all(not c.calls for c in FakeClient.instances))

    def test_deploy_wait_tracks_the_requested_release(self):
        code, out, err = self.run_cli("app", "deploy", APP_ID, "--tag", "latest", "--wait", "--json")
        self.assertEqual(code, 0, err)
        self.assertEqual(json.loads(out)["release"]["id"], "release-1")
        self.assertEqual(FakeClient.instances[-1].calls[-1][1], PREFIX + "/status")

    def test_wait_does_not_claim_success_for_a_replacement_release(self):
        request = FakeClient.request
        def replaced(client, method, path, body=None, query=None):
            result = request(client, method, path, body, query)
            if path.endswith("/status"):
                result["release"] = {"id": "somebody-elses-release", "rollout_status": "ready"}
            return result
        with patch.object(FakeClient, "request", replaced):
            code, _, err = self.run_cli("app", "deploy", APP_ID, "--tag", "latest", "--wait")
        self.assertEqual(code, 1)
        self.assertIn("replaced", err)

    def test_wait_reports_a_failed_rollout(self):
        request = FakeClient.request
        def failed(client, method, path, body=None, query=None):
            result = request(client, method, path, body, query)
            if path.endswith("/status"):
                result["app"] = {**APP, "status": "failed"}
                result["release"] = {"id": "release-1", "rollout_status": "failed", "failure": "nothing listens on port 3000"}
            return result
        with patch.object(FakeClient, "request", failed):
            code, _, err = self.run_cli("app", "deploy", APP_ID, "--tag", "latest", "--wait")
        self.assertEqual(code, 1)
        self.assertIn("nothing listens on port 3000", err)

    def test_fixed_template_deploy_uses_its_pinned_tag(self):
        request = FakeClient.request
        def pinned(client, method, path, body=None, query=None):
            result = request(client, method, path, body, query)
            if path == PREFIX and method == "GET":
                return {"app": {**APP, "source": "image", "image_tag": "1.2.3"}}
            return result
        with patch.object(FakeClient, "request", pinned):
            code, _, err = self.run_cli("app", "deploy", APP_ID)
        self.assertEqual(code, 0, err)
        self.assertEqual(FakeClient.instances[-1].calls[-1][2], {"tag": "1.2.3"})

    def test_github_rotation_reports_success_without_printing_docker_credentials(self):
        os.environ["GITHUB_TOKEN"] = "github-test-secret"
        request = FakeClient.request
        def rotated(client, method, path, body=None, query=None):
            result = request(client, method, path, body, query)
            return {"registry": None} if path.endswith("/robots/push/rotate") else result
        with patch.object(FakeClient, "request", rotated):
            code, out, err = self.run_cli("app", "credentials", "rotate", APP_ID, "--github-token-env", "GITHUB_TOKEN", "--yes")
        self.assertEqual(code, 0, err)
        self.assertIn("GitHub repository secrets updated", out)
        self.assertNotIn("docker login", out)
        self.assertNotIn("github-test-secret", out + err)
        self.assertEqual(FakeClient.instances[-1].calls[-1][2], {"githubToken": "github-test-secret"})

    def test_admin_commands_are_removed(self):
        for args in (("admin", "template-versions", "n8n"), ("admin", "push-template", "template.yaml"),
                     ("admin", "template-toggle", "n8n", "--enabled", "false")):
            code, _, _ = self.run_cli(*args)
            self.assertEqual(code, 1)
        self.assertEqual(FakeClient.instances, [])
        self.assertNotIn("admin", cli.build_parser().format_help())

    def test_raw_api_cannot_reach_admin_routes_even_with_an_admin_token(self):
        os.environ["LAUNCHPAD_ADMIN_TOKEN"] = "admin-test-secret"
        for method, path in (("POST", "/launchpad/admin/templates"),
                             ("GET", "/launchpad/admin/templates/n8n/versions"),
                             ("PATCH", "/launchpad/admin/templates/n8n"),
                             ("GET", "/launchpad/apps/..%2fadmin%2ftemplates/n8n/versions"),
                             ("GET", "/launchpad/apps/../admin/templates/n8n/versions")):
            for suffix in ([], ["--dry-run"]):
                code, _, err = self.run_cli("api", method, path, *suffix)
                self.assertEqual(code, 1)
                self.assertIn("user deployment endpoints", err)
        self.assertEqual(FakeClient.instances, [])

    def test_operation_catalog_does_not_advertise_administration(self):
        operations = cli.operations()["operations"]
        self.assertFalse(any(o["audience"] == "admin" or "/admin/" in o["path"] for o in operations))
        self.assertNotIn("LAUNCHPAD_ADMIN_TOKEN", json.dumps(operations))

    def test_raw_api_keeps_requests_on_deployment_paths(self):
        for path in ("https://evil.example", "//evil.example", "/anything", "/launchpad/webhooks/harbor"):
            code, _, _ = self.run_cli("api", "POST", path, "--data", "{}")
            self.assertEqual(code, 1)
        code, out, err = self.run_cli("api", "POST", "/get-deployment-details-by-user-id", "--json")
        self.assertEqual(code, 0, err)

    def test_auth_login_delegates_to_xo_and_never_copies_its_key(self):
        shared = self.shared_login()
        original = shared.read_bytes()
        os.environ.pop("XO_API_KEY")
        with patch.object(cli.auth, "run_xo") as owner:
            code, out, err = self.run_cli("auth", "login")
        self.assertEqual(code, 0, err)
        owner.assert_called_once_with(["login", "xo", "--timeout", "30"])
        self.assertEqual(FakeClient.instances[-1].key, "ak_shared_key")
        self.assertEqual(FakeClient.instances[-1].calls[0][1], "/launchpad/apps")
        self.assertNotIn("ak_shared_key", out + err)
        saved = json.loads(Path(os.environ["XO_LAUNCHPAD_CONFIG"]).read_text())
        self.assertEqual(saved, {"url": "https://launchpad-beta-api.xo.builders"})
        self.assertEqual(shared.read_bytes(), original)

    def test_rejected_shared_key_does_not_change_approved_launchpad_url(self):
        path = Path(os.environ["XO_LAUNCHPAD_CONFIG"])
        original = '{"url":"https://previous.example"}'
        path.write_text(original, encoding="utf-8")
        shared = self.shared_login("ak_wrong_instance")
        shared_original = shared.read_bytes()
        os.environ.pop("XO_API_KEY")
        with patch.object(cli.auth, "run_xo"), \
                patch.object(FakeClient, "request", side_effect=CliError("HTTP 401")):
            code, out, err = self.run_cli("auth", "login", "--url", "https://launchpad-beta-api.xo.builders")
        self.assertEqual(code, 1)
        self.assertIn("same Clerk user key", err)
        self.assertNotIn("ak_wrong_instance", out + err)
        self.assertEqual(path.read_text(), original)
        self.assertEqual(shared.read_bytes(), shared_original)

    def test_manual_key_prompt_is_delegated_to_xo(self):
        with patch.object(cli.auth, "run_xo") as owner:
            code, _, err = self.run_cli("auth", "login", "--key")
        self.assertEqual(code, 0, err)
        owner.assert_called_once_with(["login", "xo", "--timeout", "30", "--key"])

    def test_auth_status_reuses_existing_xo_login_without_logging_in_again(self):
        shared = self.shared_login()
        original = shared.read_bytes()
        os.environ.pop("XO_API_KEY")
        with patch.object(cli.auth, "run_xo") as owner:
            code, out, err = self.run_cli("auth", "status")
        self.assertEqual(code, 0, err)
        self.assertTrue(json.loads(out)["authenticated"])
        self.assertEqual(json.loads(out)["credential_source"], "xo login")
        self.assertNotIn("ak_shared_key", out + err)
        owner.assert_not_called()
        self.assertEqual(shared.read_bytes(), original)
        self.assertFalse(Path(os.environ["XO_LAUNCHPAD_CONFIG"]).exists())

    def test_removed_whoami_and_ops_are_not_public_commands(self):
        for name in ("whoami", "ops"):
            code, _, _ = self.run_cli(name)
            self.assertEqual(code, 1)

    def test_legacy_login_aliases_also_delegate_to_xo(self):
        for name in ("login", "signin"):
            with patch.object(cli.auth, "run_xo") as owner:
                code, _, err = self.run_cli(name)
                self.assertEqual(code, 0, err)
                owner.assert_called_once()

    def test_shared_logout_is_explicit_and_delegated(self):
        with patch.object(cli.auth, "run_xo") as owner:
            code, _, err = self.run_cli("auth", "logout", "--yes")
        self.assertEqual(code, 0, err)
        owner.assert_called_once_with(["logout"])
        self.assertEqual(FakeClient.instances, [])

    def test_failed_xo_login_does_not_contact_launchpad(self):
        with patch.object(cli.auth, "run_xo", side_effect=CliError("xo login failed")):
            code, _, _ = self.run_cli("auth", "login")
        self.assertEqual(code, 1)
        self.assertEqual(FakeClient.instances, [])
        self.assertFalse(Path(os.environ["XO_LAUNCHPAD_CONFIG"]).exists())

    def test_config_never_prints_platform_keys(self):
        code, out, err = self.run_cli("config")
        self.assertEqual(code, 0, err)
        self.assertNotIn("ak_platform_secret", out + err)

    def test_docker_push_uses_argv_and_strips_platform_credentials(self):
        os.environ["SOME_TOKEN"] = "must-not-reach-docker"
        with patch.object(cli.subprocess, "run") as run:
            code, _, err = self.run_cli("app", "push", APP_ID)
        self.assertEqual(code, 0, err)
        argv = run.call_args.args[0]
        self.assertIn("linux/amd64", argv)
        self.assertIn("--push", argv)
        self.assertNotIn("XO_API_KEY", run.call_args.kwargs["env"])
        self.assertNotIn("SOME_TOKEN", run.call_args.kwargs["env"])
        self.assertNotIn("shell", run.call_args.kwargs)


if __name__ == "__main__":
    unittest.main()
