"""Write the demo host's settings, once: `deploy/aws/operate.sh env-init` runs this.

The host's .env is .env.example with every generated secret fresh (scripts/init_env.py), so it
shares no secret with a laptop. It takes over this checkout's model and tracing settings from
./.env, turns on the demo timing, and adds the proxy's sign-in. The sign-in itself goes to a
second file, for people; the host only gets its hash.

Usage: python3 deploy/aws/host_env.py --site <host>.sslip.io --out <private directory>
"""

from __future__ import annotations

import argparse
import json
import os
import runpy
import secrets
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
# The image that hashes the password is the one that checks it (deploy/aws/compose.aws.yml).
CADDY = "caddy:2.11.9-alpine"
# What the demo runs with, taken over from ./.env when it is set there.
CARRIED = (
    "OPENAI_API_KEY",
    "CARE_LLM_OPENAI_MODEL",
    "LANGSMITH_TRACING",
    "LANGSMITH_API_KEY",
    "LANGSMITH_PROJECT",
    "LANGSMITH_ENDPOINT",
)


def read_env(path: Path) -> dict[str, str]:
    """The KEY=value lines of an env file; comments and blank lines are skipped."""
    if not path.exists():
        return {}
    values = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            key, value = line.split("=", 1)
            values[key.strip().removeprefix("export ").strip()] = value.strip()
    return values


def with_values(text: str, values: dict[str, str]) -> str:
    """The env text with each key's line replaced, or appended; every other line kept."""
    lines, seen = [], set()
    for line in text.splitlines():
        key = line.split("=", 1)[0].strip()
        if not line.lstrip().startswith("#") and key in values:
            lines.append(f"{key}={values[key]}")
            seen.add(key)
        else:
            lines.append(line)
    lines += [f"{key}={value}" for key, value in values.items() if key not in seen]
    return "\n".join(lines) + "\n"


def bcrypt(password: str) -> str:
    """The password's bcrypt hash, as Caddy's basic_auth expects it."""
    docker = shutil.which("docker")
    if docker is None:
        raise SystemExit("docker is needed to hash the password with Caddy")
    # Every argument is a constant; the password goes in on stdin, not on the command line.
    result = subprocess.run(  # noqa: S603
        [docker, "run", "--rm", "-i", CADDY, "caddy", "hash-password"],
        input=password + "\n",
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.strip()


def write_private(path: Path, text: str) -> None:
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(text)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--site", required=True, help="the host's name, for the certificate")
    parser.add_argument("--out", required=True, type=Path, help="an empty private directory")
    args = parser.parse_args()

    render = runpy.run_path(str(ROOT / "scripts" / "init_env.py"))["render"]
    text = render((ROOT / ".env.example").read_text(encoding="utf-8"))
    local = read_env(ROOT / ".env")
    password = secrets.token_urlsafe(12)
    values = {
        # Staff sign in through the token endpoint with the staff key, which exists only in dev.
        "CARE_ENV": "dev",
        "CARE_LLM_PROVIDER": "openai" if local.get("OPENAI_API_KEY") else "offline",
        # The demo timing of `make demo`: a visit is offered after 20 s, not 10 minutes.
        "CARE_DISPATCH_HOLD_SECONDS": "20",
        "CARE_NETWORK_RECHECK_SECONDS": "10",
        **{key: local[key] for key in CARRIED if local.get(key)},
        "LANGSMITH_HIDE_INPUTS": "true",
        "LANGSMITH_HIDE_OUTPUTS": "true",
        "SITE_ADDRESS": args.site,
        "DEMO_USER": "demo",
        # In single quotes: the hash holds "$", which Compose would otherwise read as a variable.
        "DEMO_PASSWORD_HASH": f"'{bcrypt(password)}'",
        "DEMO_GATE_COOKIE": secrets.token_hex(32),
    }
    sign_in = {"site": f"https://{args.site}", "user": "demo", "password": password}

    args.out.mkdir(mode=0o700, parents=True, exist_ok=True)
    write_private(args.out / "env", with_values(text, values))
    write_private(args.out / "sign-in.json", json.dumps(sign_in, indent=2) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
