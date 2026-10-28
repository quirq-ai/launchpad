# XO Launchpad user guide

For installation and a quick start, see the [README](../README.md).

## Shared authentication with XO

Install your organization's [xo CLI](https://github.com/quirq-ai/xo) and make sure `xo` is on PATH
(`xo --version` checks this without starting a login). **XO owns authentication.** Launchpad does not
implement another OAuth flow or save another copy of your key.

These commands are the same in CMD, PowerShell, Bash and Zsh:

```sh
xo-launchpad auth login
xo-launchpad auth status
xo-launchpad app list
```

`auth login` delegates to **`xo login xo`**. XO handles browser sign-in (or its manual-key fallback),
verifies your identity, and saves its credentials. Launchpad then reads that shared key, checks
`GET /launchpad/apps`, and saves only the approved Launchpad API URL. Both services must accept the
same Clerk user API key; a failed access check is reported without copying or replacing XO credentials.

If you already ran `xo login`, you can use Launchpad directly:

```sh
xo login xo
xo-launchpad app list
```

The shared key is read from XO's documented `~/.config/xo/credentials` file, or
`$XDG_CONFIG_HOME/xo/credentials` when that directory is configured. Launchpad never writes this file.
It checks that the file is a regular file and, on POSIX, belongs to the current user with private
permissions. It also honors XO's `XO_URL` selection rather than using a key saved for a different XO
server. Browser callbacks, key expiry and sign-in behavior belong to the installed XO CLI.

```sh
xo-launchpad auth login --key              # let XO prompt for an API key
xo-launchpad auth login --xo-url https://xo.quirq.dev
xo-launchpad auth status                   # checks Launchpad access; never prints a key
xo-launchpad config                        # shows API URL, key source and settings paths
xo-launchpad auth logout                    # shared sign-out, after confirmation
```

`auth logout` delegates to `xo logout`, so it affects other tools using the same shared XO login.
It does not revoke or unset an explicit `XO_API_KEY` environment variable. `--yes` skips Launchpad's
confirmation; XO remains in charge of any prompts it owns. The older `login`, `signin` and `logout`
commands remain aliases for their `auth` equivalents. `whoami` was replaced by `auth status`; the
internal endpoint inventory is not a public command.

### Environment override for automation

Every user request still uses `Authorization: Bearer XO_API_KEY`. An explicit environment variable
wins over the shared XO login and requires no `xo` executable for ordinary API commands.

When switching back to shared XO login, clear this override: `set XO_API_KEY=` in CMD,
`Remove-Item Env:XO_API_KEY` in PowerShell, or `unset XO_API_KEY` in Bash/Zsh.

### API URL and local settings

The default API is **https://launchpad-beta-api.xo.builders**. URL precedence is `--url`,
`XO_LAUNCHPAD_URL`, the approved URL in local settings, then the default. Only HTTPS origins are
accepted, except HTTP localhost for development; API redirects are refused.

```sh
xo-launchpad --url https://another-launchpad.example auth login
```

This explicitly verifies and approves a new API origin before sending the shared key there on normal
commands. Changing `--url` or `XO_LAUNCHPAD_URL` alone does not silently forward a shared key to a new
origin. The default beta API is trusted by this CLI. `--xo-url` and `XO_URL` configure XO's own login
server, not the Launchpad deployment API.

Local Launchpad settings live in `~/.config/xo-launchpad/config.json` (under `XDG_CONFIG_HOME` when
configured); they contain only `{"url": "https://launchpad-beta-api.xo.builders"}`.
`XO_LAUNCHPAD_CONFIG` can select another settings file for testing or automation. The CLI does not
load `.env` files. Keys are never printed or embedded in Docker commands.

**Upgrading from 0.1:** Launchpad's old private `credentials.json` login is no longer used. Run
`xo-launchpad auth login` to use shared XO authentication. No private Launchpad API key is migrated
into XO, and this CLI does not delete or modify old credential files automatically.

## Create an app and get Docker commands

```sh
xo-launchpad app create my-workspace --template blank --port 3000
```

The response prints your app ID/URL, these commands with the real repository and username, and the
one-time **registry password** to paste at Docker's password prompt:

```sh
docker login registry.xo.builders -u xo-my-workspace-xo-xxxxxxx+push
docker build --platform linux/amd64 -t registry.xo.builders/my-workspace-xo-xxxxxxx/app:latest .
docker push registry.xo.builders/my-workspace-xo-xxxxxxx/app:latest
```

