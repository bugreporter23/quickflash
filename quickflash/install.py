"""Install the fixed NixOS ZFS base using the prepared USB's local payload."""

import argparse
import fcntl
import json
import os
import re
from pathlib import Path
import shutil
import subprocess
import sys
import time

from bootstrap.provision import read_private
from quickflash import disk, probe, trust, wifi

MANIFEST = Path("/etc/quickflash-install/payload.json")
ROOT = Path("/mnt")
LOCK = Path("/run/quickflash-install.lock")
LOG = Path("/var/log/quickflash-install.log")
PROVISION = Path("/var/lib/fabric-provision")


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
    name = read_private(PROVISION / "hostname").strip()
    if (
        not re.fullmatch(r"[a-z][a-z0-9-]{0,52}", name)
        or name.endswith("-")
        or name in ("test", "bootstrap")
    ):
        raise ValueError(
            "Provision the USB with a valid machine name before installation"
        )
    keys = trust.validate_public_keys(
        read_private(PROVISION / "authorized_keys").splitlines()
    )
    return {"name": name, "ssh_keys": keys}


def inspect(device):
    if not Path("/sys/firmware/efi").is_dir():
        raise ValueError("Boot the prepared USB in UEFI mode")
    payload = json.loads(MANIFEST.read_text())
    identity = provisioned_identity()
    selected = disk.inspect_disk(device)
    if selected["size"] < 4 * 1024**3 or selected["log-sec"] != 512:
        raise ValueError("Target needs at least 4 GiB and 512-byte sectors")
    pools = subprocess.check_output(["zpool", "list", "-H", "-o", "name"], text=True)
    if payload["storage"]["pool"] in pools.splitlines():
        raise ValueError(
            "The installation ZFS pool is already imported; inspect it first"
        )
    return {"payload": payload, "disk": selected, "identity": identity}


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


def install(device, approved, wifi_connection=None):
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
        profile = wifi.export(wifi_connection)
        fd = os.open(LOG, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        os.close(fd)
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
            shutil.copytree(ROOT / payload["configuration"].lstrip("/"), destination)
            identity = destination / "identity.json"
            identity.write_text(json.dumps(approved["identity"]) + "\n")
            identity.chmod(0o600)
            run(
                "nixos-generate-config",
                "--no-filesystems",
                "--root",
                ROOT,
                step="Detecting hardware",
            )
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
    print("QUICKFLASH_NIXOS_INSTALLED")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("inventory")
    sub.add_parser("inspect").add_argument("device")
    write = sub.add_parser("install")
    write.add_argument("device")
    write.add_argument("--approved", required=True)
    write.add_argument("--wifi-connection")
    args = parser.parse_args()
    if args.command == "inventory":
        print(json.dumps(probe.collect()))
    elif args.command == "inspect":
        print(json.dumps(inspect(args.device)))
    else:
        install(args.device, json.loads(args.approved), args.wifi_connection)


if __name__ == "__main__":
    try:
        main()
    except (ValueError, OSError, subprocess.CalledProcessError) as error:
        print(str(error), file=sys.stderr)
        sys.exit(1)
