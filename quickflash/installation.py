"""Control a USB-local installation over authenticated SSH."""

import getpass
import json
import shlex
import sys

from . import operations as f


def ask_password(label):
    if not sys.stdin.isatty():
        raise ValueError("Password setup requires an interactive terminal")
    while True:
        password = getpass.getpass(f"{label}: ")
        if not password:
            print("Password cannot be empty.")
            continue
        if password != getpass.getpass(f"Confirm {label}: "):
            print("Passwords do not match.")
            continue
        return f.run(
            ["mkpasswd", "--method=yescrypt", "--stdin"],
            capture=True,
            input=password + "\n",
            timeout=5,
        ).strip()


def ask_accounts(settings):
    if "user" in settings:
        user = settings["user"]
    else:
        name = input("Create a login user? Name, or Enter for root only: ").strip()
        user = {"name": name} if name else None
    user = f.settings.user(user)
    if user is None:
        return {"user": None, "root_hashed_password": None}
    print(
        f"Login user: {user['name']} (sudo {'enabled' if user['sudo'] else 'disabled'})"
    )
    root_hash = ask_password("Root password")
    if "hashed_password" not in user:
        user["hashed_password"] = ask_password(f"Password for {user['name']}")
    return {"user": user, "root_hashed_password": root_hash}


def flash(args):
    f.transport.forget_host(args.target, args.connection)
    f.transport.authorize(args.target, args.connection)

    def remote(*command, timeout=30, live=False, input=None):
        return f.transport.remote(
            args.target,
            shlex.join(["quickflash-install", *command]),
            args.connection,
            timeout=timeout,
            live=live,
            input=input,
        )

    info = json.loads(remote("inventory"))
    f.show_probe(info)
    if info["architecture"] != "x86_64" or not info["bootstrap"]:
        raise ValueError("Installation requires the prepared x86_64 Quickflash USB")
    settings = info["settings"]
    print(f"\nHost: {settings['name']}")
    if "timezone" in settings:
        print(f"Timezone: {settings['timezone']}")
    print("Additional packages: " + (", ".join(settings["packages"]) or "none"))
    accounts = ask_accounts(settings)
    options = (
        ["--wifi-connection", args.wifi_connection] if args.wifi_connection else []
    )
    identity = json.loads(
        remote("stage", *options, input=json.dumps(accounts), timeout=900)
    )
    device = (args.disk or input("Whole disk to erase (/dev/disk/by-id/...): ")).strip()
    f.select_disk(info, device)
    approved = json.loads(remote("inspect", device))
    f.confirm(
        f"Erase {device} on {args.target} and install {approved['identity']['name']} from its USB.\n"
        + f.disk_description(approved["disk"])
        + f"\nLogin user: {identity['user']['name'] if identity['user'] else 'none (root only)'}"
        + "\nUEFI: 1 GiB EFI partition, remaining disk ZFS pool bowl."
        + "\nAll existing disk contents will be replaced."
        + ("" if args.no_reboot else "\nReboot into the installed system afterwards.")
    )
    print("Installing NixOS...", flush=True)
    result = remote(
        "install",
        device,
        input=json.dumps(approved),
        timeout=4 * 3600,
        live=True,
    )
    if "QUICKFLASH_NIXOS_INSTALLED" not in result.splitlines():
        raise ValueError(
            "Installation result missing; inspect the target before a fresh attempt"
        )
    key = next(
        (
            line.split(" ", 1)[1]
            for line in result.splitlines()
            if line.startswith("QUICKFLASH_HOST_KEY ")
        ),
        None,
    )
    if key is None:
        raise ValueError("Installed host SSH key missing; inspect the USB log")
    key = f.trust.public_key(key)
    print("\nInstalled host SSH key received over the authenticated bootstrap session:")
    print(f"{identity['name']}.local {key}")
    print("Add this entry to your known_hosts before connecting to the installed host.")
    if args.no_reboot:
        print("NixOS installed. Reboot when ready.")
        return
    f.transport.remote(
        args.target,
        "systemd-run --on-active=2s --timer-property=AccuracySec=1s -- systemctl reboot",
        args.connection,
    )
    print(f"Rebooting. Then: ssh root@{identity['name']}.local")