Run them in your application's folder containing its Dockerfile. Its server must listen on `0.0.0.0`
and the configured port. Creating a blank app provisions the namespace and registry; it does not
build or start your local code. Pushing auto-deploys only if the backend's Harbor webhook is configured.
Otherwise, run `app deploy` explicitly.
The `linux/amd64` build target is required on every host, including Apple Silicon Macs: it describes the
Launchpad runtime, not your laptop's operating system.

Registry passwords are different from XO_API_KEY. Creation and credential rotation deliberately display
the registry password once, including in `--json` output. The CLI does **not** persist it. Treat that
output as sensitive; don't paste it into logs or issues.

Print the commands again later (GET only; never silently rotates credentials):

```sh
xo-launchpad app docker my-workspace
xo-launchpad app docker my-workspace --shell powershell
xo-launchpad app docker my-workspace --shell posix
xo-launchpad app credentials rotate my-workspace --yes
```

Normal Docker references print without quotes, matching the Launchpad app. Values needing quoting
use PowerShell syntax on Windows and POSIX syntax on Linux/macOS; `--shell` overrides that choice.

Existing-app GET responses contain the repository and robot username, not the password. Use the password
you already saved, or explicitly rotate it. Rotation revokes old push credentials. Fixed-image template
apps have no app-specific push credentials.

## Build/push from the CLI

```sh
# Uses Docker's existing registry login; builds for linux/amd64 and pushes.
xo-launchpad app push my-workspace --context .

# Prompt for the registry password, then build/push and explicitly deploy/wait.
xo-launchpad app push my-workspace --login --context . --deploy --wait
```

`app push` runs `docker buildx build --platform linux/amd64 --provenance=false --push` using argument
arrays, never a shell. `--dockerfile PATH` and `--tag TAG` are supported. Platform credentials are stripped
from Docker's environment. Prefer a context with a `.dockerignore` excluding credentials and `.env` files.

If registry webhooks already auto-deploy, omit `--deploy`: an explicit request could otherwise create a
second release. `--wait` on push requires `--deploy`, so it can track the exact returned release ID.

## Templates and GitHub

```sh
xo-launchpad templates
xo-launchpad templates github            # parameters, defaults, sizes, allowed options
```

Windows PowerShell:

```powershell
$env:GITHUB_TOKEN = "your-github-token"
xo-launchpad app create my-repo --template github --port 3000 `
    --param repoUrl=https://github.com/your-org/your-repo `
    --param branch=main --param-env githubToken=GITHUB_TOKEN
```

Linux/macOS (Bash/Zsh):

```sh
export GITHUB_TOKEN="your-github-token"
xo-launchpad app create my-repo --template github --port 3000 \
    --param repoUrl=https://github.com/your-org/your-repo \
    --param branch=main --param-env githubToken=GITHUB_TOKEN
```

Other template examples (all platforms):

```sh
xo-launchpad app create database --template your-template --params '@params.json'
xo-launchpad app create with-disk --port 8080 --storage-path /data --storage-size 5
```

Templates come from the authenticated catalog; available names depend on what your server has installed.
`--param KEY=VALUE` parses JSON numbers, booleans, arrays and objects. `--param-env PARAM=ENV_VAR` keeps
secret values out of command arguments. `--params` accepts a JSON object, `@file`, or `-` for stdin.
GitHub creation gives the repository its workflow and credentials; it does not show Docker credentials.

## App commands

`APP` accepts an app UUID, exact name, or slug. Names/slugs are resolved within the authenticated user's
app list; no user ID is sent in the body. Hubs and executors appear in that list like other apps.

```sh
xo-launchpad app list
xo-launchpad app get APP
xo-launchpad app status APP
xo-launchpad app logs APP --tail 100 --previous
xo-launchpad app events APP
xo-launchpad app artifacts APP --refresh
xo-launchpad app releases APP
xo-launchpad app deploy APP --tag latest --wait
xo-launchpad app deploy APP --digest sha256:YOUR_64_HEX_DIGEST --port 8080
xo-launchpad app rollback APP RELEASE_ID --wait
xo-launchpad app stop APP
xo-launchpad app start APP
xo-launchpad app restart APP
xo-launchpad app open APP
xo-launchpad app delete APP --yes
```

`app deploy` without a selector uses `latest` for your own images and the pinned image for fixed-image
templates. `--wait-timeout` defaults to 600 seconds and `--interval` to 2. Waiting checks the exact release;
it fails if another operation replaces that release. Timing out locally does not cancel the server rollout.

Delete removes the app's resources and data. Deleting a hub also invokes the backend's executor cleanup.
DELETE commands, environment replacement/removal and credential rotation require confirmation or `--yes`.
A failed delete can leave the app `deleting`; inspect it and retry Delete when the underlying service recovers.

## Environment, health, port and storage

```sh
xo-launchpad app env list APP
xo-launchpad app env set APP NODE_ENV=production
```

Set a secret in the current shell, then let the CLI read it by environment variable name.

Windows PowerShell:

```powershell
$env:MY_DB_PASSWORD = "your-secret"
```

Linux/macOS:

```sh
export MY_DB_PASSWORD="your-secret"
```

The remaining commands are the same on every platform:

```sh
xo-launchpad app env set APP --from-env DB_PASSWORD=MY_DB_PASSWORD --sensitive DB_PASSWORD
xo-launchpad app env unset APP OLD_VARIABLE --yes
xo-launchpad app env replace APP --data '@environment.json' --yes

