"""One argparse CLI for Launchpad v2; no backend imports or runtime dependencies."""
from __future__ import annotations

import argparse
import getpass
import importlib.resources
import json
import math
import os
from pathlib import Path
import re
import subprocess
import sys
import time
import uuid
import webbrowser

from . import __version__
from .client import (KEY_ENV, CliError, Client, config_path, credential_source, normalize_url,
                     redact, resolve_credentials, save_settings, selected_url, segment, shared_key,
                     xo_credentials_path)
from .output import render
from . import auth


class Parser(argparse.ArgumentParser):
    def __init__(self, *args, **kwargs):
        kwargs["allow_abbrev"] = False
        super().__init__(*args, **kwargs)

    def error(self, message):
        raise CliError(re.sub(r"ak_[A-Za-z0-9_.+/=-]+", "[redacted]", message))


def positive(value):
    number = float(value)
    if not math.isfinite(number) or number <= 0:
        raise argparse.ArgumentTypeError("must be a positive finite number")
    return number


def integer_between(low, high):
    def parse(value):
        try:
            number = int(value)
        except ValueError:
            raise argparse.ArgumentTypeError(f"must be an integer from {low} to {high}") from None
        if not low <= number <= high:
            raise argparse.ArgumentTypeError(f"must be an integer from {low} to {high}")
        return number
    return parse


def json_input(value: str):
    """JSON literal, @file, or stdin. Never include input contents in parse errors."""
    try:
        raw = sys.stdin.read() if value == "-" else Path(value[1:]).read_text(encoding="utf-8-sig") if value.startswith("@") else value
        return json.loads(raw)
    except (OSError, ValueError):
        raise CliError("Could not read JSON; use valid JSON, @path/to/file.json, or - for stdin") from None


def object_input(value: str) -> dict:
    result = json_input(value)
    if not isinstance(result, dict):
        raise CliError("JSON input must be an object")
    return result


def pairs(items, *, typed=False):
    result = {}
    for item in items or []:
        key, sep, value = item.partition("=")
        if not sep or not key:
            raise CliError("Expected KEY=VALUE")
        if typed:
            try:
                value = json.loads(value)
            except ValueError:
                pass
        result[key] = value
    return result


def login_options(parser):
    parser.add_argument("--key", action="store_true", help="Ask xo to use manual API-key login instead of its browser flow")
    parser.add_argument("--xo-url", help="XO login server override; separate from the Launchpad --url")


def add_global_options(parser):
    parser.add_argument("--url", metavar="URL", help="Launchpad API URL (default: https://launchpad-beta-api.xo.builders)")
    parser.add_argument("--json", action="store_true", help="Print results as JSON for scripts")
    parser.add_argument("--timeout", type=positive, default=30, metavar="SECONDS", help="HTTP request timeout (default: 30 seconds)")
    parser.add_argument("--dry-run", action="store_true", help="Preview actions without API calls or changes; identify existing apps by UUID")
    parser.add_argument("--yes", "-y", action="store_true", help="Skip Launchpad confirmation prompts")


