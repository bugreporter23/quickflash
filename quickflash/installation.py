"""Control a USB-local installation over authenticated SSH."""

import json
import shlex

from . import operations as f


def flash(args):
    f.transport.forget_host(args.target, args.connection)
    f.transport.authorize(args.target, args.connection)

    def remote(*command, timeout=30, live=False):
        return f.transport.remote(
            args.target,
            shlex.join(["quickflash-install", *command]),
            args.connection,
            timeout=timeout,
            live=live,
        )

    info = json.loads(remote("inventory"))
    f.show_probe(info)
    if info["architecture"] != "x86_64" or not info["bootstrap"]:
        raise ValueError("Installation requires the prepared x86_64 Quickflash USB")
    device = input("Whole disk to erase (/dev/disk/by-id/...): ").strip()
    f.select_disk(info, device)
    approved = json.loads(remote("inspect", device))
    f.confirm(
        f"Erase {device} on {args.target} and install {approved['identity']['name']} from its USB.\n"
        + f.disk_description(approved["disk"])
        + "\nUEFI: 1 GiB EFI partition, remaining disk ZFS pool bowl."
        + "\nAll existing disk contents will be replaced."
    )
    options = (
        ["--wifi-connection", args.wifi_connection] if args.wifi_connection else []
    )
    print("Installing NixOS...", flush=True)
    result = remote(
        "install",
        device,
        "--approved",
        json.dumps(approved),
        *options,
        timeout=3600,
        live=True,
    )
    if "QUICKFLASH_NIXOS_INSTALLED" not in result.splitlines():
        raise ValueError(
            "Installation result missing; inspect the target before a fresh attempt"
        )
    print(
        "NixOS installation completed. Reboot deliberately, then connect to the installed host."
    )
