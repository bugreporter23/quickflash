"""Ordinary OpenSSH configuration, with bounded automation overrides."""

import contextlib
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import tempfile


def quote(value):
    value = str(value)
    if any(c in value for c in '\n\r\0"\\'):
        raise ValueError("Unsupported character in SSH configuration value")
    return '"' + value.replace("%", "%%") + '"'


def select_profile(path=None):
    if path is None:
        locator = shutil.which("quickflash-ssh-config")
        if locator is None:
            raise ValueError("Run inside nix develop or supply --ssh-config FILE")
        path = subprocess.check_output([locator], text=True, timeout=5).strip()
    path = Path(path).expanduser().resolve()
    quote(path)
    if not path.is_file():
        raise ValueError(f"SSH configuration does not exist: {path}")
    return path


def effective(target, profile):
    result = subprocess.run(
        ["ssh", "-G", "-F", str(profile), target],
        check=True,
        capture_output=True,
        text=True,
        timeout=10,
    )
    settings = {}
    for line in result.stdout.splitlines():
        key, value = line.split(" ", 1)
        settings.setdefault(key, []).append(value)
    return {
        key: values if key == "identityfile" else values[0]
        for key, values in settings.items()
    }


def forget_host(target, profile):
    settings = effective(target, profile)
    lookup = settings.get("hostkeyalias")
    if not lookup:
        lookup = settings["hostname"]
        if settings["port"] != "22":
            lookup = f"[{lookup}]:{settings['port']}"
    for name in shlex.split(settings["userknownhostsfile"]):
        path = Path(name).expanduser()
        if name != "none" and path.is_file():
            subprocess.run(
                ["ssh-keygen", "-R", lookup, "-f", str(path)],
                check=True,
                capture_output=True,
                text=True,
                timeout=5,
            )


@contextlib.contextmanager
def operation(profile, *, connect_timeout=15, pin=None, lookup=None):
    with tempfile.TemporaryDirectory(prefix="quickflash-ssh-", dir="/tmp") as tmp:
        config = Path(tmp) / "config"
        text = f"Host *\n  BatchMode yes\n  PreferredAuthentications publickey\n  StrictHostKeyChecking yes\n  UpdateHostKeys no\n  ForwardAgent no\n  ControlMaster auto\n  ControlPersist 10m\n  ConnectTimeout {connect_timeout}\n  ConnectionAttempts 1\n  ServerAliveInterval 5\n  ServerAliveCountMax 2\n"
        text += f'  ControlPath "{tmp}/mux-%C"\n'
        if lookup:
            text += "  HostKeyAlias " + quote(lookup) + "\n"
        if pin is not None:
            trust = Path(tmp) / "known_hosts"
            trust.write_text(lookup + " " + pin.strip() + "\n")
            trust.chmod(0o600)
            text += f"  UserKnownHostsFile {quote(trust)}\n  GlobalKnownHostsFile /dev/null\n  KnownHostsCommand none\n  VerifyHostKeyDNS no\n  UpdateHostKeys no\n  HostKeyAlgorithms ssh-ed25519\n"
        text += "  Include " + quote(profile) + "\n"
        config.write_text(text)
        config.chmod(0o600)
        try:
            yield config
        finally:
            for socket in Path(tmp).glob("mux-*"):
                try:
                    subprocess.run(
                        [
                            "ssh",
                            "-F",
                            str(config),
                            "-S",
                            str(socket),
                            "-O",
                            "exit",
                            "localhost",
                        ],
                        stdin=subprocess.DEVNULL,
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.PIPE,
                        text=True,
                        check=True,
                        timeout=5,
                    )
                except (OSError, subprocess.SubprocessError) as error:
                    print(
                        f"quickflash: SSH cleanup failed for {socket}: {error}",
                        file=sys.stderr,
                    )


def remote(target, command, config, *, timeout=30, input=None, stdin=None, live=False):
    try:
        result = subprocess.run(
            ["ssh", "-F", str(config), target, command],
            input=input,
            stdin=stdin,
            stdout=subprocess.PIPE,
            stderr=None if live else subprocess.PIPE,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as error:
        detail = error.stderr or ""
        if isinstance(detail, bytes):
            detail = detail.decode(errors="replace")
        raise ValueError(
            f"SSH operation on {target} exceeded {timeout}s: {detail[-4000:].strip()}"
        ) from error
    if result.returncode:
        detail = (result.stderr or "")[-4000:].strip()
        raise ValueError(
            f"SSH operation on {target} exited {result.returncode}: {detail}"
        )
    return result.stdout


def authorize(target, config):
    """Frontload OpenSSH's normal first-contact prompt; never open a remote shell."""
    print(f"Connecting to {target}...", flush=True)
    subprocess.run(
        [
            "ssh",
            "-F",
            str(config),
            "-oBatchMode=no",
            "-oStrictHostKeyChecking=ask",
            "-T",
            "--",
            target,
            "true",
        ],
        check=True,
    )


def environment(config):
    return dict(os.environ, NIX_SSHOPTS=shlex.join(["-F", str(config)]))