def build_parser():
    p = Parser(prog="xo-launchpad", description="Create apps, get Docker commands, and manage XO Launchpad deployments.",
               formatter_class=argparse.RawDescriptionHelpFormatter,
               epilog="""Quick start:
  xo-launchpad auth login
  xo-launchpad templates
  xo-launchpad app create my-app --template blank --port 3000
  xo-launchpad app docker my-app
  xo-launchpad app status my-app

App commands:
  Inspect     list, get, status, logs, events, releases, artifacts, open
  Deploy      create, docker, push, deploy, rollback
  Lifecycle   start, stop, restart, delete
  Configure   env, port, health, storage, credentials, domains

Authentication:
  Uses your existing XO login. Run 'auth login' to sign in through the xo CLI.
  For automation, set XO_API_KEY to your user API key (takes precedence).
  Creating an app prints Docker commands and its one-time registry password.
  Docker is needed only for 'app push' or running the printed commands.

More help:
  xo-launchpad app --help
  xo-launchpad app create --help
  xo-launchpad auth --help
  Run any command with --help for its arguments and options.
  Running xo-launchpad without a command shows this help.
""")
    p.add_argument("--version", action="version", version=f"xo-launchpad {__version__}")
    root = p.add_subparsers(dest="group", required=True, parser_class=Parser, title="commands", metavar="COMMAND")
    auth_parser = root.add_parser("auth", help="Shared XO sign-in and Launchpad authentication status")
    auth_actions = auth_parser.add_subparsers(dest="auth_action", required=True, parser_class=Parser)
    login_options(auth_actions.add_parser("login", help="Delegate to xo login xo, then verify Launchpad access"))
    auth_actions.add_parser("status", help="Check whether the current shared key works with Launchpad")
    auth_actions.add_parser("logout", help="Delegate to xo logout; shared login affects all XO tools")
    login_options(root.add_parser("login", aliases=["signin"], help="Alias for auth login"))
    root.add_parser("logout", help="Alias for auth logout (shared XO sign-out)")
    root.add_parser("config", help="Show API URL and credential source; never the key")
    raw = root.add_parser("api", help="Call a deployment endpoint directly")
    raw.add_argument("method", choices=["GET", "POST", "PUT", "PATCH", "DELETE"])
    raw.add_argument("path")
    raw.add_argument("--data", help="JSON, @file, or - for stdin")
    raw.add_argument("--query", action="append", default=[], metavar="KEY=VALUE")

    templates = root.add_parser("templates", help="List the catalog or inspect a template's parameters")
    templates.add_argument("name", nargs="?")

    app = root.add_parser("app", aliases=["apps"], help="App deployment and lifecycle commands")
    actions = app.add_subparsers(dest="action", required=True, parser_class=Parser)
    actions.add_parser("list", help="List all your apps, hubs and executors included")
    create = actions.add_parser("create", help="Create an app; print its URL and Docker commands")
    create.add_argument("name")
    create.add_argument("--template", default="blank")
    create.add_argument("--size", choices=["xo.mini", "xo.small", "xo.medium", "xo.large"])
    create.add_argument("--port", type=integer_between(1, 65535))
    create.add_argument("--health-path")
    create.add_argument("--storage-path")
    create.add_argument("--storage-size", type=integer_between(1, 20), metavar="GB")
    create.add_argument("--params", help="Template parameters as JSON, @file, or -")
    create.add_argument("--param", action="append", default=[], metavar="KEY=VALUE", help="Template parameter; JSON values are typed")
    create.add_argument("--param-env", action="append", default=[], metavar="PARAM=ENV_VAR", help="Read a parameter from an environment variable")
    create.add_argument("--shell", choices=["powershell", "posix"])

    for name in ("get", "status", "events", "releases", "start", "stop", "restart", "delete", "open"):
        command = actions.add_parser(name)
        command.add_argument("app", help="App UUID, exact name, or slug")
    logs = actions.add_parser("logs")
    logs.add_argument("app")
    logs.add_argument("--tail", type=integer_between(1, 5000), default=500)
    logs.add_argument("--previous", action="store_true")
    artifacts = actions.add_parser("artifacts")
    artifacts.add_argument("app")
    artifacts.add_argument("--refresh", action="store_true")
    docker = actions.add_parser("docker", help="Print Docker commands for an existing app; does not rotate credentials")
    docker.add_argument("app")
    docker.add_argument("--shell", choices=["powershell", "posix"])
    docker.add_argument("--tag", default="latest")

    deploy = actions.add_parser("deploy", help="Deploy an existing registry image (does not build locally)")
    deploy.add_argument("app")
    image = deploy.add_mutually_exclusive_group()
    image.add_argument("--tag")
    image.add_argument("--digest")
    deploy.add_argument("--port", type=integer_between(1, 65535))
    rollback = actions.add_parser("rollback")
    rollback.add_argument("app")
    rollback.add_argument("release_id")
    push = actions.add_parser("push", help="Build for linux/amd64 with Docker and push; optionally log in/deploy")
    push.add_argument("app")
    push.add_argument("--context", default=".")
    push.add_argument("--dockerfile")
    push.add_argument("--tag", default="latest")
    push.add_argument("--login", action="store_true", help="Prompt for the registry password; XO_API_KEY is never used as a Docker password")
    push.add_argument("--deploy", action="store_true", help="Explicitly deploy after push; omit when webhooks already auto-deploy")
    for command in (deploy, rollback, push):
        command.add_argument("--wait", action="store_true")
        command.add_argument("--wait-timeout", type=positive, default=600)
        command.add_argument("--interval", type=positive, default=2)

    env = actions.add_parser("env", help="Environment variables; set/unset preserve the other keys")
    env_actions = env.add_subparsers(dest="env_action", required=True, parser_class=Parser)
    for name in ("list", "set", "unset", "replace"):
        command = env_actions.add_parser(name)
        command.add_argument("app")
        if name == "set":
            command.add_argument("values", nargs="*", metavar="KEY=VALUE")
            command.add_argument("--from-env", action="append", default=[], metavar="KEY=ENV_VAR")
            command.add_argument("--sensitive", action="append", default=[], metavar="KEY")
        if name == "unset":
            command.add_argument("keys", nargs="+")
        if name == "replace":
            command.add_argument("--data", required=True, help='Full body {"env": {...}, "sensitive": [...]} as JSON/@file/-')

    for resource, action in (("port", "set"), ("health", "set"), ("storage", "resize"), ("credentials", "rotate")):
        command = actions.add_parser(resource)
        sub = command.add_subparsers(dest="resource_action", required=True, parser_class=Parser).add_parser(action)
        sub.add_argument("app")
        if resource == "port":
            sub.add_argument("port", type=integer_between(1, 65535))
        elif resource == "health":
            check = sub.add_mutually_exclusive_group(required=True)
            check.add_argument("--path", help="HTTP path beginning with /")
            check.add_argument("--tcp", action="store_true")
        elif resource == "storage":
            sub.add_argument("size", type=integer_between(1, 20), help="New disk size in GiB; grow only")
        else:
            sub.add_argument("--github-token-env", help="Environment variable containing a GitHub token for a GitHub-backed app")
            sub.add_argument("--shell", choices=["powershell", "posix"])

    domains = actions.add_parser("domains")
    domain_actions = domains.add_subparsers(dest="domain_action", required=True, parser_class=Parser)
    for name in ("list", "add", "verify", "remove"):
        command = domain_actions.add_parser(name)
        command.add_argument("app")
        if name == "add":
            command.add_argument("hostname")
        elif name in ("verify", "remove"):
            command.add_argument("domain_id", type=integer_between(1, 2**63 - 1))

    add_global_options(p.add_argument_group("global options (before or after commands)"))
    return p


