# Quickflash

A portable Nix-packaged tool for preparing a reusable USB and installing physical
machines over SSH. Run it on an x86_64 Linux distro with Nix; it supplies its tool
environment. Quickadmin supplies scoped SSH/SCP/SFTP, mDNS discovery, inspection
and health checks using your SSH config and agent. No operator resolver changes
or management daemon. See [what Quickflash saves](docs/PHYSICAL-WORLD.md).

The target is x86_64 UEFI NixOS with stock ZFS: a 1 GiB `/boot` and pool `bowl`
for `/`, `/home` and `/nix`. Use an 8 GB USB with 512-byte sectors; disable
Secure Boot. Writing and installation erase the selected disks.

`quickflash usb` prepares a USB attached to this computer. `quickflash install`
installs onto a remote machine over SSH after it boots that USB. Run either
command without arguments for help and examples.

Keep settings outside the repository in a mode-0600 JSON file:

```json
{
  "name": "compute-a",
  "ssh_keys": ["ssh-ed25519 REPLACE_WITH_YOUR_PUBLIC_KEY_DATA"],
  "packages": ["git", "vim", "htop"],
  "timezone": "America/New_York",
  "user": null,
  "wifi": {"ssid": "YOUR_SSID", "password": "YOUR_WIFI_PASSWORD"}
}
```

Wi-Fi is optional. `packages` lists additional nixpkgs attributes; the base keeps
required diagnostic tools. Optional `timezone` sets the installed machine's
timezone using an IANA name, e.g. `America/New_York` or `UTC`; omission keeps the
system default. `user: null` means root only; omit it to ask at install
time, or use `{"name": "bob"}` for a login user with sudo requiring their password.
Quickflash provisions SSH keys on root only. With a login user, install asks for
root's password and any missing user password before approval. Root-only installs
start with root's password locked. Optional user fields: `sudo`, `hashed_password`.
Setup passwords are initial-only; later `passwd` changes survive rebuild and reboot.
SSH remains key-only.
Private keys stay with your SSH configuration or agent.

```sh
nix develop
chmod 600 /private/settings.json
quickflash usb write /private/settings.json /dev/disk/by-id/usb-CHOSEN
```

This builds the image, writes the USB and applies its settings in one command.
`quickflash usb build FILE` only builds an image; it leaves the USB unchanged.

Boot the target, then:

```sh
quickadmin
quickflash install compute-a
```

Allow inbound UDP 5353 on the operator firewall for mDNS, or use `root@IP`.
Install evaluates the configuration and selected Wi-Fi before fresh disk approval.
`--disk ID` selects the disk up front; `--wifi-connection NAME_OR_UUID` selects
among saved Wi-Fi profiles. After installation it prints the installed SSH key
over the authenticated session; add it to known_hosts before connecting.
It reboots automatically (`--no-reboot` to stay). Connect to `root@compute-a.local`.

Reuse the USB with `quickflash usb provision FILE DEVICE`; omitted fields
and the USB server key are preserved. `usb reset DEVICE` clears settings.
The USB carries built target software and its compressed cache. Installation may
fetch additional hardware or stock package dependencies.

`/etc/nixos` retains default Quickflash source, identity and hardware facts.
Edit it and run `nixos-rebuild switch`; nixpkgs is pinned to the installed version.
External target modules remain explicit pinned store imports. Exported
`nixosModules.installed-base` retains host boot/storage configuration; supply
machine identity, a stable hostId and retained hardware configuration.

Run `sudo quickflash-remove-key` after setup to clear root's Quickflash keys
from `identity.json` and rebuild. Other configured keys are unchanged. Removing
the last keys prevents new SSH logins.

Compose with `lib.mkInstaller { targetModule, usbImageSize; }`; preparation also
accepts `--target-module FILE` and `--usb-image-size SIZE`. Quickflash owns the
physical host; compute datasets and fleet authority belong to selected services.
