# Quickflash

Prepare a USB and install a fixed x86_64 NixOS ZFS base.
Requires Linux with Nix, an 8 GB USB with 512-byte sectors, and UEFI with Secure
Boot disabled. Writing and installation erase the selected disks.

Keep settings outside the repository in a mode-0600 JSON file:

```json
{
  "name": "compute-a",
  "ssh_keys": ["ssh-ed25519 REPLACE_WITH_YOUR_PUBLIC_KEY_DATA"],
  "wifi": {"ssid": "YOUR_SSID", "password": "YOUR_WIFI_PASSWORD"}
}
```

Wi-Fi is optional. Private SSH keys stay with your SSH configuration or agent.

```sh
nix develop
chmod 600 /private/settings.json
quickflash bootstrap write /private/settings.json /dev/disk/by-id/usb-CHOSEN
```

Boot the target from the USB, then use the scoped administration shell:

```sh
quickadmin
quickflash flash compute-a
```

After installation, reboot and connect with `ssh root@compute-a.local`.
Use `quickflash bootstrap provision FILE DEVICE` to reuse the USB with new settings.

Composition uses `lib.mkInstaller { targetModule, usbImageSize; }`; USB preparation
also accepts `--target-module FILE` and `--usb-image-size SIZE`. See `--help`.
