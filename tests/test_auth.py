"""XO owns login and credentials; Launchpad only delegates and reads them privately."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from xo_launchpad import auth
from xo_launchpad.client import (CliError, read_xo_login, shared_key, save_settings,
                                 resolve_credentials, xo_credentials_path)


class SharedAuthTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        env = patch.dict(os.environ, {"XDG_CONFIG_HOME": self.tmp.name}, clear=True)
        env.start()
        self.addCleanup(env.stop)

    def write_login(self, data=None):
        path = xo_credentials_path()
        path.parent.mkdir(exist_ok=True)
        path.write_text(json.dumps(data or {"url": "https://xo.quirq.dev", "key": "ak_shared_secret"}), encoding="utf-8")
        path.chmod(0o600)
        return path

    def test_delegation_uses_installed_xo_without_a_shell_or_token_arguments(self):
        with patch.object(auth.shutil, "which", return_value="/tools/xo"), patch.object(auth.subprocess, "run") as run:
            auth.run_xo(["login", "xo", "--key"])
        self.assertEqual(run.call_args.args[0], ["/tools/xo", "login", "xo", "--key"])
        self.assertNotIn("shell", run.call_args.kwargs)
        self.assertTrue(run.call_args.kwargs["check"])

    def test_missing_xo_has_an_actionable_error(self):
        with patch.object(auth.shutil, "which", return_value=None), self.assertRaisesRegex(CliError, "Install quirq-ai/xo"):
            auth.run_xo(["login", "xo"])

    def test_xo_failure_does_not_fall_back_to_a_private_auth_flow(self):
        with patch.object(auth.shutil, "which", return_value="/tools/xo"), \
                patch.object(auth.subprocess, "run", side_effect=subprocess.CalledProcessError(7, ["xo"])), \
                self.assertRaisesRegex(CliError, "exit 7"):
            auth.run_xo(["login", "xo"])

    def test_shared_file_is_read_without_mutation_and_repr_hides_key(self):
        path = self.write_login()
        original = path.read_bytes()
        saved = read_xo_login()
        self.assertEqual(saved.key, "ak_shared_secret")
        self.assertNotIn("ak_shared_secret", repr(saved))
        self.assertEqual(path.read_bytes(), original)
        self.assertEqual(shared_key(), ("ak_shared_secret", "xo login"))

    def test_explicit_environment_key_wins_without_reading_shared_file(self):
        self.write_login({"not": "valid"})
        os.environ["XO_API_KEY"] = "ak_automation"
        self.assertEqual(shared_key(), ("ak_automation", "XO_API_KEY"))

    def test_malformed_shared_credentials_fail_without_echoing_contents(self):
        for data in ({"key": "ak_secret"}, {"url": "https://xo.quirq.dev", "key": "sk_wrong_secret"}):
            self.write_login(data)
            with self.assertRaises(CliError) as error:
                read_xo_login()
            self.assertNotIn("ak_secret", str(error.exception))
            self.assertNotIn("sk_wrong_secret", str(error.exception))

    def test_xo_url_must_match_the_shared_credential_owner(self):
        self.write_login()
        os.environ["XO_URL"] = "https://other-xo.example"
        with self.assertRaisesRegex(CliError, "XO_URL"):
            shared_key()

    def test_new_api_origin_needs_explicit_approval_before_using_shared_key(self):
        self.write_login()
        with self.assertRaisesRegex(CliError, "not approved"):
            resolve_credentials("https://another-launchpad.example")
        save_settings("https://another-launchpad.example")
        self.assertEqual(resolve_credentials(None), ("https://another-launchpad.example", "ak_shared_secret"))
        settings = Path(self.tmp.name) / "xo-launchpad" / "config.json"
        self.assertEqual(json.loads(settings.read_text()), {"url": "https://another-launchpad.example"})

    @unittest.skipIf(os.name == "nt", "POSIX owner-mode bits are not Windows ACLs")
    def test_shared_credentials_with_public_permissions_are_refused(self):
        path = self.write_login()
        path.chmod(0o644)
        with self.assertRaisesRegex(CliError, "not private"):
            read_xo_login()


if __name__ == "__main__":
    unittest.main()
