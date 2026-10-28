"""XO is the login owner. Launchpad delegates to its installed CLI, never implements OAuth."""
import shutil
import subprocess
import sys

from .client import CliError


def run_xo(arguments):
    executable = shutil.which("xo")
    if not executable:
        raise CliError("The xo CLI is required for shared login. Install quirq-ai/xo and put xo on PATH, then run auth login again; XO_API_KEY also works for automation")
    try:
        # XO owns prompts/browser login and writes its own credentials. Its key never appears in argv.
        subprocess.run([executable, *arguments], check=True, stdout=sys.stderr, stderr=sys.stderr)
    except FileNotFoundError:
        raise CliError("Could not start xo; check that the xo CLI is installed on PATH") from None
    except subprocess.CalledProcessError as error:
        raise CliError(f"xo did not complete successfully (exit {error.returncode}); see its output above") from None
