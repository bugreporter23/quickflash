{
  config,
  lib,
  ...
}:
let
  storage = import ../disk/install-zfs.nix;
  mounted = lib.filterAttrs (_: dataset: dataset ? mountpoint) storage.datasets;
in
{
  imports = [
    ./base.nix
    ./openzfs-zvol-sync.nix
  ];
  users.mutableUsers = false;
  users.users.root.hashedPassword = "!";
  networking.hostId = builtins.substring 0 8 (
    builtins.hashString "sha256" config.networking.hostName
  );
  boot.supportedFilesystems = [ "zfs" ];
  boot.zfs.forceImportRoot = false;
  boot.loader.systemd-boot.enable = true;
  boot.loader.efi.canTouchEfiVariables = false;
  fileSystems =
    lib.listToAttrs (
      lib.mapAttrsToList (name: dataset: {
        name = dataset.mountpoint;
        value = {
          device = "${storage.pool}/${name}";
          fsType = "zfs";
        };
      }) mounted
    )
    // {
      "/boot" = {
        device = "/dev/disk/by-partlabel/${storage.espLabel}";
        fsType = "vfat";
        options = [ "umask=0077" ];
      };
    };
}
