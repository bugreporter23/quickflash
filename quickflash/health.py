"""Bounded runtime checks, sent over SSH; no saved inventory or credentials."""

import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import tempfile


class FailedCheck(Exception):
    pass


def command(*args):
    result = subprocess.run(args, capture_output=True, text=True, timeout=15)
    if result.returncode:
        raise RuntimeError(f"{args[0]} exited {result.returncode}")
    return result.stdout


def platform_check():
    architecture = platform.machine()
    if architecture != "x86_64" or not Path("/etc/NIXOS").exists():
        raise FailedCheck("Expected x86_64 NixOS")
    mode = "bootstrap" if Path("/etc/fabric-bootstrap").exists() else "installed"
    return f"x86_64 NixOS, {mode} environment"


def service_check(unit):
    state = command(
        "systemctl", "show", "--property=ActiveState", "--value", unit
    ).strip()
    if state != "active":
        raise FailedCheck(f"{unit}: {state or 'unknown state'}")
    return f"{unit} is active"


def network_check():
    output = command(
        "nmcli",
        "-t",
        "-f",
        "GENERAL.DEVICE,GENERAL.TYPE,GENERAL.STATE",
        "device",
        "show",
    )
    devices = []
    current = {}
    for line in [*output.splitlines(), "GENERAL.DEVICE:"]:
        key, separator, value = line.partition(":")
        if not separator:
            continue
        if key == "GENERAL.DEVICE":
            if current:
                devices.append(current)
            current = {}
        current[key] = value
    connected = [
        device
        for device in devices
        if device.get("GENERAL.TYPE") in ("wifi", "ethernet")
        and device.get("GENERAL.STATE", "").split(" ", 1)[0] == "100"
    ]
    if not connected:
        raise FailedCheck(
            "No connected Wi-Fi or Ethernet device reported by NetworkManager"
        )
    return ", ".join(
        f"{device['GENERAL.DEVICE']} ({device['GENERAL.TYPE']}) connected"
        for device in connected
    )


def storage_check():
    stats = os.statvfs("/")
    available = stats.f_bavail * stats.f_frsize
    if stats.f_flag & os.ST_RDONLY:
        raise FailedCheck("Root filesystem is read-only")
    if available < 256 * 1024 * 1024:
        raise FailedCheck(
            f"Root has {available // (1024 * 1024)} MiB available; need at least 256 MiB headroom"
        )
    if stats.f_files and stats.f_favail == 0:
        raise FailedCheck("Root filesystem has no available inodes")
    with tempfile.TemporaryFile(prefix=".quickflash-health-", dir="/") as scratch:
        value = bytes(range(256)) * 16
        scratch.write(value)
        scratch.flush()
        os.fsync(scratch.fileno())
        scratch.seek(0)
        if scratch.read() != value:
            raise FailedCheck("Root filesystem scratch-file readback mismatch")
    return (
        f"Root write/fsync/readback passed; {available // (1024 * 1024)} MiB available"
    )


def inventory_check():
    disks = json.loads(command("lsblk", "--json", "--bytes", "-o", "NAME,TYPE,SIZE"))[
        "blockdevices"
    ]
    count = sum(disk["type"] == "disk" and disk["size"] > 0 for disk in disks)
    if not count:
        raise FailedCheck("No nonempty whole disks visible")
    return f"Block inventory readable; {count} whole disk(s), not selected or tested for installation"


def tools_check():
    names = ("nix", "nixos-facter", "nmtui", "lsblk", "ip", "curl")
    missing = [name for name in names if shutil.which(name) is None]
    if missing:
        raise FailedCheck("Missing tools: " + ", ".join(missing))
    return "Required provisioning tools are present (availability only)"


def kernel_observations():
    lines = command(
        "journalctl",
        "-k",
        "-b",
        "-p",
        "warning",
        "-n",
        "200",
        "-o",
        "json",
        "--no-pager",
    ).splitlines()
    messages = [json.loads(line).get("MESSAGE", "") for line in lines if line.strip()]
    return {
        "sampled_entries": len(messages),
        "sample_limit": 200,
        "correctable_pcie": sum(
            "severity=Correctable" in message for message in messages
        ),
        "uncorrectable_pcie": sum(
            "severity=Uncorrectable" in message for message in messages
        ),
        "old_microcode": any(
            "Running old microcode" in message for message in messages
        ),
        "scope": "Latest 200 kernel warning/error entries this boot; observations do not determine functional check results",
    }


def collect():
    checks = []
    for name, operation in (
        ("platform", platform_check),
        ("ssh-service", lambda: service_check("sshd.service")),
        ("network-service", lambda: service_check("NetworkManager.service")),
        ("network-link", network_check),
        ("root-storage", storage_check),
        ("block-inventory", inventory_check),
        ("tools", tools_check),
    ):
        try:
            detail = operation()
            status = "pass"
        except (FailedCheck, OSError) as exc:
            status, detail = "fail", str(exc)
        except (RuntimeError, ValueError, KeyError, subprocess.TimeoutExpired) as exc:
            status, detail = "unknown", str(exc)
        checks.append({"name": name, "status": status, "detail": detail})
    try:
        kernel = kernel_observations()
    except (OSError, RuntimeError, ValueError, subprocess.TimeoutExpired) as exc:
        kernel = {"error": str(exc)}
    return {
        "checks": checks,
        "kernel": kernel,
        "not_tested": [
            "Sustained network stability, throughput, and reconnect after reboot",
            "Automatic hostname discovery, external DNS/Internet/cache access",
            "Install-disk health/capacity, image writing and installed-image boot",
            "Firmware correctness, CPU/memory stress, and peripherals",
        ],
    }


if __name__ == "__main__":
    print(json.dumps(collect()))
