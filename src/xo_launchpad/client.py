"""The only network boundary: authenticated requests to a single configured origin."""
from __future__ import annotations

from dataclasses import dataclass, field
import json
import math
import os
import re
import stat
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

from . import __version__

DEFAULT_URL = "https://launchpad-beta-api.xo.builders"
KEY_ENV = "XO_API_KEY"
URL_ENV = "XO_LAUNCHPAD_URL"
KEY_RE = re.compile(r"[A-Za-z0-9_.+/=-]+\Z")


class CliError(Exception):
    """A user-facing error, never an unfiltered traceback or credential."""


def normalize_url(value: str) -> str:
    try:
        p = urllib.parse.urlsplit(value.strip().rstrip("/"))
        port = p.port
        local = p.hostname in {"localhost", "127.0.0.1", "::1"}
        if (not p.hostname or p.username is not None or p.password is not None or p.query or p.fragment
                or p.path not in ("", "/") or port == 0
                or not (p.scheme == "https" or (p.scheme == "http" and local))):
            raise ValueError
        return urllib.parse.urlunsplit((p.scheme, p.netloc.lower(), "", "", ""))
    except ValueError:
        raise CliError("API URL must be an HTTPS origin (HTTP is allowed only on localhost), without credentials or a path") from None


def check_key(value: str) -> str:
    value = value.strip()
    if not value or not KEY_RE.fullmatch(value):
        raise CliError("API key is missing or contains invalid characters; set XO_API_KEY or run xo-launchpad auth login")
    return value


def config_path() -> Path:
    if os.environ.get("XO_LAUNCHPAD_CONFIG"):
        return Path(os.environ["XO_LAUNCHPAD_CONFIG"]).expanduser()
    base = os.environ.get("XDG_CONFIG_HOME")
    return (Path(base) if base and Path(base).is_absolute() else Path.home() / ".config") / "xo-launchpad" / "config.json"


def read_settings() -> dict:
    path = config_path()
    if not path.exists():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(value, dict) or not isinstance(value.get("url"), str):
            raise ValueError
        return {"url": normalize_url(value["url"])}  # Legacy key fields are deliberately ignored.
    except (OSError, ValueError):
        raise CliError("Could not read Launchpad settings; check XO_LAUNCHPAD_CONFIG") from None


def save_settings(url: str) -> None:
    """Save only the approved Launchpad URL. XO owns all saved API keys."""
    path = config_path()
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, temporary = tempfile.mkstemp(prefix=".credentials-", dir=path.parent)
    try:
        os.chmod(temporary, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as out:
            json.dump({"url": normalize_url(url)}, out)
            out.write("\n")
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def xo_credentials_path() -> Path:
    """The documented credential file owned and written by `xo login`. Read-only here."""
    base = os.environ.get("XDG_CONFIG_HOME")
    return (Path(base) if base and Path(base).is_absolute() else Path.home() / ".config") / "xo" / "credentials"


@dataclass(frozen=True)
class XoLogin:
    url: str
    key: str = field(repr=False)


def read_xo_login() -> XoLogin | None:
    path = xo_credentials_path()
    fd = None
    try:
        # Keep XO's file ownership and bounded-read protections. Never modify this file.
        if path.is_symlink():
            raise CliError("XO credentials must be a regular private file; run xo login to repair it")
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0))
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode):
            raise CliError("XO credentials are not a regular file; run xo login to repair them")
        if os.name != "nt" and (st.st_mode & 0o077 or st.st_uid != os.getuid()):
            raise CliError("XO credentials are not private to this user; repair permissions or run xo login")
        with os.fdopen(fd, "r", encoding="utf-8") as source:
            fd = None
            raw = source.read(8193)
        if len(raw) > 8192:
            raise ValueError
        data = json.loads(raw)
        if (not isinstance(data, dict) or not isinstance(data.get("url"), str)
                or not isinstance(data.get("key"), str) or not data["key"].startswith("ak_")):
            raise ValueError
        return XoLogin(normalize_url(data["url"]), check_key(data["key"]))
    except FileNotFoundError:
        return None
    except (OSError, ValueError, UnicodeError):
        raise CliError("Could not read valid XO credentials; run xo login again") from None
    finally:
        if fd is not None:
            os.close(fd)


