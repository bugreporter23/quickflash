"""Read-only hardware probe, sent over SSH by quickflash and run with target Python."""

import json
import os
from pathlib import Path
import subprocess


def command(*args):
    return subprocess.check_output(args, text=True)


def collect():
    disks = json.loads(
        command(
            "lsblk",
            "--json",
            "--bytes",
            "--paths",
            "-o",
            "NAME,TYPE,SIZE,MODEL,SERIAL,TRAN,RM,RO,MOUNTPOINTS,FSTYPE",
        )
    )["blockdevices"]
    for disk in disks:
        disk["ids"] = sorted(
            str(path)
            for path in Path("/dev/disk/by-id").glob("*")
            if os.path.realpath(path) == disk["name"] and "-part" not in path.name
        )
    return {
        "architecture": command("uname", "-m").strip(),
        "boot_mode": "uefi" if Path("/sys/firmware/efi").exists() else "bios",
        "cpu": command("lscpu"),
        "memory": command("free", "-h"),
        "interfaces": json.loads(command("ip", "-j", "address")),
        "disks": disks,
        "bootstrap": Path("/etc/fabric-bootstrap").exists(),
        "facter": json.loads(command("nixos-facter")),
    }


if __name__ == "__main__":
    print(json.dumps(collect()))
