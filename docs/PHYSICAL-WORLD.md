# What Quickflash saves

Installing a physical machine means finding a USB, preparing it, getting the
firmware to boot it, establishing networking, identifying the right disk and
waiting for the install. A mistake can mean another walk to the machine,
another media write, another boot, or a destroyed disk. Those costs matter
more than the number of files in this repository.

Quickflash makes the operator environment portable across Linux distributions:
with Nix installed, run the tools as you would run `btop`. Nix supplies their
userspace dependencies. The scoped `quickadmin` shell supplies SSH/SCP/SFTP and
mDNS resolution while retaining your SSH configuration and agent. Closing the
shell ends that scope. You do not install a management service or reconfigure
your system resolver to administer a machine. From a source checkout:

```sh
nix run .#quickadmin
quickflash install MACHINE
```

This is why the boundaries are part of the product:

| Physical or operator cost | Quickflash capability |
| --- | --- |
| Preparing each operator's distro for installation and local discovery | Nix-packaged tools and scoped SSH/mDNS clients |
| Rebuilding and rewriting media for every machine | Reusable software payload with separate mutable name, keys, Wi-Fi and install choices |
| Rebuilding local software at the target or arranging a binary cache service | USB carries selected target modules and their software cache |
| Guessing which machine or disk will be erased | Authenticated inspection, stable disk IDs and fresh typed approval |
| Discovering a bad configuration after wiping the disk | Configuration and selected Wi-Fi preflight before approval |
| Returning to answer questions or reboot | Frontloaded questions and automatic reboot with an opt-out |
| Recreating the source that produced the installed machine | Retained configuration and hardware facts in `/etc/nixos` |
| Installing more tooling just to diagnose a machine | Scoped inspection and bounded runtime health checks |

The remaining physical steps are explicit: select the USB, boot the target,
provide working Ethernet or Wi-Fi, and approve the destination disk. The
operator firewall must allow mDNS replies (UDP 5353), or use an explicit IP.
The current operator packages are x86_64 Linux; the target is x86_64 UEFI NixOS
on ZFS. Distribution independence does not imply support for every operator OS,
firmware or network. Installation may fetch extra hardware or package choices;
the USB cache does not promise a fully offline install.

A cleanup should reduce the work inside these boundaries while preserving the
work they save outside the repository.
