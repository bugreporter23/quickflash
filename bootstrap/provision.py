"""Apply the USB's runtime hostname before SSH, NetworkManager, and Avahi start."""

import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import sys


def read_private(path):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd) as file:
        metadata = os.fstat(file.fileno())
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid != 0
            or metadata.st_mode & 0o077
        ):
            raise ValueError(
                "Provisioning files must be root-owned private regular files"
            )
        return file.read()


def main():
    root = Path("/var/lib/fabric-provision")
    if read_private(root / "layout-version") != "1\n":
        raise ValueError("Unsupported USB provisioning layout")
    for name, destination in (
        ("system-connections", Path("/run/NetworkManager/system-connections")),
        ("ssh", Path("/run/quickflash/ssh")),
    ):
        path = root / name
        if path.is_symlink():
            raise ValueError("Refusing a symlinked provisioning directory")
        destination.mkdir(mode=0o700, parents=True, exist_ok=True)
        shutil.copytree(path, destination, dirs_exist_ok=True, symlinks=True)
        destination.chmod(0o700)
    authorized = root / "authorized_keys"
    if authorized.exists() or authorized.is_symlink():
        content = read_private(authorized)
        destination = Path("/run/quickflash/authorized_keys")
        destination.write_text(content)
        destination.chmod(0o600)
    name = "fabric-bootstrap"
    path = root / "hostname"
    if path.exists() or path.is_symlink():
        name = read_private(path).strip()
        if not re.fullmatch(r"[a-z][a-z0-9-]{0,52}", name) or name.endswith("-"):
            raise ValueError("Invalid bootstrap hostname")
        name += "-bootstrap"
    subprocess.run([sys.argv[1], "--transient", "set-hostname", name], check=True)


if __name__ == "__main__":
    main()
