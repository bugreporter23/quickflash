"""Prepare a USB and install Quickflash's fixed NixOS ZFS base."""

import argparse
from pathlib import Path

from . import cli, installation, operations as f


def main():
    parser = argparse.ArgumentParser(
        description="Prepare a local USB or install NixOS on a remote machine over SSH.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""Examples:
  quickflash usb write ~/usb.json /dev/sdX   Build, write and configure a local USB
  quickflash install compute-a             Install on compute-a-bootstrap.local

Replace /dev/sdX with your USB disk. Writing erases that disk.
Boot the prepared USB on the remote machine before running install.""",
    )
    sub = parser.add_subparsers(dest="command")

    usb = sub.add_parser(
        "usb",
        help="Build, write or configure a USB attached to this computer",
        description="Prepare a bootable USB attached to this computer (local operations).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""Examples:
  quickflash usb write ~/usb.json /dev/sdX       Build, write and configure the USB
  quickflash usb provision ~/usb.json /dev/sdX   Update settings on an existing USB
  quickflash usb build ~/usb.json               Build an image only; USB unchanged

Replace /dev/sdX with your USB disk. Writing erases that disk.""",
    )
    boot = usb.add_subparsers(dest="action")
    build = boot.add_parser(
        "build", help="Build the prepared USB without writing a device"
    )
    write = boot.add_parser(
        "write", help="Build an image, erase the local USB and apply its settings"
    )
    for command in (build, write):
        command.add_argument(
            "provision_file",
            type=Path,
            help="Private JSON containing name, administrator public SSH keys, and optional Wi-Fi",
        )
        command.add_argument(
            "--target-module",
            type=Path,
            help="Built target NixOS module for seed and installed host",
        )
        command.add_argument(
            "--usb-image-size",
            default="6G",
            help="Prepared USB image size (default: 6G)",
        )
    write.add_argument("device")
    provision = boot.add_parser(
        "provision",
        help="Apply optional name, public keys and Wi-Fi from JSON without reflashing",
    )
    provision.add_argument(
        "file",
        nargs="?",
        type=Path,
        help="Private JSON file outside the code repository",
    )
    provision.add_argument("device")
    provision.add_argument(
        "--config-stdin", action="store_true", help=argparse.SUPPRESS
    )
    boot.add_parser(
        "reset", help="Remove provisioning while retaining the OS"
    ).add_argument("device")

    install = sub.add_parser(
        "install",
        help="Install NixOS on a remote machine over SSH after it boots the USB",
        description="Connect over SSH and install NixOS on the remote machine's disk.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""Examples:
  quickflash install compute-a          Connect to compute-a-bootstrap.local
  quickflash install root@192.0.2.10    Connect to an explicit address

Boot the prepared USB on the remote machine first.
Installation asks you to select and approve the remote disk to erase.""",
    )
    install.add_argument(
        "target",
        nargs="?",
        type=f.target,
        help="Machine name (compute-a) or root@address",
    )
    install.add_argument(
        "--disk", help="Remote disk to erase: a whole-disk /dev/disk/by-id identifier"
    )
    install.add_argument(
        "--no-reboot", action="store_true", help="Stay on the USB after installation"
    )
    install.add_argument(
        "--wifi-connection",
        help="Bootstrap Wi-Fi name or UUID; defaults to its active or sole saved connection",
    )
    cli.ssh_inputs(install)
    args = cli.parse_args(parser)
    if args.command is None:
        parser.print_help()
        return
    if args.command == "usb" and args.action is None:
        usb.print_help()
        return
    if args.command == "install" and args.target is None:
        install.print_help()
        return
    if args.command == "usb" and args.action == "provision":
        if args.config_stdin == (args.file is not None):
            parser.error("provide a provisioning file before the device")
    if args.command == "install":
        profile = f.transport.select_profile(args.ssh_config)
        with f.transport.operation(profile) as args.connection:
            dispatch(args)
    else:
        dispatch(args)


def dispatch(args):
    if args.command == "usb":
        f.bootstrap(args)
    elif args.command == "install":
        installation.flash(args)