def global_options(argv):
    """Accept global flags anywhere, like the org's xo CLI."""
    parser = Parser(add_help=False, allow_abbrev=False)
    add_global_options(parser)
    return parser.parse_known_args(argv)


def env_value(name):
    value = os.environ.get(name)
    if value is None:
        raise CliError(f"Environment variable {name} is not set")
    return value


def hide_body(value):
    if isinstance(value, dict):
        return {k: "[hidden]" if re.search(r"password|secret|token|key|^env$|^params$", k, re.I) else hide_body(v) for k, v in value.items()}
    if isinstance(value, list):
        return [hide_body(v) for v in value]
    return value


class Context:
    def __init__(self, options):
        self.options = options
        if options.dry_run:
            url = selected_url(options.url)
            self.client = None
        else:
            url, key = resolve_credentials(options.url)
            self.client = Client(url, key, timeout=options.timeout)
        self.url = url

    def request(self, method, path, body=None, query=None):
        if self.options.dry_run:
            return {"method": method, "url": self.url + path, "query": query or {},
                    "body": hide_body(body), "authorization": "Bearer [hidden]"}
        self.remember(body)
        return self.client.request(method, path, body, query)

    def remember(self, value, secret=False):
        if self.client is None:
            return
        if isinstance(value, dict):
            for key, item in value.items():
                self.remember(item, secret or bool(re.search(r"password|secret|token|key|^env$", key, re.I)))
        elif isinstance(value, (list, tuple)):
            for item in value:
                self.remember(item, secret)
        elif secret and isinstance(value, str) and value:
            self.client.secrets.append(value)

    def app(self, identifier):
        try:
            uuid.UUID(identifier)
            return identifier, None
        except ValueError:
            if self.options.dry_run:
                raise CliError("Use the app UUID with --dry-run; resolving names would require a network request") from None
        apps = self.request("GET", "/launchpad/apps").get("apps", [])
        matches = [a for a in apps if identifier in (a.get("id"), a.get("name"), a.get("slug"))]
        if len(matches) != 1:
            raise CliError("App not found or ambiguous; run app list and use its UUID")
        return matches[0]["id"], matches[0]

    def app_detail(self, identifier):
        app_id, app = self.app(identifier)
        if self.options.dry_run:
            raise CliError("This command needs current app details; omit --dry-run")
        return app or self.request("GET", f"/launchpad/apps/{segment(app_id)}")["app"]


def confirm(options, message):
    if options.yes or options.dry_run:
        return
    if not sys.stdin.isatty() or input(f"{message} [y/N] ").strip().lower() not in ("y", "yes"):
        raise CliError("Cancelled; pass --yes to confirm")


