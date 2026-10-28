# XO Launchpad CLI

Create apps, get Docker commands, and manage deployments from your terminal.
Uses the same sign-in as [xo](https://github.com/quirq-ai/xo).

## Install

Requires Python 3.11+ and Git. Install in a Python virtual environment:

```sh
python -m pip install "xo-launchpad @ git+https://github.com/quirq-ai/launchpad.git"
```

Works on Windows, Linux, and macOS. Docker is needed to build and push images.
For local development, clone this repository and run `python -m pip install -e .`.

## Sign in

Install the [xo CLI](https://github.com/quirq-ai/xo) and make sure `xo` is on PATH.

```sh
xo-launchpad auth login
xo-launchpad auth status
```

Already signed in through `xo`? Start using Launchpad directly. Both tools share the saved login.
`xo-launchpad auth logout` signs out of that shared login too.

## Create an app

```sh
xo-launchpad app create my-app --port 3000
```

Creates a blank app and prints its URL, a one-time registry password, and Docker commands:

```sh
docker login registry.xo.builders -u YOUR_PUSH_USERNAME
docker build --platform linux/amd64 -t YOUR_IMAGE:latest .
docker push YOUR_IMAGE:latest
```

The CLI fills in the real username and image. Run the commands from your app's Dockerfile folder;
your server must listen on `0.0.0.0` and the configured port. Save the registry password for Docker login.
Pushing deploys automatically when the server's registry webhook is configured; otherwise run
`xo-launchpad app deploy my-app --tag latest --wait`.

```sh
xo-launchpad app docker my-app     # print the Docker commands again
xo-launchpad app status my-app
xo-launchpad app logs my-app
```

## Commands

`APP` accepts an app UUID, exact name, or slug.

| Command | What it does |
| --- | --- |
| `auth login / status / logout` | Sign in, check access, or sign out through XO |
| `templates [NAME]` | List templates or inspect their parameters |
| `app list` | List your apps |
| `app create NAME` | Create an app; defaults to the blank template |
| `app get / status / logs / events APP` | Inspect an app and its runtime |
| `app docker APP` | Print Docker login, build, and push commands |
| `app push APP` | Build and push using your local Docker installation |
| `app deploy / rollback APP` | Deploy an image or restore a previous release |
| `app releases / artifacts APP` | Inspect release history or available images |
| `app start / stop / restart APP` | Control the app lifecycle |
| `app env / port / health / storage` | Configure the app through each command's subcommands |
| `app domains` | Manage domains through its subcommands |
| `app credentials rotate APP` | Replace the app's push credentials |
| `app open / delete APP` | Open the app URL or delete the app and its data |
| `config` | Show API settings and credential source |
| `api METHOD PATH` | Call a supported user deployment endpoint directly |

The table groups related commands. Use command help for the exact syntax:

```sh
xo-launchpad
xo-launchpad app --help
xo-launchpad app create --help
```

Global options work before or after commands: `--json`, `--url`, `--timeout`, `--dry-run`, and `--yes`.
The default API is **https://launchpad-beta-api.xo.builders**.

## More documentation

- [User guide](docs/guide.md): templates, GitHub workflows, build/push, configuration, domains, and automation.
- [API reference](docs/api.md): supported deployment endpoints.

## Development

```sh
python -m pip install -e .
python -m unittest discover -s tests -v
```

Tests use local mocks; they do not sign in to XO or create live deployments.
