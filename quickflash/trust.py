"""Public SSH key validation and private file paths; never edits SSH trust."""

import base64
from pathlib import Path
import re
import subprocess
import tempfile


def validate_public_keys(values):
    keys = []
    with tempfile.TemporaryDirectory(prefix="quickflash-public-") as tmp:
        public = Path(tmp) / "key.pub"
        for value in values:
            if not re.fullmatch(
                r"(ssh-ed25519|ssh-rsa|ecdsa-sha2-\S+|sk-\S+) [A-Za-z0-9+/]+=*( .*)?",
                value.strip(),
            ):
                raise ValueError(
                    "Expected plain public keys without authorized-key options"
                )
            kind, blob, *_ = value.split()
            key = (
                kind
                + " "
                + base64.b64encode(base64.b64decode(blob, validate=True)).decode()
            )
            public.write_text(key + "\n")
            subprocess.run(
                ["ssh-keygen", "-l", "-f", str(public)],
                check=True,
                capture_output=True,
                timeout=10,
            )
            if key not in keys:
                keys.append(key)
    if not keys:
        raise ValueError("At least one administrator public key is required")
    return keys


def regular(path, *, missing=False):
    path = Path(path).absolute()
    for part in [path, *path.parents]:
        if part.is_symlink():
            raise ValueError(f"Symlinked operation/trust path is unsupported: {part}")
    if path.exists() and not path.is_file():
        raise ValueError(f"Expected a regular file: {path}")
    if not missing and not path.exists():
        raise ValueError(f"Missing file: {path}")
    return path


def public_key(value):
    parts = value.split()
    if (
        len(parts) < 2
        or parts[0] != "ssh-ed25519"
        or not re.fullmatch(r"[A-Za-z0-9+/]+=*", parts[1])
    ):
        raise ValueError("Expected a plain Ed25519 server public key")
    return validate_public_keys([" ".join(parts[:2])])[0]