def create_body(args):
    name = args.name.strip().lower()
    if not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,23}[a-z0-9])?", name):
        raise CliError("App name must contain 1–25 lowercase letters, digits or hyphens, with no leading/trailing hyphen")
    params = object_input(args.params) if args.params else {}
    params.update(pairs(args.param, typed=True))
    params.update({k: env_value(v) for k, v in pairs(args.param_env).items()})
    if args.port is not None:
        params["port"] = args.port
    if args.health_path is not None:
        params["healthPath"] = args.health_path
    body = {"name": name, "template": args.template, "params": params}
    if args.size:
        body["size"] = args.size
    if bool(args.storage_path) != (args.storage_size is not None):
        raise CliError("Use --storage-path and --storage-size together")
    if args.storage_path:
        if not args.storage_path.startswith("/") or args.storage_path == "/" or ".." in args.storage_path:
            raise CliError("Storage path must be absolute, must not be /, and must not contain ..")
        body["storage"] = {"path": args.storage_path, "size_gi": args.storage_size}
    return body


def wait_release(ctx, path, release_id, args):
    deadline = time.monotonic() + args.wait_timeout
    while True:
        status = ctx.request("GET", path + "/status")
        release = status.get("release") or {}
        if release.get("id") != release_id:
            raise CliError("This release was replaced by another operation; inspect app status")
        if status.get("app", {}).get("status") == "running" and release.get("rollout_status") == "ready":
            return status
        if release.get("rollout_status") in ("failed", "timeout", "superseded", "stopped"):
            raise CliError("Release did not become ready: " + str(release.get("failure") or release.get("rollout_reason") or release["rollout_status"]))
        if time.monotonic() >= deadline:
            raise CliError("Timed out waiting for the rollout; deployment continues on the server. Check app status")
        time.sleep(min(args.interval, max(0, deadline - time.monotonic())))


def env_command(ctx, args, path):
    if args.env_action == "list":
        return ctx.request("GET", path + "/env"), None
    if args.env_action == "replace":
        confirm(ctx.options, "Replace the entire environment? Omitted variables will be removed.")
        body = object_input(args.data)
        if not isinstance(body.get("env"), dict) or set(body) - {"env", "sensitive"}:
            raise CliError('Expected {"env": {"KEY": "value"}, "sensitive": ["KEY"]}')
    else:
        updates = pairs(args.values) if args.env_action == "set" else {}
        if args.env_action == "set":
            updates.update({k: env_value(v) for k, v in pairs(args.from_env).items()})
            if not updates:
                raise CliError("Provide KEY=VALUE or --from-env KEY=ENV_VAR")
        if ctx.options.dry_run:
            raise CliError("env set/unset needs a read/merge/write; use env replace --data @file with --dry-run")
        current = ctx.request("GET", path + "/env").get("vars", [])
        body = {"env": {v["key"]: v["value"] for v in current}, "sensitive": [v["key"] for v in current if v["sensitive"]]}
        if args.env_action == "set":
            body["env"].update(updates)
            body["sensitive"] = sorted(set(body["sensitive"]) | set(args.sensitive))
        else:
            confirm(ctx.options, "Remove these variables and reapply the current app?")
            for key in args.keys:
                body["env"].pop(key, None)
            body["sensitive"] = [k for k in body["sensitive"] if k in body["env"]]
    return ctx.request("PUT", path + "/env", body), None


def run_docker(argv, *, password=None):
    # Do not hand the user's platform credentials to Docker/build subprocesses.
    env = {k: v for k, v in os.environ.items() if not re.search(r"(?:KEY|TOKEN|PASSWORD|SECRET)$", k, re.I)}
    try:
        subprocess.run(argv, input=password, text=True, env=env, check=True,
                       stdout=sys.stderr, stderr=sys.stderr)
    except FileNotFoundError:
        raise CliError("Docker was not found; install Docker and start its engine") from None
    except subprocess.CalledProcessError:
        raise CliError("Docker failed; no explicit deployment request was sent") from None


