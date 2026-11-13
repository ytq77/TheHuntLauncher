#!/usr/bin/env python3
"""Interactively register and verify an account on this recipe's private shadNet."""
import argparse
import configparser
import getpass
import json
from pathlib import Path
import re
import subprocess

ROOT = Path(__file__).resolve().parent


def invoke(host, port, action, *values):
    fields = {"register": ("name", "password", "email", "key"), "login": ("name", "password")}
    if action not in fields or len(values) != len(fields[action]):
        raise ValueError("Unsupported account request")
    command = ["docker", "compose", "exec", "-T", "shadnet", "shadnet-admin", host, port, action]
    try:
        result = subprocess.run(command, cwd=ROOT, capture_output=True, text=True,
                                encoding="utf-8", errors="replace", timeout=35,
                                input=json.dumps(dict(zip(fields[action], values)), ensure_ascii=True) + "\n")
    except (OSError, subprocess.SubprocessError):
        raise ValueError(f"{action} did not finish; check service health and the stdin helper version before retrying.") from None
    try:
        response = json.loads(next((row for row in reversed(result.stdout.splitlines())
                                    if row.startswith('{"ok":')), '{}'))
    except (ValueError, TypeError):
        response = {}
    if result.returncode != 0 or not isinstance(response, dict) or response.get("ok") is not True:
        raise ValueError(f"{action}: no verified success response; inspect service health and shadnet-admin version")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--user", help="optional account name; password is always prompted")
    parser.add_argument("--email", help="optional unique email address")
    args = parser.parse_args()
    try:
        config = configparser.ConfigParser(interpolation=None)
        config.read(ROOT / "shadnet-state" / "shadnet.cfg", encoding="utf-8")
        general = config["General"]
        host, port = general["Host"], general["UnsecuredPort"]
        secret = general["RegistrationSecretKey"]
        if not secret or "__" in secret:
            raise ValueError("Run prepare.py first; a registration secret is required.")
        name = args.user or input("Account name (3-16 ASCII letters/digits/_/-): ").strip()
        if not re.fullmatch(r"[A-Za-z0-9_-]{3,16}", name) or name == "DeletedUser":
            raise ValueError("Invalid account name.")
        email = args.email or input("Unique email address: ").strip()
        if "@" not in email or "." not in email:
            raise ValueError("Use a unique email address containing @ and a dot.")
        password = getpass.getpass("Password: ")
        if not password or password != getpass.getpass("Repeat password: "):
            raise ValueError("Passwords were empty or did not match.")
        invoke(host, port, "register", name, password, email, secret)
        invoke(host, port, "login", name, password)
        print(f"Account {name}: registered and authenticated.")
    except (OSError, ValueError, KeyError, configparser.Error) as error:
        parser.exit(1, f"Account creation stopped: {error}\n")
    except (EOFError, KeyboardInterrupt):
        parser.exit(1, "\nCancelled.\n")


if __name__ == "__main__":
    main()
