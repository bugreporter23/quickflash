"""Shared command-line parsing and failure reporting."""

import os
from pathlib import Path
import subprocess
import sys

from . import __version__


def ssh_inputs(parser):
    parser.add_argument(
        "--ssh-config", type=Path, help="Caller OpenSSH configuration (ssh -F FILE)"
    )


def parse_args(parser):
    parser.add_argument(
        "--version", action="version", version=f"%(prog)s {__version__}"
    )
    if "_ARGCOMPLETE" in os.environ:
        import argcomplete

        argcomplete.autocomplete(parser)
    return parser.parse_args()


def run(main, program):
    try:
        main()
    except (EOFError, KeyboardInterrupt):
        print(f"{program}: cancelled.", file=sys.stderr)
        sys.exit(130)
    except (
        ValueError,
        OSError,
        subprocess.CalledProcessError,
        subprocess.TimeoutExpired,
    ) as error:
        print(f"{program}: {error}", file=sys.stderr)
        sys.exit(1)