def push_app(ctx, args, path):
    app = ctx.app_detail(args.app)
    if app.get("source") != "user-image":
        raise CliError("app push is for blank/user-image apps; GitHub apps build through their repository workflow")
    if args.wait and not args.deploy:
        raise CliError("--wait requires --deploy; omit --deploy when registry webhooks already auto-deploy")
    image = f"{app['image']}:{args.tag}"
    if args.login:
        username = (app.get("push_robot") or {}).get("username")
        if not username:
            raise CliError("No push username; rotate the app's push credentials first")
        password = getpass.getpass("Registry password (hidden): ")
        run_docker(["docker", "login", app["image"].split("/", 1)[0], "--username", username, "--password-stdin"], password=password + "\n")
    command = ["docker", "buildx", "build", "--platform", "linux/amd64", "--provenance=false", "--push", "-t", image]
    if args.dockerfile:
        command += ["-f", args.dockerfile]
    command += [args.context]
    run_docker(command)
    if not args.deploy:
        return {"pushed": image, "note": "A registry webhook may auto-deploy this push; inspect app status."}, None
    result = ctx.request("POST", path + "/deploy", {"tag": args.tag})
    if args.wait:
        result = wait_release(ctx, path, result["release"]["id"], args)
    return result, None


def app_command(ctx, args):
    if args.action == "list":
        return ctx.request("GET", "/launchpad/apps"), "apps"
    if args.action == "create":
        return ctx.request("POST", "/launchpad/apps", create_body(args)), "created"
    app_id, _ = ctx.app(args.app)
    path = f"/launchpad/apps/{segment(app_id)}"
    if args.action in ("get", "status", "events", "releases", "logs", "artifacts"):
        suffix = "" if args.action == "get" else "/" + args.action
        query = {"tail": args.tail, "previous": args.previous} if args.action == "logs" else {"refresh": args.refresh} if args.action == "artifacts" else None
        return ctx.request("GET", path + suffix, query=query), "logs" if args.action == "logs" else None
    if args.action == "docker":
        return {"app": ctx.app_detail(app_id)}, "docker"
    if args.action == "open":
        app = ctx.app_detail(app_id)
        url = app.get("public_url") or app.get("url")
        if not url or not url.startswith("https://"):
            raise CliError("No HTTPS app URL is available")
        webbrowser.open(url)
        return {"url": url}, None
    if args.action == "delete":
        confirm(ctx.options, "Delete this app and its data? Deleting a hub also deletes its executors.")
        return ctx.request("DELETE", path), None
    if args.action in ("start", "stop", "restart"):
        return ctx.request("POST", path + "/" + args.action), None
    if args.action in ("deploy", "rollback"):
        if args.action == "rollback":
            body = {"release_id": args.release_id}
        elif args.tag or args.digest:
            body = {"tag": args.tag} if args.tag else {"digest": args.digest}
        else:
            if ctx.options.dry_run:
                raise CliError("Pass --tag or --digest with --dry-run")
            app = ctx.app_detail(app_id)
            if app.get("source") == "image":
                if app.get("image_tag"):
                    body = {"tag": app["image_tag"]}
                else:
                    release = ctx.request("GET", path + "/status").get("release") or {}
                    if not release.get("digest"):
                        raise CliError("No pinned release is available; inspect app status")
                    body = {"digest": release["digest"]}
            else:
                body = {"tag": "latest"}
        if args.action == "deploy" and args.port is not None:
            body["port"] = args.port
        result = ctx.request("POST", path + "/" + args.action, body)
        if args.wait and not ctx.options.dry_run:
            result = wait_release(ctx, path, result["release"]["id"], args)
        return result, None
    if args.action == "push":
        return push_app(ctx, args, path)
    if args.action == "env":
        return env_command(ctx, args, path)
    if args.action == "port":
        return ctx.request("PUT", path + "/port", {"port": args.port}), None
    if args.action == "health":
        value = None if args.tcp else args.path
        if value is not None and not re.fullmatch(r"/[\x21-\x7e]{0,199}", value):
            raise CliError("Health path must start with /, contain no spaces and be at most 200 characters")
        return ctx.request("PUT", path + "/health", {"path": value}), None
    if args.action == "storage":
        return ctx.request("PUT", path + "/storage", {"size_gi": args.size}), None
    if args.action == "credentials":
        confirm(ctx.options, "Rotate push credentials? The previous credentials will be revoked.")
        body = {"githubToken": env_value(args.github_token_env)} if args.github_token_env else {}
        return ctx.request("POST", path + "/robots/push/rotate", body), "credentials"
    if args.action == "domains":
        suffix = "/domains"
        if args.domain_action == "list":
            return ctx.request("GET", path + suffix), None
        if args.domain_action == "add":
            return ctx.request("POST", path + suffix, {"hostname": args.hostname}), None
        suffix += f"/{args.domain_id}"
        if args.domain_action == "verify":
            return ctx.request("POST", path + suffix + "/verify"), None
        confirm(ctx.options, "Remove this domain from the app?")
        return ctx.request("DELETE", path + suffix), None
    raise CliError("Unknown app command")


