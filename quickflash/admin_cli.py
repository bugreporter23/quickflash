"""Quickadmin: scoped mDNS/SSH administration and runtime inspection."""

import argparse
import json
import os
import sys

from . import cli, operations as f, transport


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command")
    sub.add_parser("shell", help="Open the scoped administration shell (default)")
    inspect = sub.add_parser("inspect", help="Show hardware and candidate disks")
    inspect.add_argument("target", type=f.target)
    inspect.add_argument("--json", action="store_true", help="Full hardware report")
    cli.ssh_inputs(inspect)
    health = sub.add_parser(
        "health", help="Check current services, networking and storage"
    )
    health.add_argument("target", type=f.target)
    health.add_argument("--json", action="store_true", help="Structured health report")
    cli.ssh_inputs(health)
    args = cli.parse_args(parser)
    if args.command in (None, "shell"):
        if not sys.stdin.isatty():
            raise ValueError(
                "The administration shell requires an interactive terminal"
            )
        profile = transport.select_profile()
        os.execvpe(
            "bash",
            ["bash", "--noprofile", "--norc", "-i"],
            dict(transport.environment(profile), PS1=r"quickadmin \u@\h:\w\$ "),
        )
    else:
        profile = transport.select_profile(args.ssh_config)
        with transport.operation(profile) as args.connection:
            if args.command == "health":
                f.health(args)
            else:
                info = f.probe(args.target, args.connection)
                if args.json:
                    print(json.dumps(info, indent=2))
                else:
                    print("Inspecting " + args.target + "\n")
                    f.show_probe(info)
                    print("\nUse inspect --json for the complete hardware report.")
