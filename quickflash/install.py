"""Install the fixed NixOS ZFS base using the prepared USB's local payload."""

import argparse
import fcntl
import hashlib
import json
from pathlib import Path
import secrets
import shutil
import subprocess
import sys
import time

from bootstrap.provision import read_private
from quickflash import __version__, disk, probe, settings, wifi

MANIFEST = Path("/etc/quickflash-install/payload.json")
ROOT = Path("/mnt")
LOCK = Path("/run/quickflash-install.lock")
LOG = Path("/var/log/quickflash-install.log")
PROVISION = Path("/var/lib/fabric-provision")
STAGE = Path("/run/quickflash/nixos")
APPROVAL = Path("/run/quickflash/approval.json")
PROFILE = Path("/run/quickflash/install-wifi.nmconnection")


def run(*args, step, **kwargs):
    print(f"{step}...", file=sys.stderr, flush=True)
    started = time.monotonic()
    data = kwargs.pop("input", None)
    with LOG.open("a+b") as log:
        log.write(("\n" + step + "\n").encode())
        log.flush()
        with subprocess.Popen(
            [str(arg) for arg in args],
            stdin=subprocess.PIPE if data is not None else subprocess.DEVNULL,
            stdout=log,
            stderr=subprocess.STDOUT,
            **kwargs,
        ) as process:
            while True:
                try:
                    process.communicate(input=data, timeout=10)
                    break
                except subprocess.TimeoutExpired:
                    data = None
                    print(
                        f"{step}: running ({time.monotonic() - started:.0f}s)",
                        file=sys.stderr,
                        flush=True,
                    )
            if process.returncode:
                log.seek(max(0, log.tell() - 4000))
                print(log.read().decode(errors="replace"), file=sys.stderr)
                raise ValueError(f"{step} failed; full log: {LOG}")


def provisioned_identity():
    if read_private(PROVISION / "layout-version") != "1\n":
        raise ValueError("Unsupported USB provisioning layout")
    options = PROVISION / "install-options.json"
    value = (
        json.loads(read_private(options))
        if options.exists() or options.is_symlink()
        else {}
    )
    if not isinstance(value, dict) or set(value) - settings.INSTALL_FIELDS:
        raise ValueError("Invalid USB install options")
    value.update(
        name=read_private(PROVISION / "hostname").strip(),
        ssh_keys=read_private(PROVISION / "authorized_keys").splitlines(),
    )
    return settings.validate(value)


def stage_digest():
    digest = hashlib.sha256()
    for path in sorted(STAGE.rglob("*")):
        if path.is_file():
            digest.update(str(path.relative_to(STAGE)).encode() + b"\0")
            digest.update(path.read_bytes() + b"\0")
    digest.update(PROFILE.read_bytes() if PROFILE.exists() else b"no-wifi")
    return digest.hexdigest()


