"""Whole-disk inspection shared by USB preparation and installation."""

import json
import os
from pathlib import Path
import stat
import subprocess


def mounted(disk):
    return (
        any(disk.get("mountpoints") or [])
        or disk.get("type") == "crypt"
        or any(mounted(child) for child in disk.get("children", []))
    )


def inspect_disk(device):
    if not Path("/etc/fabric-bootstrap").exists():
        raise ValueError("Flashing requires the running Quickflash bootstrap")
    if not device.startswith("/dev/disk/by-id/"):
        raise ValueError("Select a stable whole-disk identifier")
    if not stat.S_ISBLK(os.stat(device).st_mode):
        raise ValueError("Target is not a block device")
    disks = json.loads(
        subprocess.check_output(
            [
                "lsblk",
                "--json",
                "--bytes",
                "--paths",
                "-o",
                "NAME,TYPE,SIZE,MODEL,SERIAL,RO,MOUNTPOINTS,LOG-SEC",
                device,
            ],
            text=True,
        )
    )["blockdevices"]

    if len(disks) != 1:
        raise ValueError("Expected exactly one whole disk")
    disk = disks[0]
    if disk["type"] != "disk" or disk["ro"] or mounted(disk):
        raise ValueError("Refusing a read-only or mounted disk, including the USB")
    return disk
