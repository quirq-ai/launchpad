"""Terminal rendering and copyable commands. XO_API_KEY never enters a Docker command."""
import json
import os
import re
import shlex

from .client import CliError


def shell_quote(value: str, shell: str) -> str:
    # Normal Docker references need no quoting, matching the Launchpad UI.
    if re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9_./:@+=-]*", value):
        return value
    if shell == "powershell":
        return "'" + value.replace("'", "''") + "'"
    return shlex.quote(value)


def docker_commands(app: dict, registry: dict | None = None, *, shell: str | None = None, tag="latest") -> list[str]:
    shell = shell or ("powershell" if os.name == "nt" else "posix")
    if app.get("source") == "image":
        return []
    registry = registry or {}
    repository = registry.get("repository") or app.get("image")
    username = registry.get("username") or (app.get("push_robot") or {}).get("username")
    if not repository or not username:
        raise CliError("Push credentials are unavailable; use app credentials rotate for a blank app")
    server = registry.get("server") or repository.split("/", 1)[0]
    q = lambda v: shell_quote(v, shell)
    image = f"{repository}:{tag}"
    return [f"docker login {q(server)} -u {q(username)}",
            f"docker build --platform linux/amd64 -t {q(image)} .",
            f"docker push {q(image)}"]


def table(rows: list[dict], columns: list[tuple[str, str]]) -> str:
    if not rows:
        return "No results."
    def clean(value):
        return " ".join(str(value if value is not None else "-").split())
    values = [[clean(row.get(key)) for key, _ in columns] for row in rows]
    titles = [title for _, title in columns]
    widths = [max(len(titles[i]), *(len(row[i]) for row in values)) for i in range(len(columns))]
    return "\n".join("  ".join(v.ljust(widths[i]) for i, v in enumerate(row)).rstrip() for row in [titles, *values])


def render(data, *, as_json=False, kind=None, shell=None, tag="latest") -> str:
    if as_json:
        return json.dumps(data, indent=2, ensure_ascii=False)
    if kind == "apps":
        return table(data.get("apps", []), [("id", "ID"), ("name", "NAME"), ("status", "STATUS"), ("public_url", "URL")])
    if kind == "templates":
        return table(data.get("templates", []), [("name", "NAME"), ("displayName", "TEMPLATE"), ("source", "SOURCE"), ("version", "VERSION")])
    if kind == "logs":
        return data.get("logs", "")
    if kind in ("created", "docker", "credentials"):
        app = data.get("app", {})
        registry = data.get("registry") or {}
        if kind == "credentials":
            if not registry:
                return "Push credentials rotated; GitHub repository secrets updated."
            app = {"image": registry.get("repository"), "source": "user-image"}
        lines = []
        if app.get("id"):
            lines += [f"App: {app.get('name', app['id'])}", f"ID:  {app['id']}",
                      f"URL: {app.get('public_url') or app.get('url') or '-'}"]
        if data.get("notes"):
            lines += ["", str(data["notes"])]
        # GitHub creation hands credentials directly to the repository, not the caller.
        if app.get("source") == "github" and kind == "created":
            lines += ["", "GitHub workflow configured; pushes to the selected branch build and deploy."]
        else:
            commands = docker_commands(app, registry, shell=shell, tag=tag)
            if commands:
                lines += ["", "Run these from the folder containing your Dockerfile (listen on the configured app port):", "", *commands]
                if registry.get("password"):
                    lines += ["", "Registry password (shown once; paste at the docker login prompt):", registry["password"]]
                else:
                    lines += ["", "At the login prompt, use the registry password issued at creation or credential rotation."]
                lines += ["", "Push auto-deploys only when the registry webhook is configured; otherwise use app deploy."]
        return "\n".join(lines)
    return json.dumps(data, indent=2, ensure_ascii=False)
