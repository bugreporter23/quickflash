"""Prepare a USB and install Quickflash's fixed NixOS ZFS base."""

import argparse
from pathlib import Path

from . import cli, installation, operations as f


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    boot = sub.add_parser(
        "bootstrap", help="Prepare the bootable installation USB"
    ).add_subparsers(dest="action", required=True)
    build = boot.add_parser(
        "build", help="Build the prepared USB without writing a device"
    )
    write = boot.add_parser("write", help="Build and write the installation USB")
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

    flash = sub.add_parser(
        "flash", help="Install the fixed NixOS ZFS base from the target's prepared USB"
    )
    flash.add_argument("target", type=f.target)
    flash.add_argument(
        "--wifi-connection",
        help="Bootstrap Wi-Fi name or UUID; defaults to its active or sole saved connection",
    )
    cli.ssh_inputs(flash)
    args = cli.parse_args(parser)
    if args.command == "bootstrap" and args.action == "provision":
        if args.config_stdin == (args.file is not None):
            parser.error("provide a provisioning file before the device")
    if args.command == "flash":
        profile = f.transport.select_profile(args.ssh_config)
        with f.transport.operation(profile) as args.connection:
            dispatch(args)
    else:
        dispatch(args)


def dispatch(args):
    if args.command == "bootstrap":
        f.bootstrap(args)
    elif args.command == "flash":
        installation.flash(args)
