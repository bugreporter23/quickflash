"""Resolve a target NetworkManager connection and read its private keyfile."""

import base64
import configparser
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import uuid


def keyfile(ssid, password):
    if not 1 <= len(ssid.encode()) <= 32 or "\x00" in ssid:
        raise ValueError("SSID must contain 1–32 UTF-8 bytes and no NUL")
    if not (
        8 <= len(password.encode()) <= 63 or re.fullmatch("[0-9a-fA-F]{64}", password)
    ):
        raise ValueError("WPA-PSK must be 8–63 characters or 64 hex digits")
    if any(c in password for c in "\x00\r\n"):
        raise ValueError("Password cannot contain NUL or line breaks")
    escaped = password.replace("\\", "\\\\").replace(" ", "\\s").replace("\t", "\\t")
    encoded_ssid = ";".join(str(b) for b in ssid.encode()) + ";"
    return (
        f"[connection]\nid=fabric-wifi\nuuid={uuid.uuid4()}\ntype=wifi\nautoconnect=true\n"
        f"\n[wifi]\nmode=infrastructure\nssid={encoded_ssid}\n"
        f"\n[wifi-security]\nkey-mgmt=wpa-psk\npsk={escaped}\n"
        "\n[ipv4]\nmethod=auto\n\n[ipv6]\nmethod=auto\n"
    )


def split_fields(line):
    fields, value, escaped = [], "", False
    for char in line:
        if escaped:
            value += char
            escaped = False
        elif char == "\\":
            escaped = True
        elif char == ":":
            fields.append(value)
            value = ""
        else:
            value += char
    if escaped:
        raise ValueError("Invalid NetworkManager connection listing")
    return [*fields, value]


def connections():
    result = subprocess.run(
        [
            "nmcli",
            "-t",
            "--escape",
            "yes",
            "-f",
            "UUID,TYPE,ACTIVE,NAME,FILENAME",
            "connection",
            "show",
        ],
        capture_output=True,
        text=True,
        timeout=15,
        env={**os.environ, "LC_ALL": "C"},
    )
    if result.returncode:
        raise ValueError("Could not list the bootstrap's NetworkManager connections")
    records = []
    for line in result.stdout.splitlines():
        fields = split_fields(line)
        if len(fields) != 5:
            raise ValueError("Unexpected NetworkManager connection listing")
        records.append(
            dict(zip(("uuid", "type", "active", "name", "filename"), fields))
        )
    return records


def select_connection(records, selector=None):
    if selector is None:
        saved = [r for r in records if r["type"] in ("wifi", "802-11-wireless")]
        matches = [r for r in saved if r["active"] == "yes"] or saved
        if not matches:
            return None
        if len(matches) != 1:
            raise ValueError(
                "Multiple Wi-Fi connections; use --wifi-connection NAME_OR_UUID to select one"
            )
    else:
        matches = [r for r in records if r["uuid"] == selector]
        if not matches:
            matches = [r for r in records if r["name"] == selector]
        if not matches:
            raise ValueError(
                "No matching connection on the bootstrap; use nmcli connection show there"
            )
        if len(matches) != 1:
            raise ValueError(
                "Connection name is ambiguous; use --wifi-connection with its UUID"
            )
    selected = matches[0]
    if selected["type"] not in ("wifi", "802-11-wireless"):
        raise ValueError("Selected connection is not Wi-Fi")
    return selected


def validate_profile(profile, expected_uuid):
    settings = configparser.ConfigParser(interpolation=None)
    try:
        settings.read_string(profile)
    except configparser.Error:
        raise ValueError("Selected connection has an invalid keyfile") from None
    if settings.get("connection", "uuid", fallback=None) != expected_uuid:
        raise ValueError(
            "Connection changed on disk; reload it in NetworkManager and retry"
        )
    if (
        settings.get("connection", "type", fallback="")
        not in ("wifi", "802-11-wireless")
        or settings.get("wifi", "mode", fallback="infrastructure") != "infrastructure"
        or settings.get("wifi-security", "key-mgmt", fallback="") != "wpa-psk"
        or not settings.get("wifi-security", "psk", fallback="")
        or settings.get("wifi-security", "psk-flags", fallback="0") != "0"
    ):
        raise ValueError(
            "Quickflash requires an infrastructure WPA-PSK connection with its password saved in the keyfile"
        )
    if settings.get("connection", "autoconnect", fallback="true").lower() in (
        "false",
        "no",
        "0",
    ):
        raise ValueError("Selected Wi-Fi connection has autoconnect disabled")
    for section, key in (
        ("connection", "interface-name"),
        ("wifi", "mac-address"),
        ("wifi", "bssid"),
    ):
        if settings.get(section, key, fallback=""):
            raise ValueError(
                "Remove interface/MAC/BSSID binding from the Wi-Fi connection before exporting it"
            )


def read_profile(selected):
    path = Path(selected["filename"])
    allowed = (
        Path("/etc/NetworkManager/system-connections"),
        Path("/run/NetworkManager/system-connections"),
        Path("/var/lib/fabric-provision/system-connections"),
    )
    directory = path.parent.resolve()
    if directory not in allowed:
        raise ValueError(
            "Selected connection needs a saved runtime NetworkManager keyfile"
        )
    path = directory / path.name
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd) as file:
        metadata = os.fstat(file.fileno())
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid != 0
            or stat.S_IMODE(metadata.st_mode) != 0o600
        ):
            raise ValueError("Expected a root-owned mode 0600 regular Wi-Fi keyfile")
        profile = file.read()
    validate_profile(profile, selected["uuid"])
    return profile


def export(selector=None):
    selected = select_connection(connections(), selector)
    return read_profile(selected) if selected is not None else None


def install(root, profile):
    """Stage an exported profile into a fresh installation root."""
    if profile is None:
        return
    settings = configparser.ConfigParser(interpolation=None)
    settings.optionxform = str
    settings.read_string(profile)
    restricted = settings.remove_option("connection", "permissions")
    directory = Path(root) / "etc/NetworkManager/system-connections"
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    path = directory / "fabric-wifi.nmconnection"
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as file:
        if restricted:
            settings.write(file, space_around_delimiters=False)
        else:
            file.write(profile)


if __name__ == "__main__":
    try:
        profile = export(sys.argv[1] if len(sys.argv) > 1 else None)
        print(
            json.dumps(
                {
                    "profile": base64.b64encode(profile.encode()).decode()
                    if profile is not None
                    else None
                }
            )
        )
    except (ValueError, OSError, subprocess.TimeoutExpired) as exc:
        print(json.dumps({"error": str(exc)}))
