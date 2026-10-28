import io
import json
import os
from pathlib import Path
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import unittest
from unittest.mock import patch
import urllib.error

from xo_launchpad.client import Client, CliError, normalize_url, resolve_credentials, save_settings
from xo_launchpad.output import docker_commands


class Handler(BaseHTTPRequestHandler):
    received = []
    def log_message(self, *args):
        pass

    def respond(self):
        body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
        type(self).received.append((self.command, self.path, dict(self.headers), body))
        if self.path == "/redirect":
            self.send_response(302)
            self.send_header("Location", "/destination")
            self.end_headers()
            return
        if self.path == "/error":
            code, result = 422, {"detail": "echo ak_secret and github_secret"}
        elif self.path == "/gateway":
            code, result = 502, {"detail": "upstream unavailable"}
        else:
            code, result = 200, {"echo": self.headers.get("Authorization"), "ok": True}
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(result).encode())

    do_GET = respond
    do_POST = respond


class ClientTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.url = f"http://127.0.0.1:{cls.server.server_port}"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join()

    def setUp(self):
        Handler.received.clear()

    def test_authorization_json_and_query_encoding(self):
        data = Client(self.url, "ak_secret").request("POST", "/launchpad/apps", {"name": "demo"}, {"previous": True, "x": "a b"})
        method, path, headers, body = Handler.received[-1]
        self.assertEqual(headers["Authorization"], "Bearer ak_secret")
        self.assertEqual(headers["Content-Type"], "application/json")
        self.assertEqual(path, "/launchpad/apps?previous=true&x=a+b")
        self.assertEqual(json.loads(body), {"name": "demo"})
        self.assertEqual(data["echo"], "Bearer [redacted]")

    def test_redirects_never_forward_the_key(self):
        with self.assertRaisesRegex(CliError, "redirected"):
            Client(self.url, "ak_secret").request("GET", "/redirect")
        self.assertEqual(len(Handler.received), 1)

    def test_error_messages_redact_auth_and_supplied_secrets(self):
        client = Client(self.url, "ak_secret")
        client.secrets.append("github_secret")
        with self.assertRaises(CliError) as error:
            client.request("POST", "/error", {})
        self.assertNotIn("ak_secret", str(error.exception))
        self.assertNotIn("github_secret", str(error.exception))
        self.assertIn("422", str(error.exception))

    def test_failed_mutations_are_never_automatically_retried(self):
        with self.assertRaisesRegex(CliError, "502"):
            Client(self.url, "ak_secret").request("POST", "/gateway", {})
        self.assertEqual(len(Handler.received), 1)

    def test_timeout_has_uncertain_outcome_message_without_transport_details(self):
        class Timeout:
            def open(self, *args, **kwargs):
                raise urllib.error.URLError("sensitive transport detail")
        with self.assertRaisesRegex(CliError, "may have succeeded") as error:
            Client(self.url, "ak_secret", opener=Timeout()).request("POST", "/launchpad/apps", {})
        self.assertNotIn("sensitive", str(error.exception))

    def test_invalid_urls_and_paths_are_rejected(self):
        for url in ("http://example.com", "https://user:secret@example.com", "https://example.com/path", "https://example.com?key=secret", "https://example.com:0"):
            with self.subTest(url=url), self.assertRaises(CliError):
                normalize_url(url)
        client = Client(self.url, "ak_secret")
        for path in ("//evil.example", "https://evil.example", "/path?key=secret", "/path#fragment", "/path\\evil"):
            with self.subTest(path=path), self.assertRaises(CliError):
                client.request("GET", path)
        self.assertEqual(Handler.received, [])

    def test_shared_login_is_api_url_bound_and_environment_key_takes_precedence(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {"XDG_CONFIG_HOME": tmp, "XO_LAUNCHPAD_CONFIG": str(Path(tmp) / "config.json")}, clear=True):
            shared = Path(tmp) / "xo" / "credentials"
            shared.parent.mkdir()
            shared.write_text(json.dumps({"url": "https://xo.quirq.dev", "key": "ak_saved"}), encoding="utf-8")
            shared.chmod(0o600)
            save_settings("https://one.example")
            self.assertEqual(resolve_credentials(None), ("https://one.example", "ak_saved"))
            with self.assertRaisesRegex(CliError, "not approved"):
                resolve_credentials("https://two.example")
            os.environ["XO_API_KEY"] = "ak_env"
            self.assertEqual(resolve_credentials("https://two.example"), ("https://two.example", "ak_env"))
            if os.name != "nt":
                self.assertEqual((Path(tmp) / "config.json").stat().st_mode & 0o777, 0o600)

    def test_missing_key_and_invalid_timeout(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {"XDG_CONFIG_HOME": tmp, "XO_LAUNCHPAD_CONFIG": str(Path(tmp) / "none.json")}, clear=True):
            with self.assertRaisesRegex(CliError, "XO_API_KEY"):
                resolve_credentials(None)
        for timeout in (0, -1, float("inf"), float("nan")):
            with self.assertRaises(CliError):
                Client(self.url, "key", timeout=timeout)

    def test_docker_command_quoting_and_fixed_images(self):
        app = {"source": "user-image", "image": "registry.example/demo/app", "push_robot": {"username": "robot$with'quote"}}
        commands = docker_commands(app, shell="powershell")
        self.assertIn("'robot$with''quote'", commands[0])
        commands = docker_commands(app, shell="posix")
        self.assertIn("'\"'\"'", commands[0])
        self.assertEqual(docker_commands({"source": "image"}), [])


if __name__ == "__main__":
    unittest.main()
