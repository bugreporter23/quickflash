{ config, lib, ... }:
let
  label = "sanae-${
    builtins.substring 0 16 (builtins.hashString "sha256" config.networking.hostName)
  }";
in
{
  disko.devices.disk.main = {
    type = "disk";
    device = "/dev/disk/by-id/QUICKFLASH-USB";
    content = {
      type = "gpt";
      partitions = {
        bios = {
          label = "${label}-bios";
          size = "1M";
          type = "EF02";
          priority = 1;
        };
        ESP = {
          label = "${label}-esp";
          size = "512M";
          type = "EF00";
          content = {
            type = "filesystem";
            format = "vfat";
            mountpoint = "/boot";
            mountOptions = [ "umask=0077" ];
          };
        };
        root = {
          label = "${label}-root";
          size = "100%";
          content = {
            type = "filesystem";
            format = "ext4";
            mountpoint = "/";
          };
        };
      };
    };
  };
  boot.loader.grub = {
    enable = true;
    device = config.disko.devices.disk.main.device;
    efiSupport = lib.mkDefault true;
    efiInstallAsRemovable = lib.mkDefault true;
  };
  boot.loader.efi.canTouchEfiVariables = false;
}