def stage(user, root_hashed_password=None, wifi_connection=None):
    with LOCK.open("w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        APPROVAL.unlink(missing_ok=True)
        if not Path("/sys/firmware/efi").is_dir():
            raise ValueError("Boot the prepared USB in UEFI mode")
        provisioned = provisioned_identity()
        identity = dict(provisioned)
        user = settings.user(user)
        if "user" in provisioned:
            expected = provisioned["user"]
            if (expected is None and user is not None) or (
                expected is not None
                and (user is None or any(user.get(k) != v for k, v in expected.items()))
            ):
                raise ValueError("Provisioned user changed; start flash again")
        identity["user"] = user
        if user is None:
            if root_hashed_password is not None:
                raise ValueError("Root-only installs do not accept a password")
        else:
            if "hashed_password" not in user:
                raise ValueError("Login user requires a password before approval")
            identity["root_hashed_password"] = settings.password_hash(
                root_hashed_password, "Root password"
            )
        identity["host_id"] = secrets.token_hex(4)
        payload = json.loads(MANIFEST.read_text())
        profile = wifi.export(wifi_connection)
        PROFILE.unlink(missing_ok=True)
        shutil.rmtree(STAGE, ignore_errors=True)
        shutil.copytree(payload["configuration"], STAGE)
        STAGE.chmod(0o700)
        if profile is not None:
            PROFILE.touch(mode=0o600)
            PROFILE.write_text(profile)
        path = STAGE / "identity.json"
        path.touch(mode=0o600)
        path.write_text(json.dumps(identity, indent=2) + "\n")
        hardware = subprocess.check_output(
            ["nixos-generate-config", "--show-hardware-config", "--no-filesystems"],
            text=True,
        )
        (STAGE / "hardware-configuration.nix").write_text(hardware)
        LOG.touch(mode=0o600)
        run(
            "nix-instantiate",
            STAGE / "system.nix",
            "-A",
            "config.system.build.toplevel",
            "-I",
            "nixpkgs=" + payload["nixpkgs"],
            step="Evaluating the host configuration before erasure",
        )
        approval = {
            "payload": payload,
            "provisioned": provisioned,
            "identity": identity,
            "stage_digest": stage_digest(),
        }
        APPROVAL.touch(mode=0o600)
        APPROVAL.write_text(json.dumps(approval) + "\n")
        return identity


def inspect(device):
    if not Path("/sys/firmware/efi").is_dir():
        raise ValueError("Boot the prepared USB in UEFI mode")
    payload = json.loads(MANIFEST.read_text())
    approval = json.loads(read_private(APPROVAL))
    if (
        approval["payload"] != payload
        or approval["provisioned"] != provisioned_identity()
        or approval["stage_digest"] != stage_digest()
    ):
        raise ValueError(
            "USB settings, payload or staged configuration changed; stage again"
        )
    selected = disk.inspect_disk(device)
    if selected["size"] < 4 * 1024**3 or selected["log-sec"] != 512:
        raise ValueError("Target needs at least 4 GiB and 512-byte sectors")
    pools = subprocess.check_output(["zpool", "list", "-H", "-o", "name"], text=True)
    if payload["storage"]["pool"] in pools.splitlines():
        raise ValueError(
            "The installation ZFS pool is already imported; inspect it first"
        )
    return {**approval, "disk": selected}


def copy_payload(payload):
    cache = Path(payload["cache"])
    for source, index, step in (
        ("local", "shared-paths", "Copying shared packages from USB store"),
        (
            "file://" + str(cache),
            "cached-paths",
            "Copying remaining packages from USB cache",
        ),
    ):
        paths = (cache / index).read_text()
        if paths:
            run(
                "nix",
                "copy",
                "--from",
                source,
                "--to",
                f"local?root={ROOT}",
                "--no-check-sigs",
                "--no-recursive",
                "--stdin",
                input=paths,
                text=True,
                step=step,
            )


def install(device, approved):
    with LOCK.open("w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if inspect(device) != approved:
            raise ValueError(
                "USB payload or disk changed since approval; nothing written"
            )
        if any(
            Path(entry.split()[1]).is_relative_to(ROOT)
            for entry in Path("/proc/mounts").read_text().splitlines()
        ):
            raise ValueError("Installation root is already mounted; inspect it first")
        profile = read_private(PROFILE) if PROFILE.exists() else None
        print(f"Install log: {LOG}", file=sys.stderr, flush=True)
        payload = approved["payload"]
        storage = payload["storage"]
        if inspect(device) != approved:
            raise ValueError(
                "Disk or payload changed during preflight; nothing written"
            )
        run(
            "sfdisk",
            "--lock=nonblock",
            "--no-tell-kernel",
            "--wipe",
            "always",
            "--wipe-partitions",
            "always",
            device,
            input=f"label: gpt\nsize={storage['espSize']},type=U,name={storage['espLabel']}\ntype=L,name={storage['zfsLabel']}\n",
            text=True,
            step="Partitioning disk",
        )
        run("blockdev", "--rereadpt", device, step="Reading partition table")
        run("udevadm", "settle", "--timeout=30", step="Waiting for partition devices")
        table = json.loads(
            subprocess.check_output(
                ["lsblk", "--json", "--paths", "-o", "NAME,PARTLABEL", device],
                text=True,
            )
        )["blockdevices"][0]["children"]
        partitions = {entry["partlabel"]: entry["name"] for entry in table}
        esp, root = partitions[storage["espLabel"]], partitions[storage["zfsLabel"]]
        run("mkfs.vfat", "-F", "32", esp, step="Formatting EFI partition")
        ROOT.mkdir(exist_ok=True)
        pool = storage["pool"]
        options = ["-o", "cachefile=none"]
        for key, value in storage["poolOptions"].items():
            options.extend(["-o", f"{key}={value}"])
        for key, value in storage["rootOptions"].items():
            options.extend(["-O", f"{key}={value}"])
        run(
            "zpool",
            "create",
            "-R",
            ROOT,
            *options,
            pool,
            root,
            step="Creating ZFS pool",
        )
        mounted = False
        try:
            for name, dataset in storage["datasets"].items():
                options = []
                for key, value in dataset["options"].items():
                    options.extend(["-o", f"{key}={value}"])
                run(
                    "zfs",
                    "create",
                    "-u",
                    "-p",
                    *options,
                    f"{pool}/{name}",
                    step=f"Creating {pool}/{name}",
                )
            for mountpoint, name in sorted(
                (dataset["mountpoint"], name)
                for name, dataset in storage["datasets"].items()
                if "mountpoint" in dataset
            ):
                destination = ROOT / mountpoint.lstrip("/")
                destination.mkdir(parents=True, exist_ok=True)
                run(
                    "mount",
                    "-t",
                    "zfs",
                    f"{pool}/{name}",
                    destination,
                    step=f"Mounting {mountpoint}",
                )
                mounted = True
            (ROOT / "boot").mkdir()
            run("mount", esp, ROOT / "boot", step="Mounting EFI partition")
            copy_payload(payload)
            destination = ROOT / "etc/nixos"
            shutil.copytree(STAGE, destination)
            destination.chmod(0o755)
            run(
                "nixos-install",
                "--file",
                destination / "system.nix",
                "--root",
                ROOT,
                "--no-root-passwd",
                "--no-channel-copy",
                "-I",
                "nixpkgs=" + payload["nixpkgs"],
                step="Building and installing NixOS",
            )
            wifi.install(ROOT, profile)
            key = ROOT / "etc/ssh/ssh_host_ed25519_key"
            key.parent.mkdir(parents=True, exist_ok=True)
            run(
                "ssh-keygen",
                "-q",
                "-t",
                "ed25519",
                "-N",
                "",
                "-f",
                key,
                step="Generating the installed host SSH key",
            )
            public = key.with_suffix(".pub").read_text().split()[:2]
        finally:
            failed = sys.exc_info()[0] is not None
            cleanup = [("zpool", "export", pool)]
            if mounted:
                cleanup.insert(0, ("umount", "--recursive", ROOT))
            error = None
            for command in cleanup:
                try:
                    run(
                        *command,
                        step="Unmounting target"
                        if command[0] == "umount"
                        else "Exporting ZFS pool",
                    )
                except (ValueError, OSError, subprocess.CalledProcessError) as caught:
                    print(f"Cleanup failed: {caught}", file=sys.stderr, flush=True)
                    error = caught
            if error is not None and not failed:
                raise error
    print("QUICKFLASH_HOST_KEY " + " ".join(public))
    print("QUICKFLASH_NIXOS_INSTALLED")


def main():
    parser = argparse.ArgumentParser(prog="quickflash-install", description=__doc__)
    parser.add_argument(
        "--version", action="version", version=f"%(prog)s {__version__}"
    )
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("inventory")
    prepare = sub.add_parser("stage")
    prepare.add_argument("--wifi-connection")
    sub.add_parser("inspect").add_argument("device")
    write = sub.add_parser("install")
    write.add_argument("device")
    args = parser.parse_args()
    if args.command == "inventory":
        print(json.dumps({**probe.collect(), "settings": provisioned_identity()}))
    elif args.command == "stage":
        accounts = json.load(sys.stdin)
        print(
            json.dumps(
                stage(
                    accounts["user"],
                    accounts["root_hashed_password"],
                    args.wifi_connection,
                )
            )
        )
    elif args.command == "inspect":
        print(json.dumps(inspect(args.device)))
    else:
        install(args.device, json.load(sys.stdin))


if __name__ == "__main__":
    try:
        main()
    except (ValueError, OSError, subprocess.CalledProcessError) as error:
        print(str(error), file=sys.stderr)
        sys.exit(1)
