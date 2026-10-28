"""Run the installed CLI as a real process against a local HTTP API; never the beta server."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

APP_ID = "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee"


class LaunchpadHandler(BaseHTTPRequestHandler):
    calls = []
    app = {"id": APP_ID, "name": "demo", "source": "user-image", "status": "created",
           "image": "registry.example/demo/app", "public_url": "https://demo.apps.example",
           "push_robot": {"username": "robot+demo", "expires_at": -1}}
    environment = {"TOKEN": None, "PLAIN": "keep"}
    sensitive = ["TOKEN"]

    def log_message(self, *args):
        pass

    def respond(self):
        cls = type(self)
        raw = self.rfile.read(int(self.headers.get("Content-Length", 0)))
        body = json.loads(raw) if raw else None
        cls.calls.append((self.command, self.path, dict(self.headers), body))
        status = 200
        if self.headers.get("Authorization") != "Bearer ak_e2e_secret":
            status, result = 401, {"detail": "rejected"}
        elif self.path == "/launchpad/apps" and self.command == "POST":
            status, result = 201, {"app": cls.app, "registry": {"server": "registry.example", "username": "robot+demo",
                                     "password": "one-time-registry-password", "repository": cls.app["image"]}, "notes": "Push your image"}
        elif self.path == "/launchpad/apps":
            result = {"apps": [cls.app]}
        elif self.path.endswith("/env"):
            if self.command == "PUT":
                cls.environment = body["env"]
                cls.sensitive = body["sensitive"]
            result = {"vars": [{"key": k, "value": None if k in cls.sensitive else v, "sensitive": k in cls.sensitive} for k, v in cls.environment.items()]}
        else:
            result = {"app": cls.app}
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(result).encode())

    do_GET = respond
    do_POST = respond
    do_PUT = respond


class EndToEndTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), LaunchpadHandler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.url = f"http://127.0.0.1:{cls.server.server_port}"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join()

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.config = Path(self.tmp.name) / "config.json"
        self.env = {k: v for k, v in os.environ.items() if not k.startswith(("XO_", "LAUNCHPAD_"))}
        self.env.update(XO_API_KEY="ak_e2e_secret", XO_LAUNCHPAD_CONFIG=str(self.config), XDG_CONFIG_HOME=self.tmp.name, PYTHONDONTWRITEBYTECODE="1")
        LaunchpadHandler.calls.clear()
        LaunchpadHandler.environment = {"TOKEN": None, "PLAIN": "keep"}
        LaunchpadHandler.sensitive = ["TOKEN"]

    def command(self, *args, env=None):
        return subprocess.run([sys.executable, "-m", "xo_launchpad", "--url", self.url, *args],
                              capture_output=True, text=True, env=self.env if env is None else env, timeout=15)

    def test_real_create_list_and_docker_command_flow(self):
        created = self.command("app", "create", "demo", "--port", "3000", "--shell", "powershell")
        self.assertEqual(created.returncode, 0, created.stderr)
        self.assertIn("docker login registry.example -u robot+demo", created.stdout)
        self.assertIn("one-time-registry-password", created.stdout)
        self.assertNotIn("ak_e2e_secret", created.stdout + created.stderr)
        self.assertEqual(LaunchpadHandler.calls[-1][3], {"name": "demo", "template": "blank", "params": {"port": 3000}})
        listed = self.command("app", "list", "--json")
        self.assertEqual(listed.returncode, 0, listed.stderr)
        self.assertEqual(json.loads(listed.stdout)["apps"][0]["id"], APP_ID)
        docker = self.command("app", "docker", "demo", "--shell", "powershell")
        self.assertEqual(docker.returncode, 0, docker.stderr)
        self.assertIn("registry.example/demo/app:latest", docker.stdout)
        self.assertNotIn("one-time-registry-password", docker.stdout)
        self.assertTrue(all(c[2]["Authorization"] == "Bearer ak_e2e_secret" for c in LaunchpadHandler.calls))

    def test_real_env_update_preserves_masked_secret(self):
        result = self.command("app", "env", "set", APP_ID, "ADDED=ok", "--json")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual([c[0] for c in LaunchpadHandler.calls], ["GET", "PUT"])
        self.assertEqual(LaunchpadHandler.environment, {"TOKEN": None, "PLAIN": "keep", "ADDED": "ok"})
        self.assertEqual(LaunchpadHandler.sensitive, ["TOKEN"])


    def test_real_auth_failure_has_nonzero_exit_without_key(self):
        env = {**self.env, "XO_API_KEY": "ak_wrong_secret"}
        result = self.command("app", "list", env=env)
        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stdout, "")
        self.assertIn("401", result.stderr)
        self.assertNotIn("ak_wrong_secret", result.stderr)

    def shared_credentials(self):
        path = Path(self.tmp.name) / "xo" / "credentials"
        path.parent.mkdir(exist_ok=True)
        path.write_text(json.dumps({"url": "https://xo.quirq.dev", "key": "ak_e2e_secret"}), encoding="utf-8")
        path.chmod(0o600)
        self.config.write_text(json.dumps({"url": self.url}), encoding="utf-8")
        return path

    def test_real_existing_xo_login_is_reused_and_logout_takes_effect(self):
        shared = self.shared_credentials()
        original = shared.read_bytes()
        env = {k: v for k, v in self.env.items() if k != "XO_API_KEY"}
        listed = self.command("app", "list", "--json", env=env)
        self.assertEqual(listed.returncode, 0, listed.stderr)
        self.assertEqual(json.loads(listed.stdout)["apps"][0]["id"], APP_ID)
        self.assertEqual(shared.read_bytes(), original)
        shared.unlink()  # Simulate the owner `xo logout` removing its credential.
        calls = len(LaunchpadHandler.calls)
        listed = self.command("app", "list", "--json", env=env)
        self.assertEqual(listed.returncode, 1)
        self.assertIn("auth login", listed.stderr)
        self.assertEqual(len(LaunchpadHandler.calls), calls)
        self.assertEqual(json.loads(self.config.read_text()), {"url": self.url})

    def test_real_auth_status_uses_shared_credentials(self):
        self.shared_credentials()
        env = {k: v for k, v in self.env.items() if k != "XO_API_KEY"}
        status = self.command("auth", "status", "--json", env=env)
        self.assertEqual(status.returncode, 0, status.stderr)
        self.assertTrue(json.loads(status.stdout)["authenticated"])
        self.assertEqual(json.loads(status.stdout)["credential_source"], "xo login")
        self.assertNotIn("ak_e2e_secret", status.stdout + status.stderr)

    def test_real_auth_login_delegates_and_saves_no_platform_secret(self):
        env = {k: v for k, v in self.env.items() if k != "XO_API_KEY"}
        script = '''
import json, os
from pathlib import Path
from unittest.mock import patch
from xo_launchpad import cli, auth
def fake_xo(arguments):
    root = Path(os.environ["XDG_CONFIG_HOME"])
    (root / "owner-call.json").write_text(json.dumps(arguments))
    path = root / "xo" / "credentials"
    path.parent.mkdir(exist_ok=True)
    path.write_text(json.dumps({"url": "https://xo.quirq.dev", "key": "ak_e2e_secret"}))
    path.chmod(0o600)
with patch.object(auth, "run_xo", fake_xo):
    raise SystemExit(cli.main())
'''
        result = subprocess.run([sys.executable, "-c", script, "--url", self.url, "auth", "login", "--key"],
                                capture_output=True, text=True, env=env, timeout=15)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(json.loads(result.stdout)["authenticated"])
        self.assertNotIn("ak_e2e_secret", result.stdout + result.stderr)
        self.assertEqual(json.loads((Path(self.tmp.name) / "owner-call.json").read_text()), ["login", "xo", "--timeout", "30", "--key"])
        self.assertEqual(json.loads(self.config.read_text()), {"url": self.url})
        listed = self.command("app", "list", "--json", env=env)
        self.assertEqual(listed.returncode, 0, listed.stderr)

    def test_real_old_standalone_key_is_not_used(self):
        self.config.write_text(json.dumps({"url": self.url, "key": "ak_e2e_secret"}), encoding="utf-8")
        env = {k: v for k, v in self.env.items() if k != "XO_API_KEY"}
        result = self.command("app", "list", env=env)
        self.assertEqual(result.returncode, 1)
        self.assertIn("auth login", result.stderr)
        self.assertEqual(LaunchpadHandler.calls, [])



if __name__ == "__main__":
    unittest.main()