def shared_key(xo_url: str | None = None) -> tuple[str, str]:
    """Return the explicit automation key, else XO's shared key and its owner service URL."""
    if os.environ.get(KEY_ENV, "").strip():
        return check_key(os.environ[KEY_ENV]), "XO_API_KEY"
    saved = read_xo_login()
    if saved is None:
        raise CliError("Not signed in to XO; run xo-launchpad auth login (or xo login), or set XO_API_KEY")
    selected = xo_url or os.environ.get("XO_URL")
    if selected and normalize_url(selected) != saved.url:
        raise CliError("XO_URL does not match the saved XO login; run xo login for that server")
    return saved.key, "xo login"


def credential_source() -> str:
    if os.environ.get(KEY_ENV, "").strip():
        return KEY_ENV
    return "xo login" if read_xo_login() else "none"


def selected_url(flag: str | None = None) -> str:
    settings = read_settings()
    return normalize_url(flag or os.environ.get(URL_ENV) or settings.get("url") or DEFAULT_URL)


def resolve_credentials(url_flag: str | None) -> tuple[str, str]:
    url = selected_url(url_flag)
    key, source = shared_key()
    approved_url = normalize_url(read_settings().get("url") or DEFAULT_URL)
    if source == "xo login" and url != approved_url:
        raise CliError("The shared XO key is not approved for this Launchpad URL; run auth login --url URL to verify and approve it, or explicitly set XO_API_KEY")
    return url, key


def redact(value: Any, secrets: tuple[str, ...] | list[str]) -> Any:
    """Remove credentials even when echoed in a successful response or an error."""
    if isinstance(value, str):
        for secret in sorted({s for s in secrets if s}, key=len, reverse=True):
            for form in {secret, urllib.parse.quote(secret, safe=""), json.dumps(secret)[1:-1]}:
                value = value.replace(form, "[redacted]")
        return value
    if isinstance(value, list):
        return [redact(v, secrets) for v in value]
    if isinstance(value, dict):
        return {redact(str(k), secrets): redact(v, secrets) for k, v in value.items()}
    return value


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class Client:
    def __init__(self, url: str, key: str, *, timeout: float = 30, opener=None):
        self.url = normalize_url(url)
        self.key = check_key(key)
        if not math.isfinite(timeout) or timeout <= 0:
            raise CliError("--timeout must be a positive finite number of seconds")
        self.timeout = timeout
        self.opener = opener or urllib.request.build_opener(NoRedirect())
        self.secrets = [self.key]

    def request(self, method: str, path: str, body=None, query: dict | None = None):
        if not path.startswith("/") or path.startswith("//") or "?" in path or "#" in path or "\\" in path:
            raise CliError("API path must be relative to the configured server, with query values passed separately")
        url = self.url + path
        if query:
            url += "?" + urllib.parse.urlencode({k: str(v).lower() if isinstance(v, bool) else v for k, v in query.items() if v is not None})
        data = json.dumps(body, ensure_ascii=False).encode("utf-8") if body is not None else None
        request = urllib.request.Request(url, data=data, method=method.upper(), headers={
            "Authorization": f"Bearer {self.key}", "Accept": "application/json",
            "User-Agent": f"xo-launchpad/{__version__}",
            **({"Content-Type": "application/json"} if data is not None else {}),
        })
        try:
            with self.opener.open(request, timeout=self.timeout) as response:
                raw = response.read().decode("utf-8", errors="replace")
                if not raw.strip():
                    return None
                try:
                    return redact(json.loads(raw), [self.key])
                except ValueError:
                    raise CliError("API returned a non-JSON response; check the API URL") from None
        except urllib.error.HTTPError as error:
            code = error.code
            if 300 <= code < 400:
                raise CliError("API redirected the request; no credentials were forwarded. Set the final API origin explicitly") from None
            if code == 401:
                hint = "authentication rejected; check XO_API_KEY"
            elif code == 403:
                hint = "access denied for this key"
            else:
                try:
                    payload = json.loads(error.read().decode("utf-8", errors="replace"))
                    detail = payload.get("detail", payload) if isinstance(payload, dict) else payload
                    hint = detail if isinstance(detail, str) else json.dumps(detail, ensure_ascii=False)
                except (ValueError, OSError):
                    hint = "request failed"
            hints = {409: " Another change may be in progress; reload before retrying.",
                     429: " An app/domain limit or rate limit was reached.",
                     502: " The backend did not finish; inspect app status before retrying."}
            raise CliError(redact(f"HTTP {code}: {hint}{hints.get(code, '')}", self.secrets)) from None
        except (urllib.error.URLError, TimeoutError, OSError):
            suffix = " The operation may have succeeded; check app list/status before retrying." if method.upper() != "GET" else ""
            raise CliError("Could not reach Launchpad within the request timeout." + suffix) from None


def segment(value: str) -> str:
    return urllib.parse.quote(str(value), safe="")