def operations():
    return json.loads(importlib.resources.files("xo_launchpad").joinpath("operations.json").read_text(encoding="utf-8"))


def user_operation(method, path):
    """Raw API access stays within the bundled user deployment routes."""
    from urllib.parse import unquote
    decoded = unquote(path)
    if decoded != path or "\\" in path or any(part in (".", "..") for part in path.split("/")):
        return False
    for operation in operations()["operations"]:
        if operation["audience"] != "user" or operation["method"] != method:
            continue
        parts = operation["path"].split("/")
        pattern = "/".join(r"[^/?#\\]+" if p.startswith("{") and p.endswith("}") else re.escape(p) for p in parts)
        if re.fullmatch(pattern, path):
            return True
    return False


def main(argv=None):
    options = None
    ctx = None
    try:
        options, rest = global_options(list(sys.argv[1:] if argv is None else argv))
        parser = build_parser()
        if not rest:
            parser.print_help()
            return 0
        args = parser.parse_args(rest)
        if args.group == "config":
            print(render({"url": selected_url(options.url), "credential_source": credential_source(),
                          "settings_file": str(config_path()), "xo_credentials_file": str(xo_credentials_path())}, as_json=True))
            return 0
        auth_action = args.auth_action if args.group == "auth" else "login" if args.group in ("login", "signin") else "logout" if args.group == "logout" else None
        if auth_action == "logout":
            confirm(options, "Sign out of shared XO authentication? Other XO tools will also lose their saved login.")
            if options.dry_run:
                print(render({"command": ["xo", "logout"], "shared": True}, as_json=True))
            else:
                auth.run_xo(["logout"])
                print(render({"shared_xo_logout": True, "environment_key_still_set": bool(os.environ.get(KEY_ENV))}, as_json=True))
            return 0
        if auth_action == "login":
            url = selected_url(options.url)
            command = ["login", "xo", "--timeout", str(options.timeout)]
            if args.key:
                command += ["--key"]
            if args.xo_url:
                command += ["--url", normalize_url(args.xo_url)]
            if options.dry_run:
                print(render({"command": ["xo", *command], "launchpad_url": url}, as_json=True))
                return 0
            auth.run_xo(command)
            key, source = shared_key(args.xo_url)
            ctx = type("LoginContext", (), {"client": Client(url, key, timeout=options.timeout)})()
            try:
                ctx.client.request("GET", "/launchpad/apps")
            except CliError as error:
                raise CliError("XO login completed, but Launchpad access was not verified. "
                               "The services must accept the same Clerk user key. " + str(error)) from None
            save_settings(url)
            print(render({"authenticated": True, "url": url, "credential_source": source}, as_json=True))
            return 0
        if args.group == "api":
            if not user_operation(args.method, args.path):
                raise CliError("Raw requests are limited to supported user deployment endpoints")
        ctx = Context(options)
        if auth_action == "status":
            result = ctx.request("GET", "/launchpad/apps")
            data, kind = ({"authenticated": True, "url": ctx.url, "credential_source": credential_source()}, None) if not options.dry_run else (result, None)
        elif args.group == "templates":
            data, kind = ctx.request("GET", "/launchpad/templates"), "templates"
            if args.name and not options.dry_run:
                matches = [t for t in data["templates"] if t["name"] == args.name]
                if not matches:
                    raise CliError("Template not found; run templates to see the catalog")
                data, kind = matches[0], None
        elif args.group in ("app", "apps"):
            data, kind = app_command(ctx, args)
        else:
            if args.method == "DELETE":
                confirm(options, "Delete this deployment resource?")
            data, kind = ctx.request(args.method, args.path, object_input(args.data) if args.data else None, pairs(args.query)), None
        print(render(data, as_json=options.json or options.dry_run, kind=kind,
                     shell=getattr(args, "shell", None), tag=getattr(args, "tag", None) or "latest"))
        return 0
    except KeyboardInterrupt:
        print("Cancelled. If a request was sent, check app list/status before retrying.", file=sys.stderr)
        return 130
    except (CliError, OSError, ValueError) as error:
        secrets = [os.environ.get(KEY_ENV, "")]
        if ctx is not None and ctx.client is not None:
            secrets += ctx.client.secrets
        print("Error: " + str(redact(str(error), secrets)), file=sys.stderr)
        return 1
