#!/usr/bin/env python3
"""Resolve one LAN hostname, then relay OpenSSH's connection without host NSS."""

import argparse
import ipaddress
import os
import re
import socket
import time
import sys

from zeroconf import (
    AddressResolverIPv4,
    DNSQuestionType,
    InterfaceChoice,
    IPVersion,
    Zeroconf,
)

SOCAT = "socat"                                                      


def local_name(value):
    value = value.lower().removesuffix(".")
    if not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.local", value):
        raise ValueError(
            "Expected a single-label mDNS hostname such as compute-a.local"
        )
    return value + "."


def resolve(name, interface=None):
    name = local_name(name)
    interfaces = (
        [str(ipaddress.IPv4Address(interface))] if interface else InterfaceChoice.All
    )
    with Zeroconf(
        interfaces=interfaces, ip_version=IPVersion.V4Only, unicast=False
    ) as zc:
        answer = AddressResolverIPv4(name)
        if not answer.request(zc, 3000, question_type=DNSQuestionType.QM):
            raise ValueError(
                f"No mDNS IPv4 answer for {name} within 3 seconds. "
                "Check the LAN/multicast firewall, select a local interface with "
                "FABRIC_MDNS_INTERFACE=IPv4, or use the target's explicit IP."
            )
        addresses = sorted(set(answer.parsed_addresses(IPVersion.V4Only)))
        for value in addresses:
            address = ipaddress.IPv4Address(value)
            if address.is_loopback or address.is_multicast or address.is_unspecified:
                raise ValueError(
                    f"Unusable LAN address advertised for {name}: {address}"
                )
        return addresses


def connect(addresses, port):
    deadline = time.monotonic() + 5
    for index, address in enumerate(addresses):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        try:
            return socket.create_connection(
                (address, port), timeout=remaining / (len(addresses) - index)
            )
        except OSError:
            continue
    raise OSError(
        f"No advertised mDNS address accepted TCP within 5 seconds: {', '.join(addresses)}"
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("hostname")
    parser.add_argument("port", type=int)
    args = parser.parse_args()
    try:
        if not 1 <= args.port <= 65535:
            raise ValueError("Port must be between 1 and 65535")
        addresses = resolve(args.hostname, os.environ.get("FABRIC_MDNS_INTERFACE"))
        connection = connect(addresses, args.port)
        connection.settimeout(None)
        connection.set_inheritable(True)
        os.execv(SOCAT, [SOCAT, "STDIO", f"FD:{connection.fileno()}"])
    except (OSError, ValueError) as error:
        print(f"quickflash-mdns-connect: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
