"""Operator CLI; credentials stay in environment, never command arguments."""

import argparse
import json
import os
from pathlib import Path

import yaml
from deerflow_extension_api import ExtensionPrincipal

from .adapter import project, verify_projection
from .registry import Capability, Registry


def main():
    parser = argparse.ArgumentParser(description="Enterprise registry operator tools")
    parser.add_argument("--database-url-env", default="ENTERPRISE_DB_URL")
    commands = parser.add_subparsers(dest="command", required=True)
    seed = commands.add_parser("import-json")
    seed.add_argument("file", type=Path)
    for name in ("activate", "deactivate"):
        command = commands.add_parser(name)
        command.add_argument("id")
        if name == "activate":
            command.add_argument("version")
    deploy = commands.add_parser("project")
    deploy.add_argument("--team", required=True)
    deploy.add_argument("--base", type=Path, required=True)
    deploy.add_argument("--destination", type=Path, required=True)
    verify = commands.add_parser("verify")
    verify.add_argument("bundle", type=Path)
    member = commands.add_parser("membership")
    member.add_argument("--team", required=True)
    member.add_argument("--user-id", required=True)
    member.add_argument("--remove", action="store_true")
    args = parser.parse_args()
    if args.command == "verify":
        print(verify_projection(args.bundle)["revision"])
        return
    url = os.environ.get(args.database_url_env)
    if not url:
        parser.error("The configured registry database environment variable is missing")
    registry = Registry(url)
    principal = ExtensionPrincipal("operator-cli", is_admin=True)
    if args.command == "import-json":
        items = json.loads(args.file.read_text(encoding="utf-8"))
        if not isinstance(items, list) or len(items) > 10000:
            parser.error("Supply a list of at most 10,000 capability revisions")
        # Validate all definitions before writing the first revision.
        capabilities = [Capability.model_validate(item) for item in items]
        with registry.transaction() as db:
            for item in capabilities:
                registry._register(db, item, principal.user_id)
        print(f"Registered {len(items)} immutable revisions; no activation performed")
    elif args.command == "activate":
        print(registry.activate(args.id, args.version, principal)["digest"])
    elif args.command == "deactivate":
        registry.deactivate(args.id, principal)
        print(f"Deactivated {args.id}")
    elif args.command == "project":
        base = yaml.safe_load(args.base.read_text(encoding="utf-8"))
        print(project(registry, args.team, base, args.destination, database_url_env=args.database_url_env))
    elif args.command == "membership":
        registry.set_membership(args.team, args.user_id, not args.remove, principal)
        print("Team membership updated")


if __name__ == "__main__":
    main()
