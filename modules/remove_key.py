"""Remove Quickflash's root authorized keys from the installed configuration."""

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile


def remove_keys(path):
    identity = json.loads(path.read_text())
    identity["ssh_keys"] = []
    with tempfile.NamedTemporaryFile(mode="w", dir=path.parent, delete=False) as file:
        temporary = Path(file.name)
        try:
            json.dump(identity, file, indent=2)
            file.write("\n")
            file.flush()
            os.fsync(file.fileno())
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)


def main():
    if len(sys.argv) != 1:
        raise ValueError("Usage: sudo quickflash-remove-key")
    if os.geteuid() != 0:
        raise ValueError("Run with sudo or as root")
    remove_keys(Path("/etc/nixos/identity.json"))
    print(
        "Root's Quickflash keys removed from identity.json; activating configuration...",
        flush=True,
    )
    subprocess.run(["nixos-rebuild", "switch"], check=True)
    print("Root's Quickflash keys removed. Separately configured keys are unchanged.")


if __name__ == "__main__":
    try:
        main()
    except (ValueError, OSError, subprocess.CalledProcessError) as error:
        print(f"quickflash-remove-key: {error}", file=sys.stderr)
        sys.exit(1)