xo-launchpad app port set APP 8080
xo-launchpad app health set APP --path /healthz
xo-launchpad app health set APP --tcp
xo-launchpad app storage resize APP 10
```

The backend's environment PUT **replaces the whole set**. `set` and `unset` first fetch it and preserve
other keys, including `null` placeholders for write-only secrets. This is a read/merge/write, not an atomic
patch: avoid concurrent environment edits from another client. `replace` intentionally sends your exact set.

`environment.json` contains `{"env": {"NODE_ENV": "production", "TOKEN": null}, "sensitive": ["TOKEN"]}`.
Null keeps a stored value; omission removes it. Template-protected keys cannot be removed. Env changes
reapply the current release. Mark secrets `--sensitive`; unmarked values can be returned by the backend.
Storage grows only, up to the API's 20 GiB namespace limit; it cannot be added to an app that has no disk.

## Domains

```sh
xo-launchpad app domains list APP
xo-launchpad app domains add APP shop.example.com
xo-launchpad app domains verify APP DOMAIN_ID
xo-launchpad app domains remove APP DOMAIN_ID --yes
```

Add returns DNS instructions. Verify checks ownership/routing; listing observes certificate status.
The CLI exposes the server's behavior and does not fix its custom-domain concurrency or retry defects.

## Automation and raw API access

Global flags work before or after a command: `--json`, `--url`, `--timeout`, `--dry-run`, `--yes`.
JSON responses go to stdout and errors to stderr; exit 0 means the command succeeded, 1 means failure,
and 130 means interruption. Secrets returned intentionally by the API (registry password, readable env)
remain in JSON output; the authentication key is always redacted.

```sh
xo-launchpad app list --json
xo-launchpad app create preview --port 3000 --dry-run
xo-launchpad app deploy APP_UUID --tag latest --dry-run
xo-launchpad api GET /launchpad/apps
xo-launchpad api POST /launchpad/apps/APP_UUID/deploy --data '{"tag":"latest"}'
```

Dry runs perform no network requests and need no key. Use UUIDs rather than names; commands needing
live details or a read/merge/write explain why a dry run isn't available. The bundled endpoint map is
internal; see [the endpoint reference](api.md). Legacy adapters are reachable through `api`; normal
commands use canonical `/launchpad/*` routes. Harbor webhooks are service callbacks, not CLI operations.
Only listed user deployment operations are accepted by `api`; template publication and management are
outside this CLI's scope. All API commands use the user's `XO_API_KEY`.

For GitHub push-robot rotation: `app credentials rotate APP --github-token-env GITHUB_TOKEN --yes`.

## Verification and API contract

After activating the environment on any platform:

```sh
python -m unittest discover -s tests -v
```

Tests use a local HTTP mock, isolated temporary config files and fake Docker/API clients. They exercise
every canonical user endpoint, user-only API scope, secret preservation, Docker command quoting, HTTP failures,
redirect refusal, one-time credentials, delegation to XO, shared credential ownership and logout
propagation. No live deployments or real XO logins are created by the test suite.

The contract is sourced from `xo-swarm-api` branch `feat/launchpad-v2` at
[`fc15278`](https://github.com/sharmasuraj0123/xo-swarm-api/tree/fc15278/launchpad), not from guessed
routes. See [the endpoint map](api.md). Agents/executor endpoints added only on the hub branch are
outside this v2 CLI's named commands. Install from GitHub; this package is not published to PyPI.
